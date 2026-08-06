import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { ApiError, api } from "@/services/api";
import type {
  LoreCandidate,
  LoreCandidateActionResponse,
  LoreCandidateEditInput,
  LoreElementDetail,
  LoreElementListItem,
  LoreElementUpdateInput,
  LoreFieldDefinition,
  LoreFieldState,
  LoreListResponse,
  LoreOverview,
  LoreSuggestionResolution,
  LoreTypeDefinition,
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
  legacy_type_uneditable: "兼容资料模式下无法安全修正缺失或无效类型；可拒绝此候选，或等待项目升级后再编辑",
};

const SUGGESTION_KIND: Record<string, string> = {
  possible_duplicate: "可能与已有设定重复",
  possible_conflict: "可能与已有设定冲突",
};

const SUGGESTION_RESOLUTION: Array<[LoreSuggestionResolution, string]> = [
  ["deferred", "稍后处理"],
  ["accept_as_new", "仍作为新设定接纳"],
  ["dismissed", "忽略这条提示"],
];

const FIELD_STATE: Record<string, string> = {
  provided: "已确认有内容",
  unknown: "信息为空",
  needs_confirmation: "待确认",
};

const VALUE_ORIGIN: Record<string, string> = {
  ai_extraction: "原文提取",
  user_override: "用户已补充",
  user_cleared: "用户已清空",
};

const SOURCE_KIND: Record<string, string> = {
  manual: "手动创建",
  manual_review: "人工复核",
  document_import: "文档导入",
  system_extract: "AI 提取",
  migration: "旧数据迁移",
  legacy_import: "旧数据导入",
};

const CONFIRMATION_STATUS: Record<string, string> = {
  candidate: "待确认",
  confirmed: "已确认",
  rejected: "已拒绝",
};

