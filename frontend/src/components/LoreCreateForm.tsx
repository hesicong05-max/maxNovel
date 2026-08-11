import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { ApiError, api } from "@/services/api";
import { clearDraft, saveDraft, type DraftScope } from "@/services/maintenanceDrafts";
import type {
  LoreElementCreateInput,
  LoreElementCreateResponse,
  LoreFieldState,
  LoreTypeDefinition,
} from "@/types/lore";

export interface LoreCreateDraft {
  typeKey: string;
  name: string;
  summary: string;
  payload: Record<string, string>;
  fieldStates: Record<string, LoreFieldState>;
  sourceReference: string;
  sourceExcerpt: string;
}

export type LoreCreatePhase = "draft" | "outcome_unknown" | "maintenance" | "retryable" | "conflict";

export interface LoreCreateStoredPayload {
  operationKey: string;
  draft: LoreCreateDraft;
  frozenInput: LoreElementCreateInput | null;
  phase: LoreCreatePhase;
}

const OPERATION_KEY = /^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$/;

function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value);
}

function isStringRecord(value: unknown): value is Record<string, string> {
  return isRecord(value) && Object.values(value).every((item) => typeof item === "string");
}

function isFieldStateRecord(value: unknown): value is Record<string, LoreFieldState> {
  return isStringRecord(value) && Object.values(value).every(
    (item) => item === "provided" || item === "unknown" || item === "needs_confirmation"
  );
}

function isNullableStringRecord(value: unknown): value is Record<string, string | null> {
  return isRecord(value) && Object.values(value).every(
    (item) => typeof item === "string" || item === null
  );
}

function isCreateInput(value: unknown, operationKey: string): value is LoreElementCreateInput {
  if (!isRecord(value)) return false;
  if (
    value.operation_key !== operationKey ||
    typeof value.type_key !== "string" ||
    typeof value.name !== "string" ||
    typeof value.summary !== "string" ||
    !isNullableStringRecord(value.payload) ||
    !isFieldStateRecord(value.field_states) ||
    !Array.isArray(value.sources)
  ) return false;
  return value.sources.every((source) => (
    isRecord(source) &&
    typeof source.kind === "string" &&
    (source.reference === undefined || source.reference === null || typeof source.reference === "string") &&
    (source.excerpt === undefined || source.excerpt === null || typeof source.excerpt === "string") &&
    (source.locator === undefined || isRecord(source.locator)) &&
    (source.is_primary === undefined || typeof source.is_primary === "boolean") &&
    (source.confirmation_status === undefined ||
      source.confirmation_status === "provided" ||
      source.confirmation_status === "needs_confirmation")
  ));
}

export function isLoreCreateStoredPayload(value: unknown): value is LoreCreateStoredPayload {
  if (!isRecord(value) || typeof value.operationKey !== "string" || !OPERATION_KEY.test(value.operationKey)) {
    return false;
  }
  if (!["draft", "outcome_unknown", "maintenance", "retryable", "conflict"].includes(String(value.phase))) {
    return false;
  }
  if (!isRecord(value.draft)) return false;
  const draft = value.draft;
  if (
    typeof draft.typeKey !== "string" ||
    typeof draft.name !== "string" ||
    typeof draft.summary !== "string" ||
    typeof draft.sourceReference !== "string" ||
    typeof draft.sourceExcerpt !== "string" ||
    !isStringRecord(draft.payload) ||
    !isFieldStateRecord(draft.fieldStates)
  ) return false;
  if (value.frozenInput !== null && !isCreateInput(value.frozenInput, value.operationKey)) return false;
  if (value.phase !== "draft" && value.frozenInput === null) return false;
  return true;
}

function newOperationKey(): string {
  if (typeof globalThis.crypto?.randomUUID === "function") {
    return `lore-create:${globalThis.crypto.randomUUID()}`;
  }
  return `lore-create:${Date.now().toString(36)}:${Math.random().toString(36).slice(2, 14)}`;
}

function emptyDraft(type: LoreTypeDefinition | undefined): LoreCreateDraft {
  const fields = type?.field_schema ?? [];
  return {
    typeKey: type?.key ?? "",
    name: "",
    summary: "",
    payload: Object.fromEntries(fields.map((field) => [field.key, ""])),
    fieldStates: Object.fromEntries(fields.map((field) => [field.key, "unknown" as const])),
    sourceReference: "",
    sourceExcerpt: "",
  };
}

