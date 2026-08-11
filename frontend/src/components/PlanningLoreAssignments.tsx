import { FormEvent, KeyboardEvent, useEffect, useId, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError, api } from "@/services/api";
import type { LoreElementListItem } from "@/types/lore";
import type {
  PlanningAssignmentHistoryResponse,
  PlanningAssignmentScopeResponse,
  PlanningAssignmentSnapshot,
  PlanningAssignmentSource,
  PlanningEffectiveElement,
  PlanningScopeSnapshot,
} from "@/types/planning";

type SectionKey = "direct" | "inherited" | "ineligible" | "removed";

interface AssignmentCardModel {
  elementId: string;
  element: PlanningEffectiveElement["element"];
  effective: PlanningEffectiveElement | null;
  activeDirect: PlanningAssignmentSnapshot | null;
  removedDirect: PlanningAssignmentSnapshot | null;
  section: SectionKey;
}

interface Props {
  projectId: string;
  response: PlanningAssignmentScopeResponse | null;
  loading: boolean;
  error: string;
  writeDisabled: boolean;
  searchRefreshToken: number;
  onReload: () => void;
  onNavigateScope: (scope: PlanningScopeSnapshot) => void;
  onOpenLore: () => boolean;
  onAssign: (element: LoreElementListItem) => void;
  onRemove: (assignment: PlanningAssignmentSnapshot) => void;
  onRestore: (assignment: PlanningAssignmentSnapshot) => void;
}

const sectionLabels: Record<SectionKey, string> = {
  direct: "可用于生成 · 本范围直接",
  inherited: "可用于生成 · 从上级继承",
  ineligible: "当前失效 · 不参与生成",
  removed: "已从本范围移除",
};

const reasonLabels: Record<string, string> = {
  element_candidate: "设定尚未确认",
  element_rejected: "设定已被拒绝",
  confirmation_not_confirmed: "设定尚未确认",
  confirmation_rejected: "设定已被拒绝",
  element_archived: "设定已归档",
  element_merged: "设定已合并",
  element_disabled: "设定已暂停用于生成",
  fields_need_confirmation: "部分字段仍待确认",
  type_archived: "设定类型已归档",
  scope_archived: "当前范围已归档",
};

function errorText(error: unknown): string {
  if (error instanceof ApiError) return error.detail;
  return error instanceof Error ? error.message : "请求失败，请稍后重试。";
}

function scopeText(scope: PlanningScopeSnapshot): string {
  if (scope.scope_type === "novel") return "整部小说";
  if (scope.scope_type === "part") return `篇章《${scope.title}》`;
  return `章节《${scope.title}》`;
}

function uniqueSources(sources: PlanningAssignmentSource[]): PlanningAssignmentSource[] {
  const order = { novel: 0, part: 1, chapter: 2 } as const;
  return [...new Map(sources.map((source) => [source.assignment_id, source])).values()]
    .sort((left, right) => order[left.scope.scope_type] - order[right.scope.scope_type]);
}

function bucket(response: PlanningAssignmentScopeResponse): Record<SectionKey, AssignmentCardModel[]> {
  const effective = new Map(response.effective_elements.map((item) => [item.element_id, item]));
  const direct = new Map<string, PlanningAssignmentSnapshot[]>();
  for (const item of response.direct_assignments) {
    direct.set(item.element_id, [...(direct.get(item.element_id) ?? []), item]);
  }
  const ids = new Set([...effective.keys(), ...direct.keys()]);
  const result: Record<SectionKey, AssignmentCardModel[]> = { direct: [], inherited: [], ineligible: [], removed: [] };
  for (const elementId of ids) {
    const effectiveItem = effective.get(elementId) ?? null;
    const directItems = direct.get(elementId) ?? [];
    const activeDirect = directItems.find((item) => item.status === "active") ?? null;
    const removedDirect = directItems.find((item) => item.status === "removed") ?? null;
    const element = effectiveItem?.element ?? activeDirect?.element ?? removedDirect?.element;
    if (!element) continue;
    const section: SectionKey = effectiveItem && !effectiveItem.generation_eligible
      ? "ineligible"
      : effectiveItem && activeDirect
        ? "direct"
        : effectiveItem
          ? "inherited"
          : "removed";
    result[section].push({ elementId, element, effective: effectiveItem, activeDirect, removedDirect, section });
  }
  for (const key of Object.keys(result) as SectionKey[]) {
    result[key].sort((left, right) => left.element.name.localeCompare(right.element.name, "zh-CN"));
  }
  return result;
}