const LIFECYCLE_STATUS: Record<string, string> = {
  active: "使用中",
  archived: "已归档",
  merged: "已合并",
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

function actionReasons(candidate: LoreCandidate, action: "edit" | "accept" | "reject"): string[] {
  return candidate.actions?.disabled_reasons?.[action] ?? (
    action === "accept" ? candidate.disabled_reasons : []
  );
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
  const [loreTypes, setLoreTypes] = useState<LoreTypeDefinition[]>([]);
  const [typesProjectId, setTypesProjectId] = useState<string | null>(null);
  const [typesLoading, setTypesLoading] = useState(false);
  const [typesError, setTypesError] = useState("");
  const [candidateDirty, setCandidateDirty] = useState(false);
  const [candidateMutationBusy, setCandidateMutationBusy] = useState(false);
  const [formalDirty, setFormalDirty] = useState(false);
  const [formalMutationBusy, setFormalMutationBusy] = useState(false);
  const [actionNotice, setActionNotice] = useState("");
  const [preservedCandidateDrafts, setPreservedCandidateDrafts] = useState<Record<string, CandidateDraft>>({});
  const requestSequence = useRef(0);
  const overviewSequence = useRef(0);
  const detailSequence = useRef(0);
  const typesSequence = useRef(0);
  const loadMoreController = useRef<AbortController | null>(null);
  const detailRef = useRef<HTMLElement | null>(null);
  const candidateCardRefs = useRef(new Map<string, HTMLButtonElement>());
  const listHeadingRef = useRef<HTMLHeadingElement | null>(null);
  const nextCandidateAfterMutation = useRef<string | null>(null);
  const focusAfterMutation = useRef(false);
  const nextFormalAfterMutation = useRef<string | null>(null);
  const focusFormalAfterMutation = useRef(false);
  const contextKey = `${id ?? ""}?${queryKey}`;
  const contextKeyRef = useRef(contextKey);
  contextKeyRef.current = contextKey;

  const selectedCandidate = useMemo(
    () => candidates.find((item) => item.id === selectedCandidateId) ?? null,
    [candidates, selectedCandidateId]
  );

  function confirmDiscardDrafts(): boolean {
    if (candidateMutationBusy || formalMutationBusy) {
      setActionNotice("设定操作正在提交，请等待结果后再切换页面或筛选。");
      return false;
    }
    if (!candidateDirty && !formalDirty) return true;
    if (!window.confirm("当前设定有尚未保存的修改，确定放弃这些修改吗？")) {
      return false;
    }
    setCandidateDirty(false);
    setFormalDirty(false);
    return true;
  }

  function updateQuery(changes: Record<string, string | null>) {
    if (!confirmDiscardDrafts()) return;
    setRecoveryNotice("");
    clearSelection(true);
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
    setLoreTypes([]);
    setTypesProjectId(null);
    setPreservedCandidateDrafts({});
  }, [id]);

  useEffect(() => {
    if (!candidateDirty && !formalDirty) return;
    const protectDraft = (event: BeforeUnloadEvent) => {
      event.preventDefault();
    };
    window.addEventListener("beforeunload", protectDraft);
    return () => window.removeEventListener("beforeunload", protectDraft);
  }, [candidateDirty, formalDirty]);

  useEffect(() => {
    if (
      !id ||
      !selectedCandidate ||
      overview?.migration_status.storage_mode !== "relational" ||
      typesProjectId === id
    ) return;
    const controller = new AbortController();
    const sequence = ++typesSequence.current;
    setLoreTypes([]);
    setTypesLoading(true);
    setTypesError("");
    api.listLoreTypes(id, controller.signal)
      .then((data) => {
        if (sequence === typesSequence.current) {
          setLoreTypes(data.items.filter((item) => item.status === "active" && item.is_builtin));
          setTypesProjectId(id);
        }
      })
      .catch((error) => {
        if ((error as Error).name !== "AbortError" && sequence === typesSequence.current) {
          setTypesError(errorMessage(error));
        }
      })
      .finally(() => {
        if (sequence === typesSequence.current) setTypesLoading(false);
      });
    return () => controller.abort();
  }, [id, overview?.migration_status.storage_mode, selectedCandidate?.id, typesProjectId]);

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
    clearSelection(true);

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
        const desired = nextFormalAfterMutation.current;
        if (desired && data.items.some((item) => item.id === desired)) {
          setSelectedFormalId(desired);
        } else if (focusFormalAfterMutation.current) {
          requestAnimationFrame(() => listHeadingRef.current?.focus());
          focusFormalAfterMutation.current = false;
        }
        nextFormalAfterMutation.current = null;
      } else if (scope === "review" && "query_signature" in data) {
        setCandidates(data.items);
        setCandidateTotal(data.total);
        setCandidateCursor(data.next_cursor);
        setCandidateHasMore(data.has_more);
        const desired = nextCandidateAfterMutation.current;
        if (desired && data.items.some((item) => item.id === desired)) {
          setSelectedCandidateId(desired);
        }
        if (focusAfterMutation.current) {
          requestAnimationFrame(() => {
            const target = desired ? candidateCardRefs.current.get(desired) : null;
            (target ?? listHeadingRef.current)?.focus();
          });
          focusAfterMutation.current = false;
        }
        nextCandidateAfterMutation.current = null;
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
        if (sequence === detailSequence.current) {
          setDetail(data);
          if (focusFormalAfterMutation.current) {
            requestAnimationFrame(() => detailRef.current?.focus());
            focusFormalAfterMutation.current = false;
          }
        }
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
    if (!confirmDiscardDrafts()) return;
    setRecoveryNotice("");
    clearSelection(true);
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

  function clearSelection(force = false) {
    if (!force && !confirmDiscardDrafts()) return false;
    detailSequence.current += 1;
    setSelectedFormalId(null);
    setSelectedCandidateId(null);
    setDetail(null);
    setDetailError("");
    setDetailLoading(false);
    setCandidateDirty(false);
    setFormalDirty(false);
    return true;
  }

  function selectFormal(elementId: string) {
    if (formalMutationBusy) return;
    if (elementId !== selectedFormalId && !confirmDiscardDrafts()) return;
    setFormalDirty(false);
    setActionNotice("");
    setSelectedCandidateId(null);
    setSelectedFormalId(elementId);
  }

  function selectCandidate(candidateId: string) {
    if (candidateMutationBusy) return;
    if (candidateId !== selectedCandidateId && !confirmDiscardDrafts()) return;
    setCandidateDirty(false);
    setActionNotice("");
    setSelectedFormalId(null);
    setSelectedCandidateId(candidateId);
  }

  function finishFormalMutation(elementId: string, notice: string) {
    setFormalDirty(false);
    setActionNotice(notice);
    nextFormalAfterMutation.current = elementId;
    focusFormalAfterMutation.current = true;
    setReloadToken((value) => value + 1);
  }

  function updateCandidate(updated: LoreCandidate, notice = "已载入最新候选内容。") {
    setCandidateDirty(false);
    setActionNotice(notice);
    nextCandidateAfterMutation.current = updated.id;
    setReloadToken((value) => value + 1);
  }

  function finishCandidateAction(response: LoreCandidateActionResponse) {
    const accepted = response.action_result === "accepted" || response.action_result === "already_accepted";
    setActionNotice(
      accepted
        ? response.replayed ? "该候选此前已接纳，已同步最终状态。" : "候选已接纳为正式设定。"
        : response.replayed ? "该候选此前已拒绝，已同步最终状态。" : "候选已拒绝，原始记录仍保留。"
    );
    setCandidateDirty(false);
    setPreservedCandidateDrafts((current) => {
      const next = { ...current };
      delete next[response.candidate.id];
      return next;
    });
    setSelectedCandidateId(null);
    nextCandidateAfterMutation.current = response.next_pending_candidate_id;
    focusAfterMutation.current = true;
    setReloadToken((value) => value + 1);
  }

  if (!id) return <div className="empty-state">项目地址无效</div>;

  const formalItems = formal?.items ?? [];
  const hasMore = scope === "formal" ? formal?.has_more : candidateHasMore;
  const total = scope === "formal" ? formal?.total ?? 0 : candidateTotal;
  const activeFilters = Array.from(searchParams.keys()).some((key) => key !== "scope");

  return (
    <div className="lore-page">
      <button className="btn-back" onClick={() => {
        if (confirmDiscardDrafts()) navigate(`/project/${id}`);
      }}>← 返回创作项目</button>
      <header className="page-header lore-header">
        <div>
          <h1>世界观设定仓库</h1>
          <p>集中管理正式设定，并逐项审核 AI 从用户原文提取的候选。</p>
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
          <strong>{overview?.disabled ?? "—"}</strong><span>暂停用于生成</span>
        </button>
        <button type="button" onClick={() => changeScope("formal", { lifecycle: "archived" })}>
          <strong>{overview?.archived ?? "—"}</strong><span>已归档</span>
        </button>
      </section>

      {overview && !overview.capabilities.candidate_accept && (
        <div className="lore-note">当前项目仍使用兼容资料模式：候选可审阅或拒绝，但不能接纳为正式设定。</div>
      )}

      {actionNotice && <div className="lore-note" role="status">{actionNotice}</div>}

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
            <label><span>生成权限</span><select className="form-select" value={searchParams.get("enabled") ?? ""} onChange={(event) => updateQuery({ enabled: event.target.value || null })}><option value="">全部</option><option value="true">允许用于生成</option><option value="false">暂停用于生成</option></select></label>
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
          <div className="lore-list-heading"><h2 tabIndex={-1} ref={listHeadingRef}>{scope === "formal" ? "正式设定" : "待审核提取"}</h2><span aria-live="polite">{loading ? "加载中…" : `共 ${total} 项`}</span></div>
          {!loading && !listError && total === 0 && (
            <div className="lore-empty"><strong>{activeFilters ? "没有匹配的设定" : "这里还没有内容"}</strong><span>{activeFilters ? "可清除筛选后重新查看。" : scope === "formal" ? "完成提取审核后，正式设定会显示在这里。" : "导入世界观文本后，提取结果会显示在这里。"}</span></div>
          )}
          {scope === "formal" && formalItems.map((item) => (
            <FormalCard key={item.id} item={item} selected={selectedFormalId === item.id} disabled={formalMutationBusy} onSelect={() => selectFormal(item.id)} />
          ))}
          {scope === "review" && candidates.map((item) => (
            <CandidateCard key={item.id} item={item} selected={selectedCandidateId === item.id} disabled={candidateMutationBusy} buttonRef={(node) => {
              if (node) candidateCardRefs.current.set(item.id, node);
              else candidateCardRefs.current.delete(item.id);
            }} onSelect={() => selectCandidate(item.id)} />
          ))}
          {hasMore && <button className="btn btn-secondary lore-load-more" type="button" disabled={loadingMore} onClick={loadMore}>{loadingMore ? "加载中…" : "加载更多"}</button>}
        </section>

        <aside className="lore-detail" aria-label="设定详情" tabIndex={-1} ref={detailRef}>
          {(selectedFormalId || selectedCandidate) && <button className="btn btn-secondary lore-detail-back" type="button" onClick={() => clearSelection()}>← 返回设定列表</button>}
          {!selectedFormalId && !selectedCandidate && <div className="lore-empty"><strong>选择一项查看详情</strong><span>可核对字段、关联数量和原始文本出处。</span></div>}
          {detailLoading && <div className="lore-empty">详情加载中…</div>}
          {detailError && <div className="lore-alert" role="alert">{detailError}<button type="button" onClick={() => setDetailReloadToken((value) => value + 1)}>重试</button></div>}
          {detail && <FormalDetail
            key={`${detail.id}-${detail.lock_version}`}
            projectId={id}
            detail={detail}
            onDirtyChange={setFormalDirty}
            onBusyChange={setFormalMutationBusy}
            onMutationComplete={finishFormalMutation}
          />}
          {selectedCandidate && (
            <CandidateDetail
              key={selectedCandidate.id}
              projectId={id}
              candidate={selectedCandidate}
              candidateAcceptEnabled={overview?.capabilities.candidate_accept === true}
              relationalMode={overview?.migration_status.storage_mode === "relational"}
              loreTypes={typesProjectId === id ? loreTypes : []}
              typesLoading={typesLoading}
              typesError={typesError}
              onDirtyChange={setCandidateDirty}
              onBusyChange={setCandidateMutationBusy}
              initialPreservedDraft={preservedCandidateDrafts[selectedCandidate.id] ?? null}
              onPreserveDraft={(draft) => setPreservedCandidateDrafts((current) => {
                const next = { ...current };
                if (draft) next[selectedCandidate.id] = draft;
                else delete next[selectedCandidate.id];
                return next;
              })}
              onCandidateUpdate={updateCandidate}
              onActionComplete={finishCandidateAction}
            />
          )}
        </aside>
      </div>
    </div>
  );
}

