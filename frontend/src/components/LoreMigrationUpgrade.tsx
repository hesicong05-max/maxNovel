import { KeyboardEvent as ReactKeyboardEvent, useEffect, useMemo, useRef, useState } from "react";
import { ApiError, api } from "@/services/api";
import { clearDraft, loadDraft, saveDraft, type DraftScope } from "@/services/maintenanceDrafts";
import type {
  LoreMigrationCommitInput,
  LoreMigrationOperation,
  LoreMigrationPreviewResponse,
} from "@/types/lore";

type MigrationPhase =
  | "confirming"
  | "outcome_unknown"
  | "validating"
  | "maintenance"
  | "retryable"
  | "failed";

export interface StoredMigrationDraft {
  version: 1;
  phase: MigrationPhase;
  input: LoreMigrationCommitInput;
  checkedAt: string;
  legacyTotal: number;
}

const OPERATION_KEY = /^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$/;
const CHECKSUM = /^[a-f0-9]{64}$/;

function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value);
}

export function isStoredMigrationDraft(value: unknown): value is StoredMigrationDraft {
  if (!isRecord(value) || value.version !== 1) return false;
  if (!["confirming", "outcome_unknown", "validating", "maintenance", "retryable", "failed"].includes(String(value.phase))) return false;
  if (typeof value.checkedAt !== "string" || typeof value.legacyTotal !== "number" || value.legacyTotal < 1) return false;
  if (!isRecord(value.input)) return false;
  const input = value.input;
  return typeof input.operation_key === "string"
    && OPERATION_KEY.test(input.operation_key)
    && typeof input.preview_schema_version === "number"
    && input.preview_schema_version >= 1
    && typeof input.mapping_version === "number"
    && input.mapping_version >= 1
    && typeof input.expected_source_checksum === "string"
    && CHECKSUM.test(input.expected_source_checksum)
    && typeof input.expected_semantic_result_checksum === "string"
    && CHECKSUM.test(input.expected_semantic_result_checksum)
    && input.confirm_legacy_retained_no_automatic_rollback === true;
}

function operationKey(): string | null {
  try {
    if (typeof globalThis.crypto?.randomUUID !== "function") return null;
    return `lore-migration:${globalThis.crypto.randomUUID()}`;
  } catch {
    return null;
  }
}

function operationMatches(operation: LoreMigrationOperation, draft: StoredMigrationDraft): boolean {
  const input = draft.input;
  return operation.operation_key === input.operation_key
    && operation.source_checksum === input.expected_source_checksum
    && operation.preview_schema_version === input.preview_schema_version
    && operation.mapping_version === input.mapping_version
    && operation.semantic_result_checksum === input.expected_semantic_result_checksum;
}

function previewMatches(preview: LoreMigrationPreviewResponse, draft: StoredMigrationDraft): boolean {
  const input = draft.input;
  return preview.overall_status === "ready"
    && preview.commit_available
    && preview.counts.legacy_total === draft.legacyTotal
    && preview.preview_schema_version === input.preview_schema_version
    && preview.mapping_version === input.mapping_version
    && preview.source_checksum === input.expected_source_checksum
    && preview.semantic_result_checksum === input.expected_semantic_result_checksum;
}

function failedMessage(errorCode: string | null): string {
  if (errorCode === "LORE_MIGRATION_PREVIEW_STALE" || errorCode === "LORE_MIGRATION_PREVIEW_VERSION_MISMATCH") {
    return "检查结果已过期，升级未完成。旧世界观资料仍保留，请重新检查。";
  }
  if (errorCode === "LORE_MIGRATION_VALIDATION_FAILED" || errorCode === "LORE_MIGRATION_RESULT_CHECKSUM_MISMATCH") {
    return "升级校验未通过，旧世界观资料仍保留。请稍后重新检查或联系维护人员。";
  }
  return "升级未完成，旧世界观资料仍保留。请重新检查后再决定是否继续。";
}