function errorText(error: unknown): string {
  if (error instanceof ApiError) return error.detail;
  return error instanceof Error ? error.message : "创建请求未能确认结果。";
}

export default function LoreCreateForm({
  projectId,
  scope,
  loreTypes,
  typesLoading,
  typesError,
  initialStored,
  onDirtyChange,
  onBusyChange,
  onComplete,
  onCancel,
}: {
  projectId: string;
  scope: DraftScope;
  loreTypes: LoreTypeDefinition[];
  typesLoading: boolean;
  typesError: string;
  initialStored: LoreCreateStoredPayload | null;
  onDirtyChange: (dirty: boolean) => void;
  onBusyChange: (busy: boolean) => void;
  onComplete: (response: LoreElementCreateResponse) => void;
  onCancel: () => void;
}) {
  const [stored, setStored] = useState<LoreCreateStoredPayload>(() => initialStored ?? {
    operationKey: newOperationKey(),
    draft: emptyDraft(loreTypes[0]),
    frozenInput: null,
    phase: "draft",
  });
  const [busy, setBusy] = useState(false);
  const [storageReady, setStorageReady] = useState(true);
  const [message, setMessage] = useState(initialStored ? "已恢复这台设备上的未完成草稿。" : "");
  const [error, setError] = useState(() => {
    if (initialStored?.phase === "outcome_unknown") return "上次创建的结果尚未确认。编辑已冻结，请主动核对。";
    if (initialStored?.phase === "conflict") return "上次创建请求与服务器记录不一致。编辑已冻结，可放弃草稿后重新新建。";
    if (initialStored?.phase === "maintenance") return "上次创建遇到维护冻结；当前草稿已完整保留，可稍后重试。";
    if (initialStored?.phase === "retryable") return "上次创建已安全回滚；当前草稿已冻结，可使用相同内容重试。";
    return "";
  });
  const headingRef = useRef<HTMLHeadingElement | null>(null);
  const errorRef = useRef<HTMLDivElement | null>(null);
  const frozen = stored.phase !== "draft";
  const selectedType = useMemo(
    () => loreTypes.find((item) => item.key === stored.draft.typeKey),
    [loreTypes, stored.draft.typeKey]
  );
  const fields = useMemo(
    () => [...(selectedType?.field_schema ?? [])].sort((left, right) => left.order - right.order),
    [selectedType]
  );
  const hasContent = Boolean(
    stored.draft.name.trim() || stored.draft.summary.trim() || stored.draft.sourceReference.trim() ||
    stored.draft.sourceExcerpt.trim() ||
    Object.values(stored.draft.payload).some((value) => value.trim())
  );

  useEffect(() => {
    headingRef.current?.focus();
  }, []);

  useEffect(() => {
    onDirtyChange(hasContent || initialStored !== null);
    return () => onDirtyChange(false);
  }, [hasContent, initialStored, onDirtyChange]);

  useEffect(() => {
    onBusyChange(busy);
    return () => onBusyChange(false);
  }, [busy, onBusyChange]);

  useEffect(() => {
    const result = saveDraft(scope, stored, null);
    setStorageReady(result.status === "saved");
  }, [scope, stored]);

  useEffect(() => {
    if (!error) return;
    requestAnimationFrame(() => errorRef.current?.focus());
  }, [error]);

  useEffect(() => {
    if (initialStored || stored.draft.typeKey || loreTypes.length === 0) return;
    setStored((current) => {
      if (current.draft.typeKey) return current;
      return {
        ...current,
        draft: {
          ...emptyDraft(loreTypes[0]),
          name: current.draft.name,
          summary: current.draft.summary,
          sourceReference: current.draft.sourceReference,
          sourceExcerpt: current.draft.sourceExcerpt,
        },
      };
    });
  }, [initialStored, loreTypes, stored.draft.typeKey]);

  function updateDraft(update: (current: LoreCreateDraft) => LoreCreateDraft) {
    if (frozen || busy) return;
    setStored((current) => ({ ...current, draft: update(current.draft), phase: "draft", frozenInput: null }));
    setError("");
    setMessage("");
  }

  function changeType(typeKey: string) {
    if (typeKey === stored.draft.typeKey) return;
    if (hasContent && !window.confirm("切换类型会清空当前类型的字段内容，是否继续？")) return;
    const nextType = loreTypes.find((item) => item.key === typeKey);
    updateDraft((current) => ({ ...emptyDraft(nextType), name: current.name, summary: current.summary, sourceReference: current.sourceReference, sourceExcerpt: current.sourceExcerpt }));
  }

  function changeFieldState(key: string, state: LoreFieldState) {
    updateDraft((current) => ({
      ...current,
      payload: state === "unknown" ? { ...current.payload, [key]: "" } : current.payload,
      fieldStates: { ...current.fieldStates, [key]: state },
    }));
  }

  function buildInput(): LoreElementCreateInput | null {
    if (!selectedType) {
      setError(typesError || "请选择一个当前可用的设定类型。");
      return null;
    }
    const name = stored.draft.name.trim();
    if (!name) {
      setError("请填写设定名称。");
      return null;
    }
    const payload: Record<string, string | null> = {};
    const fieldStates: Record<string, LoreFieldState> = {};
    for (const field of fields) {
      const state = stored.draft.fieldStates[field.key] ?? "unknown";
      const value = stored.draft.payload[field.key]?.trim() ?? "";
      if (state === "provided" && !value) {
        setError(`“${field.label}”标记为已确认有内容时不能为空。`);
        return null;
      }
      payload[field.key] = state === "unknown" ? null : value || null;
      fieldStates[field.key] = state;
    }
    return {
      operation_key: stored.operationKey,
      type_key: selectedType.key,
      name,
      summary: stored.draft.summary.trim(),
      payload,
      field_states: fieldStates,
      sources: [{
        kind: "manual",
        reference: stored.draft.sourceReference.trim() || null,
        locator: {},
        excerpt: stored.draft.sourceExcerpt.trim() || null,
        is_primary: true,
        confirmation_status: "provided",
      }],
    };
  }

  async function send(input: LoreElementCreateInput) {
    if (busy) return;
    const pending: LoreCreateStoredPayload = {
      ...stored,
      frozenInput: input,
      phase: "outcome_unknown",
    };
    const saved = saveDraft(scope, pending, null);
    if (saved.status !== "saved") {
      setStorageReady(false);
      setError("浏览器无法安全保存创建请求。为避免刷新后重复创建，本次没有提交；请保留页面并检查浏览器存储设置。");
      return;
    }
    setStored(pending);
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const response = await api.createLoreElement(projectId, input);
      clearDraft(scope);
      onComplete(response);
    } catch (caught) {
      if (caught instanceof ApiError) {
        if (caught.status === 503) {
          setStored({ ...pending, phase: "maintenance" });
          setError("设定仓库正在维护，本次创建已停止；当前草稿已冻结，可稍后使用相同内容重试。");
        } else if (caught.status === 409 && caught.code === "LORE_CREATE_IDEMPOTENCY_CONFLICT") {
          setStored({ ...pending, phase: "conflict" });
          setError("上次创建请求与服务器记录不一致。为避免重复，编辑已冻结；可放弃此草稿后重新新建。");
        } else if (caught.status === 409 && caught.retryable) {
          setStored({ ...pending, phase: "retryable" });
          setError("本次创建已安全回滚，没有写入新设定；当前草稿已冻结，可使用相同内容重试。");
        } else if (caught.status === 409) {
          setStored({ ...pending, phase: "conflict" });
          setError(`${caught.detail} 为避免重复，当前草稿已冻结，请放弃后重新新建。`);
        } else if (caught.status >= 500) {
          setStored(pending);
          setError("服务器未能确认创建结果。编辑已冻结，请使用原请求核对结果，不要重新填写后创建。");
        } else {
          setStored({ ...pending, phase: "draft", frozenInput: null });
          setError(errorText(caught));
        }
      } else {
        setStored(pending);
        setError("网络中断，无法确认设定是否已经创建。请使用原请求核对结果，不要重新填写后创建。");
      }
    } finally {
      setBusy(false);
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    const input = buildInput();
    if (input) await send(input);
  }

  async function reconcile() {
    if (stored.frozenInput) await send(stored.frozenInput);
  }

  function cancel() {
    if ((hasContent || frozen) && !window.confirm("确定放弃这份新建设定草稿吗？草稿会从本机移除。")) return;
    const result = clearDraft(scope);
    if (result.status === "unavailable") {
      setError("无法从浏览器安全清除草稿，请检查存储设置后重试。");
      return;
    }
    onCancel();
  }

  return (
    <div className="lore-candidate-review">
      <div className="lore-detail-heading">
        <span className="lore-type">手动创建</span>
        <h2 ref={headingRef} tabIndex={-1}>新建设定模块</h2>
        <p>只保存你明确填写的内容；空缺字段会标记为“信息为空”。</p>
      </div>

      {message && <div className="lore-note" role="status">{message}</div>}
      {!storageReady && <div className="lore-alert" role="alert">本机草稿存储当前不可用，提交已被安全阻止。</div>}
      {error && <div className="lore-alert lore-operation-message" role="alert" tabIndex={-1} ref={errorRef}>
        <span>{error}</span>
        {stored.phase === "outcome_unknown" && stored.frozenInput && (
          <button type="button" disabled={busy} onClick={() => void reconcile()}>{busy ? "核对中…" : "核对上次创建结果"}</button>
        )}
        {(stored.phase === "maintenance" || stored.phase === "retryable") && stored.frozenInput && (
          <button type="button" disabled={busy} onClick={() => void reconcile()}>{busy ? "重试中…" : "重试上次创建"}</button>
        )}
      </div>}

      <form className="lore-candidate-form" aria-busy={busy} onSubmit={(event) => void submit(event)}>
        <fieldset disabled={busy || frozen || typesLoading}>
          <legend>设定内容</legend>
          <label><span>类型</span><select className="form-select" value={stored.draft.typeKey} onChange={(event) => changeType(event.target.value)}><option value="">请选择类型</option>{loreTypes.map((item) => <option key={item.id} value={item.key}>{item.display_name}</option>)}</select>{typesLoading && <small>正在加载权威字段定义…</small>}{typesError && <small>{typesError}</small>}</label>
          <label><span>名称</span><input className="form-input" value={stored.draft.name} maxLength={200} onChange={(event) => updateDraft((current) => ({ ...current, name: event.target.value }))} /></label>
          <label><span>摘要（可选）</span><textarea className="form-textarea" value={stored.draft.summary} maxLength={2000} onChange={(event) => updateDraft((current) => ({ ...current, summary: event.target.value }))} /></label>

          <section className="lore-edit-fields" aria-label="类型字段">
            <h3>类型字段</h3>
            {selectedType && fields.length === 0 && <p>该类型当前没有额外字段。</p>}
            {fields.map((field) => {
              const state = stored.draft.fieldStates[field.key] ?? "unknown";
              return <div className="lore-edit-field" key={field.key}>
                <label><span>{field.label}</span>{field.control === "text" ? <input className="form-input" value={stored.draft.payload[field.key] ?? ""} disabled={state === "unknown"} onChange={(event) => updateDraft((current) => ({ ...current, payload: { ...current.payload, [field.key]: event.target.value } }))} /> : <textarea className="form-textarea" value={stored.draft.payload[field.key] ?? ""} disabled={state === "unknown"} onChange={(event) => updateDraft((current) => ({ ...current, payload: { ...current.payload, [field.key]: event.target.value } }))} />}</label>
                <label><span>{field.label}的信息状态</span><select className="form-select" value={state} onChange={(event) => changeFieldState(field.key, event.target.value as LoreFieldState)}><option value="provided">已确认有内容</option><option value="unknown">信息为空</option><option value="needs_confirmation">待确认</option></select></label>
                {field.help && <small>{field.help}</small>}
              </div>;
            })}
          </section>

          <section className="lore-create-source" aria-label="原始出处">
            <h3>原始出处</h3>
            <label><span>来源说明</span><input className="form-input" value={stored.draft.sourceReference} maxLength={200} onChange={(event) => updateDraft((current) => ({ ...current, sourceReference: event.target.value }))} /></label>
            <label><span>原文摘录（可选）</span><textarea className="form-textarea" value={stored.draft.sourceExcerpt} maxLength={2000} onChange={(event) => updateDraft((current) => ({ ...current, sourceExcerpt: event.target.value }))} /></label>
            <small>留空时仅记录“手动创建”这一方式；系统不会把创建方式伪装成你提供的出处，也不会自动补写原文。</small>
          </section>
        </fieldset>
        <div className="lore-candidate-actions">
          <button className="btn btn-primary" type="submit" disabled={busy || frozen || typesLoading || !storageReady}>{busy ? "创建中…" : "创建正式设定"}</button>
          <button className="btn btn-secondary" type="button" disabled={busy} onClick={cancel}>放弃草稿</button>
        </div>
      </form>
    </div>
  );
}
