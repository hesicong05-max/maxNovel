import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import PlanningStructurePanel, { type PlanningSelection } from "@/components/PlanningStructurePanel";
import { useAuth } from "@/components/AuthContext";
import { ApiError, api } from "@/services/api";
import {
  clearPendingPlanningOperation,
  createPlanningOperationKey,
  loadPendingPlanningOperation,
  savePendingPlanningOperation,
  shouldKeepPlanningOperation,
  type PendingPlanningOperation,
} from "@/services/planningOperations";
import type {
  NovelPlan,
  PlanningChapter,
  PlanningChapterCreateInput,
  PlanningChapterUpdateInput,
  PlanningNodeStateInput,
  PlanningPart,
  PlanningPartCreateInput,
  PlanningPartUpdateInput,
  PlanningReorderInput,
} from "@/types/planning";

type LoadState = "loading" | "ready" | "uninitialized" | "migration" | "legacy" | "error";

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.detail;
  return error instanceof Error ? error.message : "操作失败，请稍后重试。";
}

function recoveryHint(error: ApiError): string {
  const hints: Record<string, string> = {
    retry_later: "请等待维护结束后重试。",
    refresh_planning: "请刷新规划并核对最新结构。",
    review_current_node: "服务器已有更新，请核对最新节点后再保存。",
    move_chapters_first: "请先移动或处理该篇章下的全部章节。",
    restore_parent: "请先恢复所属篇章，再恢复章节。",
    remove_assignments_first: "该范围仍有活动设定分配；请在设定分配功能中移除本级分配后重试。",
  };
  return error.recommendedAction ? hints[error.recommendedAction] ?? "请根据提示核对后重试。" : "";
}

function activeReorder(plan: NovelPlan): PlanningReorderInput["parts"] {
  return plan.parts
    .filter((part) => part.status === "active")
    .map((part) => ({
      part_id: part.id,
      chapter_ids: part.chapters
        .filter((chapter) => chapter.status === "active")
        .map((chapter) => chapter.id),
    }));
}

function locate(plan: NovelPlan, selected: PlanningSelection) {
  if (selected.kind === "part") {
    return { part: plan.parts.find((part) => part.id === selected.id) ?? null, chapter: null };
  }
  if (selected.kind === "chapter") {
    for (const part of plan.parts) {
      const chapter = part.chapters.find((item) => item.id === selected.id);
      if (chapter) return { part, chapter };
    }
  }
  return { part: null, chapter: null };
}