function emptyMessage(section: SectionKey, scope: PlanningScopeSnapshot): string {
  if (section === "direct") return "当前范围还没有可用于生成的直接设定。你可以添加设定，也可能继续使用上级继承项。";
  if (section === "inherited") return scope.scope_type === "novel"
    ? "整部小说没有上级范围。"
    : "当前范围没有从上级继承的可用设定。";
  if (section === "ineligible") return "当前没有失效设定。";
  return "当前范围没有已移除的直接设定记录。";
}

export default function PlanningLoreAssignments({
  projectId,
  response,
  loading,
  error,
  writeDisabled,
  searchRefreshToken,
  onReload,
  onNavigateScope,
  onOpenLore,
  onAssign,
  onRemove,
  onRestore,
}: Props) {
  const sections = useMemo(() => response ? bucket(response) : null, [response]);
  const [searchOpen, setSearchOpen] = useState(false);
  const addButtonRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    setSearchOpen(false);
  }, [response?.scope.scope_type, response?.scope.scope_target_id]);

  if (loading && !response) return <section className="planning-assignments" aria-busy="true"><p>正在载入当前范围的设定…</p></section>;
  if (error && !response) {
    return <section className="planning-assignments"><div className="planning-assignment-error" role="alert"><span>当前范围的设定暂时无法加载。{error}</span><button className="btn btn-secondary" onClick={onReload}>重新加载设定</button></div></section>;
  }
  if (!response || !sections) return null;

  const activeDirectIds = new Set(response.direct_assignments.filter((item) => item.status === "active").map((item) => item.element_id));
  const removedByElement = new Map(response.direct_assignments.filter((item) => item.status === "removed").map((item) => [item.element_id, item]));
  const inheritedIds = new Set(response.effective_elements.filter((item) => item.inherited_from.length > 0).map((item) => item.element_id));

  return (
    <section className="planning-assignments" aria-busy={loading}>
      <div className="planning-assignment-heading">
        <div>
          <h3>本范围使用的设定</h3>
          <p>当前正在编辑：{scopeText(response.scope)}。移除只影响本范围的直接来源。</p>
        </div>
        <button ref={addButtonRef} className="btn btn-secondary" disabled={writeDisabled || response.scope.status === "archived"} onClick={() => setSearchOpen(true)}>添加设定</button>
      </div>
      <div className="planning-assignment-counts" aria-label="设定使用概况">
        <span>可用直接 {sections.direct.length}</span>
        <span>可用继承 {sections.inherited.length}</span>
        <span>当前失效 {sections.ineligible.length}</span>
        <span>仅移除记录 {sections.removed.length}</span>
      </div>
      {error && <div className="planning-assignment-error" role="alert"><span>设定列表刷新失败，当前显示上次成功读取的内容。{error}</span><button className="btn btn-secondary" onClick={onReload}>重新加载设定</button></div>}
      {response.scope.status === "archived" && <p className="planning-assignment-warning" role="status">当前范围已归档：可以查看并移除现有直接分配，但不能新增或恢复。</p>}
      {searchOpen && (
        <AssignmentSearch
          projectId={projectId}
          activeDirectIds={activeDirectIds}
          removedByElement={removedByElement}
          inheritedIds={inheritedIds}
          disabled={writeDisabled || response.scope.status === "archived"}
          refreshToken={searchRefreshToken}
          onAssign={onAssign}
          onRestore={onRestore}
          onClose={() => { setSearchOpen(false); window.setTimeout(() => addButtonRef.current?.focus(), 0); }}
        />
      )}
      {(Object.keys(sectionLabels) as SectionKey[]).map((key) => (
        <AssignmentSection
          key={key}
          section={key}
          title={sectionLabels[key]}
          items={sections[key]}
          scope={response.scope}
          projectId={projectId}
          writeDisabled={writeDisabled}
          onNavigateScope={onNavigateScope}
          onOpenLore={onOpenLore}
          onRemove={onRemove}
          onRestore={onRestore}
        />
      ))}
    </section>
  );
}

