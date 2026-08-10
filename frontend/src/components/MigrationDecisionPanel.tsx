import { useEffect, useMemo, useRef, useState } from "react";
import { ApiError, api } from "@/services/api";
import { clearDraft, loadDraft, saveDraft, type DraftScope } from "@/services/maintenanceDrafts";
import type {
  LegacyLoreResolution,
  LegacyLoreResolutionInput,
  LegacyLoreResolutionRevokeInput,
  LoreMigrationPreviewItem,
  LoreMigrationPreviewResponse,
} from "@/types/lore";

const DECIDABLE_REASONS = new Set([
  "type_confirmation_required",
  "source_missing",
  "raw_text_excerpt_unverified",
  "unmapped_fields",
  "duplicate_name_same_type",
  "duplicate_name_cross_type",
]);

const REASON_LABEL: Record<string, string> = {
  type_confirmation_required: "选择迁移后的模块类型",
  source_missing: "补充资料来源说明",
  raw_text_excerpt_unverified: "人工确认未精确定位的原文来源",
  unmapped_fields: "确认保留未映射字段",
  duplicate_name_same_type: "确认同名对象是不同设定",
  duplicate_name_cross_type: "确认跨类型同名对象是不同设定",
};

const TYPES: Array<[string, string]> = [
  ["world", "世界观"], ["character", "角色"], ["location", "地点"],
  ["scene", "场景"], ["faction", "阵营"], ["item", "物品"],
  ["conflict", "冲突"], ["event", "事件"], ["foreshadow", "伏笔"],
  ["rule", "规则与限制"], ["ability_system", "能力体系"], ["race", "种族"],
  ["historical_event", "历史事件"], ["social_institution", "社会制度"],
  ["other", "其他"],
];

const SOURCE: Array<[string, string]> = [
  ["manual", "手动创建"], ["imported", "文档导入"], ["hybrid", "文档导入与手动补充"],
];

type FrozenResolutionRequest =
  | { action: "decide"; input: LegacyLoreResolutionInput }
  | { action: "revoke"; resolutionId: string; input: LegacyLoreResolutionRevokeInput };

function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value);
}

function isFrozenRequest(value: unknown): value is FrozenResolutionRequest {
  if (!isRecord(value) || (value.action !== "decide" && value.action !== "revoke")) return false;
  if (!isRecord(value.input) || typeof value.input.operation_key !== "string") return false;
  return value.action === "decide"
    ? typeof value.input.item_fingerprint === "string"
    : typeof value.resolutionId === "string";
}

function isFrozenRequestForItem(
  request: FrozenResolutionRequest,
  item: LoreMigrationPreviewItem,
  resolutions: LegacyLoreResolution[],
  reasons: string[]
): boolean {
  if (request.action === "decide") {
    return (
      request.input.item_fingerprint === item.item_fingerprint
      && request.input.legacy_category === item.legacy_category
      && request.input.legacy_index === item.legacy_index
      && reasons.includes(request.input.reason_code)
    );
  }
  return resolutions.some((resolution) => resolution.id === request.resolutionId);
}

function typeLabel(typeKey: unknown): string {
  if (typeof typeKey !== "string") return "待确认类型";
  return TYPES.find(([key]) => key === typeKey)?.[1] ?? "未识别类型，请重新预检";
}

function operationKey(): string | null {
  try {
    if (typeof globalThis.crypto?.randomUUID !== "function") return null;
    return `migration-resolution:${globalThis.crypto.randomUUID()}`;
  } catch {
    return null;
  }
}

function resolutionLabel(resolution: LegacyLoreResolution): string {
  if (resolution.reason_code === "type_confirmation_required") {
    return `已确认类型：${TYPES.find(([key]) => key === resolution.decision_payload.type_key)?.[1] ?? "已选类型"}`;
  }
  if (resolution.reason_code === "source_missing") {
    return `已补充来源：${SOURCE.find(([key]) => key === resolution.decision_payload.source_kind)?.[1] ?? "作者说明"}`;
  }
  if (resolution.reason_code === "raw_text_excerpt_unverified") {
    return "作者已人工核对；仍未建立精确段落定位";
  }
  if (resolution.reason_code === "unmapped_fields") return "未映射字段将完整保留在原始来源中";
  return "作者已确认这些同名对象是不同设定";
}