export default function ChapterPlanningPage() {
  const { id } = useParams<{ id: string }>();
  const { user } = useAuth();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [plan, setPlan] = useState<NovelPlan | null>(null);
  const [loadState, setLoadState] = useState<LoadState>("loading");
  const [error, setError] = useState("");
  const [errorHint, setErrorHint] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [maintenance, setMaintenance] = useState(false);
  const [pending, setPending] = useState<PendingPlanningOperation | null>(null);
  const [mobileDetail, setMobileDetail] = useState(() => !!searchParams.get("target"));
  const [conflict, setConflict] = useState(false);
  const [serverSyncToken, setServerSyncToken] = useState(0);
  const [focusTarget, setFocusTarget] = useState<string | null>(null);
  const [refreshRequired, setRefreshRequired] = useState(false);
  const conflictRef = useRef<HTMLDivElement | null>(null);
  const requestGeneration = useRef(0);
  const planRef = useRef<NovelPlan | null>(null);
  planRef.current = plan;

  const selection = useMemo<PlanningSelection>(() => {
    const kind = searchParams.get("scope");
    const target = searchParams.get("target");
    if (plan && target && (kind === "part" || kind === "chapter")) {
      const candidate = { kind, id: target } as PlanningSelection;
      const found = locate(plan, candidate);
      if ((kind === "part" && found.part) || (kind === "chapter" && found.chapter)) return candidate;
    }
    return { kind: "novel", id: id ?? "" };
  }, [id, plan, searchParams]);

  const located = useMemo(() => plan ? locate(plan, selection) : { part: null, chapter: null }, [plan, selection]);

  const loadPlan = useCallback(async (showLoading = true, generation = requestGeneration.current): Promise<boolean> => {
    const projectId = id;
    if (!projectId) return false;
    if (showLoading) setLoadState("loading");
    setError("");
    setErrorHint("");
    try {
      const value = await api.getPlanning(projectId);
      if (generation !== requestGeneration.current) return false;
      setPlan(value);
      setLoadState("ready");
      setMaintenance(false);
      setRefreshRequired(false);
      return true;
    } catch (cause) {
      if (generation !== requestGeneration.current) return false;
      if (cause instanceof ApiError && cause.code === "PLANNING_NOT_INITIALIZED") {
        setPlan(null);
        setLoadState("uninitialized");
      } else if (cause instanceof ApiError && cause.code === "PLANNING_LORE_MIGRATION_REQUIRED") {
        setLoadState("migration");
      } else if (cause instanceof ApiError && cause.code === "PLANNING_LEGACY_IMPORT_REQUIRED") {
        setLoadState("legacy");
      } else {
        setError(errorMessage(cause));
        if (cause instanceof ApiError) {
          setErrorHint(recoveryHint(cause));
          if (cause.status === 503) setMaintenance(true);
        }
        if (!planRef.current) setLoadState("error");
        else setLoadState("ready");
      }
      return false;
    }
  }, [id]);

  useEffect(() => {
    const generation = ++requestGeneration.current;
    setPlan(null);
    setPending(null);
    setNotice("");
    setError("");
    setErrorHint("");
    setMaintenance(false);
    setConflict(false);
    setRefreshRequired(false);
    setBusy(false);
    setMobileDetail(!!searchParams.get("target"));
    void loadPlan(true, generation);
  }, [id, user?.id]);

  useEffect(() => {
    if (!mobileDetail) return;
    window.setTimeout(() => {
      document.querySelector<HTMLElement>(".planning-workspace__detail h2")?.focus();
    }, 0);
  }, [mobileDetail, selection.kind, selection.id]);

  useEffect(() => {
    if (!focusTarget || !plan) return;
    window.setTimeout(() => {
      if (mobileDetail) {
        document.querySelector<HTMLElement>(".planning-workspace__detail h2")?.focus();
        setFocusTarget(null);
        return;
      }
      const node = document.querySelector<HTMLButtonElement>(`.planning-node[data-node-id="${CSS.escape(focusTarget)}"]`);
      (node ?? document.querySelector<HTMLButtonElement>(".planning-node"))?.focus();
      setFocusTarget(null);
    }, 0);
  }, [focusTarget, plan, mobileDetail]);

  useEffect(() => { if (conflict && error) conflictRef.current?.focus(); }, [conflict, error]);

  useEffect(() => {
    if (!id || !user || loadState !== "ready") return;
    const stored = loadPendingPlanningOperation(user.id, id);
    if (!stored) { setPending(null); return; }
    if (stored.user_id !== user.id || stored.project_id !== id) { setPending(null); return; }
    if ((stored.payload as { operation_key?: unknown }).operation_key !== stored.operation_key) {
      clearPendingPlanningOperation(user.id, id);
      setPending(null);
      setError("检测到载荷不一致的恢复记录，已安全停止重试。");
      return;
    }
    const generation = requestGeneration.current;
    setPending(stored);
    void api.getPlanningOperation(id, stored.operation_key)
      .then(async () => {
        if (generation !== requestGeneration.current) return;
        clearPendingPlanningOperation(user.id, id);
        setPending(null);
        const refreshed = await loadPlan(false, generation);
        if (generation !== requestGeneration.current) return;
        if (refreshed) {
          setNotice("已找回上次操作结果，并重新载入最新规划。");
        } else {
          setRefreshRequired(true);
          setError("操作结果已确认，但最新规划尚未读取；已暂停新的写入。");
        }
      })
      .catch((cause) => {
        if (generation !== requestGeneration.current) return;
        if (!(cause instanceof ApiError && cause.status === 404)) {
          setNotice("上次操作结果暂时无法核对，已暂停新的规划写入。");
        }
      });
  }, [id, user?.id, loadState]);

  function selectScope(next: PlanningSelection) {
    setSearchParams(next.kind === "novel" ? {} : { scope: next.kind, target: next.id });
    setMobileDetail(true);
  }

  function returnToMobileStructure() {
    setMobileDetail(false);
    window.setTimeout(() => {
      const current = document.querySelector<HTMLButtonElement>(".planning-node[aria-current='true']");
      current?.focus();
    }, 0);
  }

  async function initialize() {
    if (!id || busy) return;
    const projectId = id;
    const generation = requestGeneration.current;
    setBusy(true);
    setError("");
    setErrorHint("");
    try {
      const initialized = await api.initializePlanning(projectId);
      if (generation !== requestGeneration.current) return;
      setPlan(initialized);
      setLoadState("ready");
      setNotice("空白章节规划已创建，可以新建第一个篇章。");
    } catch (cause) {
      if (generation !== requestGeneration.current) return;
      const apiError = cause instanceof ApiError ? cause : null;
      if (apiError?.code === "PLANNING_LORE_MIGRATION_REQUIRED") setLoadState("migration");
      else if (apiError?.code === "PLANNING_LEGACY_IMPORT_REQUIRED") setLoadState("legacy");
      else if (apiError?.status === 503) setMaintenance(true);
      setError(errorMessage(cause));
    } finally {
      if (generation === requestGeneration.current) setBusy(false);
    }
  }

  async function handleWriteError(cause: unknown, generation = requestGeneration.current) {
    setError(errorMessage(cause));
    if (!(cause instanceof ApiError)) return;
    setErrorHint(recoveryHint(cause));
    if (cause.status === 503) setMaintenance(true);
    if (cause.code === "PLANNING_LORE_MIGRATION_REQUIRED") setLoadState("migration");
    if (cause.code === "PLANNING_LEGACY_IMPORT_REQUIRED") setLoadState("legacy");
    if (
      cause.code?.includes("VERSION_CONFLICT") ||
      cause.recommendedAction === "refresh_planning" ||
      cause.recommendedAction === "review_current_node"
    ) {
      const isVersionConflict = cause.code?.includes("VERSION_CONFLICT") === true;
      const refreshed = await loadPlan(false);
      if (generation !== requestGeneration.current) return;
      if (isVersionConflict) {
        if (refreshed) {
          setError(errorMessage(cause));
          setErrorHint(recoveryHint(cause));
          setConflict(true);
        } else {
          setConflict(false);
          setRefreshRequired(true);
          setError("检测到版本冲突，但最新规划读取失败；已保持禁写。");
          setErrorHint("请只重新载入最新规划，不要重复提交原写入。");
        }
      } else if (!refreshed) {
        setRefreshRequired(true);
      }
    }
  }

  async function execute<T extends object>(
    action: string,
    targetId: string | null,
    payload: T,
    request: (body: T) => Promise<unknown>,
    success: string,
    focusAfter?: string
  ) {
    if (!id || !user || busy || pending || maintenance || refreshRequired || conflict) return;
    const operation: PendingPlanningOperation<T> = {
      schema_version: 1,
      user_id: user.id,
      project_id: id,
      operation_key: (payload as { operation_key?: string }).operation_key ?? createPlanningOperationKey(action),
      action,
      target_id: targetId,
      payload,
      created_at: new Date().toISOString(),
    };
    const generation = requestGeneration.current;
    if (!savePendingPlanningOperation(operation)) {
      setError("浏览器无法安全保存操作恢复信息，已停止写入。请检查会话存储设置。");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const result = await request(payload) as { affected_node?: { id?: string } | null };
      clearPendingPlanningOperation(user.id, id);
      if (generation !== requestGeneration.current) return;
      setPending(null);
      const refreshed = await loadPlan(false, generation);
      if (generation !== requestGeneration.current) return;
      if (!refreshed) {
        setRefreshRequired(true);
        setError("操作已成功，但最新规划暂时无法读取；已暂停新的写入，请只刷新规划，不要重复提交。 ");
        setErrorHint("服务端已确认本次操作，请勿重试原写入。");
        return;
      }
      if (action === "part_archive") {
        setSearchParams({});
        setMobileDetail(false);
      } else if (action === "chapter_archive" && focusAfter) {
        setSearchParams({ scope: "part", target: focusAfter });
        setMobileDetail(false);
      }
      setFocusTarget(focusAfter ?? result.affected_node?.id ?? targetId);
      setNotice(success);
    } catch (cause) {
      if (!shouldKeepPlanningOperation(cause)) {
        clearPendingPlanningOperation(user.id, id);
      } else if (generation === requestGeneration.current) {
        setPending(operation as PendingPlanningOperation);
      }
      if (generation !== requestGeneration.current) return;
      await handleWriteError(cause, generation);
      if (generation !== requestGeneration.current) return;
    } finally {
      if (generation === requestGeneration.current) setBusy(false);
    }
  }

  async function retryPending() {
    if (!id || !user || !pending || busy || refreshRequired) return;
    const payloadKey = (pending.payload as { operation_key?: unknown }).operation_key;
    if (
      pending.project_id !== id ||
      pending.user_id !== user.id ||
      payloadKey !== pending.operation_key
    ) {
      clearPendingPlanningOperation(user.id, id);
      setPending(null);
      setError("检测到不属于当前项目或载荷不一致的恢复记录，已安全停止重试。");
      return;
    }
    const p = pending.payload as Record<string, unknown>;
    const generation = requestGeneration.current;
    const action = pending.action;
    const target = pending.target_id;
    const handlers: Record<string, () => Promise<unknown>> = {
      part_create: () => api.createPlanningPart(id, p as unknown as PlanningPartCreateInput),
      part_update: () => api.updatePlanningPart(id, target!, p as unknown as PlanningPartUpdateInput),
      part_archive: () => api.changePlanningPartState(id, target!, "archive", p as unknown as PlanningNodeStateInput),
      part_restore: () => api.changePlanningPartState(id, target!, "restore", p as unknown as PlanningNodeStateInput),
      chapter_create: () => api.createPlanningChapter(id, target!, p as unknown as PlanningChapterCreateInput),
      chapter_update: () => api.updatePlanningChapter(id, target!, p as unknown as PlanningChapterUpdateInput),
      chapter_archive: () => api.changePlanningChapterState(id, target!, "archive", p as unknown as PlanningNodeStateInput),
      chapter_restore: () => api.changePlanningChapterState(id, target!, "restore", p as unknown as PlanningNodeStateInput),
      structure_reorder: () => api.reorderPlanningStructure(id, p as unknown as PlanningReorderInput),
    };
    const handler = handlers[action];
    if (!handler) return;
    setBusy(true);
    setError("");
    try {
      await handler();
      clearPendingPlanningOperation(user.id, id);
      if (generation !== requestGeneration.current) return;
      setPending(null);
      const refreshed = await loadPlan(false, generation);
      if (generation !== requestGeneration.current) return;
      if (!refreshed) {
        setRefreshRequired(true);
        setError("操作已确认，但最新规划暂时无法读取；已暂停新的写入。");
        return;
      }
      setNotice("上次未确认的操作已使用原操作编号安全完成。");
    } catch (cause) {
      if (generation !== requestGeneration.current) return;
      await handleWriteError(cause, generation);
      if (generation !== requestGeneration.current) return;
      if (!shouldKeepPlanningOperation(cause)) {
        clearPendingPlanningOperation(user.id, id);
        setPending(null);
      }
    } finally {
      if (generation === requestGeneration.current) setBusy(false);
    }
  }

  function reorder(parts: PlanningReorderInput["parts"], success: string, focusId: string) {
    if (!plan) return;
    const body: PlanningReorderInput = {
      operation_key: createPlanningOperationKey("structure_reorder"),
      expected_structure_version: plan.structure_version,
      parts,
    };
    void execute("structure_reorder", focusId, body, (value) => api.reorderPlanningStructure(id!, value), success);
  }

  function movePart(partId: string, direction: -1 | 1) {
    if (!plan) return;
    const parts = activeReorder(plan);
    const index = parts.findIndex((part) => part.part_id === partId);
    const target = index + direction;
    if (index < 0 || target < 0 || target >= parts.length) return;
    [parts[index], parts[target]] = [parts[target], parts[index]];
    reorder(parts, "篇章顺序已更新。", partId);
  }

  function moveChapter(chapterId: string, direction: -1 | 1) {
    if (!plan) return;
    const parts = activeReorder(plan);
    const part = parts.find((item) => item.chapter_ids.includes(chapterId));
    if (!part) return;
    const index = part.chapter_ids.indexOf(chapterId);
    const target = index + direction;
    if (target < 0 || target >= part.chapter_ids.length) return;
    [part.chapter_ids[index], part.chapter_ids[target]] = [part.chapter_ids[target], part.chapter_ids[index]];
    reorder(parts, "章节顺序已更新。", chapterId);
  }

  function moveChapterTo(chapter: PlanningChapter, targetPartId: string) {
    if (!plan || chapter.part_id === targetPartId) return;
    const parts = activeReorder(plan);
    for (const part of parts) part.chapter_ids = part.chapter_ids.filter((idValue) => idValue !== chapter.id);
    parts.find((part) => part.part_id === targetPartId)?.chapter_ids.push(chapter.id);
    reorder(parts, "章节已移动到目标篇章末尾。", chapter.id);
  }

  if (!id) return <div className="card empty-state" role="alert">项目地址无效。</div>;

  return (
    <div className="planning-page" aria-busy={loadState === "loading" || busy}>
      <button className="btn-back" onClick={() => navigate(`/project/${id}`)}>← 返回项目</button>
      <header className="page-header planning-header">
        <div><h1>章节规划</h1><p>在生成正文前组织篇章、章节和使用范围。</p></div>
        <Link className="btn btn-secondary" to={`/project/${id}/lore`}>打开设定仓库</Link>
      </header>

      <div className="planning-live" aria-live="polite">{notice}</div>
      {error && <div className="planning-notice is-error" role="alert" tabIndex={-1} ref={conflictRef}><span>{error}{errorHint && <small className="planning-notice__hint">{errorHint}</small>}</span>{conflict ? <span className="planning-notice__actions"><button className="btn btn-secondary" onClick={() => { setServerSyncToken((value) => value + 1); setConflict(false); setError(""); setNotice("已载入服务器最新字段。"); }}>载入服务器最新值</button><button className="btn btn-secondary" onClick={() => { setConflict(false); setError(""); setNotice("旧草稿已保留；请与服务器最新值核对后再保存。"); }}>保留草稿并继续核对</button></span> : <button className="btn btn-secondary" onClick={() => loadPlan(false)}>刷新规划</button>}</div>}
      {maintenance && <div className="planning-notice" role="status">项目资料正在维护；已保留当前只读内容并暂停写入。</div>}
      {pending && (
        <div className="planning-notice" role="alert">
          <span>检测到结果尚未确认的操作，已暂停新的写入。</span>
          <button className="btn btn-secondary" disabled={busy} onClick={retryPending}>使用原操作编号安全重试</button>
        </div>
      )}

      {loadState === "loading" && <div className="card empty-state">正在加载章节规划…</div>}
      {loadState === "error" && <div className="card empty-state"><h2>规划暂时无法加载</h2><button className="btn btn-primary" onClick={() => loadPlan()}>重新加载</button></div>}
      {loadState === "uninitialized" && (
        <section className="card empty-state"><h2>创建空白章节规划</h2><p>系统不会生成大纲，也不会覆盖现有正文。你可以自行建立篇章和章节。</p><button className="btn btn-primary" disabled={busy} onClick={initialize}>{busy ? "正在创建…" : "创建章节规划"}</button></section>
      )}
      {loadState === "migration" && (
        <section className="card empty-state"><h2>请先升级设定仓库</h2><p>章节规划只会引用已确认的模块化设定。</p><Link className="btn btn-primary" to={`/project/${id}/lore?migration=preview`}>打开设定仓库</Link></section>
      )}
      {loadState === "legacy" && (
        <section className="card empty-state"><h2>检测到历史章节资料</h2><p>系统不会自动迁移或覆盖旧大纲、章节正文和故事记忆。</p><Link className="btn btn-primary" to={`/project/${id}`}>返回项目继续兼容流程</Link></section>
      )}

      {loadState === "ready" && plan && (
        <div className={`planning-workspace${mobileDetail ? " show-detail" : ""}`}>
          <aside className="card planning-workspace__tree">
            <div className="planning-section-heading"><h2>篇章结构</h2><CreatePartForm plan={plan} busy={busy || !!pending || maintenance || conflict || refreshRequired} onCreate={(body) => execute("part_create", null, body, (value) => api.createPlanningPart(id, value), "篇章已创建。")} /></div>
            <PlanningStructurePanel plan={plan} selected={selection} busy={busy || !!pending || maintenance || conflict || refreshRequired} onSelect={selectScope} onMovePart={movePart} onMoveChapter={moveChapter} />
          </aside>
          <main className="card planning-workspace__detail">
            <button className="btn btn-secondary planning-mobile-back" onClick={returnToMobileStructure}>← 返回结构</button>
            {selection.kind === "novel" && <NovelDetail plan={plan} />}
            {selection.kind === "part" && located.part && (
              <PartDetail
                plan={plan}
                part={located.part}
                busy={busy || !!pending || maintenance || conflict || refreshRequired}
                serverSyncToken={serverSyncToken}
                onUpdate={(body) => execute("part_update", located.part!.id, body, (value) => api.updatePlanningPart(id, located.part!.id, value), "篇章已保存。")}
                onState={(action, body) => execute(`part_${action}`, located.part!.id, body, (value) => api.changePlanningPartState(id, located.part!.id, action, value), action === "archive" ? "篇章已归档。" : "篇章已恢复。", action === "archive" ? plan.project_id : located.part!.id)}
                onCreateChapter={(body) => execute("chapter_create", located.part!.id, body, (value) => api.createPlanningChapter(id, located.part!.id, value), "章节已创建。")}
              />
            )}
            {selection.kind === "chapter" && located.part && located.chapter && (
              <ChapterDetail
                plan={plan}
                part={located.part}
                chapter={located.chapter}
                busy={busy || !!pending || maintenance || conflict || refreshRequired}
                serverSyncToken={serverSyncToken}
                onUpdate={(body) => execute("chapter_update", located.chapter!.id, body, (value) => api.updatePlanningChapter(id, located.chapter!.id, value), "章节已保存。")}
                onState={(action, body) => execute(`chapter_${action}`, located.chapter!.id, body, (value) => api.changePlanningChapterState(id, located.chapter!.id, action, value), action === "archive" ? "章节已归档。" : "章节已恢复。", action === "archive" ? located.part!.id : located.chapter!.id)}
                onMove={(targetPartId) => moveChapterTo(located.chapter!, targetPartId)}
              />
            )}
          </main>
        </div>
      )}
    </div>
  );
}

