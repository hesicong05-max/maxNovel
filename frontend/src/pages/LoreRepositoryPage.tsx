import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { ApiError, api } from "@/services/api";
import type {
  LoreCandidate,
  LoreElementDetail,
  LoreElementListItem,
  LoreListResponse,
  LoreOverview,
} from "@/types/lore";

type Scope = "formal" | "review";

const CANDIDATE_STATUS: Record<string, string> = {
  pending_review: "待审核",
  accepted: "已接纳",
  rejected: "已拒绝",
  failed: "处理失败",
};

const DISABLED_REASON: Record<string, string> = {
  name_missing: "缺少名称",
  type_missing: "类型待确认",
  type_invalid: "暂不支持的类型",
  fields_need_confirmation: "部分字段需要确认",
  candidate_not_pending: "当前状态不允许接纳",
  suggestions_unresolved: "重复或冲突建议尚未处理",
  lore_mode_not_relational: "项目尚未完成模块化存储升级",
};

const FIELD_STATE: Record<string, string> = {
  provided: "原文已提供",
  unknown: "原文未提供",
  needs_confirmation: "待确认",
};

const SOURCE_KIND: Record<string, string> = {
  manual: "手动创建",
  manual_review: "人工复核",
  document_import: "文档导入",
  system_extract: "AI 提取",
  migration: "旧数据迁移",
  legacy_import: "旧数据导入",
};

const TYPE_OPTIONS = [
  ["world", "世界观"], ["character", "角色"], ["location", "地点"],
  ["scene", "场景"], ["faction", "阵营"], ["item", "物品"],
  ["conflict", "冲突"], ["event", "事件"], ["foreshadow", "伏笔"],
  ["rule", "规则与限制"], ["ability_system", "能力体系"], ["race", "种族"],
  ["historical_event", "历史事件"], ["social_institution", "社会制度"], ["other", "其他"],
] as const;

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 503) return "设定仓库正在维护，请稍后重试。";
    if (error.reloadRequired) return `${error.detail}请重新加载最新数据。`;
    return error.detail;
  }
  return error instanceof Error ? error.message : "加载失败，请稍后重试。";
}

function uniqueById<T extends { id: string }>(items: T[]): T[] {
  return Array.from(new Map(items.map((item) => [item.id, item])).values());
}

function valueText(value: unknown): string {
  if (value === null || value === undefined || value === "") return "未提供";
  if (typeof value === "string") return value;
  if (typeof value === "boolean") return value ? "是" : "否";
  if (Array.isArray(value)) return value.length ? value.map(valueText).join("、") : "未提供";
  return JSON.stringify(value, null, 2);
}

function candidateTypeLabel(typeKey: string | null, displayName: string | null): string {
  const known = TYPE_OPTIONS.find(([key]) => key === typeKey);
  return known?.[1] || displayName || "类型待确认";
}

