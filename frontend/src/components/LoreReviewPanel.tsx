import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { ApiError, api } from "@/services/api";
import {
  clearDraft,
  loadDraft,
  saveDraft,
  type DraftScope,
} from "@/services/maintenanceDrafts";
import type {
  LoreReviewDecisionInput,
  LoreReviewDetail,
  LoreReviewKind,
  LoreReviewListItem,
  LoreReviewStatus,
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
  onDirtyChange,
  onBusyChange,
  onOpenElement,
  onOverviewRefresh,
}: {
  projectId: string;
  userId: string;
  readOnly: boolean;
  onDirtyChange: (dirty: boolean) => void;
  onBusyChange: (busy: boolean) => void;
  onOpenElement: (elementId: string) => void;
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
  const [reloadToken, setReloadToken] = useState(0);
  const listSequence = useRef(0);
  const detailSequence = useRef(0);
  const headingRef = useRef<HTMLHeadingElement | null>(null);
  const detailHeadingRef = useRef<HTMLHeadingElement | null>(null);
  const errorRef = useRef<HTMLDivElement | null>(null);
  const confirmRef = useRef<HTMLDivElement | null>(null);
  const cardRefs = useRef(new Map<string, HTMLButtonElement>());

  const draftScope = useMemo<DraftScope | null>(() => selectedId ? ({
    userId,
    projectId,
    kind: "lore-suggestion-review",
    objectId: selectedId,
  }) : null, [projectId, selectedId, userId]);
  const dirty = decision !== "" || note.trim() !== "" || frozenInput !== null;

  useEffect(() => onDirtyChange(dirty), [dirty, onDirtyChange]);
  useEffect(() => onBusyChange(busy !== null), [busy, onBusyChange]);
  useEffect(() => () => {
    onDirtyChange(false);
    onBusyChange(false);
  }, [onBusyChange, onDirtyChange]);
  useEffect(() => {
    if (detailError || storageError) requestAnimationFrame(() => errorRef.current?.focus());
  }, [detailError, storageError]);
  useEffect(() => {
    if (confirmOpen) requestAnimationFrame(() => confirmRef.current?.focus());
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
    } else if (loaded.status === "corrupt") {
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
    if (id !== selectedId && dirty && !window.confirm("确定放弃当前尚未提交的判断草稿吗？")) return;
    if (id !== selectedId && dirty && !discardDraft()) return;
    setNotice("");
    setSelectedId(id);
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
      setReloadToken((value) => value + 1);
      if (status === "needs_review") {
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
          <p>系统只提供待核对线索，不会自动认定、合并或改写设定。</p>
        </div>
        <button className="btn btn-primary" type="button" disabled={readOnly || busy !== null} onClick={scan}>
          {busy === "scan" ? "扫描中…" : "扫描正式设定"}
        </button>
      </div>
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
          <div className="lore-list-heading"><h3>系统线索</h3><span aria-live="polite">{loading ? "加载中…" : `共 ${total} 项`}</span></div>
          {!loading && !listError && total === 0 && <div className="lore-empty"><strong>当前没有待核对的系统线索</strong><span>这不表示仓库一定没有重复或冲突；可扫描正式设定，或查看已判断线索。</span></div>}
          {items.map((item) => <button
            key={item.id}
            ref={(node) => { if (node) cardRefs.current.set(item.id, node); else cardRefs.current.delete(item.id); }}
            type="button"
            className={`lore-card ${selectedId === item.id ? "selected" : ""}`}
            aria-current={selectedId === item.id ? "true" : undefined}
            disabled={busy !== null}
            onClick={() => selectItem(item.id)}
          >
            <span className="lore-card-top"><span className="lore-type">系统线索 · {KIND_LABEL[item.kind]}</span>{item.stale && <span className="lore-badge lore-badge--warning">依据已变化</span>}</span>
            <strong>{item.left.name} ↔ {item.right.name}</strong>
            <span className="lore-summary">{item.primary_reason}</span>
            <span className="lore-meta">{STATUS_LABEL[item.review_status]} · {item.left.type.display_name} · 证据版本 {item.evidence_revision}</span>
          </button>)}
          {cursor && <button className="btn btn-secondary lore-load-more" type="button" disabled={loadingMore} onClick={loadMore}>{loadingMore ? "加载中…" : "加载更多"}</button>}
        </section>
        <aside className="lore-detail lore-review-detail" aria-label="线索详情">
          {selectedId && <button className="btn btn-secondary lore-detail-back" type="button" onClick={() => { if (!dirty || window.confirm("确定放弃当前判断草稿吗？")) { if (!dirty || discardDraft()) setSelectedId(null); } }}>← 返回线索列表</button>}
          {!selectedId && <div className="lore-empty"><strong>选择一条系统线索</strong><span>可对比双方版本、相关字段和原始来源。</span></div>}
          {detailLoading && <div className="lore-empty">线索详情加载中…</div>}
          {(detailError || storageError) && <div ref={errorRef} tabIndex={-1} className="lore-alert" role="alert">{detailError || storageError}{(phase === "outcome_unknown" || phase === "conflict" || phase === "stale") && <button type="button" disabled={busy !== null} onClick={checkLatest}>{busy === "check" ? "核对中…" : "核对最新状态"}</button>}</div>}
          {detail && <>
            <header className="lore-review-detail-header">
              <h2 ref={detailHeadingRef} tabIndex={-1}>核对这条设定线索</h2>
              <p>系统发现的是可能性，以下内容尚未被人工确认。</p>
              <span className="lore-badge">{KIND_LABEL[detail.kind]}</span>
              <strong>{STATUS_LABEL[detail.review_status]}</strong>
            </header>
            {detail.stale && <div className="lore-alert" role="alert">对比依据已变化，请重新扫描后再判断。</div>}
            <section className="lore-review-comparison" aria-label="两项设定对比">
              <ReviewEndpointCard label="左侧设定" endpoint={detail.left_snapshot} onOpen={() => onOpenElement(detail.left.id)} />
              <div className="lore-review-evidence"><h3>系统发现的线索</h3><p>{detail.primary_reason}</p>{detail.evidence.length === 0 ? <p>双方名称与类型相同，未发现可安全判定的字段差异。</p> : detail.evidence.map((evidence) => <article key={evidence.field_key}><strong>{evidence.label}：内容不同</strong><span>左：{evidence.left_value || "一侧为空"}</span><span>右：{evidence.right_value || "一侧为空"}</span></article>)}<p className="lore-note">内容不同也可能是补充、时间变化或同名对象，并不自动代表事实矛盾。</p></div>
              <ReviewEndpointCard label="右侧设定" endpoint={detail.right_snapshot} onOpen={() => onOpenElement(detail.right.id)} />
            </section>
            <form className="lore-review-decision" onSubmit={requestDecision}>
              <h3>记录人工判断</h3>
              <label><span>判断</span><select className="form-select" value={decision} disabled={readOnly || busy !== null || detail.stale} onChange={(event) => updateDraft(event.target.value as Decision | "", note)}><option value="">请选择</option>{DECISIONS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
              <label><span>备注（可选，最多 500 字）</span><textarea className="form-textarea" maxLength={500} value={note} disabled={readOnly || busy !== null || detail.stale} onChange={(event) => updateDraft(decision, event.target.value)} /></label>
              <p>提交只记录人工判断，不会自动合并、删除、停用或改写任何设定。</p>
              <button className="btn btn-primary" type="submit" disabled={readOnly || busy !== null || detail.stale || !decision || Boolean(storageError)}>{busy === "decide" ? "记录中…" : frozenInput ? "使用相同请求安全重试" : "记录判断"}</button>
            </form>
            <section className="lore-review-history"><h3>判断历史</h3>{detail.history.length === 0 ? <p>尚无人工判断记录。</p> : detail.history.map((event) => <article key={event.id}><strong>{STATUS_LABEL[event.previous_status]} → {STATUS_LABEL[event.new_status]}</strong><span>{new Date(event.created_at).toLocaleString()}</span>{event.note && <p>{event.note}</p>}{!event.applied && <span>状态未变化</span>}</article>)}</section>
          </>}
        </aside>
      </div>
      {confirmOpen && frozenInput && <div className="modal-overlay"><div ref={confirmRef} tabIndex={-1} className="modal-content lore-review-confirm" role="alertdialog" aria-modal="true" aria-labelledby="review-confirm-title"><h2 id="review-confirm-title">确认记录“{STATUS_LABEL[frozenInput.decision]}”</h2><p>这只会记录人工判断，不会自动合并、删除、停用或改写任何设定，两项设定及生成权限保持不变。</p><div className="modal-actions"><button className="btn btn-secondary" type="button" onClick={() => setConfirmOpen(false)}>取消</button><button className="btn btn-primary" type="button" onClick={submitDecision}>确认记录</button></div></div></div>}
    </section>
  );
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