function FormalCard({ item, selected, disabled, onSelect }: { item: LoreElementListItem; selected: boolean; disabled: boolean; onSelect: () => void }) {
  return (
    <button type="button" className={`lore-card ${selected ? "selected" : ""}`} onClick={onSelect} aria-pressed={selected} disabled={disabled}>
      <span className="lore-card-top"><span className="lore-type">{item.type.display_name}</span><span className={`lore-badge ${item.lifecycle_status === "archived" ? "lore-badge--muted" : ""}`}>{LIFECYCLE_STATUS[item.lifecycle_status] || "状态待确认"}</span></span>
      <strong>{item.name}</strong>
      <span className="lore-summary">{item.summary || "暂无摘要"}</span>
      <span className="lore-meta">{CONFIRMATION_STATUS[item.confirmation_status] || "确认状态待核对"} · {item.enabled ? "允许用于生成" : "暂停用于生成"} · {item.generation_eligible ? "当前可用于生成" : "当前不可用于生成"}</span>
      <span className="lore-meta">{item.source_summary || "来源待补充"} · {item.relation_count} 项关联 · 版本 {item.current_version}</span>
    </button>
  );
}

function CandidateCard({ item, selected, disabled, buttonRef, onSelect }: { item: LoreCandidate; selected: boolean; disabled: boolean; buttonRef: (node: HTMLButtonElement | null) => void; onSelect: () => void }) {
  return (
    <button ref={buttonRef} type="button" className={`lore-card ${selected ? "selected" : ""}`} onClick={onSelect} aria-pressed={selected} disabled={disabled}>
      <span className="lore-card-top"><span className="lore-type">{candidateTypeLabel(item.type_key, item.type_display_name)}</span>{item.needs_attention && <span className="lore-badge lore-badge--warning">需要关注</span>}</span>
      <strong>{item.name || "名称待确认"}</strong>
      <span className="lore-summary">{item.summary || "原文未提供摘要"}</span>
      <span className="lore-meta">{CANDIDATE_STATUS[item.status] || "状态待确认"} · {item.evidence.length} 条原文证据 · 修订 {item.revision}</span>
    </button>
  );
}

interface FormalDraft {
  name: string;
  summary: string;
  payload: Record<string, string>;
  fieldStates: Record<string, LoreFieldState>;
}

type FormalStateAction = "enable" | "disable" | "archive" | "restore-archive";

type FormalPendingIntent =
  | { kind: "edit"; draft: FormalDraft }
  | { kind: "state"; action: FormalStateAction };

const FORMAL_ACTION: Record<FormalStateAction, { title: string; description: string; success: string }> = {
  enable: {
    title: "允许用于生成",
    description: "允许系统在该设定已确认、未归档且没有待确认字段时，把它纳入生成依据。",
    success: "该设定已允许用于生成。",
  },
  disable: {
    title: "暂停用于生成",
    description: "设定内容、来源、版本和关系都会保留，但暂不作为生成依据。",
    success: "该设定已暂停用于生成。",
  },
  archive: {
    title: "归档设定",
    description: "设定会标记为已归档并停止用于生成；来源、版本和关系会保留，可通过已归档筛选集中查看和恢复。",
    success: "该设定已归档，可在已归档筛选中恢复。",
  },
  "restore-archive": {
    title: "恢复到设定库",
    description: "设定会恢复为使用中，并保留归档前的启停状态；满足条件时可能重新成为生成依据。",
    success: "该设定已恢复到设定库。",
  },
};

function stringValue(value: unknown): string {
  if (value === null || value === undefined) return "";
  return typeof value === "string" ? value : valueText(value);
}

function buildFormalDraft(detail: LoreElementDetail): FormalDraft {
  return {
    name: detail.name,
    summary: detail.summary,
    payload: Object.fromEntries(
      detail.field_definitions.map((field) => [field.key, stringValue(detail.payload[field.key])])
    ),
    fieldStates: Object.fromEntries(
      detail.field_definitions.map((field) => [
        field.key,
        (detail.field_states[field.key] as LoreFieldState | undefined) ?? "unknown",
      ])
    ),
  };
}

function formalEditInput(detail: LoreElementDetail, draft: FormalDraft): LoreElementUpdateInput | null {
  const name = draft.name.trim();
  if (!name) return null;
  const payload: Record<string, string | null> = {};
  const fieldStates: Record<string, LoreFieldState> = {};
  for (const field of detail.field_definitions) {
    const state = draft.fieldStates[field.key] ?? "unknown";
    const value = draft.payload[field.key]?.trim() ?? "";
    if (state === "provided" && !value) return null;
    payload[field.key] = state === "unknown" ? null : value || null;
    fieldStates[field.key] = state;
  }
  return {
    expected_version: detail.lock_version,
    name,
    summary: draft.summary.trim(),
    payload,
    field_states: fieldStates,
  };
}

function detailMatchesDraft(detail: LoreElementDetail, draft: FormalDraft): boolean {
  const input = formalEditInput(detail, draft);
  if (!input || detail.name !== input.name || detail.summary !== input.summary) return false;
  return detail.field_definitions.every((field) => (
    stringValue(detail.payload[field.key]) === stringValue(input.payload[field.key]) &&
    (detail.field_states[field.key] || "unknown") === input.field_states[field.key]
  ));
}

function stateReached(detail: LoreElementDetail, action: FormalStateAction): boolean {
  if (action === "enable") return detail.enabled;
  if (action === "disable") return !detail.enabled;
  if (action === "archive") return detail.lifecycle_status === "archived";
  return detail.lifecycle_status === "active";
}