export default function LoreRepositoryPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const queryKey = searchParams.toString();
  const scope: Scope = searchParams.get("scope") === "review" ? "review" : "formal";
  const [searchDraft, setSearchDraft] = useState(searchParams.get("q") ?? "");
  const [overview, setOverview] = useState<LoreOverview | null>(null);
  const [overviewError, setOverviewError] = useState("");
  const [formal, setFormal] = useState<LoreListResponse | null>(null);
  const [formalFacets, setFormalFacets] = useState<LoreListResponse["facets"] | null>(null);
  const [candidates, setCandidates] = useState<LoreCandidate[]>([]);
  const [candidateTotal, setCandidateTotal] = useState(0);
  const [candidateCursor, setCandidateCursor] = useState<string | null>(null);
  const [candidateHasMore, setCandidateHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [listError, setListError] = useState("");
  const [selectedFormalId, setSelectedFormalId] = useState<string | null>(null);
  const [selectedCandidateId, setSelectedCandidateId] = useState<string | null>(null);
  const [detail, setDetail] = useState<LoreElementDetail | null>(null);
  const [detailError, setDetailError] = useState("");
  const [detailLoading, setDetailLoading] = useState(false);
  const [reloadToken, setReloadToken] = useState(0);
  const [detailReloadToken, setDetailReloadToken] = useState(0);
  const [recoveryNotice, setRecoveryNotice] = useState("");
  const requestSequence = useRef(0);
  const overviewSequence = useRef(0);
  const detailSequence = useRef(0);
  const loadMoreController = useRef<AbortController | null>(null);
  const detailRef = useRef<HTMLElement | null>(null);
  const contextKey = `${id ?? ""}?${queryKey}`;
  const contextKeyRef = useRef(contextKey);
  contextKeyRef.current = contextKey;

  const selectedCandidate = useMemo(
    () => candidates.find((item) => item.id === selectedCandidateId) ?? null,
    [candidates, selectedCandidateId]
  );

  function updateQuery(changes: Record<string, string | null>) {
    setRecoveryNotice("");
    clearSelection();
    const next = new URLSearchParams(searchParams);
    Object.entries(changes).forEach(([key, value]) => {
      if (value === null || value === "") next.delete(key);
      else next.set(key, value);
    });
    setSearchParams(next);
  }

  useEffect(() => {
    setSearchDraft(searchParams.get("q") ?? "");
  }, [queryKey]);

  useEffect(() => {
    setFormalFacets(null);
  }, [id]);

  useEffect(() => {
    if (!id) return;
    const controller = new AbortController();
    const sequence = ++overviewSequence.current;
    setOverview(null);
    setOverviewError("");
    api.getLoreOverview(id, controller.signal).then((data) => {
      if (sequence === overviewSequence.current) setOverview(data);
    }).catch((error) => {
      if ((error as Error).name !== "AbortError" && sequence === overviewSequence.current) {
        setOverviewError(errorMessage(error));
      }
    });
    return () => controller.abort();
  }, [id, reloadToken]);

  useEffect(() => {
    if (!id) return;
    const controller = new AbortController();
    const sequence = ++requestSequence.current;
    loadMoreController.current?.abort();
    loadMoreController.current = null;
    setLoadingMore(false);
    setLoading(true);
    setListError("");
    setFormal(null);
    setCandidates([]);
    setCandidateTotal(0);
    setCandidateCursor(null);
    setCandidateHasMore(false);
    clearSelection();

    const q = searchParams.get("q") || undefined;
    const request = scope === "formal"
      ? api.listLoreElements(id, {
          q,
          type: searchParams.get("type") || undefined,
          confirmation_status: searchParams.get("confirmation") || undefined,
          source_kind: searchParams.get("source") || undefined,
          lifecycle_status: searchParams.get("lifecycle") || undefined,
          enabled: searchParams.has("enabled")
            ? searchParams.get("enabled") === "true"
            : undefined,
          has_relation: searchParams.has("has_relation")
            ? searchParams.get("has_relation") === "true"
            : undefined,
          limit: 20,
        }, controller.signal)
      : api.listLoreCandidates(id, {
          q,
          type: searchParams.get("type") || undefined,
          needs_attention: searchParams.has("needs_attention")
            ? searchParams.get("needs_attention") === "true"
            : undefined,
          limit: 20,
        }, controller.signal);

    request.then((data) => {
      if (sequence !== requestSequence.current) return;
      if (scope === "formal" && "facets" in data) {
        setFormal(data);
        setFormalFacets(data.facets);
      } else if (scope === "review" && "query_signature" in data) {
        setCandidates(data.items);
        setCandidateTotal(data.total);
        setCandidateCursor(data.next_cursor);
        setCandidateHasMore(data.has_more);
      }
    }).catch((error) => {
      if ((error as Error).name !== "AbortError" && sequence === requestSequence.current) {
        setListError(errorMessage(error));
      }
    }).finally(() => {
      if (sequence === requestSequence.current) setLoading(false);
    });
    return () => controller.abort();
  }, [id, queryKey, reloadToken, scope, searchParams]);

  useEffect(() => {
    const sequence = ++detailSequence.current;
    if (!id || !selectedFormalId) {
      setDetail(null);
      setDetailError("");
      setDetailLoading(false);
      return;
    }
    const controller = new AbortController();
    setDetail(null);
    setDetailError("");
    setDetailLoading(true);
    api.getLoreElement(id, selectedFormalId, controller.signal)
      .then((data) => {
        if (sequence === detailSequence.current) setDetail(data);
      })
      .catch((error) => {
        if ((error as Error).name !== "AbortError" && sequence === detailSequence.current) {
          setDetailError(errorMessage(error));
        }
      })
      .finally(() => {
        if (sequence === detailSequence.current) setDetailLoading(false);
      });
    return () => controller.abort();
  }, [detailReloadToken, id, selectedFormalId]);

  useEffect(() => {
    if (!selectedFormalId && !selectedCandidateId) return;
    if (!window.matchMedia("(max-width: 480px)").matches) return;
    requestAnimationFrame(() => detailRef.current?.focus());
  }, [selectedCandidateId, selectedFormalId]);

  function submitSearch(event: FormEvent) {
    event.preventDefault();
    updateQuery({ q: searchDraft.trim() || null });
  }

  function changeScope(nextScope: Scope, extra: Record<string, string> = {}) {
    setRecoveryNotice("");
    clearSelection();
    const next = new URLSearchParams({ scope: nextScope, ...extra });
    setSearchDraft("");
    setSearchParams(next);
  }

  async function loadMore() {
    if (!id || loadingMore) return;
    const cursor = scope === "formal" ? formal?.next_cursor : candidateCursor;
    if (!cursor) return;
    const controller = new AbortController();
    loadMoreController.current?.abort();
    loadMoreController.current = controller;
    const requestedContext = contextKeyRef.current;
    const requestedGeneration = requestSequence.current;
    setLoadingMore(true);
    setListError("");
    try {
      const q = searchParams.get("q") || undefined;
      if (scope === "formal") {
        const data = await api.listLoreElements(id, {
          q,
          type: searchParams.get("type") || undefined,
          confirmation_status: searchParams.get("confirmation") || undefined,
          source_kind: searchParams.get("source") || undefined,
          lifecycle_status: searchParams.get("lifecycle") || undefined,
          enabled: searchParams.has("enabled") ? searchParams.get("enabled") === "true" : undefined,
          has_relation: searchParams.has("has_relation") ? searchParams.get("has_relation") === "true" : undefined,
          cursor,
          limit: 20,
        }, controller.signal);
        if (
          requestedContext !== contextKeyRef.current ||
          requestedGeneration !== requestSequence.current
        ) return;
        setFormal((current) => current ? {
          ...data,
          items: uniqueById([...current.items, ...data.items]),
          facets: current.facets,
        } : data);
      } else {
        const data = await api.listLoreCandidates(id, {
          q,
          type: searchParams.get("type") || undefined,
          needs_attention: searchParams.has("needs_attention")
            ? searchParams.get("needs_attention") === "true"
            : undefined,
          cursor,
          limit: 20,
        }, controller.signal);
        if (
          requestedContext !== contextKeyRef.current ||
          requestedGeneration !== requestSequence.current
        ) return;
        setCandidates((current) => uniqueById([...current, ...data.items]));
        setCandidateCursor(data.next_cursor);
        setCandidateHasMore(data.has_more);
      }
    } catch (error) {
      if ((error as Error).name === "AbortError") return;
      if (
        requestedContext !== contextKeyRef.current ||
        requestedGeneration !== requestSequence.current
      ) return;
      if (error instanceof ApiError && (error.status === 400 || error.status === 409)) {
        setRecoveryNotice("列表已更新，已从第一页重新加载。");
        setReloadToken((value) => value + 1);
      } else {
        setListError(errorMessage(error));
      }
    } finally {
      if (loadMoreController.current === controller) {
        loadMoreController.current = null;
        setLoadingMore(false);
      }
    }
  }

  function clearSelection() {
    detailSequence.current += 1;
    setSelectedFormalId(null);
    setSelectedCandidateId(null);
    setDetail(null);
    setDetailError("");
    setDetailLoading(false);
  }

  if (!id) return <div className="empty-state">项目地址无效</div>;

  const formalItems = formal?.items ?? [];
  const hasMore = scope === "formal" ? formal?.has_more : candidateHasMore;
  const total = scope === "formal" ? formal?.total ?? 0 : candidateTotal;
  const activeFilters = Array.from(searchParams.keys()).some((key) => key !== "scope");

  return (
    <div className="lore-page">
      <button className="btn-back" onClick={() => navigate(`/project/${id}`)}>← 返回创作项目</button>
      <header className="page-header lore-header">
        <div>
          <h1>世界观设定仓库</h1>
          <p>集中查看正式设定和待审核提取结果。当前页面只读，不会修改已有世界观。</p>
        </div>
        {overview?.migration_status.read_only && (
          <span className="lore-badge lore-badge--muted">
            {overview.migration_status.storage_mode === "legacy" ? "兼容资料 · 只读" : "当前仓库 · 只读"}
          </span>
        )}
      </header>

      {overviewError && (
        <div className="lore-alert" role="alert">
          {overviewError}<button type="button" onClick={() => setReloadToken((value) => value + 1)}>重试</button>
        </div>
      )}

      <section className="lore-overview" aria-label="设定概况">
        <button type="button" onClick={() => changeScope("formal")}>
          <strong>{overview?.formal_total ?? "—"}</strong><span>正式设定</span>
        </button>
        <button type="button" onClick={() => changeScope("review")}>
          <strong>{overview?.pending_review ?? "—"}</strong><span>待审核提取</span>
        </button>
        <button type="button" onClick={() => changeScope("review", { needs_attention: "true" })}>
          <strong>{overview?.needs_attention ?? "—"}</strong><span>需要关注</span>
        </button>
        <button type="button" onClick={() => changeScope("formal", { enabled: "false", lifecycle: "active" })}>
          <strong>{overview?.disabled ?? "—"}</strong><span>已停用</span>
        </button>
        <button type="button" onClick={() => changeScope("formal", { lifecycle: "archived" })}>
          <strong>{overview?.archived ?? "—"}</strong><span>已归档</span>
        </button>
      </section>

      {overview && !overview.capabilities.candidate_accept && (
        <div className="lore-note">提取结果目前支持审阅和追溯；正式接纳功能将在后续安全写入阶段开放。</div>
      )}

      <nav className="lore-scope-tabs" aria-label="设定范围">
        <button type="button" aria-current={scope === "formal" ? "page" : undefined} className={scope === "formal" ? "active" : ""} onClick={() => changeScope("formal")}>正式设定</button>
        <button type="button" aria-current={scope === "review" ? "page" : undefined} className={scope === "review" ? "active" : ""} onClick={() => changeScope("review")}>待审核提取</button>
      </nav>

      <form className="lore-filters" onSubmit={submitSearch} aria-label="筛选设定">
        <label className="lore-search">
          <span>搜索名称或摘要</span>
          <input className="form-input" value={searchDraft} onChange={(event) => setSearchDraft(event.target.value)} placeholder="输入关键词" />
        </label>
        {scope === "formal" ? (
          <>
            <label><span>类型</span><select className="form-select" value={searchParams.get("type") ?? ""} onChange={(event) => updateQuery({ type: event.target.value || null })}><option value="">全部类型</option>{formalFacets?.types.map((facet) => <option key={facet.key} value={facet.key}>{facet.label}（{facet.count}）</option>)}</select></label>
            <label><span>确认状态</span><select className="form-select" value={searchParams.get("confirmation") ?? ""} onChange={(event) => updateQuery({ confirmation: event.target.value || null })}><option value="">全部状态</option>{formalFacets?.confirmation_statuses.map((facet) => <option key={facet.key} value={facet.key}>{facet.label}（{facet.count}）</option>)}</select></label>
            <label><span>原始来源</span><select className="form-select" value={searchParams.get("source") ?? ""} onChange={(event) => updateQuery({ source: event.target.value || null })}><option value="">全部来源</option>{formalFacets?.sources.map((facet) => <option key={facet.key} value={facet.key}>{facet.label}（{facet.count}）</option>)}</select></label>
            <label><span>状态</span><select className="form-select" value={searchParams.get("lifecycle") ?? ""} onChange={(event) => updateQuery({ lifecycle: event.target.value || null })}><option value="">全部状态</option>{formalFacets?.lifecycle_statuses.map((facet) => <option key={facet.key} value={facet.key}>{facet.label}（{facet.count}）</option>)}</select></label>
            <label><span>启用情况</span><select className="form-select" value={searchParams.get("enabled") ?? ""} onChange={(event) => updateQuery({ enabled: event.target.value || null })}><option value="">全部</option><option value="true">已启用</option><option value="false">已停用</option></select></label>
            <label><span>关联情况</span><select className="form-select" value={searchParams.get("has_relation") ?? ""} onChange={(event) => updateQuery({ has_relation: event.target.value || null })}><option value="">全部</option><option value="true">存在关联</option><option value="false">暂无关联</option></select></label>
          </>
        ) : (
          <>
            <label><span>类型</span><select className="form-select" value={searchParams.get("type") ?? ""} onChange={(event) => updateQuery({ type: event.target.value || null })}><option value="">全部类型</option>{TYPE_OPTIONS.map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
            <div className="lore-fixed-filter"><span>审核状态</span><strong>仅显示待审核候选</strong></div>
            <label><span>关注标记</span><select className="form-select" value={searchParams.get("needs_attention") ?? ""} onChange={(event) => updateQuery({ needs_attention: event.target.value || null })}><option value="">全部</option><option value="true">需要关注</option><option value="false">无需关注</option></select></label>
          </>
        )}
        <div className="lore-filter-actions">
          <button className="btn btn-primary" type="submit">搜索</button>
          {activeFilters && <button className="btn btn-secondary" type="button" onClick={() => changeScope(scope)}>清除筛选</button>}
        </div>
      </form>

      {listError && <div className="lore-alert" role="alert">{listError}<button type="button" onClick={() => setReloadToken((value) => value + 1)}>重试</button></div>}
      {recoveryNotice && <div className="lore-note" role="status">{recoveryNotice}</div>}

      <div className={`lore-workspace ${selectedFormalId || selectedCandidateId ? "has-selection" : ""}`}>
        <section className="lore-list" aria-busy={loading} aria-label={scope === "formal" ? "正式设定列表" : "待审核提取列表"}>
          <div className="lore-list-heading"><h2>{scope === "formal" ? "正式设定" : "待审核提取"}</h2><span aria-live="polite">{loading ? "加载中…" : `共 ${total} 项`}</span></div>
          {!loading && !listError && total === 0 && (
            <div className="lore-empty"><strong>{activeFilters ? "没有匹配的设定" : "这里还没有内容"}</strong><span>{activeFilters ? "可清除筛选后重新查看。" : scope === "formal" ? "完成提取审核后，正式设定会显示在这里。" : "导入世界观文本后，提取结果会显示在这里。"}</span></div>
          )}
          {scope === "formal" && formalItems.map((item) => (
            <FormalCard key={item.id} item={item} selected={selectedFormalId === item.id} onSelect={() => setSelectedFormalId(item.id)} />
          ))}
          {scope === "review" && candidates.map((item) => (
            <CandidateCard key={item.id} item={item} selected={selectedCandidateId === item.id} onSelect={() => setSelectedCandidateId(item.id)} />
          ))}
          {hasMore && <button className="btn btn-secondary lore-load-more" type="button" disabled={loadingMore} onClick={loadMore}>{loadingMore ? "加载中…" : "加载更多"}</button>}
        </section>

        <aside className="lore-detail" aria-label="设定详情" tabIndex={-1} ref={detailRef}>
          {(selectedFormalId || selectedCandidate) && <button className="btn btn-secondary lore-detail-back" type="button" onClick={clearSelection}>← 返回设定列表</button>}
          {!selectedFormalId && !selectedCandidate && <div className="lore-empty"><strong>选择一项查看详情</strong><span>可核对字段、关联数量和原始文本出处。</span></div>}
          {detailLoading && <div className="lore-empty">详情加载中…</div>}
          {detailError && <div className="lore-alert" role="alert">{detailError}<button type="button" onClick={() => setDetailReloadToken((value) => value + 1)}>重试</button></div>}
          {detail && <FormalDetail detail={detail} />}
          {selectedCandidate && <CandidateDetail candidate={selectedCandidate} />}
        </aside>
      </div>
    </div>
  );
}

function FormalCard({ item, selected, onSelect }: { item: LoreElementListItem; selected: boolean; onSelect: () => void }) {
  return (
    <button type="button" className={`lore-card ${selected ? "selected" : ""}`} onClick={onSelect} aria-pressed={selected}>
      <span className="lore-card-top"><span className="lore-type">{item.type.display_name}</span><span className={`lore-badge ${item.enabled ? "" : "lore-badge--muted"}`}>{item.enabled ? "已启用" : "已停用"}</span></span>
      <strong>{item.name}</strong>
      <span className="lore-summary">{item.summary || "暂无摘要"}</span>
      <span className="lore-meta">{item.source_summary || "来源待补充"} · {item.relation_count} 项关联 · 版本 {item.current_version}</span>
    </button>
  );
}

function CandidateCard({ item, selected, onSelect }: { item: LoreCandidate; selected: boolean; onSelect: () => void }) {
  return (
    <button type="button" className={`lore-card ${selected ? "selected" : ""}`} onClick={onSelect} aria-pressed={selected}>
      <span className="lore-card-top"><span className="lore-type">{candidateTypeLabel(item.type_key, item.type_display_name)}</span>{item.needs_attention && <span className="lore-badge lore-badge--warning">需要关注</span>}</span>
      <strong>{item.name || "名称待确认"}</strong>
      <span className="lore-summary">{item.summary || "原文未提供摘要"}</span>
      <span className="lore-meta">{CANDIDATE_STATUS[item.status] || "状态待确认"} · {item.evidence.length} 条原文证据 · 修订 {item.revision}</span>
    </button>
  );
}

function FormalDetail({ detail }: { detail: LoreElementDetail }) {
  const definitions = [...detail.field_definitions]
    .sort((left, right) => left.order - right.order);
  return (
    <div>
      <div className="lore-detail-heading"><span className="lore-type">{detail.type.display_name}</span><h2>{detail.name}</h2><p>{detail.summary || "暂无摘要"}</p></div>
      <dl className="lore-fields">{definitions.map((definition) => <div key={definition.key}><dt>{definition.label}<span>{FIELD_STATE[detail.field_states[definition.key]] || "状态待确认"}</span></dt><dd>{valueText(detail.payload[definition.key])}</dd></div>)}</dl>
      <section className="lore-sources"><h3>原始出处</h3>{detail.sources.length ? detail.sources.map((source, index) => <article key={source.id ?? `${source.kind}-${index}`}><strong>{SOURCE_KIND[source.kind] || "其他来源"}{source.is_primary ? " · 主要来源" : ""}</strong><p>{source.excerpt || "暂无可展示的原文摘录"}</p>{source.reference && <small>{source.reference}</small>}</article>) : <p>暂无来源记录</p>}</section>
    </div>
  );
}

function CandidateDetail({ candidate }: { candidate: LoreCandidate }) {
  return (
    <div>
      <div className="lore-detail-heading"><span className="lore-type">{candidateTypeLabel(candidate.type_key, candidate.type_display_name)}</span><h2>{candidate.name || "名称待确认"}</h2><p>{candidate.summary || "原文未提供摘要"}</p></div>
      {candidate.disabled_reasons.length > 0 && <section className="lore-reasons"><h3>需要确认的问题</h3><ul>{candidate.disabled_reasons.map((reason) => <li key={reason}>{DISABLED_REASON[reason] || "其他待确认问题"}</li>)}</ul></section>}
      <section className="lore-sources"><h3>原文证据</h3>{candidate.evidence.length ? candidate.evidence.map((evidence) => <article key={evidence.id}><strong>{evidence.label || "设定字段"} · {FIELD_STATE[evidence.current_state] || "状态待确认"}</strong><p>{evidence.excerpt || evidence.current_value || "原文没有提供可确认内容"}</p></article>) : <p>暂无原文证据，不能作为正式设定接纳。</p>}</section>
    </div>
  );
}