export default function LoreMigrationUpgrade({
  projectId,
  userId,
  report,
  onRequestPreviewReload,
  onUpgraded,
}: {
  projectId: string;
  userId: string;
  report: LoreMigrationPreviewResponse | null;
  onRequestPreviewReload: () => void;
  onUpgraded: () => void;
}) {
  const migrationScope = useMemo<DraftScope>(() => ({
    userId,
    projectId,
    kind: "lore-migration",
    objectId: "legacy-to-relational-v1",
  }), [projectId, userId]);
  const worldviewScope = useMemo<DraftScope>(() => ({
    userId,
    projectId,
    kind: "worldview",
    objectId: "worldview",
  }), [projectId, userId]);
  const [stored, setStored] = useState<StoredMigrationDraft | null>(null);
  const [operation, setOperation] = useState<LoreMigrationOperation | null>(null);
  const [busy, setBusy] = useState<"commit" | "check" | "retry" | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [acknowledged, setAcknowledged] = useState(false);
  const [storageChecked, setStorageChecked] = useState(false);
  const [blocked, setBlocked] = useState(false);
  const [message, setMessage] = useState("");
  const [notice, setNotice] = useState("");
  const confirmRef = useRef<HTMLDivElement | null>(null);
  const confirmCancelRef = useRef<HTMLButtonElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const messageRef = useRef<HTMLDivElement | null>(null);
  const successRef = useRef<HTMLHeadingElement | null>(null);
  const submitLock = useRef(false);
  const checkLock = useRef(false);
  const recoveryStarted = useRef(false);
  const pollCount = useRef(0);

  function persist(next: StoredMigrationDraft): boolean {
    const saved = saveDraft(migrationScope, next, null);
    if (saved.status === "saved") return true;
    setBlocked(true);
    setMessage("浏览器无法安全保存升级请求。为避免刷新后重复处理，本次没有提交；请检查浏览器存储设置。");
    return false;
  }

  function setFailure(text: string, shouldBlock = false) {
    setMessage(text);
    if (shouldBlock) setBlocked(true);
  }

  function handleOperation(next: LoreMigrationOperation, draft: StoredMigrationDraft) {
    if (!operationMatches(next, draft)) {
      setFailure("服务器返回的升级记录与本机请求不一致。为避免重复处理，已停止新的升级，请联系维护人员。", true);
      return;
    }
    setOperation(next);
    if (next.status === "ready") {
      setMessage("");
      setNotice(next.replayed
        ? "已核对：这次升级此前已经完成，没有重复创建设定。"
        : "设定仓库升级完成。原世界观资料仍保留为历史来源。"
      );
      const cleared = clearDraft(migrationScope);
      if (cleared.status === "unavailable") {
        setNotice("设定仓库升级完成，但本机请求记录未能清除；请勿再次提交，可刷新后继续核对同一结果。");
      } else {
        setStored(null);
      }
      onUpgraded();
      return;
    }
    const phase: MigrationPhase = next.status === "validating" ? "validating" : "failed";
    const updated = { ...draft, phase };
    persist(updated);
    setStored(updated);
    if (next.status === "validating") {
      setMessage("");
      setNotice(pollCount.current >= 3
        ? "自动核对已暂告一段落，升级仍在安全校验中；你可以继续手动核对同一次请求。"
        : "正在安全校验这一次升级；系统不会创建新的请求。"
      );
    } else {
      setNotice("");
      setFailure(failedMessage(next.error_code));
    }
  }

  async function reconcile(draft: StoredMigrationDraft) {
    if (checkLock.current) return;
    checkLock.current = true;
    setBusy("check");
    setMessage("");
    try {
      const next = await api.getLoreMigrationOperationByKey(projectId, draft.input.operation_key);
      handleOperation(next, draft);
    } catch (error) {
      if (error instanceof ApiError && error.status === 404 && error.code === "LORE_MIGRATION_OPERATION_NOT_FOUND") {
        const retryable = { ...draft, phase: "retryable" as const };
        persist(retryable);
        setStored(retryable);
        setNotice("");
        setFailure("服务器尚未找到这次升级。可先核对最新检查结果，再使用原请求安全重试。旧资料未因此更改。");
      } else if (error instanceof ApiError && error.status === 404) {
        setFailure("当前项目或升级记录无法核对。为避免重复处理，已停止新的升级，请返回项目或联系维护人员。", true);
      } else if (error instanceof ApiError && error.status === 401) {
        setFailure("登录状态已失效。请重新登录后返回本项目；本机已保留这次请求，系统不会创建新请求。");
      } else if (error instanceof ApiError && error.status === 403) {
        setFailure("当前账号无法查看或操作该项目。本机请求已保留，系统不会创建新请求。", true);
      } else {
        const unknown = { ...draft, phase: "outcome_unknown" as const };
        persist(unknown);
        setStored(unknown);
        setFailure("暂时无法确认升级结果。原请求已冻结，请稍后继续核对；不要创建新的升级请求。");
      }
    } finally {
      setBusy(null);
      checkLock.current = false;
    }
  }

  useEffect(() => {
    if (recoveryStarted.current) return;
    recoveryStarted.current = true;
    const loaded = loadDraft<unknown>(migrationScope);
    if (loaded.status === "unavailable") {
      setFailure("浏览器存储不可用。为避免无法恢复升级结果，已停止新的升级。", true);
    } else if (loaded.status === "corrupt") {
      setFailure("无法安全确认上次升级结果。本机记录缺失或损坏，为避免重复处理，已停止新的升级，请联系维护人员。", true);
    } else if (loaded.status === "available" || loaded.status === "expired") {
      if (!isStoredMigrationDraft(loaded.draft.payload)) {
        setFailure("无法安全确认上次升级结果。本机记录缺失或损坏，为避免重复处理，已停止新的升级，请联系维护人员。", true);
      } else {
        const draft = loaded.draft.payload;
        setStored(draft);
        if (draft.phase === "confirming") {
          setConfirmOpen(true);
          setNotice("已恢复尚未提交的升级确认；系统没有自动开始升级。请重新确认。 ");
        } else {
          void reconcile(draft);
        }
      }
    }
    setStorageChecked(true);
    // Scope changes remount this component from the repository page.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [migrationScope]);

  useEffect(() => {
    if (!storageChecked || stored || operation?.status === "ready") return;
    if (report?.storage_mode === "migrating") {
      setFailure("项目正在升级，但本机没有可核对的安全请求记录。为避免重复处理，已停止新的升级，请联系维护人员。", true);
    }
  }, [operation?.status, report?.storage_mode, storageChecked, stored]);

  useEffect(() => {
    if (!stored || operation?.status !== "validating" || pollCount.current >= 3) return;
    pollCount.current += 1;
    const timer = window.setTimeout(() => void reconcile(stored), 1500);
    return () => window.clearTimeout(timer);
    // Reconcile deliberately follows the newest server timestamp.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [operation?.updated_at, operation?.status, stored]);

  useEffect(() => {
    pollCount.current = 0;
  }, [stored?.input.operation_key]);

  useEffect(() => {
    if (confirmOpen) requestAnimationFrame(() => confirmCancelRef.current?.focus());
  }, [confirmOpen]);
  useEffect(() => {
    if (message) requestAnimationFrame(() => messageRef.current?.focus());
  }, [message]);
  useEffect(() => {
    if (operation?.status === "ready") requestAnimationFrame(() => successRef.current?.focus());
  }, [operation?.status]);

  function openConfirmation() {
    if (!report || !report.commit_available || report.overall_status !== "ready" || report.counts.legacy_total < 1 || busy || blocked || stored) return;
    const worldviewDraft = loadDraft<unknown>(worldviewScope);
    if (worldviewDraft.status !== "missing") {
      setFailure(worldviewDraft.status === "unavailable"
        ? "无法确认是否存在未保存的世界观草稿，本次没有提交。请检查浏览器存储设置。"
        : "检测到尚未处理的世界观草稿。请先返回世界观编辑器保存或处理草稿，再重新检查。"
      );
      return;
    }
    const nextOperationKey = operationKey();
    if (!nextOperationKey) {
      setFailure("当前浏览器无法生成可靠的安全请求标识。为避免重复处理，本次没有提交；请升级浏览器后重试。", true);
      return;
    }
    const input: LoreMigrationCommitInput = {
      operation_key: nextOperationKey,
      preview_schema_version: report.preview_schema_version,
      mapping_version: report.mapping_version,
      expected_source_checksum: report.source_checksum,
      expected_semantic_result_checksum: report.semantic_result_checksum,
      confirm_legacy_retained_no_automatic_rollback: true,
    };
    const next: StoredMigrationDraft = {
      version: 1,
      phase: "confirming",
      input,
      checkedAt: report.checked_at,
      legacyTotal: report.counts.legacy_total,
    };
    if (!persist(next)) return;
    setStored(next);
    setAcknowledged(false);
    setMessage("");
    setConfirmOpen(true);
  }

  function closeConfirmation() {
    if (busy) return;
    const cleared = clearDraft(migrationScope);
    if (cleared.status === "unavailable") {
      setFailure("无法安全清除尚未提交的确认记录。系统没有开始升级，请检查浏览器存储设置。", true);
      return;
    }
    setStored(null);
    setConfirmOpen(false);
    setAcknowledged(false);
    requestAnimationFrame(() => triggerRef.current?.focus());
  }

  async function send(draft: StoredMigrationDraft, mode: "commit" | "retry") {
    if (submitLock.current) return;
    submitLock.current = true;
    const pending = { ...draft, phase: "outcome_unknown" as const };
    if (!persist(pending)) {
      submitLock.current = false;
      return;
    }
    setStored(pending);
    setConfirmOpen(false);
    setBusy(mode);
    setMessage("");
    setNotice("正在升级设定仓库；系统只会处理这一次请求。");
    try {
      handleOperation(await api.commitLoreMigration(projectId, pending.input), pending);
    } catch (error) {
      if (error instanceof ApiError && error.status === 409 && (
        error.code === "LORE_MIGRATION_OPERATION_KEY_CONFLICT"
        || error.code === "LORE_MIGRATION_CONCURRENT_CONFLICT"
        || error.code === "LORE_MIGRATION_ANOTHER_OPERATION_ACTIVE"
      )) {
        setNotice("");
        setFailure("操作记录可能已有结果，系统将先核对原请求，不会创建新的升级请求。");
        await reconcile(pending);
      } else if (error instanceof ApiError && error.status === 409 && (
        error.code === "LORE_MIGRATION_PREVIEW_STALE"
        || error.code === "LORE_MIGRATION_PREVIEW_VERSION_MISMATCH"
        || error.code === "LORE_MIGRATION_PREVIEW_NOT_READY"
      )) {
        const failed = { ...pending, phase: "failed" as const };
        persist(failed);
        setStored(failed);
        setNotice("");
        setFailure("检查结果已变化或不再满足升级条件。本次未继续升级，请重新检查。旧资料仍保留。");
      } else if (error instanceof ApiError && error.status === 409) {
        const unknown = { ...pending, phase: "outcome_unknown" as const };
        persist(unknown);
        setStored(unknown);
        setNotice("");
        setFailure("检测到并发或状态冲突。原请求已冻结，请先核对结果；系统不会创建新的升级请求。");
      } else if (error instanceof ApiError && error.status === 422) {
        setNotice("");
        setFailure("安全请求格式未被服务器接受。为避免重复处理，已停止新的升级，请联系维护人员。", true);
      } else if (error instanceof ApiError && error.status === 503 && !error.outcomeUnknown) {
        const maintenance = { ...pending, phase: "maintenance" as const };
        persist(maintenance);
        setStored(maintenance);
        setNotice("");
        setFailure("当前暂不能安全升级。系统没有开始新的升级，旧资料未更改；可稍后使用原请求重试。");
      } else if (error instanceof ApiError && error.status === 401) {
        setNotice("");
        setFailure("登录状态已失效。请重新登录后返回本项目；本机已保留这次请求。");
      } else if (error instanceof ApiError && error.status === 403) {
        setNotice("");
        setFailure("当前账号无法操作该项目。本机请求已保留，系统不会创建新请求。", true);
      } else {
        setNotice("");
        setFailure("无法确认升级是否完成。原请求已冻结，请先核对结果，不要创建新的升级请求。");
      }
    } finally {
      setBusy(null);
      submitLock.current = false;
    }
  }

  async function confirmUpgrade() {
    if (!stored || stored.phase !== "confirming" || !acknowledged) return;
    await send(stored, "commit");
  }

  async function retryOriginal() {
    if (!stored || (stored.phase !== "retryable" && stored.phase !== "maintenance")) return;
    setBusy("retry");
    setMessage("");
    try {
      const latest = await api.getLoreMigrationPreview(projectId);
      if (!previewMatches(latest, stored)) {
        const failed = { ...stored, phase: "failed" as const };
        persist(failed);
        setStored(failed);
        setFailure("最新检查结果与原请求不一致，系统没有重试。请重新检查后开始新的升级。 ");
        onRequestPreviewReload();
        return;
      }
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        setFailure("登录状态已失效。请重新登录后返回本项目；本机已保留原请求，系统没有重试。");
      } else if (error instanceof ApiError && error.status === 403) {
        setFailure("当前账号无法操作该项目。原请求仍保留，系统没有重试。", true);
      } else if (error instanceof ApiError && error.status === 503) {
        const maintenance = { ...stored, phase: "maintenance" as const };
        persist(maintenance);
        setStored(maintenance);
        setFailure("当前暂不能安全核对并重试。原请求仍保留，旧资料未更改，请稍后再试。");
      } else if (error instanceof ApiError && (error.status === 409 || error.status === 422)) {
        const failed = { ...stored, phase: "failed" as const };
        persist(failed);
        setStored(failed);
        setFailure("最新检查已失效或不再满足升级条件，系统没有重试。请重新检查旧资料。");
        onRequestPreviewReload();
      } else {
        setFailure("暂时无法核对最新检查结果，系统没有重试。原请求仍已保留。 ");
      }
      return;
    } finally {
      setBusy(null);
    }
    await send(stored, "retry");
  }

  function restartAfterKnownFailure() {
    if (!stored || stored.phase !== "failed") return;
    const cleared = clearDraft(migrationScope);
    if (cleared.status === "unavailable") {
      setFailure("无法安全清除已结束的本机记录，请检查浏览器存储设置后重试。", true);
      return;
    }
    setStored(null);
    setOperation(null);
    setMessage("");
    setNotice("");
    onRequestPreviewReload();
  }

  function handleDialogKeyDown(event: ReactKeyboardEvent<HTMLDivElement>) {
    if (event.key === "Escape") {
      event.preventDefault();
      closeConfirmation();
      return;
    }
    if (event.key !== "Tab") return;
    const controls = Array.from(event.currentTarget.querySelectorAll<HTMLElement>("button:not(:disabled), input:not(:disabled), [tabindex]:not([tabindex='-1'])"));
    if (controls.length === 0) return;
    const first = controls[0];
    const last = controls[controls.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  if (operation?.status === "ready") {
    return <section className="lore-migration-upgrade__success" role="status">
      <h2 ref={successRef} tabIndex={-1}>设定仓库升级完成</h2>
      <p>{notice}</p>
      <p>原世界观资料仍保留为历史来源；后续请在设定仓库中管理独立设定。</p>
    </section>;
  }

  return <section className="lore-migration-upgrade" aria-label="升级为设定仓库" aria-busy={busy !== null}>
    {notice && <div className="lore-note" role="status">{notice}</div>}
    {message && <div className="lore-alert lore-operation-message" role="alert" tabIndex={-1} ref={messageRef}>
      <span>{message}</span>
      {stored && !blocked && (stored.phase === "outcome_unknown" || stored.phase === "validating") && (
        <button type="button" disabled={busy !== null} onClick={() => void reconcile(stored)}>{busy === "check" ? "核对中…" : "继续核对升级结果"}</button>
      )}
      {stored && !blocked && (stored.phase === "retryable" || stored.phase === "maintenance") && (
        <button type="button" disabled={busy !== null} onClick={() => void retryOriginal()}>{busy === "retry" ? "核对并重试中…" : "使用原请求安全重试"}</button>
      )}
      {stored?.phase === "failed" && !blocked && (
        <button type="button" disabled={busy !== null} onClick={restartAfterKnownFailure}>重新检查旧资料</button>
      )}
    </div>}

    {stored?.phase === "validating" && !message && !blocked && <div className="lore-note lore-operation-message" role="status">
      <span>{pollCount.current >= 3 ? "升级仍在安全校验中，可继续核对同一次请求。" : "系统正在核对同一次升级请求。"}</span>
      <button type="button" disabled={busy !== null} onClick={() => void reconcile(stored)}>{busy === "check" ? "核对中…" : "继续核对升级结果"}</button>
    </div>}

    {storageChecked && !stored && !blocked && report?.overall_status === "ready" && report.counts.legacy_total > 0 && (
      report.commit_available
        ? <div className="lore-migration-upgrade__entry">
          <div><strong>这些资料已具备安全升级条件</strong><span>升级前仍需再次确认；系统会保留原世界观资料。</span></div>
          <button ref={triggerRef} className="btn btn-primary" type="button" disabled={busy !== null} onClick={openConfirmation}>升级为设定仓库</button>
        </div>
        : <div className="lore-note" role="status">预检已通过，当前尚未开放安全升级窗口；旧资料没有改变。</div>
    )}

    {confirmOpen && stored?.phase === "confirming" && <div className="modal-overlay">
      <div ref={confirmRef} className="modal-content lore-migration-confirm" role="alertdialog" aria-modal="true" aria-labelledby="lore-migration-confirm-title" onKeyDown={handleDialogKeyDown}>
        <h2 id="lore-migration-confirm-title">确认升级为设定仓库？</h2>
        <p>本次将把 {stored.legacyTotal} 项旧世界观资料转换为可独立管理的设定模块。</p>
        <ul>
          <li>原世界观资料会保留，升级后仅作为历史来源查看。</li>
          <li>升级完成后，旧世界观入口将不能继续保存；后续请在设定仓库中管理。</li>
          <li>当前不提供一键回退；如需恢复需由维护人员处理。</li>
        </ul>
        <label className="lore-migration-confirm__ack"><input type="checkbox" checked={acknowledged} onChange={(event) => setAcknowledged(event.target.checked)} /><span>我已了解原资料将转为只读，且当前不能一键回退。</span></label>
        <div className="modal-actions">
          <button ref={confirmCancelRef} className="btn btn-secondary" type="button" onClick={closeConfirmation}>暂不升级</button>
          <button className="btn btn-danger" type="button" disabled={!acknowledged || busy !== null} onClick={() => void confirmUpgrade()}>确认并开始升级</button>
        </div>
      </div>
    </div>}
  </section>;
}