function CreatePartForm({ plan, busy, onCreate }: { plan: NovelPlan; busy: boolean; onCreate: (body: PlanningPartCreateInput) => void }) {
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState("");
  function submit(event: FormEvent) {
    event.preventDefault();
    if (!title.trim()) return;
    onCreate({ operation_key: createPlanningOperationKey("part_create"), expected_structure_version: plan.structure_version, title: title.trim(), description: "" });
    setTitle(""); setOpen(false);
  }
  if (!open) return <button className="btn btn-secondary" disabled={busy} onClick={() => setOpen(true)}>新建篇章</button>;
  return <form className="planning-inline-form" onSubmit={submit}><label>篇章名称<input autoFocus value={title} maxLength={200} onChange={(event) => setTitle(event.target.value)} /></label><div><button className="btn btn-primary" disabled={busy || !title.trim()}>创建</button><button type="button" className="btn btn-secondary" onClick={() => setOpen(false)}>取消</button></div></form>;
}

function NovelDetail({ plan }: { plan: NovelPlan }) {
  const activeParts = plan.parts.filter((part) => part.status === "active");
  const activeChapters = activeParts.flatMap((part) => part.chapters.filter((chapter) => chapter.status === "active"));
  return (
    <section>
      <h2 tabIndex={-1}>整部小说</h2>
      <p className="planning-scope-meta">结构版本 {plan.structure_version} · 设定分配版本 {plan.assignment_version}</p>
      <div className="planning-summary">
        <div><strong>{activeParts.length}</strong><span>活动篇章</span></div>
        <div><strong>{activeChapters.length}</strong><span>活动章节</span></div>
      </div>
      <p>选择左侧篇章或章节进行编辑和排序。设定分配将在下一连续交付中接入。</p>
    </section>
  );
}