function FormalDetail({
  projectId,
  detail,
  onDirtyChange,
  onBusyChange,
  onMutationComplete,
}: {
  projectId: string;
  detail: LoreElementDetail;
  onDirtyChange: (dirty: boolean) => void;
  onBusyChange: (busy: boolean) => void;
  onMutationComplete: (elementId: string, notice: string) => void;
}) {
  const definitions = [...detail.field_definitions].sort((left, right) => left.order - right.order);
  const [baseDetail, setBaseDetail] = useState(detail);
  const [draft, setDraft] = useState(() => buildFormalDraft(detail));
  const [editing, setEditing] = useState(false);
  const [busyAction, setBusyAction] = useState<"save" | "state" | "check" | null>(null);
  const [confirmAction, setConfirmAction] = useState<FormalStateAction | null>(null);
  const [reason, setReason] = useState("");
  const [operationError, setOperationError] = useState("");
  const [conflict, setConflict] = useState(false);
  const [outcomeUnknown, setOutcomeUnknown] = useState(false);
  const [pendingIntent, setPendingIntent] = useState<FormalPendingIntent | null>(null);
  const [serverLatest, setServerLatest] = useState<LoreElementDetail | null>(null);
  const [preservedDraft, setPreservedDraft] = useState("");
  const nameRef = useRef<HTMLInputElement | null>(null);
  const errorRef = useRef<HTMLDivElement | null>(null);
  const confirmRef = useRef<HTMLDivElement | null>(null);
  const editButtonRef = useRef<HTMLButtonElement | null>(null);
  const stateTriggerRef = useRef<HTMLButtonElement | null>(null);

  const dirty = editing && JSON.stringify(draft) !== JSON.stringify(buildFormalDraft(baseDetail));
  const hasSemanticChanges = !detailMatchesDraft(baseDetail, draft);
  const writable = !baseDetail.read_only && baseDetail.lifecycle_status !== "merged";

  useEffect(() => onDirtyChange(dirty), [dirty, onDirtyChange]);
  useEffect(() => onBusyChange(busyAction !== null), [busyAction, onBusyChange]);
  useEffect(() => () => {
    onDirtyChange(false);
    onBusyChange(false);
  }, [onBusyChange, onDirtyChange]);
  useEffect(() => {
    if (operationError) requestAnimationFrame(() => errorRef.current?.focus());
  }, [operationError]);
  useEffect(() => {
    if (confirmAction) requestAnimationFrame(() => confirmRef.current?.focus());
  }, [confirmAction]);

  function beginEdit() {
    setOperationError("");
    setConflict(false);
    setOutcomeUnknown(false);
    setPendingIntent(null);
    setEditing(true);
    requestAnimationFrame(() => nameRef.current?.focus());
  }

  function cancelEdit() {
    if (dirty && !window.confirm("确定放弃当前尚未保存的正式设定修改吗？")) return;
    setDraft(buildFormalDraft(baseDetail));
    setEditing(false);
    setOperationError("");
    setConflict(false);
    setOutcomeUnknown(false);
    setPendingIntent(null);
    requestAnimationFrame(() => editButtonRef.current?.focus());
  }

  function changeFieldState(key: string, state: LoreFieldState) {
    setDraft((current) => ({
      ...current,
      payload: state === "unknown" ? { ...current.payload, [key]: "" } : current.payload,
      fieldStates: { ...current.fieldStates, [key]: state },
    }));
  }

  async function reconcile(intent: FormalPendingIntent) {
    setBusyAction("check");
    try {
      const latest = await api.getLoreElement(projectId, baseDetail.id);
      if (intent.kind === "edit" && detailMatchesDraft(latest, intent.draft)) {
        onMutationComplete(baseDetail.id, "服务器内容与本地提交一致，已同步最新列表；无法确认是否由本次请求产生。当前列表已刷新。");
        return;
      }
      if (intent.kind === "state" && stateReached(latest, intent.action)) {
        onMutationComplete(baseDetail.id, `服务器显示“${FORMAL_ACTION[intent.action].title}”目标状态已达成，但无法确认是否由本次请求产生；列表已同步。`);
        return;
      }
      setServerLatest(latest);
      if (intent.kind === "edit") setPreservedDraft(JSON.stringify(intent.draft, null, 2));
      setOperationError("服务器当前内容与本次提交不一致。请先查看最新内容；系统不会自动覆盖或重复提交。");
      setOutcomeUnknown(false);
      setConflict(true);
    } catch (error) {
      setOperationError(`暂时无法核对服务器最新状态：${errorMessage(error)} 本地内容仍保留。`);
      setOutcomeUnknown(true);
    } finally {
      setBusyAction(null);
    }
  }

  async function saveElement() {
    if (busyAction) return;
    if (conflict || outcomeUnknown) {
      setOperationError("请先核对并载入服务器最新内容，再决定是否重新编辑；系统不会用旧版本重复提交。");
      return;
    }
    if (!hasSemanticChanges) {
      setOperationError("当前内容没有变化，无需保存。");
      return;
    }
    const input = formalEditInput(baseDetail, draft);
    if (!draft.name.trim()) {
      setOperationError("名称不能为空。");
      return;
    }
    const invalidField = definitions.find((field) => (
      draft.fieldStates[field.key] === "provided" && !draft.payload[field.key]?.trim()
    ));
    if (!input || invalidField) {
      setOperationError(invalidField
        ? `“${invalidField.label}”标记为已确认有内容时不能为空。`
        : "请检查设定字段后重试。");
      return;
    }
    const intent: FormalPendingIntent = { kind: "edit", draft: { ...draft, payload: { ...draft.payload }, fieldStates: { ...draft.fieldStates } } };
    setBusyAction("save");
    setOperationError("");
    setConflict(false);
    setOutcomeUnknown(false);
    setPendingIntent(intent);
    try {
      await api.updateLoreElement(projectId, baseDetail.id, input);
      onMutationComplete(baseDetail.id, "正式设定修改已保存，并已生成新内容版本。");
    } catch (error) {
      if (error instanceof ApiError) {
        setOperationError(errorMessage(error));
        if (error.status === 409) {
          setConflict(true);
          setPreservedDraft(JSON.stringify(intent.draft, null, 2));
        }
      } else {
        setOutcomeUnknown(true);
        setOperationError("网络结果不确定，正在核对服务器状态；不会自动重复提交。");
        await reconcile(intent);
      }
    } finally {
      setBusyAction(null);
    }
  }

  function openStateConfirm(action: FormalStateAction, trigger: HTMLButtonElement) {
    setOperationError("");
    setReason("");
    stateTriggerRef.current = trigger;
    setConfirmAction(action);
  }

  function cancelStateConfirm() {
    setConfirmAction(null);
    requestAnimationFrame(() => stateTriggerRef.current?.focus());
  }

  async function performStateAction(action: FormalStateAction) {
    if (busyAction) return;
    const intent: FormalPendingIntent = { kind: "state", action };
    setBusyAction("state");
    setConfirmAction(null);
    setOperationError("");
    setConflict(false);
    setOutcomeUnknown(false);
    setPendingIntent(intent);
    try {
      await api.changeLoreElementState(projectId, baseDetail.id, action, {
        expected_version: baseDetail.lock_version,
        reason: reason.trim(),
      });
      onMutationComplete(baseDetail.id, FORMAL_ACTION[action].success);
    } catch (error) {
      if (error instanceof ApiError) {
        if (error.code === "LORE_ELEMENT_ACTIVE_RELATIONS") {
          setOperationError("该设定刚刚出现了启用关系。关系管理将在后续里程碑开放；当前暂不能归档此设定。");
          try {
            const latest = await api.getLoreElement(projectId, baseDetail.id);
            setBaseDetail(latest);
            setDraft(buildFormalDraft(latest));
            setPendingIntent(null);
          } catch {
            setConflict(true);
            setOutcomeUnknown(true);
          }
        } else {
          setOperationError(errorMessage(error));
          if (error.status === 409 && error.code === "LORE_VERSION_CONFLICT") setConflict(true);
        }
      } else {
        setOutcomeUnknown(true);
        setOperationError("网络结果不确定，正在核对服务器状态；不会自动重复提交。");
        await reconcile(intent);
      }
    } finally {
      setBusyAction(null);
    }
  }

  function loadServerLatest() {
    if (!serverLatest) return;
    setBaseDetail(serverLatest);
    setDraft(buildFormalDraft(serverLatest));
    setServerLatest(null);
    setEditing(false);
    setOperationError("");
    setConflict(false);
    setOutcomeUnknown(false);
    setPendingIntent(null);
  }

  const stateActions: FormalStateAction[] = baseDetail.lifecycle_status === "archived"
    ? ["restore-archive"]
    : [baseDetail.enabled ? "disable" : "enable", ...(baseDetail.relation_count === 0 ? ["archive" as const] : [])];
  const confirmTitleId = confirmAction
    ? `lore-formal-confirm-${baseDetail.id}-${confirmAction}`
    : undefined;

  return (
    <div className="lore-formal-review">
      <div className="lore-detail-heading">
        <span className="lore-type">{baseDetail.type.display_name}</span>
        <h2>{baseDetail.name}</h2>
        <p>{baseDetail.summary || "暂无摘要"}</p>
        <div className="lore-status-grid" aria-label="设定当前状态">
          <span>{CONFIRMATION_STATUS[baseDetail.confirmation_status] || "确认状态待核对"}</span>
          <span>{LIFECYCLE_STATUS[baseDetail.lifecycle_status] || "生命周期待核对"}</span>
          <span>{baseDetail.enabled ? "允许用于生成" : "暂停用于生成"}</span>
          <span>{baseDetail.generation_eligible ? "当前可用于生成" : "当前不可用于生成"}</span>
        </div>
      </div>

      {operationError && <div className="lore-alert lore-operation-message" role="alert" tabIndex={-1} ref={errorRef}>
        <span>{operationError}</span>
        {(conflict || outcomeUnknown) && pendingIntent && <button type="button" disabled={busyAction !== null} onClick={() => void reconcile(pendingIntent)}>{busyAction === "check" ? "核对中…" : "核对最新状态"}</button>}
      </div>}

      {serverLatest && <section className="lore-server-latest">
        <h3>服务器最新内容</h3>
        <p>{serverLatest.name} · 内容版本 {serverLatest.current_version} · {LIFECYCLE_STATUS[serverLatest.lifecycle_status] || serverLatest.lifecycle_status}</p>
        <button className="btn btn-secondary" type="button" onClick={loadServerLatest}>载入服务器最新内容</button>
      </section>}

      {preservedDraft && <details className="lore-preserved-draft"><summary>查看冲突前保留的本地草稿</summary><textarea className="form-textarea" readOnly value={preservedDraft} aria-label="冲突前保留的本地草稿" /></details>}

      {editing ? (
        <form className="lore-candidate-form" onSubmit={(event) => { event.preventDefault(); void saveElement(); }}>
          <fieldset disabled={busyAction !== null}>
            <legend>编辑正式设定</legend>
            <label><span>类型</span><input className="form-input" value={baseDetail.type.display_name} disabled /><small>普通编辑不能切换类型。</small></label>
            <label><span>名称</span><input ref={nameRef} className="form-input" value={draft.name} maxLength={200} onChange={(event) => setDraft((current) => ({ ...current, name: event.target.value }))} /></label>
            <label><span>摘要</span><textarea className="form-textarea" value={draft.summary} maxLength={2000} onChange={(event) => setDraft((current) => ({ ...current, summary: event.target.value }))} /></label>
            <section className="lore-edit-fields"><h3>类型字段</h3>{definitions.length ? definitions.map((field) => <div className="lore-edit-field" key={field.key}>
              <label><span>{field.label}</span>{field.control === "text" ? <input className="form-input" value={draft.payload[field.key] ?? ""} disabled={draft.fieldStates[field.key] === "unknown"} onChange={(event) => setDraft((current) => ({ ...current, payload: { ...current.payload, [field.key]: event.target.value } }))} /> : <textarea className="form-textarea" value={draft.payload[field.key] ?? ""} disabled={draft.fieldStates[field.key] === "unknown"} onChange={(event) => setDraft((current) => ({ ...current, payload: { ...current.payload, [field.key]: event.target.value } }))} />}</label>
              <label><span>信息状态</span><select className="form-select" value={draft.fieldStates[field.key] ?? "unknown"} onChange={(event) => changeFieldState(field.key, event.target.value as LoreFieldState)}><option value="provided">已确认有内容</option><option value="unknown">信息为空</option><option value="needs_confirmation">待确认</option></select></label>
              {field.help && <small>{field.help}</small>}
            </div>) : <p>当前类型没有额外字段。</p>}</section>
          </fieldset>
          <div className="lore-candidate-actions">
            <button className="btn btn-primary" type="submit" disabled={busyAction !== null || !hasSemanticChanges || conflict || outcomeUnknown}>{busyAction === "save" ? "保存中…" : "保存修改"}</button>
            <button className="btn btn-secondary" type="button" disabled={busyAction !== null} onClick={cancelEdit}>取消</button>
          </div>
        </form>
      ) : (
        <>
          <dl className="lore-fields">{definitions.map((definition) => <div key={definition.key}><dt>{definition.label}<span>{FIELD_STATE[baseDetail.field_states[definition.key]] || "状态待确认"}</span></dt><dd>{valueText(baseDetail.payload[definition.key])}</dd></div>)}</dl>
          <section className="lore-sources"><h3>原始出处</h3>{baseDetail.sources.length ? baseDetail.sources.map((source, index) => <article key={source.id ?? `${source.kind}-${index}`}><strong>{SOURCE_KIND[source.kind] || "其他来源"}{source.is_primary ? " · 主要来源" : ""}</strong><p>{source.excerpt || "暂无可展示的原文摘录"}</p>{source.reference && <small>{source.reference}</small>}</article>) : <p>暂无来源记录</p>}</section>
          {writable ? <section className="lore-candidate-action-panel">
            <h3>管理正式设定</h3>
            <p>内容编辑会生成新版本；暂停或归档不会删除内容、来源、版本或关系。</p>
            <div className="lore-candidate-actions">
              <button ref={editButtonRef} className="btn btn-primary" type="button" disabled={busyAction !== null || conflict || outcomeUnknown} onClick={beginEdit}>编辑内容</button>
              {stateActions.map((action) => <button key={action} className="btn btn-secondary" type="button" disabled={busyAction !== null || conflict || outcomeUnknown} onClick={(event) => openStateConfirm(action, event.currentTarget)}>{FORMAL_ACTION[action].title}</button>)}
            </div>
            {baseDetail.lifecycle_status === "active" && baseDetail.relation_count > 0 && <p className="lore-action-reasons">该设定有 {baseDetail.relation_count} 条启用关系；关系管理将在后续里程碑开放，归档关系前暂不能归档此设定。</p>}
          </section> : <div className="lore-note">当前资料为只读或已合并，不能在这里修改。</div>}
        </>
      )}

      {confirmAction && <section className="lore-confirm" role="alertdialog" aria-labelledby={confirmTitleId} tabIndex={-1} ref={confirmRef}>
        <h3 id={confirmTitleId}>确认{FORMAL_ACTION[confirmAction].title}</h3>
        <p>{FORMAL_ACTION[confirmAction].description}</p>
        <label><span>原因（可选）</span><input className="form-input" value={reason} maxLength={200} onChange={(event) => setReason(event.target.value)} /></label>
        <div className="lore-candidate-actions">
          <button className="btn btn-primary" type="button" disabled={busyAction !== null} onClick={() => void performStateAction(confirmAction)}>确认{FORMAL_ACTION[confirmAction].title}</button>
          <button className="btn btn-secondary" type="button" disabled={busyAction !== null} onClick={cancelStateConfirm}>取消</button>
        </div>
      </section>}
    </div>
  );
}