export default function MigrationDecisionPanel({
  projectId,
  userId,
  report,
  item,
  onChanged,
}: {
  projectId: string;
  userId: string;
  report: LoreMigrationPreviewResponse;
  item: LoreMigrationPreviewItem;
  onChanged: () => void;
}) {
  const reasons = (item.effective_reason_codes ?? item.reason_codes).filter((reason) => DECIDABLE_REASONS.has(reason));
  const [openReason, setOpenReason] = useState<string | null>(null);
  const [choice, setChoice] = useState("");
  const [acknowledged, setAcknowledged] = useState(false);
  const [busy, setBusy] = useState<"save" | "retry" | "revoke" | null>(null);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [frozenRequest, setFrozenRequest] = useState<FrozenResolutionRequest | null>(null);
  const [draftProblem, setDraftProblem] = useState<"corrupt" | "unavailable" | null>(null);
  const [evidence, setEvidence] = useState<
    { status: "idle" | "loading" | "ready" | "error"; rawText: string }
  >({ status: "idle", rawText: "" });
  const [evidenceViewed, setEvidenceViewed] = useState(false);
  const headingRef = useRef<HTMLHeadingElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const statusRef = useRef<HTMLDivElement | null>(null);
  const errorRef = useRef<HTMLDivElement | null>(null);

  const scope = useMemo<DraftScope>(() => ({
    userId,
    projectId,
    kind: "lore-migration-resolution",
    objectId: item.item_fingerprint,
  }), [item.item_fingerprint, projectId, userId]);
  const worldviewScope = useMemo<DraftScope>(() => ({
    userId,
    projectId,
    kind: "worldview",
    objectId: "worldview",
  }), [projectId, userId]);

  const group = item.group_fingerprint
    ? report.items.filter((candidate) => candidate.group_fingerprint === item.group_fingerprint)
    : [];
  const resolutions = Array.from(
    new Map((item.resolution_states ?? []).map((resolution) => [resolution.id, resolution])).values()
  );
  const effectiveSourceKind = item.effective_source_kind ?? item.source_kind;
  const evidenceEligible = typeof effectiveSourceKind === "string"
    && ["imported", "hybrid"].includes(effectiveSourceKind);

  useEffect(() => {
    if (!openReason) return;
    setChoice("");
    setAcknowledged(false);
    setEvidence({ status: "idle", rawText: "" });
    setEvidenceViewed(false);
    setError("");
    setMessage("");
    setTimeout(() => headingRef.current?.focus(), 0);
  }, [openReason]);

  useEffect(() => {
    const loaded = loadDraft<FrozenResolutionRequest>(scope);
    if (
      loaded.status === "available"
      && isFrozenRequest(loaded.draft.payload)
      && isFrozenRequestForItem(loaded.draft.payload, item, resolutions, reasons)
    ) {
      setFrozenRequest(loaded.draft.payload);
      setDraftProblem(null);
      if (loaded.draft.payload.action === "decide") {
        setOpenReason(loaded.draft.payload.input.reason_code);
      }
      setError("检测到一个结果尚未确认的迁移决定请求。请使用原请求核对结果，不要重新选择后提交。");
    } else if (loaded.status === "corrupt" || loaded.status === "available") {
      setFrozenRequest(null);
      setDraftProblem("corrupt");
      setError("这项迁移决定的本地恢复记录已损坏，系统没有提交任何决定。");
    } else if (loaded.status === "unavailable") {
      setFrozenRequest(null);
      setDraftProblem("unavailable");
      setError("浏览器存储不可用。为避免重复，本项暂不提交；请检查浏览器设置。");
    } else {
      setFrozenRequest(null);
      setDraftProblem(null);
    }
  }, [scope]);

  useEffect(() => {
    if (error) setTimeout(() => errorRef.current?.focus(), 0);
  }, [error]);

  function close() {
    if (busy || frozenRequest || draftProblem) return;
    setOpenReason(null);
    setTimeout(() => triggerRef.current?.focus(), 0);
  }

  function clearCorruptDraft() {
    if (draftProblem !== "corrupt") return;
    if (!window.confirm("只清除这项损坏的本地恢复记录吗？其他草稿和旧世界观不会改变。")) return;
    const result = clearDraft(scope);
    if (result.status === "unavailable") {
      setError("浏览器存储仍不可用，没有宣称清除成功；本项继续停止提交。");
      return;
    }
    setDraftProblem(null);
    setError("");
    setMessage("这项损坏的本地恢复记录已清除；其他草稿和旧世界观没有改变。");
    setTimeout(() => (headingRef.current ?? statusRef.current)?.focus(), 0);
  }

  async function loadEvidence() {
    if (busy || evidence.status === "loading") return;
    if (!evidenceEligible) {
      setEvidence({ status: "error", rawText: "" });
      setError("当前设定未被作者确认为导入资料，不能使用完整导入原文作为证据。");
      return;
    }
    setEvidence({ status: "loading", rawText: "" });
    setEvidenceViewed(false);
    setError("");
    try {
      const worldview = await api.getWorldview(projectId);
      if (
        worldview.source_checksum !== report.source_checksum
        || typeof worldview.raw_text !== "string"
        || worldview.raw_text.trim().length === 0
      ) {
        setEvidence({ status: "error", rawText: "" });
        setError("完整导入原文已变化或不可用，不能确认来源。请重新预检后再核对。");
        return;
      }
      setEvidence({ status: "ready", rawText: worldview.raw_text });
    } catch {
      setEvidence({ status: "error", rawText: "" });
      setError("完整导入原文加载失败，本次不能确认来源，也没有提交任何决定。");
    }
  }

  function buildInput(reason: string): LegacyLoreResolutionInput | null {
    const key = operationKey();
    if (!key) {
      setError("浏览器无法生成安全操作标识，本次没有提交。请刷新页面后重试。");
      return null;
    }
    let decisionCode = "";
    let decisionPayload: Record<string, unknown> = {};
    if (reason === "type_confirmation_required" && choice) {
      decisionCode = "confirm_type";
      decisionPayload = { type_key: choice };
    } else if (reason === "source_missing" && choice) {
      decisionCode = "confirm_source";
      decisionPayload = { source_kind: choice };
    } else if (
      reason === "raw_text_excerpt_unverified"
      && acknowledged
      && evidence.status === "ready"
      && evidenceViewed
    ) {
      decisionCode = "accept_unlocated_source";
      decisionPayload = { confirmed_by_author: true, exact_excerpt_available: false };
    } else if (reason === "unmapped_fields" && acknowledged) {
      decisionCode = "preserve_unmapped_fields";
      decisionPayload = { field_names: [...(item.effective_unmapped_fields ?? item.unmapped_fields)].sort() };
    } else if ((reason === "duplicate_name_same_type" || reason === "duplicate_name_cross_type") && acknowledged) {
      decisionCode = "confirm_distinct_same_name";
      decisionPayload = { member_fingerprints: group.map((candidate) => candidate.item_fingerprint).sort() };
    } else {
      setError("请先作出明确选择；系统不会替你默认确认。");
      return null;
    }
    const previous = resolutions.find((resolution) => resolution.reason_code === reason && resolution.status !== "expired");
    return {
      operation_key: key,
      preview_schema_version: report.preview_schema_version,
      mapping_version: report.mapping_version,
      expected_source_checksum: report.source_checksum,
      expected_semantic_result_checksum: report.semantic_result_checksum,
      item_fingerprint: item.item_fingerprint,
      group_fingerprint: item.group_fingerprint ?? null,
      legacy_category: item.legacy_category,
      legacy_index: item.legacy_index,
      reason_code: reason,
      decision_code: decisionCode,
      decision_payload: decisionPayload,
      expected_resolution_version: previous?.lock_version ?? null,
    };
  }

  async function send(request: FrozenResolutionRequest, mode: "save" | "retry") {
    if (busy) return;
    if (mode === "save") {
      const worldviewDraft = loadDraft(worldviewScope);
      if (worldviewDraft.status !== "missing" && worldviewDraft.status !== "expired") {
        setError("检测到尚未处理的世界观草稿。请先返回编辑器保存或放弃草稿，再作迁移决定。");
        return;
      }
      const saved = saveDraft(scope, request, report.source_checksum);
      if (saved.status !== "saved") {
        setError("浏览器无法安全保存本次决定请求。为避免刷新后重复提交，本次没有发送；请检查存储设置。");
        return;
      }
      setFrozenRequest(request);
    }
    setBusy(mode === "save" && request.action === "revoke" ? "revoke" : mode);
    setError("");
    setMessage("");
    try {
      if (request.action === "decide") {
        await api.decideLoreMigrationResolution(projectId, request.input);
      } else {
        await api.revokeLoreMigrationResolution(
          projectId, request.resolutionId, request.input
        );
      }
      clearDraft(scope);
      setFrozenRequest(null);
      setMessage(request.action === "decide"
        ? "决定已保存，旧资料没有被修改，迁移尚未开始。正在重新预检。"
        : "决定已撤销，旧资料没有被修改。正在重新预检。");
      setTimeout(() => statusRef.current?.focus(), 0);
      onChanged();
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 409) {
        clearDraft(scope);
        setFrozenRequest(null);
        setError(request.action === "decide"
          ? "旧资料或已有决定已经变化，本次决定未生效。请重新预检后再次查看证据。"
          : "旧资料或决定版本已经变化，原撤销请求不能继续使用。请重新预检。");
      } else if (caught instanceof ApiError && caught.status === 503) {
        setError(request.action === "decide"
          ? "设定仓库正在维护，没有开始新的写入。原请求已冻结，可稍后使用同一请求核对结果。"
          : "维护期间没有开始新的撤销；原请求已保留，可稍后安全重试。");
      } else if (caught instanceof ApiError && caught.status < 500) {
        clearDraft(scope);
        setFrozenRequest(null);
        setError(`${caught.detail} 旧资料没有被修改。`);
      } else {
        setError(request.action === "decide"
          ? "无法确认决定是否保存。请使用原请求核对结果，不要重新选择后提交。"
          : "无法确认撤销是否完成，请使用原请求核对结果。");
      }
    } finally {
      setBusy(null);
    }
  }

  function revoke(resolution: LegacyLoreResolution) {
    const key = operationKey();
    if (!key || busy) return;
    if (!window.confirm("撤销后该问题会重新阻塞迁移，旧资料本身不会改变。确定撤销吗？")) return;
    void send({
      action: "revoke",
      resolutionId: resolution.id,
      input: {
        operation_key: key,
        expected_source_checksum: report.source_checksum,
        expected_resolution_version: resolution.lock_version,
      },
    }, "save");
  }

  if (
    reasons.length === 0
    && resolutions.length === 0
    && !frozenRequest
    && !draftProblem
  ) return null;

  return (
    <section className="migration-decision" aria-label="迁移决定">
      {resolutions.map((resolution) => (
        <div key={resolution.id} className={`migration-decision__summary is-${resolution.status}`}>
          <strong>{resolution.status === "expired" ? "决定已过期" : resolution.status === "revoked" ? "决定已撤销" : "已作出决定"}</strong>
          <span>{resolutionLabel(resolution)}</span>
          <small>{new Date(resolution.updated_at).toLocaleString("zh-CN")}</small>
          {resolution.status === "active" && (
            <button className="btn btn-secondary" type="button" disabled={busy !== null || !!frozenRequest || !!draftProblem} onClick={() => revoke(resolution)}>
              {busy === "revoke" ? "正在撤销…" : "撤销决定"}
            </button>
          )}
        </div>
      ))}

      {!openReason && reasons.length > 0 && (
        <div className="migration-decision__triggers">
          {reasons.map((reason) => (
            <button
              key={reason}
              className="btn btn-primary"
              type="button"
              disabled={!!frozenRequest || !!draftProblem}
              onClick={(event) => {
                triggerRef.current = event.currentTarget;
                setOpenReason(reason);
              }}
            >
              {REASON_LABEL[reason] ?? "作出迁移决定"}
            </button>
          ))}
        </div>
      )}

      {draftProblem && (
        <div className="migration-decision__recovery">
          <div ref={errorRef} className="lore-alert" role="alert" tabIndex={-1}>{error}</div>
          {draftProblem === "corrupt" && (
            <button className="btn btn-secondary" type="button" onClick={clearCorruptDraft}>
              清除这项损坏记录
            </button>
          )}
          <small>保留记录不会修改旧世界观；本项会继续停止提交。</small>
        </div>
      )}

      {frozenRequest && !openReason && (
        <div className="migration-decision__recovery">
          <div ref={errorRef} className="lore-alert" role="alert" tabIndex={-1}>
            无法确认撤销是否完成，请使用原请求核对结果。
          </div>
          <button className="btn btn-primary" type="button" disabled={busy !== null} onClick={() => void send(frozenRequest, "retry")}>
            {busy === "retry" ? "正在核对…" : "使用原请求核对撤销结果"}
          </button>
        </div>
      )}

      {message && !openReason && (
        <div ref={statusRef} className="lore-alert" role="status" tabIndex={-1}>{message}</div>
      )}

      {openReason && (
        <div className="migration-decision__panel" aria-busy={busy !== null}>
          <h3 ref={headingRef} tabIndex={-1}>{REASON_LABEL[openReason]}</h3>
          <p>此操作只保存迁移决定，不会修改旧世界观，也不会自动开始升级。</p>

          {openReason === "type_confirmation_required" && (
            <label>迁移后的模块类型
              <select value={choice} disabled={busy !== null || !!frozenRequest || !!draftProblem} onChange={(event) => setChoice(event.target.value)}>
                <option value="">请选择设定类型</option>
                {TYPES.map(([key, label]) => <option key={key} value={key}>{label}</option>)}
              </select>
              <small>系统建议仅供参考，不会自动选择。</small>
            </label>
          )}
          {openReason === "source_missing" && (
            <label>作者补充的来源说明
              <select value={choice} disabled={busy !== null || !!frozenRequest || !!draftProblem} onChange={(event) => setChoice(event.target.value)}>
                <option value="">请选择资料来源</option>
                {SOURCE.map(([key, label]) => <option key={key} value={key}>{label}</option>)}
              </select>
            </label>
          )}
          {openReason === "raw_text_excerpt_unverified" && (
            <fieldset>
              <legend>原文证据核对</legend>
              <p>以下是整份导入资料，系统无法确认本项位于哪一段。</p>
              {evidence.status !== "ready" && (
                <button className="btn btn-secondary" type="button" disabled={busy !== null || evidence.status === "loading" || !!frozenRequest || !evidenceEligible} onClick={() => void loadEvidence()}>
                  {evidence.status === "loading" ? "正在加载完整原文…" : "加载并查看完整导入原文"}
                </button>
              )}
              {!evidenceEligible && (
                <small>当前设定未被作者确认为导入资料，不能将完整导入原文作为这项的证据。</small>
              )}
              {evidence.status === "ready" && (
                <details
                  className="migration-decision__evidence"
                  onToggle={(event) => {
                    if (event.currentTarget.open) setEvidenceViewed(true);
                  }}
                >
                  <summary>完整导入原文（未定位到本项）</summary>
                  <pre>{evidence.rawText}</pre>
                </details>
              )}
              <label><input type="checkbox" checked={acknowledged} disabled={busy !== null || !!frozenRequest || evidence.status !== "ready" || !evidenceViewed} onChange={(event) => setAcknowledged(event.target.checked)} /> 我已阅读完整原文，并确认其中包含这项设定；系统仍无法标出具体段落</label>
            </fieldset>
          )}
          {openReason === "unmapped_fields" && (
            <fieldset>
              <legend>未映射字段</legend>
              <p>{(item.effective_unmapped_fields ?? item.unmapped_fields).join("、")}</p>
              <label><input type="checkbox" checked={acknowledged} disabled={busy !== null || !!frozenRequest} onChange={(event) => setAcknowledged(event.target.checked)} /> 我确认这些字段不静默丢弃，并完整保留在原始来源中</label>
            </fieldset>
          )}
          {(openReason === "duplicate_name_same_type" || openReason === "duplicate_name_cross_type") && (
            <fieldset>
              <legend>同名对象对比</legend>
              <div className="migration-decision__group">
                {group.map((candidate) => (
                  <div key={candidate.item_fingerprint}>
                    <strong>{candidate.name}</strong>
                    <span>旧资料第 {candidate.legacy_index + 1} 项 · {typeLabel(candidate.effective_proposed_type_key ?? candidate.proposed_type_key)}</span>
                    <pre>{JSON.stringify(candidate.original_value, null, 2)}</pre>
                  </div>
                ))}
              </div>
              <label><input type="checkbox" checked={acknowledged} disabled={busy !== null || !!frozenRequest} onChange={(event) => setAcknowledged(event.target.checked)} /> 我确认当前这项与列表中的同名资料是不同设定，允许保留同名</label>
            </fieldset>
          )}

          {error && !draftProblem && <div ref={errorRef} className="lore-alert" role="alert" tabIndex={-1}>{error}</div>}
          {message && <div ref={statusRef} className="lore-alert" role="status" tabIndex={-1}>{message}</div>}
          <div className="migration-decision__actions">
            {frozenRequest ? (
              <button className="btn btn-primary" type="button" disabled={busy !== null} onClick={() => void send(frozenRequest, "retry")}>
                {busy === "retry" ? "正在核对…" : "使用原请求核对结果"}
              </button>
            ) : (
              <button className="btn btn-primary" type="button" disabled={busy !== null} onClick={() => {
                const input = buildInput(openReason);
                if (input) void send({ action: "decide", input }, "save");
              }}>
                {busy === "save" ? "正在保存决定…" : "保存迁移决定"}
              </button>
            )}
            <button className="btn btn-secondary" type="button" disabled={busy !== null || !!frozenRequest || !!draftProblem} onClick={close}>暂不决定</button>
          </div>
        </div>
      )}
    </section>
  );
}