function PartDetail({ plan, part, busy, serverSyncToken, onUpdate, onState, onCreateChapter }: { plan: NovelPlan; part: PlanningPart; busy: boolean; serverSyncToken: number; onUpdate: (body: PlanningPartUpdateInput) => void; onState: (action: "archive" | "restore", body: PlanningNodeStateInput) => void; onCreateChapter: (body: PlanningChapterCreateInput) => void }) {
  const [title, setTitle] = useState(part.title); const [description, setDescription] = useState(part.description); const [chapterTitle, setChapterTitle] = useState("");
  useEffect(() => { setTitle(part.title); setDescription(part.description); }, [part.id, serverSyncToken]);
  const activeCount = part.chapters.filter((item) => item.status === "active").length;
  const archivedCount = part.chapters.length - activeCount;
  return (
    <section>
      <h2 tabIndex={-1}>{part.title}</h2>
      <p className="planning-scope-meta">篇章 · {part.status === "active" ? "使用中" : "已归档"}</p>
      <form className="planning-editor" onSubmit={(event) => { event.preventDefault(); onUpdate({ operation_key: createPlanningOperationKey("part_update"), expected_structure_version: plan.structure_version, expected_lock_version: part.lock_version, title: title.trim(), description }); }}>
        <label>篇章名称<input value={title} maxLength={200} disabled={busy || part.status === "archived"} onChange={(event) => setTitle(event.target.value)} /></label>
        <label>篇章说明<textarea value={description} maxLength={10000} disabled={busy || part.status === "archived"} onChange={(event) => setDescription(event.target.value)} /></label>
        {part.status === "active" && <button className="btn btn-primary" disabled={busy || !title.trim()}>保存篇章</button>}
      </form>
      {part.status === "active" && (
        <form className="planning-inline-form planning-create-chapter" onSubmit={(event) => { event.preventDefault(); if (!chapterTitle.trim()) return; onCreateChapter({ operation_key: createPlanningOperationKey("chapter_create"), expected_structure_version: plan.structure_version, title: chapterTitle.trim(), summary: "", target_word_count: null }); setChapterTitle(""); }}>
          <label>新章节名称<input value={chapterTitle} maxLength={200} disabled={busy} onChange={(event) => setChapterTitle(event.target.value)} /></label>
          <button className="btn btn-secondary" disabled={busy || !chapterTitle.trim()}>添加章节</button>
        </form>
      )}
      <div className="planning-danger-zone">
        {part.status === "active" && part.chapters.length > 0 && (
          <p className="planning-blocker" role="status">当前含 {activeCount} 个活动章节、{archivedCount} 个已归档章节。请先移动或处理全部章节，才能归档篇章。</p>
        )}
        {part.status === "active" ? (
          <button className="btn btn-secondary" disabled={busy || part.chapters.length > 0} onClick={() => window.confirm("归档后可恢复，确定归档这个篇章吗？") && onState("archive", { operation_key: createPlanningOperationKey("part_archive"), expected_structure_version: plan.structure_version })}>归档篇章</button>
        ) : (
          <button className="btn btn-secondary" disabled={busy} onClick={() => onState("restore", { operation_key: createPlanningOperationKey("part_restore"), expected_structure_version: plan.structure_version })}>恢复篇章</button>
        )}
      </div>
    </section>
  );
}

