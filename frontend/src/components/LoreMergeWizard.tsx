import { FormEvent, KeyboardEvent as ReactKeyboardEvent, useEffect, useMemo, useRef, useState } from "react";
import { ApiError, api } from "@/services/api";
import { clearDraft, loadDraft, saveDraft, type DraftScope } from "@/services/maintenanceDrafts";
import type {
  LoreElementDetail,
  LoreFieldDefinition,
  LoreFieldState,
  LoreMergeChoice,
  LoreMergeCommitInput,
  LoreMergeOperation,
  LoreMergePreviewInput,
  LoreMergePreviewResponse,
  LoreReviewDetail,
  LoreTypeDefinition,
} from "@/types/lore";

type Choice = LoreMergeChoice | "";
type MergePhase = "selecting" | "preview_ready" | "maintenance" | "outcome_unknown" | "stale" | "succeeded";

interface MergeSelections {
  survivorId: string;
  name: Choice;
  summary: Choice;
  fields: Record<string, Choice>;
  manualName: string;
  manualSummary: string;
  manualFields: Record<string, string>;
  manualStates: Record<string, LoreFieldState>;
}

interface StoredMergeDraft {
  version: 1;
  phase: MergePhase;
  selections: MergeSelections;
  previewInput: LoreMergePreviewInput | null;
  preview: LoreMergePreviewResponse | null;
  frozenCommit: LoreMergeCommitInput | null;
  result: LoreMergeOperation | null;
}

const EMPTY_SELECTIONS: MergeSelections = {
  survivorId: "",
  name: "",
  summary: "",
  fields: {},
  manualName: "",
  manualSummary: "",
  manualFields: {},
  manualStates: {},
};

const FIELD_STATE_LABEL: Record<LoreFieldState, string> = {
  provided: "已确认",
  unknown: "待补充",
  needs_confirmation: "待确认",
};

function mergeOperationKey(): string {
  try {
    return `merge-${globalThis.crypto.randomUUID()}`;
  } catch {
    return `merge-${Date.now()}-${Math.random().toString(36).slice(2)}`;
  }
}

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.detail;
  return error instanceof Error ? error.message : "操作失败，请稍后重试。";
}

function fieldState(value: unknown): LoreFieldState {
  return value === "provided" || value === "needs_confirmation" ? value : "unknown";
}

function displayValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "未提供";
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function isStoredMergeDraft(value: unknown): value is StoredMergeDraft {
  if (!value || typeof value !== "object") return false;
  const draft = value as Partial<StoredMergeDraft>;
  return draft.version === 1
    && ["selecting", "preview_ready", "maintenance", "outcome_unknown", "stale", "succeeded"].includes(String(draft.phase))
    && Boolean(draft.selections && typeof draft.selections === "object")
    && (draft.previewInput === null || typeof draft.previewInput === "object")
    && (draft.preview === null || typeof draft.preview === "object")
    && (draft.frozenCommit === null || typeof draft.frozenCommit === "object")
    && (draft.result === null || typeof draft.result === "object");
}

function actionLabel(action: string): string {
  if (action === "rewire") return "改连至保留项";
  if (action === "exact_duplicate_archive") return "保留一条并归档重复关系";
  if (action === "self_loop_archive") return "归档合并后形成的自身关系";
  return "需要先处理";
}

