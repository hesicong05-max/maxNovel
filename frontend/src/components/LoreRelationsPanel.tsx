import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { useAuth } from "@/components/AuthContext";
import { ApiError, api } from "@/services/api";
import { clearDraft, loadDraft, saveDraft, type DraftScope } from "@/services/maintenanceDrafts";
import type {
  LoreElementDetail,
  LoreElementListItem,
  LoreRelation,
  LoreRelationCreateInput,
  LoreRelationType,
} from "@/types/lore";

type CreatePhase = "draft" | "outcome_unknown" | "maintenance" | "retryable" | "endpoint_changed" | "conflict";

interface RelationDraft {
  relationType: string;
  customForwardLabel: string;
  customReverseLabel: string;
  description: string;
  target: LoreElementListItem | null;
  targetQuery: string;
}

interface StoredRelationCreate {
  operationKey: string;
  draft: RelationDraft;
  frozenInput: LoreRelationCreateInput | null;
  phase: CreatePhase;
}

function newOperationKey(): string {
  if (typeof globalThis.crypto?.randomUUID === "function") {
    return `lore-relation:${globalThis.crypto.randomUUID()}`;
  }
  return `lore-relation:${Date.now().toString(36)}:${Math.random().toString(36).slice(2, 14)}`;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value);
}

function isStoredRelationCreate(value: unknown): value is StoredRelationCreate {
  if (!isRecord(value) || typeof value.operationKey !== "string" || !isRecord(value.draft)) return false;
  const draft = value.draft;
  if (
    typeof draft.relationType !== "string" ||
    typeof draft.customForwardLabel !== "string" ||
    typeof draft.customReverseLabel !== "string" ||
    typeof draft.description !== "string" ||
    typeof draft.targetQuery !== "string" ||
    (draft.target !== null && !isRecord(draft.target)) ||
    !["draft", "outcome_unknown", "maintenance", "retryable", "endpoint_changed", "conflict"].includes(String(value.phase))
  ) return false;
  return value.frozenInput === null || isRecord(value.frozenInput);
}

function emptyStored(defaultType = "related_to"): StoredRelationCreate {
  return {
    operationKey: newOperationKey(),
    draft: {
      relationType: defaultType,
      customForwardLabel: "",
      customReverseLabel: "",
      description: "",
      target: null,
      targetQuery: "",
    },
    frozenInput: null,
    phase: "draft",
  };
}

function relationView(relation: LoreRelation, elementId: string) {
  const outgoing = relation.source.id === elementId;
  return {
    outgoing,
    other: outgoing ? relation.target : relation.source,
    label: outgoing ? relation.forward_label : relation.reverse_label,
  };
}

function errorText(error: unknown): string {
  if (error instanceof ApiError) return error.detail;
  return error instanceof Error ? error.message : "关系请求未能确认结果。";
}