function ChapterDetail({ plan, part, chapter, busy, serverSyncToken, onUpdate, onState, onMove }: { plan: NovelPlan; part: PlanningPart; chapter: PlanningChapter; busy: boolean; serverSyncToken: number; onUpdate: (body: PlanningChapterUpdateInput) => void; onState: (action: "archive" | "restore", body: PlanningNodeStateInput) => void; onMove: (targetPartId: string) => void }) {
  const [title, setTitle] = useState(chapter.title); const [summary, setSummary] = useState(chapter.summary); const [wordCount, setWordCount] = useState(chapter.target_word_count?.toString() ?? ""); const [targetPart, setTargetPart] = useState(chapter.part_id);
  useEffect(() => { setTitle(chapter.title); setSummary(chapter.summary); setWordCount(chapter.target_word_count?.toString() ?? ""); setTargetPart(chapter.part_id); }, [chapter.id, serverSyncToken]);
  const targetValue = wordCount ? Number(wordCount) : null;
  return (
    <section>
      <h2 tabIndex={-1}>{chapter.title}</h2>
      <p className="planning-scope-meta">章节 · {part.title} · {chapter.status === "active" ? "使用中" : "已归档"}</p>
      <form className="planning-editor" onSubmit={(event) => { event.preventDefault(); onUpdate({ operation_key: createPlanningOperationKey("chapter_update"), expected_structure_version: plan.structure_version, expected_lock_version: chapter.lock_version, title: title.trim(), summary, ...(targetValue === null ? { clear_target_word_count: true } : { target_word_count: targetValue }) }); }}>
        <label>章节名称<input value={title} maxLength={200} disabled={busy || chapter.status === "archived"} onChange={(event) => setTitle(event.target.value)} /></label>
        <label>章节摘要<textarea value={summary} maxLength={20000} disabled={busy || chapter.status === "archived"} onChange={(event) => setSummary(event.target.value)} /></label>
        <label>目标字数（可选，500–10000）<input type="number" min={500} max={10000} value={wordCount} disabled={busy || chapter.status === "archived"} onChange={(event) => setWordCount(event.target.value)} /></label>
        {chapter.status === "active" && <button className="btn btn-primary" disabled={busy || !title.trim() || (targetValue !== null && (targetValue < 500 || targetValue > 10000))}>保存章节</button>}
      </form>
      {chapter.status === "active" && <div className="planning-move"><label>移动至篇章<select value={targetPart} disabled={busy} onChange={(event) => setTargetPart(event.target.value)}>{plan.parts.filter((item) => item.status === "active").map((item) => <option key={item.id} value={item.id}>{item.title}</option>)}</select></label><button className="btn btn-secondary" disabled={busy || targetPart === chapter.part_id} onClick={() => onMove(targetPart)}>移动到目标篇章末尾</button></div>}
      <div className="planning-danger-zone">
        {chapter.status === "archived" && part.status === "archived" && <p className="planning-blocker" role="status">请先恢复所属篇章，再恢复此章节。</p>}
        {chapter.status === "active" ? <button className="btn btn-secondary" disabled={busy} onClick={() => window.confirm("归档后可恢复，确定归档这个章节吗？") && onState("archive", { operation_key: createPlanningOperationKey("chapter_archive"), expected_structure_version: plan.structure_version })}>归档章节</button> : <button className="btn btn-secondary" disabled={busy || part.status === "archived"} onClick={() => onState("restore", { operation_key: createPlanningOperationKey("chapter_restore"), expected_structure_version: plan.structure_version })}>恢复章节</button>}
      </div>
    </section>
  );
}