export default function LoreMergeWizard({
  projectId,
  userId,
  detail,
  loreTypes,
  enabled,
  readOnly,
  onDirtyChange,
  onBusyChange,
  onMerged,
}: {
  projectId: string;
  userId: string;
  detail: LoreReviewDetail;
  loreTypes: LoreTypeDefinition[];
  enabled: boolean;
  readOnly: boolean;
  onDirtyChange: (dirty: boolean) => void;
  onBusyChange: (busy: boolean) => void;
  onMerged: (elementId: string, notice: string) => void;
}) {
  const [active, setActive] = useState(false);
  const [phase, setPhase] = useState<MergePhase>("selecting");
  const [selections, setSelections] = useState<MergeSelections>(EMPTY_SELECTIONS);
  const [left, setLeft] = useState<LoreElementDetail | null>(null);
  const [right, setRight] = useState<LoreElementDetail | null>(null);
  const [previewInput, setPreviewInput] = useState<LoreMergePreviewInput | null>(null);
  const [preview, setPreview] = useState<LoreMergePreviewResponse | null>(null);
  const [frozenCommit, setFrozenCommit] = useState<LoreMergeCommitInput | null>(null);
  const [result, setResult] = useState<LoreMergeOperation | null>(null);
  const [busy, setBusy] = useState<"load" | "preview" | "commit" | "check" | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [confirmOpen, setConfirmOpen] = useState(false);
  const errorRef = useRef<HTMLDivElement | null>(null);
  const confirmRef = useRef<HTMLDivElement | null>(null);
  const confirmTriggerRef = useRef<HTMLButtonElement | null>(null);
  const successRef = useRef<HTMLHeadingElement | null>(null);

  const draftScope = useMemo<DraftScope>(() => ({
    userId,
    projectId,
    kind: "lore-merge",
    objectId: detail.id,
  }), [detail.id, projectId, userId]);
  const typeDefinition = loreTypes.find((item) => item.key === detail.left.type.key && item.status === "active") ?? null;
  const fields = useMemo(() => [...(typeDefinition?.field_schema ?? [])].sort((a, b) => a.order - b.order), [typeDefinition]);
  const dirty = active && (selections.survivorId !== "" || frozenCommit !== null || preview !== null);
  const locked = busy !== null
    || phase === "outcome_unknown"
    || phase === "succeeded"
    || (phase === "maintenance" && frozenCommit !== null);

  useEffect(() => onDirtyChange(dirty), [dirty, onDirtyChange]);
  useEffect(() => onBusyChange(busy !== null), [busy, onBusyChange]);
  useEffect(() => () => {
    onDirtyChange(false);
    onBusyChange(false);
  }, [onBusyChange, onDirtyChange]);
  useEffect(() => {
    if (error) requestAnimationFrame(() => errorRef.current?.focus());
  }, [error]);
  useEffect(() => {
    if (confirmOpen) requestAnimationFrame(() => confirmRef.current?.focus());
  }, [confirmOpen]);
  useEffect(() => {
    if (phase === "succeeded") requestAnimationFrame(() => successRef.current?.focus());
  }, [phase]);

  useEffect(() => {
    const loaded = loadDraft<unknown>(draftScope);
    if ((loaded.status === "available" || loaded.status === "expired") && isStoredMergeDraft(loaded.draft.payload)) {
      const stored = loaded.draft.payload;
      setActive(true);
      setSelections(stored.selections);
      setPreviewInput(stored.previewInput);
      setPreview(stored.preview);
      setFrozenCommit(stored.frozenCommit);
      setResult(stored.result);
      setPhase(stored.phase);
      setNotice(loaded.status === "expired"
        ? "已恢复较早的合并草稿；系统没有自动提交，请先核对状态。"
        : "已恢复这台设备上的合并草稿；系统没有自动提交。");
      void loadEndpoints();
    } else if (loaded.status === "corrupt" || loaded.status === "unavailable") {
      setError("合并草稿存储不可用或已损坏；为避免重复合并，本次入口已停止使用。请检查浏览器存储后重试。");
    }
    // The draft scope is stable for the selected suggestion.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draftScope]);

  function persist(next: StoredMergeDraft): boolean {
    const saved = saveDraft(draftScope, next, null);
    if (saved.status === "unavailable") {
      setError("浏览器无法安全保存合并草稿；系统没有提交，请检查浏览器存储设置。");
      return false;
    }
    return true;
  }

  function snapshot(next: Partial<StoredMergeDraft> = {}): StoredMergeDraft {
    return {
      version: 1,
      phase: next.phase ?? phase,
      selections: next.selections ?? selections,
      previewInput: Object.hasOwn(next, "previewInput") ? next.previewInput ?? null : previewInput,
      preview: Object.hasOwn(next, "preview") ? next.preview ?? null : preview,
      frozenCommit: Object.hasOwn(next, "frozenCommit") ? next.frozenCommit ?? null : frozenCommit,
      result: Object.hasOwn(next, "result") ? next.result ?? null : result,
    };
  }

  async function loadEndpoints({ clearExistingError = true }: { clearExistingError?: boolean } = {}) {
    setBusy("load");
    if (clearExistingError) setError("");
    try {
      const [nextLeft, nextRight] = await Promise.all([
        api.getLoreElement(projectId, detail.left.id),
        api.getLoreElement(projectId, detail.right.id),
      ]);
      setLeft(nextLeft);
      setRight(nextRight);
      if (nextLeft.type.key !== nextRight.type.key || nextLeft.type.key !== detail.left.type.key) {
        setError("两项设定的类型已经变化，当前不能继续合并，请重新扫描。");
      }
    } catch (nextError) {
      setError(`无法加载最新设定：${errorMessage(nextError)}`);
    } finally {
      setBusy(null);
    }
  }

  async function start() {
    setActive(true);
    setNotice("");
    const stored = snapshot({ phase: "selecting", selections: EMPTY_SELECTIONS, previewInput: null, preview: null, frozenCommit: null, result: null });
    setSelections(EMPTY_SELECTIONS);
    setPhase("selecting");
    setPreviewInput(null);
    setPreview(null);
    setFrozenCommit(null);
    setResult(null);
    if (!persist(stored)) return;
    await loadEndpoints();
  }

  function updateSelections(updater: (current: MergeSelections) => MergeSelections) {
    if (locked) return;
    const next = updater(selections);
    setSelections(next);
    setPreviewInput(null);
    setPreview(null);
    setFrozenCommit(null);
    setPhase("selecting");
    setError("");
    persist(snapshot({ phase: "selecting", selections: next, previewInput: null, preview: null, frozenCommit: null, result: null }));
  }

  function selectedEndpoints(): { survivor: LoreElementDetail; merged: LoreElementDetail } | null {
    if (!left || !right || !selections.survivorId) return null;
    return selections.survivorId === left.id
      ? { survivor: left, merged: right }
      : selections.survivorId === right.id
        ? { survivor: right, merged: left }
        : null;
  }

  function chosenValue(choice: Choice, survivorValue: unknown, mergedValue: unknown, manualValue: unknown): unknown {
    if (choice === "survivor") return survivorValue;
    if (choice === "merged") return mergedValue;
    return manualValue;
  }

  function buildPreviewInput(): LoreMergePreviewInput | null {
    const endpoints = selectedEndpoints();
    if (!endpoints || !typeDefinition || fields.length !== typeDefinition.field_schema.length) return null;
    if (!selections.name || !selections.summary || fields.some((field) => !selections.fields[field.key])) return null;
    const finalName = String(chosenValue(selections.name, endpoints.survivor.name, endpoints.merged.name, selections.manualName) ?? "").trim();
    if (!finalName) return null;
    const finalSummary = String(chosenValue(selections.summary, endpoints.survivor.summary, endpoints.merged.summary, selections.manualSummary) ?? "");
    const finalPayload: Record<string, unknown> = {};
    const finalFieldStates: Record<string, LoreFieldState> = {};
    for (const field of fields) {
      const choice = selections.fields[field.key];
      finalPayload[field.key] = chosenValue(
        choice,
        endpoints.survivor.payload[field.key] ?? null,
        endpoints.merged.payload[field.key] ?? null,
        selections.manualFields[field.key] ?? ""
      );
      finalFieldStates[field.key] = choice === "survivor"
        ? fieldState(endpoints.survivor.field_states[field.key])
        : choice === "merged"
          ? fieldState(endpoints.merged.field_states[field.key])
          : fieldState(selections.manualStates[field.key]);
      if (choice === "manual" && finalFieldStates[field.key] === "unknown") finalPayload[field.key] = null;
    }
    return {
      suggestion_expected_version: detail.lock_version,
      expected_evidence_revision: detail.evidence_revision,
      survivor_element_id: endpoints.survivor.id,
      merged_element_id: endpoints.merged.id,
      survivor_expected_lock_version: endpoints.survivor.lock_version,
      survivor_expected_content_version: endpoints.survivor.current_version,
      merged_expected_lock_version: endpoints.merged.lock_version,
      merged_expected_content_version: endpoints.merged.current_version,
      name_choice: selections.name,
      summary_choice: selections.summary,
      field_choices: Object.fromEntries(fields.map((field) => [field.key, selections.fields[field.key] as LoreMergeChoice])),
      final_name: finalName,
      final_summary: finalSummary,
      final_payload: finalPayload,
      final_field_states: finalFieldStates,
    };
  }

  async function requestPreview(event: FormEvent) {
    event.preventDefault();
    const input = buildPreviewInput();
    if (!input) {
      setError("请选择保留项，并完成名称、摘要和全部设定字段的取值。");
      return;
    }
    setBusy("preview");
    setError("");
    try {
      const response = await api.previewLoreMerge(projectId, detail.id, input);
      if (!response.commit_available) {
        setError("服务器当前未开放正式合并提交；已保留你的选择，没有修改任何设定。");
        return;
      }
      setPreviewInput(input);
      setPreview(response);
      setPhase("preview_ready");
      persist(snapshot({ phase: "preview_ready", previewInput: input, preview: response, frozenCommit: null, result: null }));
    } catch (nextError) {
      if (nextError instanceof ApiError && nextError.status === 503) {
        setPhase("maintenance");
        setError("仓库正在维护，字段选择已保留；系统没有自动重试。");
      } else if (nextError instanceof ApiError && (nextError.status === 409 || nextError.status === 422)) {
        setPhase("stale");
        setError(`${nextError.detail} 你的字段选择仍已保留，请核对最新设定后重新生成预览。`);
        await loadEndpoints({ clearExistingError: false });
      } else {
        setError(`无法生成合并预览：${errorMessage(nextError)} 系统没有修改任何设定。`);
      }
      persist(snapshot({ phase: nextError instanceof ApiError && nextError.status === 503 ? "maintenance" : "stale", previewInput: null, preview: null, frozenCommit: null }));
      setPreviewInput(null);
      setPreview(null);
    } finally {
      setBusy(null);
    }
  }

  function openConfirmation() {
    if (!preview || !previewInput || preview.blockers.length > 0 || !preview.commit_available) return;
    const input: LoreMergeCommitInput = frozenCommit ?? {
      operation_key: mergeOperationKey(),
      preview_token: preview.preview_token,
      preview: previewInput,
    };
    if (!persist(snapshot({ frozenCommit: input }))) return;
    setFrozenCommit(input);
    setConfirmOpen(true);
  }

  function handleConfirmationKeyDown(event: ReactKeyboardEvent<HTMLDivElement>) {
    if (event.key === "Escape") {
      event.preventDefault();
      if (busy === "commit") return;
      setConfirmOpen(false);
      requestAnimationFrame(() => confirmTriggerRef.current?.focus());
      return;
    }
    if (event.key !== "Tab") return;
    const controls = Array.from(event.currentTarget.querySelectorAll<HTMLElement>("button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex='-1'])"));
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

  function finish(operation: LoreMergeOperation) {
    setResult(operation);
    setPhase("succeeded");
    setFrozenCommit(null);
    setConfirmOpen(false);
    const cleared = clearDraft(draftScope);
    if (cleared.status === "unavailable") {
      setNotice("合并已完成，但本机草稿未能清除；请勿再次提交。");
    }
    onMerged(operation.survivor_element_id, operation.replayed
      ? "已核对：该合并此前已经完成，没有重复修改。"
      : "设定合并已完成；被合并项没有删除，现已停用并指向保留项。"
    );
  }

  async function submitCommit() {
    if (!frozenCommit) return;
    setConfirmOpen(false);
    setBusy("commit");
    setError("");
    try {
      finish(await api.commitLoreMerge(projectId, detail.id, frozenCommit));
    } catch (nextError) {
      if (nextError instanceof ApiError && nextError.status === 503) {
        setPhase("maintenance");
        setError("仓库正在维护，本次请求已保留；请稍后使用相同请求重试。");
        persist(snapshot({ phase: "maintenance" }));
      } else if (nextError instanceof ApiError && nextError.status === 409) {
        if (nextError.code === "LORE_MERGE_OPERATION_KEY_REUSED") {
          setPhase("outcome_unknown");
          setError("操作标识可能已有结果，请先核对合并状态；系统不会创建新请求。");
          persist(snapshot({ phase: "outcome_unknown" }));
        } else {
          setPhase("stale");
          setPreview(null);
          setPreviewInput(null);
          setFrozenCommit(null);
          setError(`${nextError.detail} 信息已经变化，未继续合并；字段选择仍保留，请重新生成预览。`);
          persist(snapshot({ phase: "stale", preview: null, previewInput: null, frozenCommit: null }));
          await loadEndpoints({ clearExistingError: false });
        }
      } else {
        setPhase("outcome_unknown");
        setError("网络结果不确定。系统已冻结原请求，不会更换操作标识或自动重复提交；请先核对合并结果。");
        persist(snapshot({ phase: "outcome_unknown" }));
      }
    } finally {
      setBusy(null);
    }
  }

  async function checkOutcome() {
    if (!frozenCommit) return;
    setBusy("check");
    setError("");
    try {
      finish(await api.getLoreMergeOperationByKey(projectId, frozenCommit.operation_key));
    } catch (nextError) {
      if (nextError instanceof ApiError && nextError.status === 404) {
        setPhase("maintenance");
        setError("服务器尚未找到这次合并。你可以使用原请求安全重试；系统不会创建新的操作标识。");
        persist(snapshot({ phase: "maintenance" }));
      } else {
        setPhase("outcome_unknown");
        setError(`暂时无法核对合并结果：${errorMessage(nextError)} 原请求仍保持冻结。`);
      }
    } finally {
      setBusy(null);
    }
  }

  if (detail.review_status !== "confirmed_duplicate") return null;

  if (!active) {
    return <section className="lore-merge-entry" aria-label="合并重复设定">
      <h3>合并这两项重复设定</h3>
      <p>人工判断为重复不会自动执行合并。开始后需要逐项选择保留内容并核对关系影响。</p>
      <p className="lore-note">另一项不会被删除；合并后将暂停用于生成并指向保留项。当前不能自动撤销。</p>
      <button className="btn btn-primary" type="button" disabled={!enabled || readOnly || detail.stale || Boolean(error)} onClick={start}>开始合并</button>
      {!enabled && <span className="lore-meta">当前项目尚未开放安全合并提交。</span>}
      {error && <div className="lore-alert" role="alert">{error}</div>}
    </section>;
  }

  const endpoints = selectedEndpoints();
  const canPreview = Boolean(buildPreviewInput()) && !locked && !detail.stale;

  return <section className="lore-merge-wizard" aria-label="合并重复设定向导" aria-busy={busy !== null}>
    <header>
      <h3>安全合并重复设定</h3>
      <p>另一项不会被删除；合并后将停用并指向保留项。当前不能自动撤销。</p>
    </header>
    <ol className="lore-merge-steps" aria-label="合并步骤">
      <li aria-current={phase === "selecting" || phase === "stale" || phase === "maintenance" ? "step" : undefined}>1. 选择内容</li>
      <li aria-current={phase === "preview_ready" ? "step" : undefined}>2. 检查影响</li>
      <li aria-current={phase === "outcome_unknown" || phase === "succeeded" ? "step" : undefined}>3. 确认结果</li>
    </ol>
    {notice && <div className="lore-note" role="status">{notice}</div>}
    {error && <div ref={errorRef} tabIndex={-1} className="lore-alert" role="alert">{error}{phase === "outcome_unknown" && <button type="button" disabled={busy !== null} onClick={checkOutcome}>{busy === "check" ? "核对中…" : "核对合并结果"}</button>}{phase === "maintenance" && frozenCommit && <button type="button" disabled={busy !== null} onClick={submitCommit}>使用原请求安全重试</button>}</div>}
      {!left || !right ? <div className="lore-empty">{busy === "load" ? "正在加载最新设定…" : <button className="btn btn-secondary" type="button" onClick={() => void loadEndpoints()}>重新加载最新设定</button>}</div> : <>
      <form className="lore-merge-selection" onSubmit={requestPreview}>
        <fieldset disabled={locked}>
          <legend>选择保留项（不默认选择）</legend>
          {[left, right].map((endpoint) => <label key={endpoint.id}>
            <input type="radio" name={`survivor-${detail.id}`} checked={selections.survivorId === endpoint.id} onChange={() => updateSelections((current) => ({ ...current, survivorId: endpoint.id, name: "", summary: "", fields: {} }))} />
            <span><strong>保留“{endpoint.name}”</strong><small>{endpoint.type.display_name} · 内容版本 {endpoint.current_version}</small></span>
          </label>)}
        </fieldset>
        {endpoints && <div className="lore-merge-fields">
          <MergeFieldChooser label="名称" choice={selections.name} survivorValue={endpoints.survivor.name} mergedValue={endpoints.merged.name} manualValue={selections.manualName} required onChange={(choice, value) => updateSelections((current) => ({ ...current, name: choice, manualName: value }))} />
          <MergeFieldChooser label="摘要" choice={selections.summary} survivorValue={endpoints.survivor.summary} mergedValue={endpoints.merged.summary} manualValue={selections.manualSummary} onChange={(choice, value) => updateSelections((current) => ({ ...current, summary: choice, manualSummary: value }))} />
          {!typeDefinition && <div className="lore-alert" role="alert">当前类型字段定义不可用，不能生成安全预览。</div>}
          {fields.map((field) => <MergeFieldChooser
            key={field.key}
            label={field.label}
            choice={selections.fields[field.key] ?? ""}
            survivorValue={endpoints.survivor.payload[field.key]}
            mergedValue={endpoints.merged.payload[field.key]}
            manualValue={selections.manualFields[field.key] ?? ""}
            field={field}
            manualState={selections.manualStates[field.key] ?? "needs_confirmation"}
            onChange={(choice, value, state) => updateSelections((current) => ({
              ...current,
              fields: { ...current.fields, [field.key]: choice },
              manualFields: { ...current.manualFields, [field.key]: value },
              manualStates: { ...current.manualStates, [field.key]: state ?? "needs_confirmation" },
            }))}
          />)}
        </div>}
        <button className="btn btn-primary" type="submit" disabled={!canPreview}>{busy === "preview" ? "正在生成预览…" : "生成合并预览"}</button>
      </form>
      {preview && previewInput && <section className="lore-merge-preview">
        <h4>检查合并结果</h4>
        <dl><div><dt>最终名称</dt><dd>{preview.final_name}</dd></div><div><dt>原始来源</dt><dd>双方共 {preview.source_impact.preserved_total} 条，仍保存在各自历史中</dd></div><div><dt>用于生成</dt><dd>{preview.would_be_generation_eligible ? "可以" : "暂不可以"}</dd></div></dl>
        <details><summary>查看最终字段</summary>{fields.map((field) => <p key={field.key}><strong>{field.label}：</strong>{displayValue(preview.final_payload[field.key])}</p>)}</details>
        <div className="lore-merge-relations"><h4>关系处理</h4>{preview.relation_plan.length === 0 ? <p>没有需要改连的关系。</p> : preview.relation_plan.map((plan) => <article key={plan.relation_id} className={plan.action === "blocker" ? "is-blocker" : ""}><strong>{actionLabel(plan.action)}</strong><span>{plan.action === "rewire" ? "保留关系，并把被合并项替换为保留项。" : plan.action === "exact_duplicate_archive" ? "语义完全相同的关系保留一条，另一条只留在审计历史中。" : plan.action === "self_loop_archive" ? "重定向后会指向自身，因此停止作为有效关系使用。" : "关系内容存在差异，需要先回到设定关系中处理。"}</span></article>)}</div>
        {preview.blockers.length > 0 && <div className="lore-alert" role="alert"><strong>存在需要先处理的关系，当前不能合并。</strong><span>请打开双方正式设定核对关系后，再重新生成预览。</span></div>}
        <p className="lore-meta">预览有效至 {new Date(preview.expires_at).toLocaleString()}；设定或关系变化后必须重新预览。</p>
        <div className="lore-merge-actions"><button className="btn btn-secondary" type="button" disabled={locked} onClick={() => updateSelections((current) => ({ ...current }))}>返回修改</button><button ref={confirmTriggerRef} className="btn btn-primary" type="button" disabled={locked || preview.blockers.length > 0} onClick={openConfirmation}>继续确认合并</button></div>
      </section>}
      {phase === "succeeded" && result && <section className="lore-merge-success" role="status"><h4 ref={successRef} tabIndex={-1}>合并已完成</h4><p>{result.replayed ? "已核对：该合并此前已经完成，没有重复修改。" : "被合并项没有删除，现已停用并指向保留项。"}</p><button className="btn btn-primary" type="button" onClick={() => onMerged(result.survivor_element_id, "已打开保留项，可在下方查看合并历史。")}>查看保留项与合并历史</button></section>}
    </>}
    {confirmOpen && frozenCommit && preview && <div className="modal-overlay"><div ref={confirmRef} tabIndex={-1} className="modal-content lore-merge-confirm" role="alertdialog" aria-modal="true" aria-labelledby="merge-confirm-title" aria-busy={busy === "commit"} onKeyDown={handleConfirmationKeyDown}><h2 id="merge-confirm-title">确认合并这两项设定？</h2><p>将保留“{preview.survivor.name}”，并把“{preview.merged.name}”标记为已合并。</p><ul><li>双方 {preview.source_impact.preserved_total} 条原始来源仍保留。</li><li>{preview.relation_plan.length} 条关系会按预览结果处理。</li><li>被合并项不会物理删除，但会停用并重定向；当前不能自动撤销。</li></ul><div className="modal-actions"><button className="btn btn-secondary" type="button" disabled={busy === "commit"} onClick={() => { setConfirmOpen(false); requestAnimationFrame(() => confirmTriggerRef.current?.focus()); }}>返回检查</button><button className="btn btn-danger" type="button" disabled={busy === "commit"} onClick={submitCommit}>{busy === "commit" ? "正在安全合并…" : "确认合并，暂不可自动撤销"}</button></div></div></div>}
  </section>;
}

function MergeFieldChooser({ label, choice, survivorValue, mergedValue, manualValue, field, required, manualState, onChange }: {
  label: string;
  choice: Choice;
  survivorValue: unknown;
  mergedValue: unknown;
  manualValue: string;
  field?: LoreFieldDefinition;
  required?: boolean;
  manualState?: LoreFieldState;
  onChange: (choice: Choice, manualValue: string, manualState?: LoreFieldState) => void;
}) {
  const name = `merge-field-${field?.key ?? label}`;
  return <fieldset className="lore-merge-field">
    <legend>{label}{required ? "（必填）" : ""}</legend>
    <label><input type="radio" name={name} checked={choice === "survivor"} onChange={() => onChange("survivor", manualValue, manualState)} /><span>采用保留项：{displayValue(survivorValue)}</span></label>
    <label><input type="radio" name={name} checked={choice === "merged"} onChange={() => onChange("merged", manualValue, manualState)} /><span>采用另一项：{displayValue(mergedValue)}</span></label>
    <label><input type="radio" name={name} checked={choice === "manual"} onChange={() => onChange("manual", manualValue, manualState)} /><span>手动填写</span></label>
    {choice === "manual" && <div className="lore-merge-manual"><label><span>{label}内容</span>{field?.control === "textarea" || !field ? <textarea className="form-textarea" value={manualValue} required={required} onChange={(event) => onChange("manual", event.target.value, manualState)} /> : <input className="form-input" value={manualValue} required={required} onChange={(event) => onChange("manual", event.target.value, manualState)} />}</label>{field && <label><span>信息状态</span><select className="form-select" value={manualState ?? "needs_confirmation"} onChange={(event) => onChange("manual", manualValue, event.target.value as LoreFieldState)}>{Object.entries(FIELD_STATE_LABEL).map(([value, text]) => <option key={value} value={value}>{text}</option>)}</select></label>}</div>}
  </fieldset>;
}