interface CandidateDraft {
  typeKey: string;
  name: string;
  summary: string;
  payload: Record<string, string>;
  fieldStates: Record<string, LoreFieldState>;
  resolutions: Record<string, LoreSuggestionResolution>;
}

function buildCandidateDraft(candidate: LoreCandidate): CandidateDraft {
  return {
    typeKey: candidate.type_key ?? "",
    name: candidate.name ?? "",
    summary: candidate.summary,
    payload: Object.fromEntries(
      Object.entries(candidate.payload ?? {}).map(([key, value]) => [key, value ?? ""])
    ),
    fieldStates: { ...(candidate.field_states ?? {}) },
    resolutions: { ...(candidate.suggestion_resolutions ?? {}) },
  };
}

function candidateFields(
  candidate: LoreCandidate,
  typeKey: string,
  loreTypes: LoreTypeDefinition[]
): LoreFieldDefinition[] {
  const authoritative = loreTypes.find((item) => item.key === typeKey)?.field_schema;
  if (authoritative) return [...authoritative].sort((left, right) => left.order - right.order);
  const evidenceByField = new Map(
    candidate.evidence
      .filter((item) => !item.is_name)
      .map((item, index) => [item.field_key, {
        key: item.field_key,
        label: item.label || item.field_key,
        control: "textarea",
        value_type: "text",
        help: "",
        order: index,
        required: false,
      } satisfies LoreFieldDefinition])
  );
  Object.keys(candidate.payload ?? {}).forEach((key, index) => {
    if (!evidenceByField.has(key)) {
      evidenceByField.set(key, {
        key,
        label: key,
        control: "textarea",
        value_type: "text",
        help: "",
        order: candidate.evidence.length + index,
        required: false,
      });
    }
  });
  return Array.from(evidenceByField.values()).sort((left, right) => left.order - right.order);
}

