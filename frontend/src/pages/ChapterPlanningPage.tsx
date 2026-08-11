import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import PlanningStructurePanel, { type PlanningSelection } from "@/components/PlanningStructurePanel";
import PlanningLoreAssignments from "@/components/PlanningLoreAssignments";
import { useAuth } from "@/components/AuthContext";
import { ApiError, api } from "@/services/api";
import {
  clearPendingPlanningOperation,
  createPlanningOperationKey,
  loadPendingPlanningOperation,
  savePendingPlanningOperation,
  shouldKeepPlanningOperation,
  type PendingPlanningOperation,
  type PlanningOperationAction,
} from "@/services/planningOperations";
import type { LoreElementListItem } from "@/types/lore";
import type {
  NovelPlan,
  PlanningAssignmentCreateInput,
  PlanningAssignmentScopeResponse,
  PlanningAssignmentSnapshot,
  PlanningAssignmentStateInput,
  PlanningChapter,
  PlanningChapterCreateInput,
  PlanningChapterUpdateInput,
  PlanningNodeStateInput,
  PlanningOperationReceipt,
  PlanningPart,
  PlanningPartCreateInput,
  PlanningPartUpdateInput,
  PlanningReorderInput,
  PlanningScopeSnapshot,
  PlanningScopeType,
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

function selectionScope(selected: PlanningSelection, projectId: string): { scopeType: PlanningScopeType; scopeTargetId: string } {
  return {
    scopeType: selected.kind,
    scopeTargetId: selected.kind === "novel" ? projectId : selected.id,
  };
}

function scopeIdentity(scopeType: PlanningScopeType, scopeTargetId: string): string {
  return `${scopeType}:${scopeTargetId}`;
}

function receiptMatchesPending(
  receipt: PlanningOperationReceipt,
  operation: PendingPlanningOperation,
  projectId: string
): boolean {
  const assignment = operation.action.startsWith("assignment_");
  return receipt.project_id === projectId
    && receipt.operation_key === operation.operation_key
    && receipt.operation_type === operation.action
    && receipt.receipt_kind === (assignment ? "assignment" : "structure");
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
  const [assignmentConflict, setAssignmentConflict] = useState(false);
  const [assignmentResponse, setAssignmentResponse] = useState<PlanningAssignmentScopeResponse | null>(null);
  const [assignmentLoading, setAssignmentLoading] = useState(false);
  const [assignmentError, setAssignmentError] = useState("");
  const [assignmentRefreshRequired, setAssignmentRefreshRequired] = useState(false);
  const [assignmentSearchRefreshToken, setAssignmentSearchRefreshToken] = useState(0);
  const [pendingStorageIssue, setPendingStorageIssue] = useState<"corrupt" | "unavailable" | null>(null);
  const [serverSyncToken, setServerSyncToken] = useState(0);
  const [focusTarget, setFocusTarget] = useState<string | null>(null);
  const [assignmentFocusTarget, setAssignmentFocusTarget] = useState<{ elementId: string; scopeIdentity: string } | null>(null);
  const [refreshRequired, setRefreshRequired] = useState(false);
  const [hasUnsavedStructureDraft, setHasUnsavedStructureDraft] = useState(false);
  const [hasUnsavedPartCreationDraft, setHasUnsavedPartCreationDraft] = useState(false);
  const conflictRef = useRef<HTMLDivElement | null>(null);
  const assignmentConflictRef = useRef<HTMLDivElement | null>(null);
  const requestGeneration = useRef(0);
  const assignmentGeneration = useRef(0);
  const selectionRef = useRef<PlanningSelection>({ kind: "novel", id: id ?? "" });
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
  selectionRef.current = selection;
  const planningWriteDisabled = busy || !!pending || maintenance || conflict || assignmentConflict
    || refreshRequired || assignmentRefreshRequired || !!pendingStorageIssue;
  const assignmentWriteDisabled = planningWriteDisabled || assignmentLoading || !!assignmentError;

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

  const loadAssignmentScope = useCallback(async (
    selected: PlanningSelection,
    generation: number,
    signal?: AbortSignal,
    showLoading = true
  ): Promise<boolean> => {
    const projectId = id;
    if (!projectId) return false;
    const scope = selectionScope(selected, projectId);
    if (showLoading) setAssignmentLoading(true);
    setAssignmentError("");
    try {
      const value = await api.getPlanningLoreAssignments(projectId, scope.scopeType, scope.scopeTargetId, signal);
      if (generation !== assignmentGeneration.current) return false;
      if (scopeIdentity(selectionRef.current.kind, selectionRef.current.kind === "novel" ? projectId : selectionRef.current.id)
        !== scopeIdentity(scope.scopeType, scope.scopeTargetId)) return false;
      setAssignmentResponse(value);
      return true;
    } catch (cause) {
      if (signal?.aborted || generation !== assignmentGeneration.current) return false;
      setAssignmentError(errorMessage(cause));
      if (cause instanceof ApiError && cause.status === 503) setMaintenance(true);
      return false;
    } finally {
      if (generation === assignmentGeneration.current) setAssignmentLoading(false);
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
    setAssignmentConflict(false);
    setAssignmentResponse(null);
    setAssignmentLoading(false);
    setAssignmentError("");
    setAssignmentRefreshRequired(false);
    setAssignmentSearchRefreshToken(0);
    setPendingStorageIssue(null);
    assignmentGeneration.current += 1;
    setRefreshRequired(false);
    setBusy(false);
    setAssignmentFocusTarget(null);
    setHasUnsavedStructureDraft(false);
    setHasUnsavedPartCreationDraft(false);
    setMobileDetail(!!searchParams.get("target"));
    void loadPlan(true, generation);
  }, [id, user?.id]);

  useEffect(() => {
    if (!id || loadState !== "ready" || !plan) return;
    const controller = new AbortController();
    const generation = ++assignmentGeneration.current;
    setAssignmentResponse(null);
    void loadAssignmentScope(selection, generation, controller.signal);
    return () => controller.abort();
  }, [id, loadState, plan?.structure_version, selection.kind, selection.id]);

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

  useEffect(() => { if (error) conflictRef.current?.focus(); }, [error]);
  useEffect(() => { if (assignmentConflict) assignmentConflictRef.current?.focus(); }, [assignmentConflict]);

  useEffect(() => {
    if (!assignmentFocusTarget || !assignmentResponse) return;
    const currentScope = selectionScope(selectionRef.current, id ?? "");
    if (assignmentFocusTarget.scopeIdentity !== scopeIdentity(currentScope.scopeType, currentScope.scopeTargetId)) {
      setAssignmentFocusTarget(null);
      return;
    }
    window.setTimeout(() => {
      const card = document.querySelector<HTMLElement>(`.planning-assignment-card[data-element-id="${CSS.escape(assignmentFocusTarget.elementId)}"] h5`);
      (card ?? document.querySelector<HTMLElement>(".planning-assignments h3"))?.focus();
      setAssignmentFocusTarget(null);
    }, 0);
  }, [assignmentFocusTarget, assignmentResponse, id]);

  useEffect(() => {
    if (!id || !user || loadState !== "ready") return;
    const loaded = loadPendingPlanningOperation(user.id, id);
    if (loaded.status === "missing") { setPending(null); setPendingStorageIssue(null); return; }
    if (loaded.status === "corrupt" || loaded.status === "unavailable") {
      setPending(null);
      setPendingStorageIssue(loaded.status);
      setError(loaded.status === "corrupt"
        ? "检测到损坏或不受支持的规划恢复记录，已安全停止全部规划写入。"
        : "浏览器会话存储当前不可用，无法保证写入可恢复；已安全停止全部规划写入。");
      return;
    }
    const stored = loaded.operation;
    setPendingStorageIssue(null);
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
      .then(async (receipt) => {
        if (generation !== requestGeneration.current) return;
        if (!receiptMatchesPending(receipt, stored, id)) {
          setPending(null);
          setPendingStorageIssue("corrupt");
          setError("服务器返回的操作收据与本地恢复记录不一致，已安全停止全部规划写入。");
          return;
        }
        clearPendingPlanningOperation(user.id, id);
        setPending(null);
        const refreshed = stored.action.startsWith("assignment_")
          ? await refreshConfirmedAssignmentOperation(stored, generation)
          : await loadPlan(false, generation);
        if (generation !== requestGeneration.current) return;
        if (refreshed) {
          setNotice("已找回上次操作结果，并重新载入最新规划与分配。");
        } else {
          if (stored.action.startsWith("assignment_")) setAssignmentRefreshRequired(true);
          else setRefreshRequired(true);
          setError("操作结果已确认，但权威数据尚未完整读取；已暂停新的写入。");
        }
      })
      .catch((cause) => {
        if (generation !== requestGeneration.current) return;
        if (!(cause instanceof ApiError && cause.status === 404)) {
          setNotice("上次操作结果暂时无法核对，已暂停新的规划写入。");
        }
      });
  }, [id, user?.id, loadState]);

  function confirmEditorUnload(actionConfirmation?: string, includePartCreationDraft = false): boolean {
    const hasDraft = hasUnsavedStructureDraft || (includePartCreationDraft && hasUnsavedPartCreationDraft);
    const message = hasDraft
      ? `${includePartCreationDraft && hasUnsavedPartCreationDraft ? "当前还有尚未提交的结构草稿。" : "当前篇章或章节还有尚未保存的修改。"}${actionConfirmation ? ` 继续此操作将放弃这些修改。${actionConfirmation}` : " 离开将放弃这些修改，是否继续？"}`
      : actionConfirmation;
    return !message || window.confirm(message);
  }

  useEffect(() => {
    if (!hasUnsavedStructureDraft && !hasUnsavedPartCreationDraft) return;
    const preventUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", preventUnload);
    return () => window.removeEventListener("beforeunload", preventUnload);
  }, [hasUnsavedStructureDraft, hasUnsavedPartCreationDraft]);

  function selectScope(next: PlanningSelection) {
    const current = selectionRef.current;
    const changingScope = current.kind !== next.kind || current.id !== next.id;
    if (changingScope && !confirmEditorUnload()) return;
    setSearchParams(next.kind === "novel" ? {} : { scope: next.kind, target: next.id });
    setMobileDetail(true);
  }

  function navigateToAssignmentScope(scope: PlanningScopeSnapshot) {
    const next: PlanningSelection = scope.scope_type === "novel"
      ? { kind: "novel", id: id ?? scope.scope_target_id }
      : { kind: scope.scope_type, id: scope.scope_target_id };
    selectScope(next);
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

  async function refreshPlanningAndAssignmentScope(
    scopeType: PlanningScopeType,
    scopeTargetId: string,
    generation = requestGeneration.current
  ): Promise<boolean> {
    if (!id) return false;
    const projectId = id;
    try {
      for (let attempt = 0; attempt < 2; attempt += 1) {
        const [latestPlan, latestAssignments] = await Promise.all([
          api.getPlanning(projectId),
          api.getPlanningLoreAssignments(projectId, scopeType, scopeTargetId),
        ]);
        if (generation !== requestGeneration.current) return false;
        if (latestPlan.assignment_version !== latestAssignments.assignment_version) continue;
        const currentScope = selectionScope(selectionRef.current, projectId);
        let currentAssignments = latestAssignments;
        if (scopeIdentity(currentScope.scopeType, currentScope.scopeTargetId) !== scopeIdentity(scopeType, scopeTargetId)) {
          currentAssignments = await api.getPlanningLoreAssignments(projectId, currentScope.scopeType, currentScope.scopeTargetId);
          if (generation !== requestGeneration.current) return false;
          const selectedAfterRead = selectionScope(selectionRef.current, projectId);
          if (scopeIdentity(selectedAfterRead.scopeType, selectedAfterRead.scopeTargetId)
            !== scopeIdentity(currentScope.scopeType, currentScope.scopeTargetId)) continue;
          if (latestPlan.assignment_version !== currentAssignments.assignment_version) continue;
        }
        setPlan(latestPlan);
        setLoadState("ready");
        setMaintenance(false);
        setAssignmentRefreshRequired(false);
        assignmentGeneration.current += 1;
        setAssignmentResponse(currentAssignments);
        setAssignmentError("");
        setAssignmentLoading(false);
        return true;
      }
      setAssignmentError("规划版本与设定分配版本尚未同步，请重新读取。");
      return false;
    } catch (cause) {
      if (generation !== requestGeneration.current) return false;
      setAssignmentError(errorMessage(cause));
      if (cause instanceof ApiError && cause.status === 503) setMaintenance(true);
      return false;
    }
  }

  async function refreshConfirmedAssignmentOperation(
    operation: PendingPlanningOperation,
    generation = requestGeneration.current
  ): Promise<boolean> {
    const payload = operation.payload as Record<string, unknown>;
    const scopeType = payload.scope_type as PlanningScopeType;
    const scopeTargetId = payload.scope_target_id as string;
    return refreshPlanningAndAssignmentScope(scopeType, scopeTargetId, generation);
  }

  async function reloadPlanningAndCurrentAssignments(): Promise<void> {
    if (!id) return;
    const scope = selectionScope(selectionRef.current, id);
    const refreshed = await refreshPlanningAndAssignmentScope(scope.scopeType, scope.scopeTargetId);
    if (refreshed) {
      setRefreshRequired(false);
      setAssignmentRefreshRequired(false);
      setError("");
      setAssignmentError("");
      setNotice("已重新载入最新规划与设定分配。");
    } else {
      setAssignmentRefreshRequired(true);
      setError("权威规划与设定分配尚未完整读取，继续保持禁写。");
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
    action: PlanningOperationAction,
    targetId: string | null,
    payload: T,
    request: (body: T) => Promise<unknown>,
    success: string,
    focusAfter?: string
  ) {
    if (!id || !user || planningWriteDisabled) return;
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
      setPendingStorageIssue("unavailable");
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

  async function handleAssignmentWriteError(
    cause: unknown,
    scopeType: PlanningScopeType,
    scopeTargetId: string,
    generation = requestGeneration.current
  ) {
    setAssignmentError(errorMessage(cause));
    if (!(cause instanceof ApiError)) return;
    if (cause.status === 503) {
      setMaintenance(true);
      return;
    }
    if (cause.recommendedAction === "open_source_scope") {
      const source = cause.context.source_scope;
      if (source && typeof source === "object") {
        const typed = source as Partial<PlanningScopeSnapshot>;
        if ((typed.scope_type === "novel" || typed.scope_type === "part" || typed.scope_type === "chapter")
          && typeof typed.scope_target_id === "string" && typeof typed.title === "string"
          && (typed.status === "active" || typed.status === "archived")) {
          navigateToAssignmentScope(typed as PlanningScopeSnapshot);
        }
      }
      return;
    }
    const shouldRefresh = cause.status === 409 || cause.recommendedAction === "refresh_assignments";
    if (!shouldRefresh) return;
    const refreshed = await refreshPlanningAndAssignmentScope(scopeType, scopeTargetId, generation);
    if (generation !== requestGeneration.current) return;
    if (!refreshed) {
      setAssignmentRefreshRequired(true);
      setAssignmentConflict(false);
      setAssignmentError("分配状态发生变化，但权威规划与分配读取失败；已保持禁写。");
      return;
    }
    const resolvedByRefresh = [
      "PLANNING_ASSIGNMENT_EXISTS",
      "PLANNING_ASSIGNMENT_REMOVED",
      "PLANNING_ASSIGNMENT_ACTIVE",
      "PLANNING_ASSIGNMENT_NOT_FOUND",
    ].includes(cause.code ?? "");
    const elementVersionConflict = cause.code === "PLANNING_ELEMENT_VERSION_CONFLICT"
      || cause.recommendedAction === "review_lore_element";
    setAssignmentConflict(!resolvedByRefresh);
    if (resolvedByRefresh) {
      setAssignmentError("");
      setNotice(`${cause.detail} 已载入当前记录。`);
    } else {
      if (elementVersionConflict) setAssignmentSearchRefreshToken((value) => value + 1);
      setAssignmentError(`${cause.detail} 已载入服务器最新分配，请核对后继续。`);
    }
  }

  async function executeAssignment<T extends PlanningAssignmentCreateInput | PlanningAssignmentStateInput>(
    action: "assignment_create" | "assignment_remove" | "assignment_restore",
    targetId: string | null,
    payload: T,
    request: (body: T) => Promise<unknown>,
    success: string,
    focusElementId: string
  ) {
    if (!id || !user || planningWriteDisabled) return;
    const operation: PendingPlanningOperation<T> = {
      schema_version: 1,
      user_id: user.id,
      project_id: id,
      operation_key: payload.operation_key,
      action,
      target_id: targetId,
      payload,
      created_at: new Date().toISOString(),
    };
    const generation = requestGeneration.current;
    if (!savePendingPlanningOperation(operation)) {
      setPendingStorageIssue("unavailable");
      setAssignmentError("浏览器无法安全保存操作恢复信息，已停止全部规划写入。请检查会话存储设置。");
      return;
    }
    setBusy(true);
    setAssignmentError("");
    try {
      await request(payload);
      clearPendingPlanningOperation(user.id, id);
      if (generation !== requestGeneration.current) return;
      setPending(null);
      const refreshed = await refreshPlanningAndAssignmentScope(payload.scope_type, payload.scope_target_id, generation);
      if (generation !== requestGeneration.current) return;
      if (!refreshed) {
        setAssignmentRefreshRequired(true);
        setAssignmentError("操作已确认，但最新规划与分配尚未完整载入。请只刷新列表，不要重复提交。");
        return;
      }
      setAssignmentFocusTarget({
        elementId: focusElementId,
        scopeIdentity: scopeIdentity(payload.scope_type, payload.scope_target_id),
      });
      setNotice(success);
    } catch (cause) {
      if (!shouldKeepPlanningOperation(cause)) {
        clearPendingPlanningOperation(user.id, id);
      } else if (generation === requestGeneration.current) {
        setPending(operation as PendingPlanningOperation);
      }
      if (generation !== requestGeneration.current) return;
      await handleAssignmentWriteError(cause, payload.scope_type, payload.scope_target_id, generation);
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
      assignment_create: () => api.createPlanningLoreAssignment(id, p as unknown as PlanningAssignmentCreateInput),
      assignment_remove: () => api.changePlanningLoreAssignmentState(id, target!, "remove", p as unknown as PlanningAssignmentStateInput),
      assignment_restore: () => api.changePlanningLoreAssignmentState(id, target!, "restore", p as unknown as PlanningAssignmentStateInput),
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
      const refreshed = action.startsWith("assignment_")
        ? await refreshConfirmedAssignmentOperation(pending, generation)
        : await loadPlan(false, generation);
      if (generation !== requestGeneration.current) return;
      if (!refreshed) {
        if (action.startsWith("assignment_")) setAssignmentRefreshRequired(true);
        else setRefreshRequired(true);
        setError("操作已确认，但权威数据尚未完整读取；已暂停新的写入。");
        return;
      }
      setNotice("上次未确认的操作已使用原操作编号安全完成。");
    } catch (cause) {
      if (generation !== requestGeneration.current) return;
      if (action.startsWith("assignment_")) {
        await handleAssignmentWriteError(
          cause,
          p.scope_type as PlanningScopeType,
          p.scope_target_id as string,
          generation
        );
      } else {
        await handleWriteError(cause, generation);
      }
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

  function assignmentScopeLabel(): string {
    if (!assignmentResponse) return "当前范围";
    if (assignmentResponse.scope.scope_type === "novel") return "整部小说";
    return `《${assignmentResponse.scope.title}》`;
  }

  function assignLoreElement(element: LoreElementListItem) {
    if (!id || !assignmentResponse) return;
    const body: PlanningAssignmentCreateInput = {
      operation_key: createPlanningOperationKey("assignment_create"),
      expected_assignment_version: assignmentResponse.assignment_version,
      element_id: element.id,
      expected_element_content_version: element.current_version,
      scope_type: assignmentResponse.scope.scope_type,
      scope_target_id: assignmentResponse.scope.scope_target_id,
    };
    void executeAssignment("assignment_create", element.id, body, (value) => api.createPlanningLoreAssignment(id, value), `《${element.name}》已加入${assignmentScopeLabel()}。`, element.id);
  }

  function changeLoreAssignment(assignment: PlanningAssignmentSnapshot, action: "remove" | "restore") {
    if (!id || !assignmentResponse) return;
    const body: PlanningAssignmentStateInput = {
      operation_key: createPlanningOperationKey(`assignment_${action}`),
      expected_assignment_version: assignmentResponse.assignment_version,
      expected_lock_version: assignment.lock_version,
      scope_type: assignmentResponse.scope.scope_type,
      scope_target_id: assignmentResponse.scope.scope_target_id,
    };
    const success = action === "remove"
      ? `《${assignment.element.name}》已从${assignmentScopeLabel()}移除；其他范围的直接分配未改变。`
      : `《${assignment.element.name}》已恢复为${assignmentScopeLabel()}的直接设定。`;
    void executeAssignment(`assignment_${action}`, assignment.id, body, (value) => api.changePlanningLoreAssignmentState(id, assignment.id, action, value), success, assignment.element_id);
  }

  function clearCorruptRecoveryRecord() {
    if (!id || !user || pendingStorageIssue !== "corrupt") return;
    if (!window.confirm("只清除这条损坏的浏览器会话恢复记录？不会删除任何小说、设定或服务器数据。")) return;
    if (clearPendingPlanningOperation(user.id, id)) {
      setPendingStorageIssue(null);
      setError("");
      setNotice("损坏的本地恢复记录已清除，可以重新开始操作。");
    } else {
      setError("浏览器会话存储仍不可用，无法清除损坏记录；继续保持禁写。");
    }
  }

  if (!id) return <div className="card empty-state" role="alert">项目地址无效。</div>;

  return (
    <div className="planning-page" aria-busy={loadState === "loading" || busy}>
      <button className="btn-back" onClick={() => confirmEditorUnload(undefined, true) && navigate(`/project/${id}`)}>← 返回项目</button>
      <header className="page-header planning-header">
        <div><h1>章节规划</h1><p>在生成正文前组织篇章、章节和使用范围。</p></div>
        <Link className="btn btn-secondary" to={`/project/${id}/lore`} onClick={(event) => { if (!confirmEditorUnload(undefined, true)) event.preventDefault(); }}>打开设定仓库</Link>
      </header>

      <div className="planning-live" aria-live="polite">{notice}</div>
      {error && <div className="planning-notice is-error" role="alert" tabIndex={-1} ref={conflictRef}><span>{error}{errorHint && <small className="planning-notice__hint">{errorHint}</small>}</span>{conflict ? <span className="planning-notice__actions"><button className="btn btn-secondary" onClick={() => { setServerSyncToken((value) => value + 1); setConflict(false); setError(""); setNotice("已载入服务器最新字段。"); }}>载入服务器最新值</button><button className="btn btn-secondary" onClick={() => { setConflict(false); setError(""); setNotice("旧草稿已保留；请与服务器最新值核对后再保存。"); }}>保留草稿并继续核对</button></span> : pendingStorageIssue === "corrupt" ? <button className="btn btn-secondary" onClick={clearCorruptRecoveryRecord}>确认清除损坏恢复记录</button> : (refreshRequired || assignmentRefreshRequired) ? <button className="btn btn-secondary" onClick={() => void reloadPlanningAndCurrentAssignments()}>重新读取规划与设定</button> : <button className="btn btn-secondary" onClick={() => loadPlan(false)}>刷新规划</button>}</div>}
      {assignmentConflict && <div ref={assignmentConflictRef} className="planning-notice is-error" role="alert" tabIndex={-1}><span>{assignmentError || "分配状态已更新，请核对服务器最新结果。"}</span><button className="btn btn-secondary" onClick={() => { setAssignmentConflict(false); setAssignmentError(""); setNotice("已核对服务器最新分配，可以继续操作。"); }}>已核对最新分配</button></div>}
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
            <div className="planning-section-heading"><h2>篇章结构</h2><CreatePartForm plan={plan} busy={planningWriteDisabled} onDirtyChange={setHasUnsavedPartCreationDraft} onCreate={(body) => execute("part_create", null, body, (value) => api.createPlanningPart(id, value), "篇章已创建。")} /></div>
            <PlanningStructurePanel plan={plan} selected={selection} busy={planningWriteDisabled} onSelect={selectScope} onMovePart={movePart} onMoveChapter={moveChapter} />
          </aside>
          <main className="card planning-workspace__detail">
            <button className="btn btn-secondary planning-mobile-back" onClick={returnToMobileStructure}>← 返回结构</button>
            {selection.kind === "novel" && <NovelDetail plan={plan} />}
            {selection.kind === "part" && located.part && (
              <PartDetail
                plan={plan}
                part={located.part}
                busy={planningWriteDisabled}
                serverSyncToken={serverSyncToken}
                onDirtyChange={setHasUnsavedStructureDraft}
                onUpdate={(body) => execute("part_update", located.part!.id, body, (value) => api.updatePlanningPart(id, located.part!.id, value), "篇章已保存。")}
                onState={(action, body) => {
                  if (action === "archive" && !confirmEditorUnload("归档后可恢复，确定继续吗？")) return;
                  void execute(`part_${action}`, located.part!.id, body, (value) => api.changePlanningPartState(id, located.part!.id, action, value), action === "archive" ? "篇章已归档。" : "篇章已恢复。", action === "archive" ? plan.project_id : located.part!.id);
                }}
                onCreateChapter={(body) => execute("chapter_create", located.part!.id, body, (value) => api.createPlanningChapter(id, located.part!.id, value), "章节已创建。")}
              />
            )}
            {selection.kind === "chapter" && located.part && located.chapter && (
              <ChapterDetail
                plan={plan}
                part={located.part}
                chapter={located.chapter}
                busy={planningWriteDisabled}
                serverSyncToken={serverSyncToken}
                onDirtyChange={setHasUnsavedStructureDraft}
                onUpdate={(body) => execute("chapter_update", located.chapter!.id, body, (value) => api.updatePlanningChapter(id, located.chapter!.id, value), "章节已保存。")}
                onState={(action, body) => {
                  if (action === "archive" && !confirmEditorUnload("归档后可恢复，确定继续吗？")) return;
                  void execute(`chapter_${action}`, located.chapter!.id, body, (value) => api.changePlanningChapterState(id, located.chapter!.id, action, value), action === "archive" ? "章节已归档。" : "章节已恢复。", action === "archive" ? located.part!.id : located.chapter!.id);
                }}
                onMove={(targetPartId) => moveChapterTo(located.chapter!, targetPartId)}
              />
            )}
            <PlanningLoreAssignments
              projectId={id}
              response={assignmentResponse}
              loading={assignmentLoading}
              error={assignmentConflict ? "" : assignmentError}
              writeDisabled={assignmentWriteDisabled}
              searchRefreshToken={assignmentSearchRefreshToken}
              onReload={() => {
                if (maintenance || refreshRequired || assignmentRefreshRequired) {
                  void reloadPlanningAndCurrentAssignments();
                  return;
                }
                const generation = ++assignmentGeneration.current;
                void loadAssignmentScope(selectionRef.current, generation);
              }}
              onNavigateScope={navigateToAssignmentScope}
              onOpenLore={() => confirmEditorUnload(undefined, true)}
              onAssign={assignLoreElement}
              onRemove={(assignment) => changeLoreAssignment(assignment, "remove")}
              onRestore={(assignment) => changeLoreAssignment(assignment, "restore")}
            />
          </main>
        </div>
      )}
    </div>
  );
}

function CreatePartForm({ plan, busy, onDirtyChange, onCreate }: { plan: NovelPlan; busy: boolean; onDirtyChange: (dirty: boolean) => void; onCreate: (body: PlanningPartCreateInput) => void }) {
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState("");
  const dirty = open && title.trim().length > 0;
  useEffect(() => { onDirtyChange(dirty); }, [dirty, onDirtyChange]);
  useEffect(() => () => onDirtyChange(false), [onDirtyChange]);
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
      <p>选择左侧篇章或章节进行编辑和排序；下方可以管理整部小说直接使用的设定。</p>
    </section>
  );
}

function PartDetail({ plan, part, busy, serverSyncToken, onDirtyChange, onUpdate, onState, onCreateChapter }: { plan: NovelPlan; part: PlanningPart; busy: boolean; serverSyncToken: number; onDirtyChange: (dirty: boolean) => void; onUpdate: (body: PlanningPartUpdateInput) => void; onState: (action: "archive" | "restore", body: PlanningNodeStateInput) => void; onCreateChapter: (body: PlanningChapterCreateInput) => void }) {
  const [title, setTitle] = useState(part.title); const [description, setDescription] = useState(part.description); const [chapterTitle, setChapterTitle] = useState("");
  useEffect(() => { setTitle(part.title); setDescription(part.description); setChapterTitle(""); }, [part.id, serverSyncToken]);
  const dirty = title !== part.title || description !== part.description || chapterTitle.trim().length > 0;
  useEffect(() => { onDirtyChange(dirty); }, [dirty, onDirtyChange]);
  useEffect(() => () => onDirtyChange(false), [onDirtyChange]);
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
          <button className="btn btn-secondary" disabled={busy || part.chapters.length > 0} onClick={() => onState("archive", { operation_key: createPlanningOperationKey("part_archive"), expected_structure_version: plan.structure_version })}>归档篇章</button>
        ) : (
          <button className="btn btn-secondary" disabled={busy} onClick={() => onState("restore", { operation_key: createPlanningOperationKey("part_restore"), expected_structure_version: plan.structure_version })}>恢复篇章</button>
        )}
      </div>
    </section>
  );
}

function ChapterDetail({ plan, part, chapter, busy, serverSyncToken, onDirtyChange, onUpdate, onState, onMove }: { plan: NovelPlan; part: PlanningPart; chapter: PlanningChapter; busy: boolean; serverSyncToken: number; onDirtyChange: (dirty: boolean) => void; onUpdate: (body: PlanningChapterUpdateInput) => void; onState: (action: "archive" | "restore", body: PlanningNodeStateInput) => void; onMove: (targetPartId: string) => void }) {
  const [title, setTitle] = useState(chapter.title); const [summary, setSummary] = useState(chapter.summary); const [wordCount, setWordCount] = useState(chapter.target_word_count?.toString() ?? ""); const [targetPart, setTargetPart] = useState(chapter.part_id);
  useEffect(() => { setTitle(chapter.title); setSummary(chapter.summary); setWordCount(chapter.target_word_count?.toString() ?? ""); setTargetPart(chapter.part_id); }, [chapter.id, chapter.part_id, serverSyncToken]);
  const targetValue = wordCount ? Number(wordCount) : null;
  const dirty = title !== chapter.title
    || summary !== chapter.summary
    || targetValue !== chapter.target_word_count
    || targetPart !== chapter.part_id;
  useEffect(() => { onDirtyChange(dirty); }, [dirty, onDirtyChange]);
  useEffect(() => () => onDirtyChange(false), [onDirtyChange]);
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
        {chapter.status === "active" ? <button className="btn btn-secondary" disabled={busy} onClick={() => onState("archive", { operation_key: createPlanningOperationKey("chapter_archive"), expected_structure_version: plan.structure_version })}>归档章节</button> : <button className="btn btn-secondary" disabled={busy || part.status === "archived"} onClick={() => onState("restore", { operation_key: createPlanningOperationKey("chapter_restore"), expected_structure_version: plan.structure_version })}>恢复章节</button>}
      </div>
    </section>
  );
}
