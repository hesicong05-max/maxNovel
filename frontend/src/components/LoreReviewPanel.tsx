import { FormEvent, KeyboardEvent as ReactKeyboardEvent, useEffect, useMemo, useRef, useState } from "react";
import { ApiError, api } from "@/services/api";
import LoreMergeWizard from "@/components/LoreMergeWizard";
import {
  clearDraft,
  loadDraft,
  saveDraft,
  type DraftScope,
} from "@/services/maintenanceDrafts";
import type {
  LoreElementListItem,
  LoreManualReviewCreateInput,
  LoreReviewDecisionInput,
  LoreReviewDetail,
  LoreReviewKind,
  LoreReviewListItem,
  LoreReviewStatus,
  LoreTypeDefinition,
} from "@/types/lore";

type Decision = Exclude<LoreReviewStatus, "pending">;
type DraftPhase = "draft" | "outcome_unknown" | "maintenance" | "conflict" | "stale";

interface StoredReviewDraft {
  version: 1;
  decision: Decision | "";
  note: string;
  phase: DraftPhase;
  frozenInput: LoreReviewDecisionInput | null;
}

const KIND_LABEL: Record<LoreReviewKind, string> = {
  possible_duplicate: "可能重复",
  possible_conflict: "可能冲突",
};

const STATUS_LABEL: Record<LoreReviewStatus, string> = {
  pending: "待核对",
  deferred: "稍后处理",
  confirmed_duplicate: "已人工判断为重复",
  confirmed_conflict: "已人工判断为存在冲突",
  not_an_issue: "已人工判断为不是问题",
};

const DECISIONS: Array<[Decision, string]> = [
  ["confirmed_duplicate", "判断为重复"],
  ["confirmed_conflict", "判断为存在冲突"],
  ["not_an_issue", "不是问题"],
  ["deferred", "稍后处理"],
];

function operationKey(): string {
  try {
    return `review-${globalThis.crypto.randomUUID()}`;
  } catch {
    return `review-${Date.now()}-${Math.random().toString(36).slice(2)}`;
  }
}

function isStoredDraft(value: unknown): value is StoredReviewDraft {
  if (!value || typeof value !== "object") return false;
  const draft = value as Partial<StoredReviewDraft>;
  return draft.version === 1
    && (draft.decision === "" || DECISIONS.some(([decision]) => decision === draft.decision))
    && typeof draft.note === "string"
    && ["draft", "outcome_unknown", "maintenance", "conflict", "stale"].includes(String(draft.phase))
    && (draft.frozenInput === null || typeof draft.frozenInput === "object");
}

function message(error: unknown): string {
  if (error instanceof ApiError) return error.detail;
  return error instanceof Error ? error.message : "操作失败，请稍后重试。";
}

function unique(items: LoreReviewListItem[]): LoreReviewListItem[] {
  return Array.from(new Map(items.map((item) => [item.id, item])).values());
}