export default function LoreRelationsPanel({
  projectId,
  element,
  writable,
  onDirtyChange,
  onBusyChange,
  onMutationComplete,
  onOpenElement,
  interactionLocked = false,
}: {
  projectId: string;
  element: LoreElementDetail;
  writable: boolean;
  onDirtyChange: (dirty: boolean) => void;
  onBusyChange: (busy: boolean) => void;
  onMutationComplete: (notice: string) => void;
  onOpenElement: (elementId: string) => void;
  interactionLocked?: boolean;
}) {
  const { user } = useAuth();
  const [statusFilter, setStatusFilter] = useState<"active" | "archived">("active");
  const [relations, setRelations] = useState<LoreRelation[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [listError, setListError] = useState("");
  const [types, setTypes] = useState<LoreRelationType[]>([]);
  const [reloadToken, setReloadToken] = useState(0);
  const [stored, setStored] = useState<StoredRelationCreate | null>(null);
  const [storageBlocked, setStorageBlocked] = useState(false);
  const [targetResults, setTargetResults] = useState<LoreElementListItem[]>([]);
  const [targetLoading, setTargetLoading] = useState(false);
  const [editing, setEditing] = useState<{
    relation: LoreRelation;
    forwardLabel: string;
    reverseLabel: string;
    description: string;
  } | null>(null);
  const [confirmState, setConfirmState] = useState<{ relation: LoreRelation; action: "archive" | "restore" } | null>(null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const headingRef = useRef<HTMLHeadingElement | null>(null);
  const errorRef = useRef<HTMLDivElement | null>(null);
  const formHeadingRef = useRef<HTMLHeadingElement | null>(null);
  const stateDialogRef = useRef<HTMLDivElement | null>(null);
  const actionTriggerRef = useRef<HTMLButtonElement | null>(null);
  const searchSequence = useRef(0);

  const draftScope = useMemo<DraftScope | null>(() => user ? ({
    userId: user.id,
    projectId,
    kind: "lore-relation",
    objectId: `${element.id}:new`,
  }) : null, [element.id, projectId, user?.id]);
  const dirty = stored !== null || editing !== null;
  const frozen = stored !== null && stored.phase !== "draft";
  const selectedType = types.find((item) => item.key === stored?.draft.relationType);
  const previewForward = selectedType?.key === "custom"
    ? stored?.draft.customForwardLabel.trim() || "（请填写关系说法）"
    : selectedType?.forward_label || "关联于";
  const previewReverse = selectedType?.key === "custom"
    ? stored?.draft.customReverseLabel.trim() || "（请填写反向说法）"
    : selectedType?.reverse_label || "关联于";

  useEffect(() => onDirtyChange(dirty), [dirty, onDirtyChange]);
  useEffect(() => onBusyChange(busy), [busy, onBusyChange]);
  useEffect(() => () => {
    onDirtyChange(false);
    onBusyChange(false);
  }, [onBusyChange, onDirtyChange]);
  useEffect(() => {
    if (error) requestAnimationFrame(() => errorRef.current?.focus());
  }, [error]);
  useEffect(() => {
    if (confirmState) requestAnimationFrame(() => stateDialogRef.current?.focus());
  }, [confirmState]);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setListError("");
    api.listLoreRelations(projectId, element.id, { status: statusFilter, limit: 100 }, controller.signal)
      .then((response) => {
        setRelations(response.items);
        setTotal(response.total);
      })
      .catch((loadError) => {
        if ((loadError as Error).name !== "AbortError") setListError(errorText(loadError));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [element.id, projectId, reloadToken, statusFilter]);

  useEffect(() => {
    const controller = new AbortController();
    api.listLoreRelationTypes(projectId, controller.signal)
      .then((response) => setTypes(response.items))
      .catch((loadError) => {
        if ((loadError as Error).name !== "AbortError") setError(`关系类型加载失败：${errorText(loadError)}`);
      });
    return () => controller.abort();
  }, [projectId]);

  useEffect(() => {
    if (!draftScope) return;
    const loaded = loadDraft<unknown>(draftScope);
    if ((loaded.status === "available" || loaded.status === "expired") && isStoredRelationCreate(loaded.draft.payload)) {
      setStored(loaded.draft.payload);
      setMessage(loaded.status === "expired" ? "已恢复超过七天的关系草稿；请核对后再提交。" : "已恢复这台设备上的未完成关系草稿。");
    } else if (loaded.status === "corrupt" || ((loaded.status === "available" || loaded.status === "expired") && !isStoredRelationCreate(loaded.draft.payload))) {
      setStorageBlocked(true);
      setError("本机保存的关系草稿已损坏。系统没有清除或覆盖它，请确认后再处理。");
    } else if (loaded.status === "unavailable") {
      setStorageBlocked(true);
      setError("浏览器草稿存储不可用；为避免刷新后重复创建，暂不能新建关系。");
    }
  }, [draftScope]);

  useEffect(() => {
    if (!stored || !draftScope || storageBlocked) return;
    const result = saveDraft(draftScope, stored, null);
    if (result.status !== "saved") {
      setStorageBlocked(true);
      setError("关系草稿无法安全写入浏览器，系统已停止提交。请检查浏览器存储设置。");
    }
  }, [draftScope, storageBlocked, stored]);

  useEffect(() => {
    if (!stored || !stored.draft.targetQuery.trim()) {
      setTargetResults([]);
      return;
    }
    const sequence = ++searchSequence.current;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      setTargetLoading(true);
      api.listLoreElements(projectId, {
        q: stored.draft.targetQuery.trim(),
        lifecycle_status: "active",
        limit: 20,
      }, controller.signal).then((response) => {
        if (sequence !== searchSequence.current) return;
        setTargetResults(response.items.filter((item) => item.id !== element.id));
      }).catch((searchError) => {
        if ((searchError as Error).name !== "AbortError") setError(`目标搜索失败：${errorText(searchError)}`);
      }).finally(() => {
        if (sequence === searchSequence.current) setTargetLoading(false);
      });
    }, 250);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [element.id, projectId, stored?.draft.targetQuery]);

  function updateDraft(update: (draft: RelationDraft) => RelationDraft) {
    if (!stored || frozen || busy) return;
    setStored((current) => current ? ({ ...current, draft: update(current.draft), frozenInput: null, phase: "draft" }) : current);
    setError("");
    setMessage("");
  }

  function startCreate() {
    if (!writable || interactionLocked || element.lifecycle_status !== "active" || storageBlocked) return;
    setStored(emptyStored(types.find((item) => item.key !== "custom")?.key));
    setError("");
    setMessage("");
    requestAnimationFrame(() => formHeadingRef.current?.focus());
  }

  function discardCreate() {
    if (!draftScope) return;
    if (stored && !window.confirm("确定放弃这份关系草稿吗？草稿会从本机移除。")) return;
    const result = clearDraft(draftScope);
    if (result.status === "unavailable") {
      setError("无法安全清除关系草稿，系统已停止关闭表单。");
      return;
    }
    setStored(null);
    setTargetResults([]);
    setError("");
    requestAnimationFrame(() => headingRef.current?.focus());
  }

  function clearCorruptDraft() {
    if (!draftScope || !window.confirm("确定清除损坏的本机关系草稿吗？此操作只清除无法读取的浏览器记录。")) return;
    const result = clearDraft(draftScope);
    if (result.status === "unavailable") {
      setError("无法清除损坏草稿，请检查浏览器存储设置。");
      return;
    }
    setStorageBlocked(false);
    setError("");
    setMessage("损坏的本机草稿已清除，可以重新创建关系。");
  }

  function buildCreateInput(): LoreRelationCreateInput | null {
    if (!stored?.draft.target) {
      setError("请先搜索并选择一个目标设定。");
      return null;
    }
    if (!selectedType) {
      setError("请选择关系类型。");
      return null;
    }
    if (selectedType.key === "custom" && (!stored.draft.customForwardLabel.trim() || !stored.draft.customReverseLabel.trim())) {
      setError("自定义关系需要填写两个方向的说法。");
      return null;
    }
    return {
      operation_key: stored.operationKey,
      target_element_id: stored.draft.target.id,
      source_expected_version: element.lock_version,
      target_expected_version: stored.draft.target.lock_version,
      relation_type: selectedType.key,
      custom_forward_label: selectedType.key === "custom" ? stored.draft.customForwardLabel.trim() : null,
      custom_reverse_label: selectedType.key === "custom" ? stored.draft.customReverseLabel.trim() : null,
      description: stored.draft.description.trim(),
    };
  }

  async function submitCreate(event?: FormEvent) {
    event?.preventDefault();
    if (!stored || busy || interactionLocked || storageBlocked) return;
    const input = stored.frozenInput ?? buildCreateInput();
    if (!input) return;
    setStored((current) => current ? ({ ...current, frozenInput: input, phase: "outcome_unknown" }) : current);
    setBusy(true);
    setError("");
    try {
      const response = await api.createLoreRelation(projectId, element.id, input);
      if (draftScope) clearDraft(draftScope);
      setStored(null);
      setMessage(response.replayed ? "这条关系此前已经创建，已安全载入原结果。" : "关系已创建。正确反向说法也已保存。");
      setStatusFilter(response.status);
      setReloadToken((value) => value + 1);
      onMutationComplete("关系已保存，设定关联数量已更新。");
    } catch (createError) {
      if (createError instanceof ApiError) {
        const phase: CreatePhase = createError.status === 503
          ? "maintenance"
          : createError.code === "LORE_RELATION_ENDPOINT_CHANGED"
            ? "endpoint_changed"
          : createError.code === "LORE_RELATION_CREATE_IDEMPOTENCY_CONFLICT"
            ? "conflict"
            : "retryable";
        setStored((current) => current ? ({ ...current, frozenInput: input, phase }) : current);
        setError(createError.status === 503
          ? "设定仓库正在维护，关系草稿和本次精确请求已保留。"
          : createError.detail);
      } else {
        setStored((current) => current ? ({ ...current, frozenInput: input, phase: "outcome_unknown" }) : current);
        setError("网络结果不确定。系统不会自动重复提交；可使用相同请求安全核对。");
      }
    } finally {
      setBusy(false);
    }
  }

  async function refreshEndpointVersions() {
    if (!stored?.frozenInput || stored.phase !== "endpoint_changed" || busy) return;
    setBusy(true);
    setError("");
    try {
      const [latestSource, latestTarget] = await Promise.all([
        api.getLoreElement(projectId, element.id),
        api.getLoreElement(projectId, stored.frozenInput.target_element_id),
      ]);
      if (latestSource.lifecycle_status !== "active" || latestTarget.lifecycle_status !== "active") {
        setError("关系两端必须都是使用中的设定；请先恢复相应设定。");
        return;
      }
      setStored((current) => current?.frozenInput ? ({
        ...current,
        frozenInput: {
          ...current.frozenInput,
          source_expected_version: latestSource.lock_version,
          target_expected_version: latestTarget.lock_version,
        },
        draft: {
          ...current.draft,
          target: current.draft.target ? {
            ...current.draft.target,
            lock_version: latestTarget.lock_version,
          } : current.draft.target,
        },
        phase: "retryable",
      }) : current);
      setMessage("已载入关系两端的最新版本；请核对预览后使用相同操作键重试。");
    } catch (refreshError) {
      setError(`无法载入关系两端的最新版本：${errorText(refreshError)}`);
    } finally {
      setBusy(false);
    }
  }

  function startEdit(relation: LoreRelation, trigger: HTMLButtonElement) {
    actionTriggerRef.current = trigger;
    setEditing({
      relation,
      forwardLabel: relation.forward_label,
      reverseLabel: relation.reverse_label,
      description: relation.description,
    });
    setError("");
    requestAnimationFrame(() => formHeadingRef.current?.focus());
  }

  async function submitEdit(event: FormEvent) {
    event.preventDefault();
    if (!editing || busy || interactionLocked) return;
    const forwardLabel = editing.forwardLabel.trim();
    const reverseLabel = editing.reverseLabel.trim();
    if (!forwardLabel || !reverseLabel) {
      setError("两个方向的关系说法都不能为空。");
      return;
    }
    setBusy(true);
    setError("");
    try {
      await api.updateLoreRelation(projectId, editing.relation.id, {
        expected_version: editing.relation.lock_version,
        forward_label: forwardLabel,
        reverse_label: reverseLabel,
        description: editing.description.trim(),
        metadata: editing.relation.metadata,
      });
      setEditing(null);
      setMessage("关系说法已保存。");
      setReloadToken((value) => value + 1);
      onMutationComplete("关系已更新。");
    } catch (editError) {
      if (!(editError instanceof ApiError)) {
        try {
          const latest = await api.getLoreRelation(projectId, editing.relation.id);
          if (latest.forward_label === forwardLabel && latest.reverse_label === reverseLabel && latest.description === editing.description.trim()) {
            setEditing(null);
            setReloadToken((value) => value + 1);
            onMutationComplete("服务器已保存相同关系内容，已同步最新状态。");
            return;
          }
        } catch { /* keep the local draft */ }
      }
      setError(editError instanceof ApiError && editError.status === 409
        ? "关系刚刚被其他操作更新。你的本地修改仍保留，请重新打开最新关系后再保存。"
        : `关系修改结果无法确认：${errorText(editError)} 本地内容仍保留。`);
    } finally {
      setBusy(false);
    }
  }

  async function performState() {
    if (!confirmState || busy || interactionLocked) return;
    const { relation, action } = confirmState;
    setBusy(true);
    setError("");
    try {
      await api.changeLoreRelationState(projectId, relation.id, action, {
        expected_version: relation.lock_version,
        reason: reason.trim(),
      });
      setConfirmState(null);
      setReason("");
      setMessage(action === "archive" ? "关系已归档，两端设定仍然保留。" : "关系已恢复使用。" );
      setReloadToken((value) => value + 1);
      onMutationComplete(action === "archive" ? "关系已归档。" : "关系已恢复。" );
    } catch (stateError) {
      if (!(stateError instanceof ApiError)) {
        try {
          const latest = await api.getLoreRelation(projectId, relation.id);
          const expectedStatus = action === "archive" ? "archived" : "active";
          if (latest.status === expectedStatus) {
            setConfirmState(null);
            setReloadToken((value) => value + 1);
            onMutationComplete("服务器已达到目标关系状态，已同步最新结果。");
            return;
          }
        } catch { /* keep confirmation and error */ }
      }
      setError(`关系状态未能确认：${errorText(stateError)} 请核对最新状态后重试。`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="lore-relations" aria-busy={loading || busy}>
      <div className="lore-relations-heading">
        <div><h3 ref={headingRef} tabIndex={-1}>设定关系</h3><p>从“{element.name}”的视角查看每条联系。</p></div>
        {writable && element.lifecycle_status === "active" && !stored && !editing && (
          <button className="btn btn-primary" type="button" disabled={busy || interactionLocked || storageBlocked} onClick={startCreate}>添加关系</button>
        )}
      </div>
      <div className="lore-relation-tabs" aria-label="关系状态">
        <button type="button" aria-pressed={statusFilter === "active"} onClick={() => setStatusFilter("active")}>使用中</button>
        <button type="button" aria-pressed={statusFilter === "archived"} onClick={() => setStatusFilter("archived")}>已归档</button>
        <span aria-live="polite">共 {loading ? "…" : total} 条</span>
      </div>
      {message && <div className="lore-note" role="status">{message}</div>}
      {error && <div className="lore-alert" role="alert" tabIndex={-1} ref={errorRef}>{error}{storageBlocked && <button type="button" onClick={clearCorruptDraft}>确认清除损坏草稿</button>}</div>}
      {listError && <div className="lore-alert" role="alert">关系加载失败：{listError}<button type="button" onClick={() => setReloadToken((value) => value + 1)}>重试</button></div>}
      {loading ? <p>关系加载中…</p> : !listError && relations.length === 0 ? <div className="lore-empty"><strong>{statusFilter === "active" ? "还没有使用中的关系" : "没有已归档的关系"}</strong><span>{writable && statusFilter === "active" ? "可添加人物、地点、阵营或事件之间的联系。" : "关系记录会在这里保留。"}</span></div> : (
        <div className="lore-relation-list">
          {relations.map((relation) => {
            const view = relationView(relation, element.id);
            const canRestore = relation.source.lifecycle_status === "active" && relation.target.lifecycle_status === "active";
            return <article className="lore-relation-card" key={relation.id} tabIndex={-1}>
              <p className="lore-relation-sentence"><strong>{element.name}</strong> {view.label} <button type="button" onClick={() => onOpenElement(view.other.id)}>{view.other.name}</button></p>
              <p>{view.other.type.display_name}{view.other.summary ? ` · ${view.other.summary}` : " · 暂无摘要"}</p>
              {relation.description && <p>{relation.description}</p>}
              <small>{view.outgoing ? "从当前设定出发" : "指向当前设定"} · {relation.status === "active" ? "使用中" : "已归档"} · 版本 {relation.version_no}</small>
              {writable && <div className="lore-candidate-actions">
                {relation.status === "active" && <button className="btn btn-secondary" type="button" disabled={busy || interactionLocked || !!editing || !!stored} onClick={(event) => startEdit(relation, event.currentTarget)}>编辑说法</button>}
                <button className="btn btn-secondary" type="button" disabled={busy || interactionLocked || !!editing || !!stored || (relation.status === "archived" && !canRestore)} title={!canRestore ? "请先恢复关系两端的设定" : undefined} onClick={(event) => { actionTriggerRef.current = event.currentTarget; setConfirmState({ relation, action: relation.status === "active" ? "archive" : "restore" }); setReason(""); }}>{relation.status === "active" ? "归档关系" : "恢复关系"}</button>
                {relation.status === "archived" && !canRestore && <span>请先恢复关系两端的设定</span>}
              </div>}
            </article>;
          })}
        </div>
      )}

      {stored && <form className="lore-relation-form" onSubmit={submitCreate}>
        <h3 ref={formHeadingRef} tabIndex={-1}>添加设定关系</h3>
        <fieldset disabled={busy || interactionLocked || frozen || storageBlocked}>
          <label><span>关系类型</span><select className="form-select" value={stored.draft.relationType} onChange={(event) => updateDraft((draft) => ({ ...draft, relationType: event.target.value }))}>{types.map((type) => <option key={type.key} value={type.key}>{type.display_name}</option>)}</select></label>
          {selectedType?.key === "custom" && <div className="lore-relation-custom-labels">
            <label><span>从当前设定看，关系是</span><input className="form-input" maxLength={100} value={stored.draft.customForwardLabel} onChange={(event) => updateDraft((draft) => ({ ...draft, customForwardLabel: event.target.value }))} /></label>
            <label><span>从对方看，关系是</span><input className="form-input" maxLength={100} value={stored.draft.customReverseLabel} onChange={(event) => updateDraft((draft) => ({ ...draft, customReverseLabel: event.target.value }))} /></label>
          </div>}
          <label><span>搜索目标设定</span><input type="search" className="form-input" value={stored.draft.targetQuery} placeholder="输入名称或摘要" onChange={(event) => updateDraft((draft) => ({ ...draft, targetQuery: event.target.value, target: null }))} /></label>
          <div className="lore-relation-targets" aria-label="目标设定搜索结果">{targetLoading ? <p>搜索中…</p> : targetResults.map((item) => <button type="button" aria-pressed={stored.draft.target?.id === item.id} key={item.id} onClick={() => updateDraft((draft) => ({ ...draft, target: item, targetQuery: item.name }))}><strong>{item.name}</strong><span>{item.type.display_name} · {item.summary || "暂无摘要"} · {item.source_summary}</span></button>)}</div>
          <label><span>关系说明（可选）</span><textarea className="form-textarea" maxLength={2000} value={stored.draft.description} onChange={(event) => updateDraft((draft) => ({ ...draft, description: event.target.value }))} /></label>
        </fieldset>
        <div className="lore-relation-preview" aria-label="关系预览">
          <strong>正反两句预览</strong>
          <p>{element.name} {previewForward} {stored.draft.target?.name || "目标设定"}</p>
          <p>{stored.draft.target?.name || "目标设定"} {previewReverse} {element.name}</p>
        </div>
        <div className="lore-candidate-actions">
          <button className="btn btn-primary" type="submit" disabled={busy || interactionLocked || storageBlocked || stored.phase === "endpoint_changed" || (frozen && !stored.frozenInput)}>{busy ? "保存中…" : frozen ? "使用相同内容安全重试" : "创建关系"}</button>
          {stored.phase === "endpoint_changed" && <button className="btn btn-secondary" type="button" disabled={busy || interactionLocked} onClick={() => void refreshEndpointVersions()}>载入最新端点版本</button>}
          <button className="btn btn-secondary" type="button" disabled={busy || interactionLocked} onClick={discardCreate}>放弃草稿</button>
        </div>
      </form>}

      {editing && <form className="lore-relation-form" onSubmit={submitEdit}>
        <h3 ref={formHeadingRef} tabIndex={-1}>编辑关系说法</h3>
        <p>关系两端保持不变；如需改变关系类型，请归档旧关系后重新创建。</p>
        <fieldset disabled={busy || interactionLocked}>
          <label><span>从“{editing.relation.source.name}”看</span><input className="form-input" maxLength={100} value={editing.forwardLabel} onChange={(event) => setEditing((current) => current ? ({ ...current, forwardLabel: event.target.value }) : current)} /></label>
          <label><span>从“{editing.relation.target.name}”看</span><input className="form-input" maxLength={100} value={editing.reverseLabel} onChange={(event) => setEditing((current) => current ? ({ ...current, reverseLabel: event.target.value }) : current)} /></label>
          <label><span>关系说明（可选）</span><textarea className="form-textarea" maxLength={2000} value={editing.description} onChange={(event) => setEditing((current) => current ? ({ ...current, description: event.target.value }) : current)} /></label>
        </fieldset>
        <div className="lore-candidate-actions"><button className="btn btn-primary" type="submit" disabled={busy || interactionLocked}>保存关系</button><button className="btn btn-secondary" type="button" disabled={busy || interactionLocked} onClick={() => { setEditing(null); requestAnimationFrame(() => actionTriggerRef.current?.focus()); }}>取消</button></div>
      </form>}

      {confirmState && <div className="lore-confirm" role="alertdialog" aria-label={`确认${confirmState.action === "archive" ? "归档" : "恢复"}关系`} tabIndex={-1} ref={stateDialogRef}>
        <h3>确认{confirmState.action === "archive" ? "归档" : "恢复"}关系</h3>
        <p>{confirmState.action === "archive" ? "只停用这条联系，不会删除关系两端的设定。" : "关系恢复后会重新作为有效联系使用。"}</p>
        <label><span>原因（可选）</span><input className="form-input" maxLength={200} value={reason} onChange={(event) => setReason(event.target.value)} /></label>
        <div className="lore-candidate-actions"><button className="btn btn-primary" type="button" disabled={busy || interactionLocked} onClick={() => void performState()}>确认</button><button className="btn btn-secondary" type="button" disabled={busy || interactionLocked} onClick={() => { setConfirmState(null); requestAnimationFrame(() => actionTriggerRef.current?.focus()); }}>取消</button></div>
      </div>}
    </section>
  );
}