function CandidateDetail({
  projectId,
  candidate,
  candidateAcceptEnabled,
  relationalMode,
  loreTypes,
  typesLoading,
  typesError,
  onDirtyChange,
  onBusyChange,
  initialPreservedDraft,
  onPreserveDraft,
  onCandidateUpdate,
  onActionComplete,
}: {
  projectId: string;
  candidate: LoreCandidate;
  candidateAcceptEnabled: boolean;
  relationalMode: boolean;
  loreTypes: LoreTypeDefinition[];
  typesLoading: boolean;
  typesError: string;
  onDirtyChange: (dirty: boolean) => void;
  onBusyChange: (busy: boolean) => void;
  initialPreservedDraft: CandidateDraft | null;
  onPreserveDraft: (draft: CandidateDraft | null) => void;
  onCandidateUpdate: (candidate: LoreCandidate, notice?: string) => void;
  onActionComplete: (response: LoreCandidateActionResponse) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<CandidateDraft>(() => buildCandidateDraft(candidate));
  const [busyAction, setBusyAction] = useState<"save" | "accept" | "reject" | "check" | null>(null);
  const [confirmAction, setConfirmAction] = useState<"accept" | "reject" | null>(null);
  const [operationError, setOperationError] = useState("");
  const [conflict, setConflict] = useState(false);
  const [outcomeUnknown, setOutcomeUnknown] = useState(false);
  const [preservedDraft, setPreservedDraft] = useState<CandidateDraft | null>(initialPreservedDraft);
  const nameInputRef = useRef<HTMLInputElement | null>(null);
  const errorRef = useRef<HTMLDivElement | null>(null);
  const confirmRef = useRef<HTMLElement | null>(null);
  const acceptButtonRef = useRef<HTMLButtonElement | null>(null);
  const rejectButtonRef = useRef<HTMLButtonElement | null>(null);
  const initialDraft = useMemo(() => buildCandidateDraft(candidate), [candidate]);
  const fields = useMemo(
    () => candidateFields(candidate, draft.typeKey, loreTypes),
    [candidate, draft.typeKey, loreTypes]
  );
  const dirty = editing && JSON.stringify(draft) !== JSON.stringify(initialDraft);
  const canUseAuthoritativeSchema = !relationalMode || loreTypes.length > 0;
  const hasEditableLegacyType = relationalMode || TYPE_OPTIONS.some(([key]) => key === candidate.type_key);

  useEffect(() => {
    setDraft(buildCandidateDraft(candidate));
    setEditing(false);
    setConfirmAction(null);
    setOperationError("");
    setConflict(false);
    setOutcomeUnknown(false);
  }, [candidate.id, candidate.revision]);

  useEffect(() => {
    onDirtyChange(dirty);
    return () => onDirtyChange(false);
  }, [dirty, onDirtyChange]);

  useEffect(() => {
    onBusyChange(busyAction !== null);
    return () => onBusyChange(false);
  }, [busyAction, onBusyChange]);

  useEffect(() => {
    if (!operationError) return;
    requestAnimationFrame(() => errorRef.current?.focus());
  }, [operationError]);

  useEffect(() => {
    if (!confirmAction) return;
    requestAnimationFrame(() => confirmRef.current?.focus());
  }, [confirmAction]);

  function startEditing() {
    setOperationError("");
    if (relationalMode && !canUseAuthoritativeSchema) {
      setOperationError(typesLoading ? "正在加载权威字段定义，请稍候。" : typesError || "字段定义加载失败，暂时不能安全编辑。");
      return;
    }
    setEditing(true);
    requestAnimationFrame(() => nameInputRef.current?.focus());
  }

  function cancelEditing() {
    if (dirty && !window.confirm("确定放弃当前候选的未保存修改吗？")) return;
    setDraft(initialDraft);
    setEditing(false);
    setOperationError("");
    setConflict(false);
  }

  function cancelConfirmation() {
    const returnTarget = confirmAction === "accept" ? acceptButtonRef.current : rejectButtonRef.current;
    setConfirmAction(null);
    requestAnimationFrame(() => returnTarget?.focus());
  }

  function changeType(typeKey: string) {
    if (!relationalMode) return;
    const nextFields = candidateFields(candidate, typeKey, loreTypes);
    setDraft((current) => ({
      ...current,
      typeKey,
      payload: Object.fromEntries(nextFields.map((field) => [field.key, current.payload[field.key] ?? ""])),
      fieldStates: Object.fromEntries(nextFields.map((field) => [
        field.key,
        current.fieldStates[field.key] ?? "unknown",
      ])),
    }));
  }

  function changeFieldState(key: string, state: LoreFieldState) {
    setDraft((current) => ({
      ...current,
      payload: state === "unknown" ? { ...current.payload, [key]: "" } : current.payload,
      fieldStates: { ...current.fieldStates, [key]: state },
    }));
  }

  function editInput(): LoreCandidateEditInput | null {
    if (!draft.typeKey) {
      setOperationError("请选择候选类型。");
      return null;
    }
    const payload: Record<string, string | null> = {};
    const fieldStates: Record<string, LoreFieldState> = {};
    for (const field of fields) {
      const state = draft.fieldStates[field.key] ?? "unknown";
      const value = draft.payload[field.key]?.trim() ?? "";
      if (state === "provided" && !value) {
        setOperationError(`“${field.label}”标记为已确认有内容时不能为空。`);
        return null;
      }
      payload[field.key] = state === "unknown" ? null : value || null;
      fieldStates[field.key] = state;
    }
    return {
      expected_version: candidate.revision,
      type_key: draft.typeKey,
      name: draft.name.trim() || null,
      summary: draft.summary.trim(),
      payload,
      field_states: fieldStates,
      suggestion_resolutions: draft.resolutions,
    };
  }

  function handleMutationError(error: unknown, preserveCurrentDraft: boolean) {
    if (preserveCurrentDraft) {
      setPreservedDraft(draft);
      onPreserveDraft(draft);
    }
    if (error instanceof ApiError && error.status === 409) {
      setConflict(true);
      setOperationError("候选已在其他位置更新。当前输入仍保留，请先核对并重新载入最新内容。");
    } else {
      setOperationError(errorMessage(error));
      setOutcomeUnknown(!(error instanceof ApiError));
    }
  }

  async function saveCandidate() {
    if (busyAction) return;
    const input = editInput();
    if (!input) return;
    setBusyAction("save");
    setOperationError("");
    setConflict(false);
    try {
      const updated = await api.editLoreCandidate(
        projectId,
        candidate.batch_id,
        candidate.id,
        input
      );
      setDraft(buildCandidateDraft(updated));
      setPreservedDraft(null);
      onPreserveDraft(null);
      setEditing(false);
      onCandidateUpdate(updated, `修改已保存，当前修订为 ${updated.revision}。`);
    } catch (error) {
      handleMutationError(error, true);
    } finally {
      setBusyAction(null);
    }
  }

  async function performAction(action: "accept" | "reject") {
    if (busyAction) return;
    setBusyAction(action);
    setConfirmAction(null);
    setOperationError("");
    setConflict(false);
    setOutcomeUnknown(false);
    try {
      const input = {
        expected_version: candidate.revision,
        suggestion_resolutions: candidate.suggestion_resolutions,
      };
      const response = action === "accept"
        ? await api.acceptLoreCandidate(projectId, candidate.batch_id, candidate.id, input)
        : await api.rejectLoreCandidate(projectId, candidate.batch_id, candidate.id, input);
      onActionComplete(response);
    } catch (error) {
      handleMutationError(error, false);
    } finally {
      setBusyAction(null);
    }
  }

  async function checkLatestStatus() {
    if (busyAction) return;
    if (dirty && !window.confirm("重新载入会把当前编辑区切换为最新版本；本地草稿将保留在页面下方，是否继续？")) return;
    setBusyAction("check");
    setOperationError("");
    if (dirty) {
      setPreservedDraft(draft);
      onPreserveDraft(draft);
    }
    try {
      const latest = await api.getLoreCandidate(projectId, candidate.batch_id, candidate.id);
      setOutcomeUnknown(false);
      setConflict(false);
      if (latest.status === "accepted" || latest.status === "rejected") {
        onActionComplete({
          candidate: latest,
          action_result: latest.status === "accepted" ? "already_accepted" : "already_rejected",
          replayed: true,
          accepted_element_id: latest.accepted_element_id,
          remaining_pending_count: 0,
          next_pending_candidate_id: null,
        });
      } else {
        onCandidateUpdate(latest, "已载入最新候选；仍为待审核状态，可重新确认后提交。若曾编辑，本地草稿保留在下方。");
      }
    } catch (error) {
      setOperationError(errorMessage(error));
    } finally {
      setBusyAction(null);
    }
  }

  const acceptReasons = actionReasons(candidate, "accept");
  const rejectReasons = actionReasons(candidate, "reject");
  const editReasons = actionReasons(candidate, "edit");
  const canEdit = candidate.actions?.can_edit === true && canUseAuthoritativeSchema && hasEditableLegacyType;
  const canAccept = candidateAcceptEnabled && candidate.actions?.can_accept === true;
  const canReject = candidate.actions?.can_reject === true;

  return (
    <div className="lore-candidate-review">
      <div className="lore-detail-heading">
        <span className="lore-type">{candidateTypeLabel(candidate.type_key, candidate.type_display_name)}</span>
        <h2>{candidate.name || "名称待确认"}</h2>
        <p>{candidate.summary || "原文未提供摘要"}</p>
        <span className="lore-badge lore-badge--muted">AI 提取候选 · 修订 {candidate.revision}</span>
      </div>

      {operationError && <div className="lore-alert lore-operation-message" role="alert" tabIndex={-1} ref={errorRef}>
        <span>{operationError}</span>
        {(conflict || outcomeUnknown) && <button type="button" disabled={busyAction !== null} onClick={checkLatestStatus}>{busyAction === "check" ? "核对中…" : "核对最新状态"}</button>}
      </div>}

      {editing ? (
        <form className="lore-candidate-form" onSubmit={(event) => { event.preventDefault(); void saveCandidate(); }}>
          <fieldset disabled={busyAction !== null}>
            <legend>编辑候选</legend>
            <label><span>名称</span><input ref={nameInputRef} className="form-input" value={draft.name} maxLength={200} onChange={(event) => setDraft((current) => ({ ...current, name: event.target.value }))} /></label>
            <label><span>类型</span><select className="form-select" value={draft.typeKey} disabled={!relationalMode || busyAction !== null} onChange={(event) => changeType(event.target.value)}><option value="">请选择类型</option>{(loreTypes.length ? loreTypes.map((item) => [item.key, item.display_name] as const) : TYPE_OPTIONS).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select>{!relationalMode && <small>兼容资料模式下保留当前类型，不能切换。</small>}</label>
            <label><span>摘要</span><textarea className="form-textarea" value={draft.summary} maxLength={2000} onChange={(event) => setDraft((current) => ({ ...current, summary: event.target.value }))} /></label>

            <section className="lore-edit-fields" aria-label="类型字段">
              <h3>类型字段</h3>
              {fields.map((field) => {
                const state = draft.fieldStates[field.key] ?? "unknown";
                return <div className="lore-edit-field" key={field.key}>
                  <label><span>{field.label}</span><textarea className="form-textarea" value={draft.payload[field.key] ?? ""} disabled={state === "unknown"} onChange={(event) => setDraft((current) => ({ ...current, payload: { ...current.payload, [field.key]: event.target.value } }))} /></label>
                  <label><span>{field.label}的信息状态</span><select className="form-select" value={state} onChange={(event) => changeFieldState(field.key, event.target.value as LoreFieldState)}><option value="provided">已确认有内容</option><option value="unknown">信息为空</option><option value="needs_confirmation">待确认</option></select></label>
                  {field.help && <small>{field.help}</small>}
                </div>;
              })}
            </section>

            {candidate.duplicate_conflict_suggestions.length > 0 && <section className="lore-suggestions"><h3>重复或冲突提示</h3>{candidate.duplicate_conflict_suggestions.map((suggestion) => <label key={suggestion.suggestion_id}><span>{SUGGESTION_KIND[suggestion.kind] || "需要人工确认"}{suggestion.target_name ? `：${suggestion.target_name}` : ""}</span><select className="form-select" value={draft.resolutions[suggestion.suggestion_id] ?? "deferred"} onChange={(event) => setDraft((current) => ({ ...current, resolutions: { ...current.resolutions, [suggestion.suggestion_id]: event.target.value as LoreSuggestionResolution } }))}>{SUGGESTION_RESOLUTION.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>)}</section>}
          </fieldset>
          <div className="lore-candidate-actions"><button className="btn btn-primary" type="submit" disabled={busyAction !== null}>{busyAction === "save" ? "保存中…" : "保存修改"}</button><button className="btn btn-secondary" type="button" disabled={busyAction !== null} onClick={cancelEditing}>取消</button></div>
        </form>
      ) : (
        <>
          {candidate.disabled_reasons.length > 0 && <section className="lore-reasons"><h3>接纳前需要确认</h3><ul>{candidate.disabled_reasons.map((reason) => <li key={reason}>{DISABLED_REASON[reason] || "其他待确认问题"}</li>)}</ul></section>}
          <section className="lore-candidate-fields"><h3>候选字段</h3>{fields.length ? <dl className="lore-fields">{fields.map((field) => <div key={field.key}><dt>{field.label}<span>{FIELD_STATE[candidate.field_states[field.key]] || "状态待确认"}</span></dt><dd>{valueText(candidate.payload[field.key])}</dd></div>)}</dl> : <p>当前类型没有额外字段。</p>}</section>
          {candidate.duplicate_conflict_suggestions.length > 0 && <section className="lore-suggestions"><h3>重复或冲突提示</h3>{candidate.duplicate_conflict_suggestions.map((suggestion) => <article key={suggestion.suggestion_id}><strong>{SUGGESTION_KIND[suggestion.kind] || "需要人工确认"}{suggestion.target_name ? `：${suggestion.target_name}` : ""}</strong><span>{SUGGESTION_RESOLUTION.find(([value]) => value === candidate.suggestion_resolutions[suggestion.suggestion_id])?.[1] || "尚未处理"}</span></article>)}</section>}
          <section className="lore-candidate-action-panel" aria-label="候选审核操作">
            <div className="lore-candidate-actions">
              <button className="btn btn-secondary" type="button" disabled={!canEdit || busyAction !== null} onClick={startEditing}>编辑候选</button>
              {candidateAcceptEnabled && <button ref={acceptButtonRef} className="btn btn-primary" type="button" disabled={!canAccept || busyAction !== null} onClick={() => setConfirmAction("accept")}>接纳为正式设定</button>}
              <button ref={rejectButtonRef} className="btn btn-secondary" type="button" disabled={!canReject || busyAction !== null} onClick={() => setConfirmAction("reject")}>拒绝并保留记录</button>
            </div>
            {(!canEdit || (candidateAcceptEnabled && !canAccept) || !canReject) && <div className="lore-action-reasons">
              {!canEdit && <p>暂不能编辑：{(editReasons.length ? editReasons : [!hasEditableLegacyType ? "legacy_type_uneditable" : typesError || "权威字段定义尚未就绪"]).map((reason) => DISABLED_REASON[reason] || reason).join("；")}</p>}
              {candidateAcceptEnabled && !canAccept && <p>暂不能接纳：{acceptReasons.map((reason) => DISABLED_REASON[reason] || reason).join("；")}</p>}
              {!canReject && <p>暂不能拒绝：{rejectReasons.map((reason) => DISABLED_REASON[reason] || reason).join("；")}</p>}
            </div>}
          </section>
        </>
      )}

      {confirmAction && <section className="lore-confirm" role="alertdialog" aria-labelledby="lore-confirm-title" tabIndex={-1} ref={confirmRef}>
        <h3 id="lore-confirm-title">{confirmAction === "accept" ? "确认接纳为正式设定？" : "确认拒绝此候选？"}</h3>
        <p>{confirmAction === "accept" ? `“${candidate.name || "未命名候选"}”将成为正式设定并保留原文出处；关系建议不会自动建立。` : "拒绝不会删除原文和审计记录，但当前版本不支持撤销拒绝。"}</p>
        <div className="lore-candidate-actions"><button className={`btn ${confirmAction === "accept" ? "btn-primary" : "btn-secondary"}`} type="button" onClick={() => void performAction(confirmAction)}>{confirmAction === "accept" ? "确认接纳" : "确认拒绝"}</button><button className="btn btn-secondary" type="button" onClick={cancelConfirmation}>取消</button></div>
      </section>}

      {preservedDraft && <details className="lore-preserved-draft"><summary>查看冲突前保留的本地草稿</summary><textarea className="form-textarea" readOnly value={JSON.stringify(preservedDraft, null, 2)} aria-label="冲突前本地草稿" /></details>}

      <section className="lore-sources"><h3>原文证据与人工修订</h3>{candidate.evidence.length ? candidate.evidence.map((evidence) => <article key={evidence.id}><strong>{evidence.label || "设定字段"} · {VALUE_ORIGIN[evidence.value_origin] || "来源待确认"} · {FIELD_STATE[evidence.current_state] || "状态待确认"}</strong><p>{evidence.excerpt || "原文没有提供可确认内容"}</p>{evidence.value_origin === "user_override" && <small>当前人工确认值：{evidence.current_value || "信息为空"}</small>}{evidence.value_origin === "user_cleared" && <small>用户已明确清空当前值；原文摘录仍保留。</small>}</article>) : <p>暂无原文证据，不能作为正式设定接纳。</p>}</section>
    </div>
  );
}