export default function LoreReviewPanel({
  projectId,
  userId,
  readOnly,
  mergeCommitEnabled,
  loreTypes,
  onDirtyChange,
  onBusyChange,
  onOpenElement,
  onOverviewRefresh,
}: {
  projectId: string;
  userId: string;
  readOnly: boolean;
  mergeCommitEnabled: boolean;
  loreTypes: LoreTypeDefinition[];
  onDirtyChange: (dirty: boolean) => void;
  onBusyChange: (busy: boolean) => void;
  onOpenElement: (elementId: string, afterSuccessfulMerge?: boolean) => void;
  onOverviewRefresh: () => void;
}) {
  const [q, setQ] = useState("");
  const [kind, setKind] = useState<"" | LoreReviewKind>("");
  const [status, setStatus] = useState("needs_review");
  const [items, setItems] = useState<LoreReviewListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [listError, setListError] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<LoreReviewDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState("");
  const [decision, setDecision] = useState<Decision | "">("");
  const [note, setNote] = useState("");
  const [phase, setPhase] = useState<DraftPhase>("draft");
  const [frozenInput, setFrozenInput] = useState<LoreReviewDecisionInput | null>(null);
  const [storageError, setStorageError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState<"scan" | "decide" | "check" | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [mergeDirty, setMergeDirty] = useState(false);
  const [mergeBusy, setMergeBusy] = useState(false);
  const [manualDirty, setManualDirty] = useState(false);
  const [manualBusy, setManualBusy] = useState(false);
  const [reloadToken, setReloadToken] = useState(0);
  const listSequence = useRef(0);
  const detailSequence = useRef(0);
  const headingRef = useRef<HTMLHeadingElement | null>(null);
  const detailHeadingRef = useRef<HTMLHeadingElement | null>(null);
  const errorRef = useRef<HTMLDivElement | null>(null);
  const confirmRef = useRef<HTMLDivElement | null>(null);
  const decisionTriggerRef = useRef<HTMLButtonElement | null>(null);
  const decisionCancelRef = useRef<HTMLButtonElement | null>(null);
  const listHeadingRef = useRef<HTMLHeadingElement | null>(null);
  const cardRefs = useRef(new Map<string, HTMLButtonElement>());

  const draftScope = useMemo<DraftScope | null>(() => selectedId ? ({
    userId,
    projectId,
    kind: "lore-suggestion-review",
    objectId: selectedId,
  }) : null, [projectId, selectedId, userId]);
  const reviewDirty = decision !== "" || note.trim() !== "" || frozenInput !== null || mergeDirty;
  const dirty = reviewDirty || manualDirty;

  useEffect(() => onDirtyChange(dirty), [dirty, onDirtyChange]);
  useEffect(() => onBusyChange(busy !== null || mergeBusy || manualBusy), [busy, manualBusy, mergeBusy, onBusyChange]);
  useEffect(() => () => {
    onDirtyChange(false);
    onBusyChange(false);
  }, [onBusyChange, onDirtyChange]);
  useEffect(() => {
    if (detailError || storageError) requestAnimationFrame(() => errorRef.current?.focus());
  }, [detailError, storageError]);
  useEffect(() => {
    if (confirmOpen) requestAnimationFrame(() => decisionCancelRef.current?.focus());
  }, [confirmOpen]);

  useEffect(() => {
    const controller = new AbortController();
    const sequence = ++listSequence.current;
    setLoading(true);
    setListError("");
    api.listLoreReviews(projectId, {
      q: q.trim() || undefined,
      kind: kind || undefined,
      review_status: status,
      limit: 20,
    }, controller.signal).then((response) => {
      if (sequence !== listSequence.current) return;
      setItems(response.items);
      setTotal(response.total);
      setCursor(response.next_cursor);
      if (selectedId && !response.items.some((item) => item.id === selectedId)) {
        setSelectedId(null);
        setDetail(null);
      }
    }).catch((error) => {
      if ((error as Error).name !== "AbortError" && sequence === listSequence.current) {
        setListError(message(error));
      }
    }).finally(() => {
      if (sequence === listSequence.current) setLoading(false);
    });
    return () => controller.abort();
  }, [kind, projectId, reloadToken, status]);

  useEffect(() => {
    const sequence = ++detailSequence.current;
    if (!selectedId) {
      setDetail(null);
      setDetailError("");
      return;
    }
    const controller = new AbortController();
    setDetailLoading(true);
    setDetailError("");
    api.getLoreReview(projectId, selectedId, controller.signal).then((response) => {
      if (sequence !== detailSequence.current) return;
      setDetail(response);
      requestAnimationFrame(() => detailHeadingRef.current?.focus());
    }).catch((error) => {
      if ((error as Error).name !== "AbortError" && sequence === detailSequence.current) {
        setDetailError(message(error));
      }
    }).finally(() => {
      if (sequence === detailSequence.current) setDetailLoading(false);
    });
    return () => controller.abort();
  }, [projectId, reloadToken, selectedId]);

  useEffect(() => {
    setDecision("");
    setNote("");
    setPhase("draft");
    setFrozenInput(null);
    setStorageError("");
    if (!draftScope) return;
    const loaded = loadDraft<unknown>(draftScope);
    if ((loaded.status === "available" || loaded.status === "expired") && isStoredDraft(loaded.draft.payload)) {
      setDecision(loaded.draft.payload.decision);
      setNote(loaded.draft.payload.note);
      setPhase(loaded.draft.payload.phase);
      setFrozenInput(loaded.draft.payload.frozenInput);
      setNotice(loaded.status === "expired"
        ? "已恢复超过七天的判断草稿；系统没有自动提交。"
        : "已恢复这台设备上的判断草稿；系统没有自动提交。");
    } else if (
      loaded.status === "corrupt"
      || loaded.status === "available"
      || loaded.status === "expired"
    ) {
      setStorageError("这条线索的本机草稿已损坏；原记录仍保留，清除前不会提交新的判断。");
    } else if (loaded.status === "unavailable") {
      setStorageError("浏览器草稿存储不可用；为避免未知结果，本次不能提交判断。");
    }
  }, [draftScope]);

  function persist(next: StoredReviewDraft): boolean {
    if (!draftScope) return false;
    const saved = saveDraft(draftScope, next, null);
    if (saved.status === "unavailable") {
      setStorageError("浏览器无法安全保存判断草稿；系统没有提交，请检查浏览器存储设置。");
      return false;
    }
    setStorageError("");
    return true;
  }

  function updateDraft(nextDecision: Decision | "", nextNote: string) {
    setDecision(nextDecision);
    setNote(nextNote);
    setPhase("draft");
    setFrozenInput(null);
    persist({ version: 1, decision: nextDecision, note: nextNote, phase: "draft", frozenInput: null });
  }

  function discardDraft(): boolean {
    if (!draftScope) return true;
    const result = clearDraft(draftScope);
    if (result.status === "unavailable") {
      setStorageError("无法安全清除本机判断草稿，已停止切换。");
      return false;
    }
    setDecision("");
    setNote("");
    setPhase("draft");
    setFrozenInput(null);
    setStorageError("");
    return true;
  }

  function selectItem(id: string) {
    if (busy) return;
    if (id !== selectedId && reviewDirty && !window.confirm("确定放弃当前尚未提交的判断草稿吗？")) return;
    if (id !== selectedId && reviewDirty && !discardDraft()) return;
    setNotice("");
    setSelectedId(id);
  }

  function closeDecisionConfirmation() {
    if (busy === "decide") return;
    setConfirmOpen(false);
    requestAnimationFrame(() => {
      const trigger = decisionTriggerRef.current;
      if (trigger?.isConnected) trigger.focus();
    });
  }

  function handleDecisionConfirmationKeyDown(event: ReactKeyboardEvent<HTMLDivElement>) {
    if (event.key === "Escape") {
      event.preventDefault();
      closeDecisionConfirmation();
      return;
    }
    if (event.key !== "Tab") return;
    const focusable = Array.from(event.currentTarget.querySelectorAll<HTMLElement>(
      "button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex='-1'])",
    ));
    if (focusable.length === 0) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  function returnToReviewList() {
    const returnId = selectedId;
    if (!returnId) return;
    if (reviewDirty && !window.confirm("确定放弃当前判断草稿吗？")) return;
    if (reviewDirty && !discardDraft()) return;
    setSelectedId(null);
    requestAnimationFrame(() => {
      const card = cardRefs.current.get(returnId);
      if (card?.isConnected && !card.disabled) card.focus();
      else listHeadingRef.current?.focus();
    });
  }

  async function scan() {
    setBusy("scan");
    setListError("");
    try {
      const response = await api.scanLoreReviews(projectId);
      setNotice(response.truncated
        ? `扫描已完成，但同名组合超过安全上限；本次新增 ${response.created} 条，结果不完整，系统没有静默省略此风险。`
        : `扫描完成：新增 ${response.created} 条，更新 ${response.updated} 条，${response.unchanged} 条无需变化。`);
      setReloadToken((value) => value + 1);
      onOverviewRefresh();
    } catch (error) {
      setListError(message(error));
    } finally {
      setBusy(null);
    }
  }

  async function loadMore() {
    if (!cursor || loadingMore) return;
    setLoadingMore(true);
    try {
      const response = await api.listLoreReviews(projectId, {
        q: q.trim() || undefined,
        kind: kind || undefined,
        review_status: status,
        cursor,
        limit: 20,
      });
      setItems((current) => unique([...current, ...response.items]));
      setCursor(response.next_cursor);
    } catch (error) {
      setListError(message(error));
    } finally {
      setLoadingMore(false);
    }
  }

  function requestDecision(event: FormEvent) {
    event.preventDefault();
    if (!detail || !decision || detail.stale || readOnly || storageError) return;
    const input: LoreReviewDecisionInput = frozenInput ?? {
      operation_key: operationKey(),
      expected_version: detail.lock_version,
      expected_evidence_revision: detail.evidence_revision,
      decision,
      note: note.trim(),
    };
    if (!persist({ version: 1, decision, note, phase: "draft", frozenInput: input })) return;
    setFrozenInput(input);
    setConfirmOpen(true);
  }

  async function submitDecision() {
    if (!detail || !draftScope || !frozenInput) return;
    setConfirmOpen(false);
    setBusy("decide");
    setDetailError("");
    try {
      const response = await api.decideLoreReview(projectId, detail.id, frozenInput);
      const cleared = clearDraft(draftScope);
      if (cleared.status === "unavailable") {
        setStorageError("判断已记录，但浏览器未能清除本机草稿；请勿再次提交，并检查存储设置。");
      }
      setDecision("");
      setNote("");
      setFrozenInput(null);
      setPhase("draft");
      setDetail(response.suggestion);
      setNotice(response.replayed
        ? "该判断此前已记录，已安全同步；没有自动合并或改写设定。"
        : response.applied
          ? "人工判断已记录；不会自动合并、删除、停用或改写设定。"
          : "判断与当前状态相同；已安全记录请求，没有修改设定。" );
      onOverviewRefresh();
      if (frozenInput.decision === "confirmed_duplicate") setStatus("confirmed_duplicate");
      setReloadToken((value) => value + 1);
      if (status === "needs_review" && frozenInput.decision !== "confirmed_duplicate") {
        setSelectedId(response.next_pending_id);
        requestAnimationFrame(() => {
          const target = response.next_pending_id ? cardRefs.current.get(response.next_pending_id) : null;
          (target ?? headingRef.current)?.focus();
        });
      }
    } catch (error) {
      if (error instanceof ApiError && error.status === 503) {
        setPhase("maintenance");
        persist({ version: 1, decision, note, phase: "maintenance", frozenInput });
        setDetailError("仓库正在维护，判断草稿已保留；系统没有自动重试。");
      } else if (error instanceof ApiError && error.status === 409) {
        const nextPhase = error.code === "LORE_REVIEW_EVIDENCE_STALE" ? "stale" : "conflict";
        setPhase(nextPhase);
        persist({ version: 1, decision, note, phase: nextPhase, frozenInput });
        setDetailError(`${error.detail} 本地判断草稿仍保留。`);
      } else {
        setPhase("outcome_unknown");
        persist({ version: 1, decision, note, phase: "outcome_unknown", frozenInput });
        setDetailError("网络结果不确定。系统不会更换请求或自动重复提交；请先核对最新状态。");
      }
    } finally {
      setBusy(null);
    }
  }

  async function checkLatest() {
    if (!selectedId) return;
    setBusy("check");
    setDetailError("");
    try {
      const latest = await api.getLoreReview(projectId, selectedId);
      setDetail(latest);
      if (latest.stale) {
        setDetailError("对比依据已变化，请先重新扫描；系统没有提交本地判断。");
      } else {
        setDetailError("单次读取不能证明这次请求由谁完成；可在核对后使用原冻结请求安全重试，系统不会创建重复历史。");
      }
    } catch (error) {
      setDetailError(message(error));
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="lore-review-panel" aria-label="重复与冲突线索">
      <div className="lore-review-toolbar">
        <div>
          <h2 ref={headingRef} tabIndex={-1}>重复与冲突</h2>
          <p>所有线索只用于待核对，不会自动认定、合并或改写设定。</p>
        </div>
        <button className="btn btn-primary" type="button" disabled={readOnly || busy !== null} onClick={scan}>
          {busy === "scan" ? "扫描中…" : "扫描正式设定"}
        </button>
      </div>
      <ManualReviewForm
        projectId={projectId}
        userId={userId}
        readOnly={readOnly}
        onDirtyChange={setManualDirty}
        onBusyChange={setManualBusy}
        onCreated={(createdDetail, created, replayed, openedExistingConflict = false) => {
          setStatus(createdDetail.needs_review ? "needs_review" : createdDetail.review_status);
          setSelectedId(createdDetail.id);
          setDetail(createdDetail);
          setNotice(openedExistingConflict
            ? "这两项设定已有另一条人工线索，已打开原记录；本次没有覆盖。"
            : replayed
              ? "该人工线索先前已安全记录，本次没有重复创建。"
              : created
                ? "人工线索已创建；它不会自动改写或合并设定。"
                : "这两项设定已有相同的人工线索，已安全复用。");
          setReloadToken((value) => value + 1);
          onOverviewRefresh();
        }}
      />
      {readOnly && <div className="lore-note">当前仓库只读，可查看已有线索，但不能扫描或记录判断。</div>}
      {notice && <div className="lore-note" role="status">{notice}</div>}
      {listError && <div className="lore-alert" role="alert">{listError}<button type="button" onClick={() => setReloadToken((value) => value + 1)}>重试</button></div>}
      <form className="lore-review-filters" onSubmit={(event) => { event.preventDefault(); setReloadToken((value) => value + 1); }}>
        <label><span>搜索两端名称</span><input className="form-input" value={q} onChange={(event) => setQ(event.target.value)} /></label>
        <label><span>线索类型</span><select className="form-select" value={kind} onChange={(event) => setKind(event.target.value as "" | LoreReviewKind)}><option value="">全部类型</option><option value="possible_duplicate">可能重复</option><option value="possible_conflict">可能冲突</option></select></label>
        <label><span>处理状态</span><select className="form-select" value={status} onChange={(event) => setStatus(event.target.value)}><option value="needs_review">待核对</option><option value="resolved">已判断</option><option value="deferred">稍后处理</option><option value="confirmed_duplicate">已判断为重复</option><option value="confirmed_conflict">已判断为冲突</option><option value="not_an_issue">不是问题</option></select></label>
        <button className="btn btn-secondary" type="submit">搜索</button>
      </form>
      <div className={`lore-workspace lore-review-workspace ${selectedId ? "has-selection" : ""}`}>
        <section className="lore-list" aria-busy={loading} aria-label="设定线索列表">
          <div className="lore-list-heading"><h3 ref={listHeadingRef} tabIndex={-1}>复核线索</h3><span aria-live="polite">{loading ? "加载中…" : `共 ${total} 项`}</span></div>
          {!loading && !listError && total === 0 && <div className="lore-empty"><strong>当前没有待核对的线索</strong><span>这不表示仓库一定没有重复或冲突；可扫描正式设定，也可创建人工线索。</span></div>}
          {items.map((item) => <button
            key={item.id}
            ref={(node) => { if (node) cardRefs.current.set(item.id, node); else cardRefs.current.delete(item.id); }}
            type="button"
            className={`lore-card ${selectedId === item.id ? "selected" : ""}`}
            aria-current={selectedId === item.id ? "true" : undefined}
            disabled={busy !== null}
            onClick={() => selectItem(item.id)}
          >
            <span className="lore-card-top"><span className="lore-type">{item.origin === "author_report" ? "作者提报" : "系统扫描"} · {KIND_LABEL[item.kind]}</span>{item.stale && <span className="lore-badge lore-badge--warning">依据已变化</span>}</span>
            <strong>{item.left.name} ↔ {item.right.name}</strong>
            <span className="lore-summary">{item.primary_reason}</span>
            <span className="lore-meta">{STATUS_LABEL[item.review_status]} · {item.left.type.display_name} · 证据版本 {item.evidence_revision}</span>
          </button>)}
          {cursor && <button className="btn btn-secondary lore-load-more" type="button" disabled={loadingMore} onClick={loadMore}>{loadingMore ? "加载中…" : "加载更多"}</button>}
        </section>
        <aside className="lore-detail lore-review-detail" aria-label="线索详情">
          {selectedId && <button className="btn btn-secondary lore-detail-back" type="button" onClick={returnToReviewList}>← 返回线索列表</button>}
          {!selectedId && <div className="lore-empty"><strong>选择一条复核线索</strong><span>可对比双方版本、相关字段和原始来源。</span></div>}
          {detailLoading && <div className="lore-empty">线索详情加载中…</div>}
          {(detailError || storageError) && <div ref={errorRef} tabIndex={-1} className="lore-alert" role="alert">{detailError || storageError}{(phase === "outcome_unknown" || phase === "conflict" || phase === "stale") && <button type="button" disabled={busy !== null} onClick={checkLatest}>{busy === "check" ? "核对中…" : "核对最新状态"}</button>}</div>}
          {detail && <>
            <header className="lore-review-detail-header">
              <h2 ref={detailHeadingRef} tabIndex={-1}>核对这条设定线索</h2>
              <p>{detail.origin === "author_report" ? "这是作者主动提报的复核线索，尚未形成正式判断。" : "系统扫描发现的是可能性，以下内容尚未被人工确认。"}</p>
              <span className="lore-badge">{KIND_LABEL[detail.kind]}</span>
              <strong>{STATUS_LABEL[detail.review_status]}</strong>
            </header>
            {detail.stale && <div className="lore-alert" role="alert">对比依据已变化，请重新扫描后再判断。</div>}
            <section className="lore-review-comparison" aria-label="两项设定对比">
              <ReviewEndpointCard label="左侧设定" endpoint={detail.left_snapshot} onOpen={() => onOpenElement(detail.left.id)} />
              <div className="lore-review-evidence"><h3>{detail.origin === "author_report" ? "作者提报内容" : "系统扫描依据"}</h3><p>{detail.primary_reason}</p>{detail.evidence.length === 0 ? <p>未记录额外依据。</p> : detail.evidence.map((evidence) => evidence.comparison === "author_report" ? <article key={evidence.field_key}><strong>{evidence.label}</strong><span>{evidence.statement || "未填写说明"}</span></article> : <article key={evidence.field_key}><strong>{evidence.label}：内容不同</strong><span>左：{evidence.left_value || "一侧为空"}</span><span>右：{evidence.right_value || "一侧为空"}</span></article>)}<p className="lore-note">线索仅用于引导人工复核，不自动代表事实矛盾或重复设定。</p></div>
              <ReviewEndpointCard label="右侧设定" endpoint={detail.right_snapshot} onOpen={() => onOpenElement(detail.right.id)} />
            </section>
            <form className="lore-review-decision" onSubmit={requestDecision}>
              <h3>记录人工判断</h3>
              <label><span>判断</span><select className="form-select" value={decision} disabled={readOnly || busy !== null || detail.stale} onChange={(event) => updateDraft(event.target.value as Decision | "", note)}><option value="">请选择</option>{DECISIONS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
              <label><span>备注（可选，最多 500 字）</span><textarea className="form-textarea" maxLength={500} value={note} disabled={readOnly || busy !== null || detail.stale} onChange={(event) => updateDraft(decision, event.target.value)} /></label>
              <p>提交只记录人工判断，不会自动合并、删除、停用或改写任何设定。</p>
              <button ref={decisionTriggerRef} className="btn btn-primary" type="submit" disabled={readOnly || busy !== null || detail.stale || !decision || Boolean(storageError)}>{busy === "decide" ? "记录中…" : frozenInput && phase !== "draft" ? "使用相同请求安全重试" : "记录判断"}</button>
            </form>
            <LoreMergeWizard
              key={`${detail.id}-${detail.evidence_revision}`}
              projectId={projectId}
              userId={userId}
              detail={detail}
              loreTypes={loreTypes}
              enabled={mergeCommitEnabled && detail.merge_allowed}
              blockedReason={detail.merge_block_reason}
              readOnly={readOnly}
              onDirtyChange={setMergeDirty}
              onBusyChange={setMergeBusy}
              onMerged={(elementId, nextNotice) => {
                setMergeDirty(false);
                setNotice(nextNotice);
                onOverviewRefresh();
                onOpenElement(elementId, true);
              }}
            />
            <section className="lore-review-history"><h3>判断历史</h3>{detail.history.length === 0 ? <p>尚无人工判断记录。</p> : detail.history.map((event) => <article key={event.id}><strong>{STATUS_LABEL[event.previous_status]} → {STATUS_LABEL[event.new_status]}</strong><span>{new Date(event.created_at).toLocaleString()}</span>{event.note && <p>{event.note}</p>}{!event.applied && <span>状态未变化</span>}</article>)}</section>
          </>}
        </aside>
      </div>
      {confirmOpen && frozenInput && <div className="modal-overlay"><div ref={confirmRef} tabIndex={-1} className="modal-content lore-review-confirm" role="alertdialog" aria-modal="true" aria-labelledby="review-confirm-title" onKeyDown={handleDecisionConfirmationKeyDown}><h2 id="review-confirm-title">确认记录“{STATUS_LABEL[frozenInput.decision]}”</h2><p>这只会记录人工判断，不会自动合并、删除、停用或改写任何设定，两项设定及生成权限保持不变。</p><div className="modal-actions"><button ref={decisionCancelRef} className="btn btn-secondary" type="button" onClick={closeDecisionConfirmation}>取消</button><button className="btn btn-primary" type="button" onClick={submitDecision}>确认记录</button></div></div></div>}
    </section>
  );
}

type ManualPhase = "draft" | "maintenance" | "conflict" | "outcome_unknown";

interface StoredManualReviewDraft {
  version: 1;
  open: boolean;
  kind: LoreReviewKind;
  note: string;
  leftQuery: string;
  rightQuery: string;
  left: LoreElementListItem | null;
  right: LoreElementListItem | null;
  frozenInput: LoreManualReviewCreateInput | null;
  phase: ManualPhase;
}

function isManualEndpoint(value: unknown): value is LoreElementListItem {
  if (!value || typeof value !== "object") return false;
  const endpoint = value as Partial<LoreElementListItem>;
  const type = endpoint.type as LoreElementListItem["type"] | undefined;
  return typeof endpoint.id === "string"
    && endpoint.id.length > 0
    && typeof endpoint.name === "string"
    && Boolean(type && typeof type.key === "string" && typeof type.display_name === "string")
    && typeof endpoint.lifecycle_status === "string"
    && typeof endpoint.confirmation_status === "string"
    && typeof endpoint.enabled === "boolean"
    && typeof endpoint.lock_version === "number"
    && Number.isInteger(endpoint.lock_version)
    && endpoint.lock_version >= 1;
}

function isManualFrozenInput(value: unknown): value is LoreManualReviewCreateInput {
  if (!value || typeof value !== "object") return false;
  const input = value as Partial<LoreManualReviewCreateInput>;
  return typeof input.operation_key === "string"
    && input.operation_key.length >= 16
    && (input.kind === "possible_duplicate" || input.kind === "possible_conflict")
    && typeof input.left_element_id === "string"
    && input.left_element_id.length > 0
    && typeof input.right_element_id === "string"
    && input.right_element_id.length > 0
    && typeof input.left_expected_lock_version === "number"
    && Number.isInteger(input.left_expected_lock_version)
    && input.left_expected_lock_version >= 1
    && typeof input.right_expected_lock_version === "number"
    && Number.isInteger(input.right_expected_lock_version)
    && input.right_expected_lock_version >= 1
    && typeof input.note === "string";
}

function isStoredManualDraft(value: unknown): value is StoredManualReviewDraft {
  if (!value || typeof value !== "object") return false;
  const draft = value as Partial<StoredManualReviewDraft>;
  return draft.version === 1
    && typeof draft.open === "boolean"
    && (draft.kind === "possible_duplicate" || draft.kind === "possible_conflict")
    && typeof draft.note === "string"
    && typeof draft.leftQuery === "string"
    && typeof draft.rightQuery === "string"
    && (draft.left === null || isManualEndpoint(draft.left))
    && (draft.right === null || isManualEndpoint(draft.right))
    && (draft.frozenInput === null || isManualFrozenInput(draft.frozenInput))
    && ["draft", "maintenance", "conflict", "outcome_unknown"].includes(String(draft.phase))
    && (
      draft.frozenInput === null
      || Boolean(
        draft.left
        && draft.right
        && draft.frozenInput.left_element_id === draft.left.id
        && draft.frozenInput.right_element_id === draft.right.id
        && draft.frozenInput.left_expected_lock_version === draft.left.lock_version
        && draft.frozenInput.right_expected_lock_version === draft.right.lock_version
        && draft.frozenInput.kind === draft.kind
        && draft.frozenInput.note === draft.note.trim()
      )
    );
}

function ManualReviewForm({
  projectId,
  userId,
  readOnly,
  onDirtyChange,
  onBusyChange,
  onCreated,
}: {
  projectId: string;
  userId: string;
  readOnly: boolean;
  onDirtyChange: (dirty: boolean) => void;
  onBusyChange: (busy: boolean) => void;
  onCreated: (
    detail: LoreReviewDetail,
    created: boolean,
    replayed: boolean,
    openedExistingConflict?: boolean,
  ) => void;
}) {
  const [open, setOpen] = useState(false);
  const [kind, setKind] = useState<LoreReviewKind>("possible_conflict");
  const [note, setNote] = useState("");
  const [leftQuery, setLeftQuery] = useState("");
  const [rightQuery, setRightQuery] = useState("");
  const [leftResults, setLeftResults] = useState<LoreElementListItem[]>([]);
  const [rightResults, setRightResults] = useState<LoreElementListItem[]>([]);
  const [leftLoading, setLeftLoading] = useState(false);
  const [rightLoading, setRightLoading] = useState(false);
  const [left, setLeft] = useState<LoreElementListItem | null>(null);
  const [right, setRight] = useState<LoreElementListItem | null>(null);
  const [frozenInput, setFrozenInput] = useState<LoreManualReviewCreateInput | null>(null);
  const [phase, setPhase] = useState<ManualPhase>("draft");
  const [busy, setBusy] = useState(false);
  const [searchError, setSearchError] = useState("");
  const [submitError, setSubmitError] = useState("");
  const [submitErrorCode, setSubmitErrorCode] = useState("");
  const [storageError, setStorageError] = useState("");
  const [storageIssue, setStorageIssue] = useState<"corrupt" | "unavailable" | null>(null);
  const errorRef = useRef<HTMLDivElement | null>(null);
  const headingRef = useRef<HTMLHeadingElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const draftScope = useMemo<DraftScope>(() => ({
    userId,
    projectId,
    kind: "lore-manual-review",
    objectId: "new",
  }), [projectId, userId]);
  const dirty = open && Boolean(left || right || note.trim() || frozenInput);

  useEffect(() => onDirtyChange(dirty), [dirty, onDirtyChange]);
  useEffect(() => onBusyChange(busy), [busy, onBusyChange]);
  useEffect(() => () => {
    onDirtyChange(false);
    onBusyChange(false);
  }, [onBusyChange, onDirtyChange]);
  useEffect(() => {
    const loaded = loadDraft<unknown>(draftScope);
    if ((loaded.status === "available" || loaded.status === "expired") && isStoredManualDraft(loaded.draft.payload)) {
      const saved = loaded.draft.payload;
      setOpen(saved.open);
      setKind(saved.kind);
      setNote(saved.note);
      setLeftQuery(saved.leftQuery);
      setRightQuery(saved.rightQuery);
      setLeft(saved.left);
      setRight(saved.right);
      setFrozenInput(saved.frozenInput);
      setPhase(saved.phase);
    } else if (
      loaded.status === "corrupt"
      || loaded.status === "available"
      || loaded.status === "expired"
    ) {
      setStorageIssue("corrupt");
      setStorageError("人工线索草稿已损坏；为避免重复创建，清除前暂停提交。");
    } else if (loaded.status === "unavailable") {
      setStorageIssue("unavailable");
      setStorageError("人工线索草稿存储不可用；为避免重复创建，暂停提交。");
    }
  }, [draftScope]);

  function snapshot(overrides: Partial<StoredManualReviewDraft> = {}): StoredManualReviewDraft {
    return {
      version: 1,
      open: overrides.open ?? open,
      kind: overrides.kind ?? kind,
      note: overrides.note ?? note,
      leftQuery: overrides.leftQuery ?? leftQuery,
      rightQuery: overrides.rightQuery ?? rightQuery,
      left: Object.hasOwn(overrides, "left") ? overrides.left ?? null : left,
      right: Object.hasOwn(overrides, "right") ? overrides.right ?? null : right,
      frozenInput: Object.hasOwn(overrides, "frozenInput") ? overrides.frozenInput ?? null : frozenInput,
      phase: overrides.phase ?? phase,
    };
  }

  function persist(next: StoredManualReviewDraft): boolean {
    const result = saveDraft(draftScope, next, null);
    if (result.status === "unavailable") {
      setStorageIssue("unavailable");
      setStorageError("浏览器无法安全保存人工线索草稿，系统没有提交。");
      return false;
    }
    setStorageIssue(null);
    setStorageError("");
    return true;
  }

  useEffect(() => {
    if (open && !storageError) persist(snapshot());
    // Persist every user-visible draft change; snapshot uses the same state values.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [frozenInput, kind, left, leftQuery, note, open, phase, right, rightQuery]);

  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      setSearchError("");
      setLeftLoading(true);
      api.listLoreElements(projectId, {
        q: leftQuery.trim() || undefined,
        confirmation_status: "confirmed",
        limit: 20,
      }, controller.signal).then((response) => {
        setLeftResults(response.items.filter((item) => item.lifecycle_status !== "merged"));
      }).catch((error) => {
        if ((error as Error).name !== "AbortError") setSearchError(`左侧设定加载失败：${message(error)}`);
      }).finally(() => {
        if (!controller.signal.aborted) setLeftLoading(false);
      });
    }, 200);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [leftQuery, open, projectId]);

  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      setSearchError("");
      setRightLoading(true);
      api.listLoreElements(projectId, {
        q: rightQuery.trim() || undefined,
        confirmation_status: "confirmed",
        limit: 20,
      }, controller.signal).then((response) => {
        setRightResults(response.items.filter((item) => item.lifecycle_status !== "merged"));
      }).catch((error) => {
        if ((error as Error).name !== "AbortError") setSearchError(`右侧设定加载失败：${message(error)}`);
      }).finally(() => {
        if (!controller.signal.aborted) setRightLoading(false);
      });
    }, 200);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [open, projectId, rightQuery]);

  function close() {
    const warning = phase === "outcome_unknown"
      ? "原请求的结果仍不确定。放弃后重新创建可能形成重复线索，确定放弃吗？"
      : "确定放弃尚未提交的人工线索草稿吗？";
    if (dirty && !window.confirm(warning)) return;
    const cleared = clearDraft(draftScope);
    if (cleared.status === "unavailable") {
      setStorageError("无法安全清除本机草稿，已停止关闭。");
      setStorageIssue("unavailable");
      return;
    }
    setOpen(false);
    setLeft(null);
    setRight(null);
    setNote("");
    setFrozenInput(null);
    setPhase("draft");
    setSubmitError("");
    setSubmitErrorCode("");
    requestAnimationFrame(() => triggerRef.current?.focus());
  }

  function clearCorruptDraft() {
    if (!window.confirm("损坏草稿无法安全恢复。确定只清除这条本机人工线索草稿吗？")) return;
    const cleared = clearDraft(draftScope);
    if (cleared.status === "unavailable") {
      setStorageIssue("unavailable");
      setStorageError("本机草稿存储仍不可用，没有清除任何内容。");
      return;
    }
    setStorageIssue(null);
    setStorageError("");
    setOpen(false);
    setLeft(null);
    setRight(null);
    setFrozenInput(null);
    setPhase("draft");
    requestAnimationFrame(() => triggerRef.current?.focus());
  }

  async function reloadSelectedEndpoints() {
    if (!left || !right) return;
    setBusy(true);
    setSubmitError("");
    try {
      const [nextLeft, nextRight] = await Promise.all([
        api.getLoreElement(projectId, left.id),
        api.getLoreElement(projectId, right.id),
      ]);
      if (nextLeft.lifecycle_status === "merged" || nextRight.lifecycle_status === "merged") {
        setSubmitError("其中一项设定已合并，不能继续使用。请关闭草稿后重新选择。");
        return;
      }
      setLeft(nextLeft);
      setRight(nextRight);
      setFrozenInput(null);
      setPhase("draft");
      setSubmitErrorCode("");
      setSubmitError("已重新加载两项设定的当前版本。请核对后创建新请求。");
    } catch (error) {
      setSubmitError(`无法重新加载设定：${message(error)}`);
    } finally {
      setBusy(false);
      requestAnimationFrame(() => errorRef.current?.focus());
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!left || !right || !note.trim() || readOnly || storageError) return;
    if (left.id === right.id) {
      setSubmitError("请选择两项不同的正式设定。");
      return;
    }
    const input = frozenInput ?? {
      operation_key: operationKey().replace(/^review-/, "manual-review-"),
      kind,
      left_element_id: left.id,
      right_element_id: right.id,
      left_expected_lock_version: left.lock_version,
      right_expected_lock_version: right.lock_version,
      note: note.trim(),
    };
    if (!persist(snapshot({ frozenInput: input, phase: "draft" }))) return;
    setFrozenInput(input);
    setBusy(true);
    setSubmitError("");
    setSubmitErrorCode("");
    try {
      const response = await api.createManualLoreReview(projectId, input);
      const cleared = clearDraft(draftScope);
      if (cleared.status === "unavailable") {
        setStorageError("线索已记录，但本机草稿未能清除；请勿再次提交。");
      }
      setOpen(false);
      setLeft(null);
      setRight(null);
      setNote("");
      setFrozenInput(null);
      setPhase("draft");
      onCreated(response.suggestion, response.created, response.replayed);
    } catch (error) {
      if (error instanceof ApiError && error.status === 503) {
        setPhase("maintenance");
        setSubmitError("仓库正在维护，草稿和原请求已保留，系统没有自动重试。");
      } else if (error instanceof ApiError && error.status === 409) {
        if (error.code === "LORE_MANUAL_REVIEW_PAIR_CONFLICT" && error.suggestionId) {
          try {
            const existing = await api.getLoreReview(projectId, error.suggestionId);
            const cleared = clearDraft(draftScope);
            if (cleared.status === "unavailable") {
              setStorageIssue("unavailable");
              setStorageError("已有线索已找到，但本机草稿无法安全清除；请勿再次提交。");
              return;
            }
            setOpen(false);
            setLeft(null);
            setRight(null);
            setNote("");
            setFrozenInput(null);
            setPhase("draft");
            onCreated(existing, false, false, true);
            return;
          } catch (loadError) {
            setSubmitError(`已存在不同的人工线索，但暂时无法打开：${message(loadError)}`);
          }
        } else if (error.code === "LORE_MANUAL_REVIEW_CONFLICT" && error.retryable) {
          setPhase("outcome_unknown");
          setSubmitError("并发结果不确定。请使用相同请求安全重试。");
        } else {
          setPhase("conflict");
          setSubmitErrorCode(error.code || "LORE_MANUAL_REVIEW_CONFLICT");
          setSubmitError(`${error.detail} 原请求已冻结，系统不会自动换键重试。`);
        }
      } else {
        setPhase("outcome_unknown");
        setSubmitError("网络结果不确定。请使用相同请求安全重试，不要重新选择。");
      }
      requestAnimationFrame(() => errorRef.current?.focus());
    } finally {
      setBusy(false);
    }
  }

  if (!open) return <section className="lore-manual-review-entry">
    <div><h3>作者主动提报</h3><p>选择两项正式设定，记录可能重复或冲突的人工线索。</p></div>
    {storageError && <div className="lore-alert" role="alert">{storageError}{storageIssue === "corrupt" && <button type="button" onClick={clearCorruptDraft}>清除损坏草稿</button>}</div>}
    <button ref={triggerRef} className="btn btn-secondary" type="button" disabled={readOnly || Boolean(storageError)} onClick={() => { setOpen(true); requestAnimationFrame(() => headingRef.current?.focus()); }}>新建人工线索</button>
  </section>;

  const resultList = (side: "left" | "right", results: LoreElementListItem[], selected: LoreElementListItem | null, loading: boolean) => <div className="lore-manual-review-results" role="group" aria-label={`${side === "left" ? "左侧" : "右侧"}设定搜索结果`} aria-busy={loading}>
    {loading ? <p>正在加载正式设定…</p> : results.length === 0 ? <p>未找到可选的正式设定。</p> : results.map((item) => <button
      key={item.id}
      type="button"
      aria-pressed={selected?.id === item.id}
      className={selected?.id === item.id ? "selected" : ""}
      disabled={busy || Boolean(frozenInput)}
      onClick={() => {
        if (side === "left") setLeft(item); else setRight(item);
        setFrozenInput(null);
        setPhase("draft");
      }}
    ><strong>{item.name}</strong><span>{selected?.id === item.id ? "已选择 · " : ""}{item.type.display_name} · {item.lifecycle_status === "archived" ? "已归档" : "使用中"} · {item.enabled ? "已启用" : "已停用"} · 版本 {item.lock_version}</span></button>)}
  </div>;

  return <form className="lore-manual-review-form" onSubmit={submit} aria-busy={busy}>
    <div className="lore-manual-review-heading"><div><h3 ref={headingRef} tabIndex={-1}>新建人工线索</h3><p>人工线索与系统扫描分开标记，只用于复核，不会自动合并。</p></div><button className="btn btn-secondary" type="button" disabled={busy} onClick={close}>关闭</button></div>
    {(searchError || submitError || storageError) && <div ref={errorRef} tabIndex={-1} className="lore-alert" role="alert">{submitError || storageError || searchError}{frozenInput && (submitErrorCode === "LORE_MANUAL_REVIEW_ENDPOINT_STALE" || submitErrorCode === "LORE_MANUAL_REVIEW_ENDPOINT_MERGED") && <button type="button" disabled={busy} onClick={reloadSelectedEndpoints}>重新加载两项设定</button>}</div>}
    <div className="lore-manual-review-pickers">
      <div className="lore-manual-review-picker"><label htmlFor="manual-review-left-search">左侧设定</label><input id="manual-review-left-search" className="form-input" value={leftQuery} disabled={busy || Boolean(frozenInput)} onChange={(event) => setLeftQuery(event.target.value)} placeholder="搜索名称、摘要或字段" />{resultList("left", leftResults, left, leftLoading)}</div>
      <div className="lore-manual-review-picker"><label htmlFor="manual-review-right-search">右侧设定</label><input id="manual-review-right-search" className="form-input" value={rightQuery} disabled={busy || Boolean(frozenInput)} onChange={(event) => setRightQuery(event.target.value)} placeholder="搜索名称、摘要或字段" />{resultList("right", rightResults, right, rightLoading)}</div>
    </div>
    {left && right && left.type.key !== right.type.key && <div className="lore-note">这是跨类型线索：可记录和复核，但不能进入合并。</div>}
    <label><span>线索类型</span><select className="form-select" value={kind} disabled={busy || Boolean(frozenInput)} onChange={(event) => { setKind(event.target.value as LoreReviewKind); setFrozenInput(null); }}><option value="possible_conflict">可能冲突</option><option value="possible_duplicate">可能重复</option></select></label>
    <label><span>需要复核的具体说明（必填，最多 500 字）</span><textarea className="form-textarea" required maxLength={500} value={note} disabled={busy || Boolean(frozenInput)} onChange={(event) => { setNote(event.target.value); setFrozenInput(null); }} /></label>
    <div className="lore-manual-review-actions"><button className="btn btn-primary" type="submit" disabled={readOnly || busy || !left || !right || left.id === right.id || !note.trim() || Boolean(storageError) || (Boolean(frozenInput) && phase === "conflict")}>{busy ? "记录中…" : frozenInput ? "使用相同请求安全重试" : "记录人工线索"}</button><span>不会修改两项正式设定。</span></div>
  </form>;
}

function ReviewEndpointCard({ label, endpoint, onOpen }: {
  label: string;
  endpoint: LoreReviewDetail["left_snapshot"];
  onOpen: () => void;
}) {
  return <article className="lore-review-endpoint">
    <span className="lore-type">{label} · {endpoint.type.display_name}</span>
    <h3>{endpoint.name}</h3>
    <p>{endpoint.summary || "未提供摘要"}</p>
    <span>内容版本 {endpoint.content_version} · {endpoint.lifecycle_status === "archived" ? "已归档" : "使用中"} · {endpoint.enabled ? "允许用于生成" : "暂停用于生成"}</span>
    <details><summary>查看原始来源</summary>{endpoint.sources.length === 0 ? <p>未记录原始来源。</p> : endpoint.sources.map((source) => <article key={source.id || `${source.kind}-${source.created_at}`}><strong>{source.label}</strong>{source.reference && <span>引用：{source.reference}</span>}<p>{source.excerpt || "未提供原文摘录"}</p></article>)}</details>
    <button className="btn btn-secondary" type="button" onClick={onOpen}>查看这项正式设定</button>
  </article>;
}