function AssignmentSection({ section, title, items, scope, projectId, writeDisabled, onNavigateScope, onOpenLore, onRemove, onRestore }: {
  section: SectionKey;
  title: string;
  items: AssignmentCardModel[];
  scope: PlanningScopeSnapshot;
  projectId: string;
  writeDisabled: boolean;
  onNavigateScope: (scope: PlanningScopeSnapshot) => void;
  onOpenLore: () => boolean;
  onRemove: (assignment: PlanningAssignmentSnapshot) => void;
  onRestore: (assignment: PlanningAssignmentSnapshot) => void;
}) {
  return (
    <section className={`planning-assignment-section is-${section}`}>
      <h4 tabIndex={-1}>{title}</h4>
      {items.length === 0 ? <p className="planning-assignment-empty">{emptyMessage(section, scope)}</p> : (
        <div className="planning-assignment-list">
          {items.map((item) => <AssignmentCard key={item.elementId} item={item} scope={scope} projectId={projectId} writeDisabled={writeDisabled} onNavigateScope={onNavigateScope} onOpenLore={onOpenLore} onRemove={onRemove} onRestore={onRestore} />)}
        </div>
      )}
    </section>
  );
}

function AssignmentCard({ item, scope, projectId, writeDisabled, onNavigateScope, onOpenLore, onRemove, onRestore }: {
  item: AssignmentCardModel;
  scope: PlanningScopeSnapshot;
  projectId: string;
  writeDisabled: boolean;
  onNavigateScope: (scope: PlanningScopeSnapshot) => void;
  onOpenLore: () => boolean;
  onRemove: (assignment: PlanningAssignmentSnapshot) => void;
  onRestore: (assignment: PlanningAssignmentSnapshot) => void;
}) {
  const [confirming, setConfirming] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const removeButtonRef = useRef<HTMLButtonElement | null>(null);
  const confirmTitleId = useId();
  const confirmImpactId = useId();
  const historyId = useId();
  const sources = uniqueSources(item.effective?.all_sources ?? []);
  const inherited = sources.filter((source) => source.scope.scope_type !== scope.scope_type || source.scope.scope_target_id !== scope.scope_target_id);
  const removedRestorable = !!item.removedDirect
    && item.removedDirect.ineligible_reasons.every((reason) => reason === "assignment_removed");
  const canRestore = !!item.removedDirect
    && (item.effective?.generation_eligible ?? removedRestorable)
    && scope.status === "active";
  const reasons = (item.effective?.ineligible_reasons ?? item.activeDirect?.ineligible_reasons ?? item.removedDirect?.ineligible_reasons ?? [])
    .filter((reason) => reason !== "assignment_removed");

  function cancelConfirm() {
    setConfirming(false);
    window.setTimeout(() => removeButtonRef.current?.focus(), 0);
  }

  const impact = scope.scope_type === "novel"
    ? "依赖此来源且没有其他来源的下级范围将不再继承；其他直接分配不变。"
    : scope.scope_type === "part"
      ? "该篇章下依赖此来源的章节将不再继承；其他范围不变。"
      : "只移除此章节的直接来源；上级和其他章节不变。";

  return (
    <article className="planning-assignment-card" data-element-id={item.elementId}>
      <header>
        <div><h5 tabIndex={-1}>{item.element.name}</h5><span>{item.element.type.display_name}</span></div>
        <strong>{item.effective?.generation_eligible ? "可用于生成" : "当前不参与生成"}</strong>
      </header>
      {item.element.summary && <p>{item.element.summary}</p>}
      <div className="planning-assignment-sources">
        {item.activeDirect && <span>本范围直接</span>}
        {inherited.map((source) => <span key={source.assignment_id}>来自{scopeText(source.scope)}</span>)}
        {!item.activeDirect && item.removedDirect && item.effective && <span>本范围直接记录已移除</span>}
      </div>
      {(item.effective?.content_changed_since_any_assignment || item.activeDirect?.content_changed_since_assignment) && <p className="planning-assignment-warning">分配后设定已有更新；后续使用当前内容。</p>}
      {reasons.length > 0 && <ul className="planning-assignment-reasons">{reasons.map((reason) => <li key={reason}>{reasonLabels[reason] ?? "当前状态不允许参与生成"}</li>)}</ul>}
      <div className="planning-assignment-actions">
        {item.activeDirect && <button ref={removeButtonRef} className="btn btn-secondary" disabled={writeDisabled} onClick={() => setConfirming(true)}>从本范围移除</button>}
        {item.effective && inherited.map((source) => <button key={source.assignment_id} className="btn btn-secondary" onClick={() => onNavigateScope(source.scope)}>前往{scopeText(source.scope)}调整</button>)}
        {item.removedDirect && !item.activeDirect && <button className="btn btn-secondary" disabled={writeDisabled || !canRestore} onClick={() => onRestore(item.removedDirect!)}>{canRestore ? "恢复到本范围" : "暂不能恢复"}</button>}
        <button className="btn btn-secondary" aria-expanded={historyOpen} aria-controls={historyId} onClick={() => setHistoryOpen((value) => !value)}>{historyOpen ? "收起分配历史" : "查看分配历史"}</button>
        <Link className="btn btn-secondary" to={`/project/${encodeURIComponent(projectId)}/lore?q=${encodeURIComponent(item.element.name)}`} onClick={(event) => { if (!onOpenLore()) event.preventDefault(); }}>在设定仓库中查找</Link>
      </div>
      {confirming && item.activeDirect && (
        <div className="planning-assignment-confirm" role="alertdialog" aria-labelledby={confirmTitleId} aria-describedby={confirmImpactId} onKeyDown={(event: KeyboardEvent<HTMLDivElement>) => { if (event.key === "Escape") cancelConfirm(); }}>
          <strong id={confirmTitleId}>确认从本范围移除《{item.element.name}》？</strong><p id={confirmImpactId}>{impact}</p>
          <div><button autoFocus className="btn btn-secondary" disabled={writeDisabled} onClick={() => { setConfirming(false); onRemove(item.activeDirect!); }}>确认从本范围移除</button><button className="btn btn-secondary" onClick={cancelConfirm}>取消</button></div>
        </div>
      )}
      {historyOpen && <div id={historyId}><AssignmentHistory projectId={projectId} elementId={item.elementId} /></div>}
    </article>
  );
}

function AssignmentSearch({ projectId, activeDirectIds, removedByElement, inheritedIds, disabled, refreshToken, onAssign, onRestore, onClose }: {
  projectId: string;
  activeDirectIds: Set<string>;
  removedByElement: Map<string, PlanningAssignmentSnapshot>;
  inheritedIds: Set<string>;
  disabled: boolean;
  refreshToken: number;
  onAssign: (element: LoreElementListItem) => void;
  onRestore: (assignment: PlanningAssignmentSnapshot) => void;
  onClose: () => void;
}) {
  const [query, setQuery] = useState("");
  const [items, setItems] = useState<LoreElementListItem[]>([]);
  const [state, setState] = useState<"idle" | "loading" | "ready" | "error">("idle");
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const generation = useRef(0);

  async function search(event?: FormEvent, append = false) {
    event?.preventDefault();
    const current = ++generation.current;
    setState("loading");
    try {
      const result = await api.listLoreElements(projectId, {
        q: query.trim() || undefined,
        confirmation_status: "confirmed",
        lifecycle_status: "active",
        enabled: true,
        cursor: append ? nextCursor ?? undefined : undefined,
        limit: 20,
      });
      if (current !== generation.current) return;
      setItems((previous) => append ? [...previous, ...result.items] : result.items);
      setNextCursor(result.next_cursor);
      setHasMore(result.has_more);
      setState("ready");
    } catch {
      if (current === generation.current) setState("error");
    }
  }

  useEffect(() => {
    if (refreshToken === 0) setQuery("");
    setNextCursor(null);
    setHasMore(false);
    void search();
    return () => { generation.current += 1; };
  }, [projectId, refreshToken]);

  return (
    <section className="planning-assignment-search">
      <div className="planning-assignment-heading"><div><h4>添加设定到当前范围</h4><p>只添加当前范围的直接来源，不会改动上级或其他章节。</p></div><button className="btn btn-secondary" onClick={onClose}>关闭</button></div>
      <form onSubmit={search}><label>搜索正式设定<input autoFocus value={query} onChange={(event) => setQuery(event.target.value)} /></label><button className="btn btn-secondary" disabled={state === "loading"}>搜索</button></form>
      {state === "loading" && <p>正在搜索设定…</p>}
      {state === "error" && <div className="planning-assignment-error" role="alert"><span>设定搜索暂时失败，搜索词已保留。</span><button className="btn btn-secondary" onClick={() => void search()}>重新搜索</button></div>}
      {state === "ready" && items.length === 0 && <p>没有找到符合条件的正式设定。</p>}
      {state === "ready" && items.length > 0 && <div className="planning-assignment-search-results">{items.map((item) => {
        const removed = removedByElement.get(item.id);
        const active = activeDirectIds.has(item.id);
        const eligible = item.generation_eligible;
        const label = active && !eligible ? "已在本范围 · 当前失效" : active ? "已在本范围" : removed ? "恢复到本范围" : inheritedIds.has(item.id) ? "设为本范围直接" : "加入当前范围";
        return <article key={item.id}><div><strong>{item.name}</strong><span>{item.type.display_name}</span><p>{item.summary || "暂无摘要"}</p>{!eligible && <small>当前不可分配：设定仍有待确认字段或类型状态限制。</small>}</div><button className="btn btn-secondary" disabled={disabled || active || !eligible} onClick={() => removed ? onRestore(removed) : onAssign(item)}>{active || eligible ? label : "当前不可分配"}</button></article>;
      })}{hasMore && <button className="btn btn-secondary" onClick={() => void search(undefined, true)}>显示更多设定</button>}</div>}
    </section>
  );
}

function AssignmentHistory({ projectId, elementId }: { projectId: string; elementId: string }) {
  const [data, setData] = useState<PlanningAssignmentHistoryResponse | null>(null);
  const [error, setError] = useState("");
  const [token, setToken] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setData(null); setError("");
    api.getPlanningLoreAssignmentHistory(projectId, elementId, controller.signal)
      .then(setData)
      .catch((cause) => { if (!controller.signal.aborted) setError(errorText(cause)); });
    return () => controller.abort();
  }, [projectId, elementId, token]);
  if (error) return <div className="planning-assignment-history" role="alert"><span>分配历史暂时无法加载。{error}</span><button className="btn btn-secondary" onClick={() => setToken((value) => value + 1)}>重试</button></div>;
  if (!data) return <div className="planning-assignment-history"><span>正在载入该设定的分配历史…</span></div>;
  if (data.assignments.length === 0) return <div className="planning-assignment-history"><span>该设定暂无分配历史。</span></div>;
  return <div className="planning-assignment-history"><strong>该设定的分配历史</strong>{data.assignments.map((assignment) => <article key={assignment.id}><span>{scopeText(assignment.scope)} · {assignment.status === "active" ? "当前使用中" : "当前已移除"}</span><ol>{assignment.events.map((event) => <li key={event.id}>{event.action === "assign" ? "加入" : event.action === "remove" ? "移除" : "恢复"} · {new Date(event.created_at).toLocaleString("zh-CN")}</li>)}</ol></article>)}</div>;
}
