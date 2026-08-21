import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { useAuth } from "@/components/AuthContext";
import { ApiError, api } from "@/services/api";
import {
  parseForeshadowHistory,
  parseForeshadowLifecycle,
  parseForeshadowList,
  parseForeshadowReceipt,
} from "@/services/foreshadowContracts";
import {
  clearPendingForeshadowOperation,
  createForeshadowOperationKey,
  loadPendingForeshadowOperation,
  pendingPayload,
  savePendingForeshadowOperation,
  type PendingForeshadowOperation,
} from "@/services/foreshadowOperations";
import { loadPendingGenerationExecution } from "@/services/generationExecution";
import { loadPendingTechnicalDemoExecution } from "@/services/technicalDemoExecution";
import { loadPendingCandidateManualEdit } from "@/services/candidateVersionOperations";
import { loadPendingCandidateSelection } from "@/services/candidateSelectionOperations";
import { clearPendingProjectOperationRecord, pendingProjectOperationKey } from "@/services/pendingProjectOperations";
import type { LoreElementListItem } from "@/types/lore";
import DemoGuide from "@/components/DemoGuide";
import { readDemoFixture } from "@/services/demoFixture";
import type { DemoFixtureCurrentResponse } from "@/types/demo";
import type {
  ForeshadowBindInput,
  ForeshadowFact,
  ForeshadowFactCreateInput,
  ForeshadowFactRetractInput,
  ForeshadowHistoryResponse,
  ForeshadowLifecycle,
  ForeshadowLifecycleInput,
  ForeshadowLifecycleStatus,
  ForeshadowListResponse,
  ForeshadowMutationReceipt,
  ForeshadowOperationType,
  ForeshadowPlanCreateInput,
  ForeshadowPlanItem,
  ForeshadowPlanStateInput,
  ForeshadowRestoreInput,
  ForeshadowState,
  ForeshadowWritePayload,
} from "@/types/foreshadow";
import type { NovelPlan, PlanningChapter, PlanningPart } from "@/types/planning";

const stateLabels: Record<ForeshadowState, string> = {
  unplanted: "未埋入",
  planted: "已埋入",
  pending_resolution: "待回收",
  resolved: "已回收",
};

const stateHelp: Record<ForeshadowState, string> = {
  unplanted: "尚无作者确认的埋入事实；可能已经存在未来计划。",
  planted: "作者已确认埋入，当前没有活动回收计划。",
  pending_resolution: "作者已确认埋入，并有活动回收计划，但尚未确认回收。",
  resolved: "作者已确认回收；系统尚未核对正文。",
};

const eventLabels: Record<string, string> = {
  create: "加入伏笔管理", archive: "归档", restore: "恢复",
  plan_create: "创建计划", plan_cancel: "取消计划", plan_restore: "恢复计划",
  fact_record: "记录作者确认事实", fact_retract: "撤回作者确认事实",
};

type RecoveryState = "idle" | "checking" | "not_found" | "corrupt";
type ConfirmAction = { title: string; body: string; confirmLabel: string; run: () => void };
type CommittedForeshadowAction = { sequence: number; type: ForeshadowOperationType; lifecycleId: string; resourceId: string | null };

function message(error: unknown): string {
  return error instanceof ApiError ? error.detail : error instanceof Error ? error.message : "操作失败，请稍后重试。";
}

function recommendedHint(error: ApiError): string {
  const hints: Record<string, string> = {
    retry_later: "请等待维护结束后，先核对原操作结果。",
    refresh_foreshadows: "请重新载入伏笔列表并核对最新状态。",
    refresh_foreshadow: "请重新载入当前伏笔并核对版本。",
    select_active_target: "请选择仍在使用的篇章或章节。",
    select_later_chapter: "请选择晚于埋入位置的章节。",
    adjust_resolve_plan: "请先调整计划回收位置。",
    review_foreshadow_history: "请展开历史，核对已经记录的操作。",
    contact_support: "资料引用不完整，请保留当前页面并联系支持。",
  };
  return error.recommendedAction ? hints[error.recommendedAction] ?? "请按提示核对最新资料。" : "";
}

function activeParts(plan: NovelPlan | null): PlanningPart[] {
  return plan?.parts.filter((part) => part.status === "active") ?? [];
}

function activeChapters(plan: NovelPlan | null): PlanningChapter[] {
  return activeParts(plan).flatMap((part) => part.chapters.filter((chapter) => chapter.status === "active"));
}

function operation<T extends ForeshadowWritePayload>(args: {
  userId: string;
  projectId: string;
  type: ForeshadowOperationType;
  lifecycleId: string | null;
  resourceId: string | null;
  payload: T;
}): PendingForeshadowOperation<T> {
  return {
    schema_version: 2,
    workspace: "foreshadow",
    user_id: args.userId,
    project_id: args.projectId,
    operation_key: args.payload.operation_key,
    operation_type: args.type,
    lifecycle_id: args.lifecycleId,
    resource_id: args.resourceId,
    payload: args.payload,
    created_at: new Date().toISOString(),
  };
}

function findTarget(plan: NovelPlan, type: "part" | "chapter", targetId: string): PlanningPart | PlanningChapter | null {
  if (type === "part") return plan.parts.find((part) => part.id === targetId) ?? null;
  for (const part of plan.parts) {
    const chapter = part.chapters.find((item) => item.id === targetId);
    if (chapter) return chapter;
  }
  return null;
}

export default function ForeshadowPlanningPage() {
  const { id: projectId } = useParams<{ id: string }>();
  const [searchParams] = useSearchParams();
  const { user } = useAuth();
  const [plan, setPlan] = useState<NovelPlan | null>(null);
  const [status, setStatus] = useState<ForeshadowLifecycleStatus>("active");
  const [state, setState] = useState<ForeshadowState | "all">("all");
  const [list, setList] = useState<ForeshadowListResponse | null>(null);
  const [listLoading, setListLoading] = useState(true);
  const [listMoreLoading, setListMoreLoading] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<ForeshadowLifecycle | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [mobileDetail, setMobileDetail] = useState(false);
  const [history, setHistory] = useState<ForeshadowHistoryResponse | null>(null);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState("");
  const [bindOpen, setBindOpen] = useState(false);
  const [bindQuery, setBindQuery] = useState("");
  const [bindResults, setBindResults] = useState<LoreElementListItem[]>([]);
  const [bindCursor, setBindCursor] = useState<string | null>(null);
  const [bindLoading, setBindLoading] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [errorHint, setErrorHint] = useState("");
  const [busy, setBusy] = useState(false);
  const [maintenance, setMaintenance] = useState(false);
  const [conflict, setConflict] = useState(false);
  const [refreshRequired, setRefreshRequired] = useState(false);
  const [pending, setPending] = useState<PendingForeshadowOperation | null>(null);
  const [serverRejectedPending, setServerRejectedPending] = useState<PendingForeshadowOperation | null>(null);
  const [foreignPending, setForeignPending] = useState<{ workspace: "planning" | "generation_execution" | "technical_demo_execution" | "candidate_manual_edit" | "candidate_selection"; chapterId: string | null; runId?: string; candidateId?: string } | null>(null);
  const [storageIssue, setStorageIssue] = useState<"corrupt" | "unavailable" | null>(null);
  const [demoFixture, setDemoFixture] = useState<DemoFixtureCurrentResponse | null>(null);
  const [recoveryState, setRecoveryState] = useState<RecoveryState>("idle");
  const [confirmAction, setConfirmAction] = useState<ConfirmAction | null>(null);
  const [committedAction, setCommittedAction] = useState<CommittedForeshadowAction | null>(null);
  const [hasDraft, setHasDraft] = useState(false);
  const requestGeneration = useRef(0);
  const listGeneration = useRef(0);
  const detailGeneration = useRef(0);
  const loreGeneration = useRef(0);
  const historyGeneration = useRef(0);
  const selectedLifecycleRef = useRef<string | null>(null);
  const errorRef = useRef<HTMLDivElement | null>(null);
  const detailTitleRef = useRef<HTMLHeadingElement | null>(null);
  const selectedButtonRef = useRef<HTMLButtonElement | null>(null);
  const confirmTitleRef = useRef<HTMLHeadingElement | null>(null);
  const confirmDialogRef = useRef<HTMLElement | null>(null);
  const confirmReturnFocus = useRef<HTMLElement | null>(null);
  const commitSequence = useRef(0);
  const corruptRecoverySnapshot = useRef<{ raw: string; workspace: "candidate_selection" | "candidate_manual_edit" | "generation_execution" | "technical_demo_execution" | "unknown" } | null>(null);

  const writesDisabled = busy || maintenance || conflict || refreshRequired || !!pending || !!foreignPending || !!storageIssue || !plan;
  const currentCounts = list?.counts ?? { unplanted: 0, planted: 0, pending_resolution: 0, resolved: 0 };

  useEffect(() => {
    if (!projectId) return;
    const controller = new AbortController();
    void readDemoFixture(controller.signal).then((value) => setDemoFixture(value.state === "ready" && value.project_id === projectId ? value : null)).catch(() => setDemoFixture(null));
    return () => controller.abort();
  }, [projectId]);

  const loadPlan = useCallback(async (generation = requestGeneration.current): Promise<boolean> => {
    if (!projectId) return false;
    try {
      const value = await api.getPlanning(projectId);
      if (generation !== requestGeneration.current) return false;
      setPlan(value);
      return true;
    } catch (cause) {
      if (generation !== requestGeneration.current) return false;
      setError(message(cause));
      setRefreshRequired(true);
      if (cause instanceof ApiError) {
        setErrorHint(recommendedHint(cause));
        if (cause.status === 503) setMaintenance(true);
      }
      return false;
    }
  }, [projectId]);

  const loadList = useCallback(async (append = false, generation = requestGeneration.current): Promise<boolean> => {
    if (!projectId) return false;
    const listRequest = ++listGeneration.current;
    append ? setListMoreLoading(true) : setListLoading(true);
    if (!append) setError("");
    try {
      const raw = await api.listForeshadows(projectId, {
        status,
        ...(state === "all" ? {} : { state }),
        ...(append && list?.next_cursor ? { after: list.next_cursor } : {}),
        limit: 25,
      });
      if (generation !== requestGeneration.current || listRequest !== listGeneration.current) return false;
      const parsed = parseForeshadowList(raw, projectId);
      setList((previous) => append && previous ? {
        ...parsed,
        items: [...previous.items, ...parsed.items.filter((item) => !previous.items.some((old) => old.id === item.id))],
      } : parsed);
      if (!append && !selectedId) {
        const anchoredLifecycle = searchParams.get("lifecycle");
        const nextId = anchoredLifecycle && /^[A-Za-z0-9]{32}$/.test(anchoredLifecycle) ? anchoredLifecycle : parsed.items[0]?.id;
        if (nextId) {
          selectedLifecycleRef.current = nextId;
          setSelectedId(nextId);
          if (anchoredLifecycle) setMobileDetail(true);
        }
      }
      return true;
    } catch (cause) {
      if (generation !== requestGeneration.current || listRequest !== listGeneration.current) return false;
      setError(message(cause));
      setRefreshRequired(true);
      if (cause instanceof ApiError) {
        setErrorHint(recommendedHint(cause));
        if (cause.status === 503) setMaintenance(true);
      }
      return false;
    } finally {
      if (generation === requestGeneration.current && listRequest === listGeneration.current) {
        setListLoading(false);
        setListMoreLoading(false);
      }
    }
  }, [projectId, status, state, list?.next_cursor, selectedId]);

  const loadDetail = useCallback(async (lifecycleId: string, showLoading = true): Promise<boolean> => {
    if (!projectId) return false;
    const generation = ++detailGeneration.current;
    if (showLoading) setDetailLoading(true);
    try {
      const raw = await api.getForeshadow(projectId, lifecycleId);
      if (generation !== detailGeneration.current || lifecycleId !== selectedLifecycleRef.current) return false;
      setDetail(parseForeshadowLifecycle(raw, projectId, lifecycleId));
      return true;
    } catch (cause) {
      if (generation !== detailGeneration.current || lifecycleId !== selectedLifecycleRef.current) return false;
      setError(message(cause));
      setRefreshRequired(true);
      if (cause instanceof ApiError) setErrorHint(recommendedHint(cause));
      return false;
    } finally {
      if (generation === detailGeneration.current) setDetailLoading(false);
    }
  }, [projectId, selectedId]);

  useEffect(() => {
    if (!projectId) return;
    const generation = ++requestGeneration.current;
    listGeneration.current += 1;
    detailGeneration.current += 1;
    selectedLifecycleRef.current = null;
    setPlan(null); setList(null); setSelectedId(null); setDetail(null); setHistory(null);
    setNotice(""); setError(""); setErrorHint(""); setMaintenance(false); setConflict(false);
    setRefreshRequired(false); setPending(null); setServerRejectedPending(null); setForeignPending(null); setStorageIssue(null);
    setRecoveryState("idle"); setBusy(false); setMobileDetail(false); setHasDraft(false); setCommittedAction(null);
    setStatus("active"); setState("all");
    void loadPlan(generation);
  }, [projectId]);

  useEffect(() => {
    if (!projectId) return;
    const generation = requestGeneration.current;
    detailGeneration.current += 1;
    historyGeneration.current += 1;
    selectedLifecycleRef.current = null;
    setList(null); setSelectedId(null); setDetail(null); setHistory(null); setHistoryError("");
    setMobileDetail(false); setHasDraft(false);
    void loadList(false, generation);
  }, [projectId, status, state]);

  useEffect(() => {
    historyGeneration.current += 1;
    selectedLifecycleRef.current = selectedId;
    if (!selectedId) { setDetail(null); return; }
    setHistory(null); setHistoryError("");
    void loadDetail(selectedId);
  }, [selectedId]);

  useEffect(() => {
    loreGeneration.current += 1;
    historyGeneration.current += 1;
    setBindOpen(false); setBindQuery(""); setBindResults([]); setBindCursor(null); setBindLoading(false);
  }, [projectId]);

  const refreshAuthoritative = useCallback(async (lifecycleId?: string | null): Promise<boolean> => {
    const generation = requestGeneration.current;
    const [planOk, listOk] = await Promise.all([loadPlan(generation), loadList(false, generation)]);
    let detailOk = true;
    if (lifecycleId) detailOk = await loadDetail(lifecycleId, false);
    return planOk && listOk && detailOk;
  }, [loadPlan, loadList, loadDetail]);

  async function requestPending(item: PendingForeshadowOperation): Promise<ForeshadowMutationReceipt> {
    if (!projectId) throw new Error("项目地址无效。");
    switch (item.operation_type) {
      case "foreshadow_bind":
        return api.bindForeshadow(projectId, pendingPayload<ForeshadowBindInput>(item));
      case "foreshadow_archive":
        return api.changeForeshadowState(projectId, item.lifecycle_id!, "archive", pendingPayload<ForeshadowLifecycleInput>(item));
      case "foreshadow_restore":
        return api.changeForeshadowState(projectId, item.lifecycle_id!, "restore", pendingPayload<ForeshadowRestoreInput>(item));
      case "foreshadow_plan_create":
        return api.createForeshadowPlan(projectId, item.lifecycle_id!, pendingPayload<ForeshadowPlanCreateInput>(item));
      case "foreshadow_plan_cancel":
        return api.changeForeshadowPlanState(projectId, item.lifecycle_id!, item.resource_id!, "cancel", pendingPayload<ForeshadowPlanStateInput>(item));
      case "foreshadow_plan_restore":
        return api.changeForeshadowPlanState(projectId, item.lifecycle_id!, item.resource_id!, "restore", pendingPayload<ForeshadowPlanStateInput>(item));
      case "foreshadow_fact_record":
        return api.recordForeshadowFact(projectId, item.lifecycle_id!, pendingPayload<ForeshadowFactCreateInput>(item));
      case "foreshadow_fact_retract":
        return api.retractForeshadowFact(projectId, item.lifecycle_id!, item.resource_id!, pendingPayload<ForeshadowFactRetractInput>(item));
    }
  }

  function validateReceipt(raw: unknown, item: PendingForeshadowOperation): ForeshadowMutationReceipt {
    return parseForeshadowReceipt(raw, {
      projectId: item.project_id,
      operationKey: item.operation_key,
      operationType: item.operation_type,
      lifecycleId: item.lifecycle_id,
      elementId: item.operation_type === "foreshadow_bind" ? item.resource_id : null,
    });
  }

  async function acceptReceipt(raw: unknown, item: PendingForeshadowOperation, recovered: boolean, successMessage?: string) {
    if (!projectId || !user || item.project_id !== projectId || item.user_id !== user.id) return;
    let receipt: ForeshadowMutationReceipt;
    try {
      receipt = validateReceipt(raw, item);
    } catch (cause) {
      setRecoveryState("corrupt");
      setStorageIssue("corrupt");
      setError(message(cause));
      return;
    }
    if (!clearPendingForeshadowOperation(user.id, projectId, item.operation_key)) {
      setStorageIssue("unavailable");
      setError("操作已确认，但无法清除本机恢复线索；已继续冻结写入，避免重复提交。");
      setDetail(receipt.lifecycle);
      return;
    }
    setPending(null);
    setServerRejectedPending(null);
    setRecoveryState("idle");
    setDetail(receipt.lifecycle);
    setCommittedAction({ sequence: ++commitSequence.current, type: item.operation_type, lifecycleId: receipt.lifecycle_id, resourceId: item.resource_id });
    selectedLifecycleRef.current = receipt.lifecycle_id;
    setSelectedId(receipt.lifecycle_id);
    setMobileDetail(true);
    const refreshed = await refreshAuthoritative(receipt.lifecycle_id);
    if (refreshed) {
      setNotice(recovered || receipt.replayed ? "已找回上次操作结果，并重新载入最新伏笔资料。" : successMessage ?? "操作已保存；计划或作者确认事实已经更新。");
      setError(""); setErrorHint(""); setRefreshRequired(false); setConflict(false); setMaintenance(false);
    } else {
      setRefreshRequired(true);
      setError("操作结果已确认，但最新规划或伏笔资料尚未完整读取；已暂停新的写入，请只重新加载。");
    }
  }

  async function reconcilePending(item: PendingForeshadowOperation) {
    if (!projectId) return;
    setRecoveryState("checking"); setBusy(true);
    try {
      const raw = await api.getForeshadowOperationByKey(projectId, item.operation_key);
      await acceptReceipt(raw, item, true);
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 404 && cause.code === "FORESHADOW_OPERATION_NOT_FOUND" && cause.recommendedAction === "retry_original_operation") {
        setRecoveryState("not_found");
        setNotice("服务器尚未找到原操作；你可以使用原编号和完全相同的内容安全重试。");
      } else {
        setRecoveryState(cause instanceof ApiError && cause.code === "FORESHADOW_OPERATION_CORRUPT" ? "corrupt" : "checking");
        setError(message(cause));
        if (cause instanceof ApiError) setErrorHint(recommendedHint(cause));
      }
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    if (!projectId || !user || !plan) return;
    const loaded = loadPendingForeshadowOperation(user.id, projectId);
    if (loaded.status === "missing") { setForeignPending(null); setStorageIssue(null); return; }
    if (loaded.status === "foreign") {
      setPending(null);
      setServerRejectedPending(null);
      setStorageIssue(null);
      const generationPending = loaded.workspace === "generation_execution"
        ? loadPendingGenerationExecution(user.id, projectId)
        : null;
      const technicalPending = loaded.workspace === "technical_demo_execution"
        ? loadPendingTechnicalDemoExecution(user.id, projectId)
        : null;
      const candidatePending = loaded.workspace === "candidate_manual_edit"
        ? loadPendingCandidateManualEdit(user.id, projectId)
        : null;
      const selectionPending = loaded.workspace === "candidate_selection"
        ? loadPendingCandidateSelection(user.id, projectId)
        : null;
      if (selectionPending?.status === "corrupt" || selectionPending?.status === "unavailable") {
        const raw = selectionPending.status === "corrupt"
          ? sessionStorage.getItem(pendingProjectOperationKey(user.id, projectId))
          : null;
        corruptRecoverySnapshot.current = raw ? { raw, workspace: "candidate_selection" } : null;
        setForeignPending(null);
        setStorageIssue(selectionPending.status);
        setError(selectionPending.status === "corrupt"
          ? "候选采用恢复记录损坏或身份不匹配，已停止伏笔写入。"
          : "浏览器恢复存储不可用，已停止伏笔写入。");
        return;
      }
      const otherCorrupt = candidatePending?.status === "corrupt" || candidatePending?.status === "unavailable"
        ? { status: candidatePending.status, workspace: "candidate_manual_edit" as const, label: "候选版本" }
        : generationPending?.status === "corrupt" || generationPending?.status === "unavailable"
          ? { status: generationPending.status, workspace: "generation_execution" as const, label: "生成执行" }
          : technicalPending?.status === "corrupt" || technicalPending?.status === "unavailable"
            ? { status: technicalPending.status, workspace: "technical_demo_execution" as const, label: "技术模拟" }
            : null;
      if (otherCorrupt) {
        const raw = otherCorrupt.status === "corrupt"
          ? sessionStorage.getItem(pendingProjectOperationKey(user.id, projectId))
          : null;
        corruptRecoverySnapshot.current = raw ? { raw, workspace: otherCorrupt.workspace } : null;
        setForeignPending(null);
        setStorageIssue(otherCorrupt.status);
        setError(otherCorrupt.status === "corrupt"
          ? `${otherCorrupt.label}恢复记录损坏或身份不匹配，已停止伏笔写入。`
          : "浏览器恢复存储不可用，已停止伏笔写入。");
        return;
      }
      setForeignPending({
        workspace: loaded.workspace,
        chapterId: generationPending?.status === "available" ? generationPending.operation.chapter_id
          : technicalPending?.status === "available" ? technicalPending.operation.chapter_id
            : candidatePending?.status === "available" ? candidatePending.operation.chapter_id
              : selectionPending?.status === "available" ? selectionPending.operation.chapter_id : null,
        runId: candidatePending?.status === "available" ? candidatePending.operation.run_id
          : selectionPending?.status === "available" ? selectionPending.operation.run_id : undefined,
        candidateId: candidatePending?.status === "available" ? candidatePending.operation.payload.parent_candidate_id
          : selectionPending?.status === "available" ? selectionPending.operation.expected_target.id : undefined,
      });
      return;
    }
    if (loaded.status === "corrupt" || loaded.status === "unavailable") {
      const raw = loaded.status === "corrupt"
        ? sessionStorage.getItem(pendingProjectOperationKey(user.id, projectId))
        : null;
      corruptRecoverySnapshot.current = raw ? { raw, workspace: "unknown" } : null;
      setStorageIssue(loaded.status);
      setError(loaded.status === "corrupt"
        ? "检测到损坏或不受支持的恢复记录；已安全停止全部伏笔写入。"
        : "浏览器会话存储不可用，无法保证操作可恢复；已安全停止全部伏笔写入。");
      return;
    }
    setForeignPending(null);
    setStorageIssue(null);
    setPending(loaded.operation);
    void reconcilePending(loaded.operation);
  }, [projectId, user?.id, !!plan]);

  async function execute(item: PendingForeshadowOperation, successMessage: string) {
    if (!projectId || !user || writesDisabled) return;
    if (!savePendingForeshadowOperation(item)) {
      setStorageIssue("unavailable");
      setError("无法安全保存操作恢复信息；为避免重复写入，本次请求未发送。");
      return;
    }
    setPending(item); setBusy(true); setError(""); setErrorHint(""); setNotice("");
    try {
      const raw = await requestPending(item);
      await acceptReceipt(raw, item, false, successMessage);
    } catch (cause) {
      await handleWriteFailure(cause, item);
    } finally {
      setBusy(false);
    }
  }

  async function retryPending() {
    if (!pending || recoveryState !== "not_found") return;
    setBusy(true);
    try {
      await acceptReceipt(await requestPending(pending), pending, false);
    } catch (cause) {
      await handleWriteFailure(cause, pending);
    } finally {
      setBusy(false);
    }
  }

  async function handleWriteFailure(cause: unknown, item: PendingForeshadowOperation) {
    if (!projectId || !user) return;
    if (cause instanceof ApiError && (cause.code === "FORESHADOW_OPERATION_CORRUPT" || cause.code === "FORESHADOW_OPERATION_KEY_REUSED")) {
      setRecoveryState("corrupt");
      setServerRejectedPending(item);
      setError(`${message(cause)} 服务端已拒绝这条操作；不会重试。你可以明确放弃被拒绝的本地恢复线索，再只读重载权威状态。`);
      setErrorHint(recommendedHint(cause));
      return;
    }
    if (cause instanceof ApiError && cause.status === 503) {
      const cleared = clearPendingForeshadowOperation(user.id, projectId, item.operation_key);
      if (cleared) setPending(null); else setStorageIssue("unavailable");
      setMaintenance(true); setError(message(cause)); setErrorHint(recommendedHint(cause));
      return;
    }
    if (cause instanceof ApiError && (cause.status === 409 || cause.status === 422)) {
      const cleared = clearPendingForeshadowOperation(user.id, projectId, item.operation_key);
      if (cleared) setPending(null); else setStorageIssue("unavailable");
      setConflict(cause.status === 409); setError(message(cause)); setErrorHint(recommendedHint(cause));
      if (cause.code === "FORESHADOW_ALREADY_TRACKED" && typeof cause.context.lifecycle_id === "string") {
        selectedLifecycleRef.current = cause.context.lifecycle_id;
        setSelectedId(cause.context.lifecycle_id); setMobileDetail(true);
      }
      return;
    }
    setError("操作结果暂时无法确认，已保留原操作编号并冻结新的伏笔写入。");
    await reconcilePending(item);
  }

  function clearCorruptRecovery() {
    if (!projectId || !user || storageIssue !== "corrupt") return;
    const failClosed = (text: string) => {
      setError(text);
      window.requestAnimationFrame(() => errorRef.current?.focus());
    };
    const corruptSnapshot = corruptRecoverySnapshot.current;
    if (!corruptSnapshot) {
      failClosed("无法确认损坏恢复记录的原始快照，未清除也未解除写入锁。");
      return;
    }
    if (!window.confirm(corruptSnapshot.workspace === "candidate_selection"
      ? "只清除这条损坏的候选采用浏览器恢复记录？不会修改服务器或伏笔数据。"
      : "只清除这条损坏的浏览器恢复记录？不会修改服务器或伏笔数据。")) return;
    let currentRaw: string | null;
    try {
      currentRaw = sessionStorage.getItem(pendingProjectOperationKey(user.id, projectId));
    } catch {
      setStorageIssue("unavailable");
      failClosed("浏览器存储仍不可用，无法核对或清除损坏恢复线索；继续保持禁写。");
      return;
    }
    const shared = loadPendingForeshadowOperation(user.id, projectId);
    const stillExactWorkspace = corruptSnapshot.workspace === "candidate_selection"
      ? shared.status === "foreign" && shared.workspace === "candidate_selection"
        && loadPendingCandidateSelection(user.id, projectId).status === "corrupt"
      : corruptSnapshot.workspace === "candidate_manual_edit"
        ? shared.status === "foreign" && shared.workspace === "candidate_manual_edit"
          && loadPendingCandidateManualEdit(user.id, projectId).status === "corrupt"
        : corruptSnapshot.workspace === "generation_execution"
          ? shared.status === "foreign" && shared.workspace === "generation_execution"
            && loadPendingGenerationExecution(user.id, projectId).status === "corrupt"
          : corruptSnapshot.workspace === "technical_demo_execution"
            ? shared.status === "foreign" && shared.workspace === "technical_demo_execution"
              && loadPendingTechnicalDemoExecution(user.id, projectId).status === "corrupt"
            : shared.status === "corrupt";
    if (currentRaw !== corruptSnapshot.raw || !stillExactWorkspace) {
      failClosed(corruptSnapshot.workspace === "candidate_selection"
        ? "恢复记录已变化或不再是损坏的候选采用记录，未清除也未解除写入锁。"
        : "恢复记录已变化或不再是原先的损坏记录，未清除也未解除写入锁。");
      return;
    }
    if (clearPendingProjectOperationRecord(user.id, projectId)
      && loadPendingForeshadowOperation(user.id, projectId).status === "missing") {
      corruptRecoverySnapshot.current = null;
      setStorageIssue(null); setError(""); setNotice("只清除了本机损坏的恢复线索；没有删除小说或服务器数据。安全起见，请重新载入页面后再写入。");
      setRefreshRequired(true);
      setError("本机恢复线索已清除。请重新读取服务器最新规划与伏笔资料后再继续写入。");
    } else {
      setStorageIssue("unavailable");
      failClosed("浏览器存储仍不可用，无法清除损坏恢复线索；继续保持禁写。");
    }
  }

  async function abandonServerRejectedPending() {
    if (!projectId || !user || !pending || !serverRejectedPending
      || JSON.stringify(pending) !== JSON.stringify(serverRejectedPending)
      || !window.confirm("明确放弃这条已被服务端拒绝的本地恢复线索？不会重试原请求，也不会修改服务器伏笔数据。")) return;
    const loaded = loadPendingForeshadowOperation(user.id, projectId);
    if (loaded.status !== "available"
      || JSON.stringify(loaded.operation) !== JSON.stringify(serverRejectedPending)
      || !clearPendingForeshadowOperation(user.id, projectId, serverRejectedPending.operation_key)
      || loadPendingForeshadowOperation(user.id, projectId).status !== "missing") {
      setError("被拒绝的本地恢复线索已变化或无法按完整身份清除；继续保持禁写且不会重试。");
      window.requestAnimationFrame(() => errorRef.current?.focus());
      return;
    }
    setPending(null);
    setServerRejectedPending(null);
    setRecoveryState("idle");
    setBusy(true);
    const refreshed = await refreshAuthoritative(selectedId);
    setBusy(false);
    if (refreshed) {
      setConflict(false);
      setRefreshRequired(false);
      setError("");
      setErrorHint("");
      setNotice("已放弃被服务端拒绝的本地恢复线索，并重新读取权威规划与伏笔状态；没有重试原请求。");
    } else {
      setRefreshRequired(true);
      setError("本地恢复线索已放弃，但权威状态重读失败；继续保持禁写，请再次核对服务器最新状态。");
    }
  }

  function selectLifecycle(lifecycleId: string) {
    if (hasDraft && !window.confirm("当前还有未提交的计划或事实表单。切换伏笔将放弃这些内容，是否继续？")) return;
    setHasDraft(false); selectedLifecycleRef.current = lifecycleId; setSelectedId(lifecycleId); setMobileDetail(true);
    window.setTimeout(() => detailTitleRef.current?.focus(), 0);
  }

  function returnToList() {
    setMobileDetail(false);
    window.setTimeout(() => selectedButtonRef.current?.focus(), 0);
  }

  function confirmDiscardDraft(reason: string): boolean {
    return !hasDraft || window.confirm(`当前还有未提交的计划或事实表单。${reason}将放弃这些内容，是否继续？`);
  }

  function changeStateFilter(next: ForeshadowState | "all") {
    if (next === state || !confirmDiscardDraft("切换筛选")) return;
    setHasDraft(false); setState(next);
  }

  function changeStatusFilter(next: ForeshadowLifecycleStatus) {
    if (next === status || !confirmDiscardDraft("切换筛选")) return;
    setHasDraft(false); setStatus(next);
  }

  async function searchLore(append = false) {
    if (!projectId) return;
    const generation = ++loreGeneration.current;
    const requestProjectId = projectId;
    setBindLoading(true);
    try {
      const result = await api.listLoreElements(requestProjectId, {
        q: bindQuery.trim(), type: "foreshadow", confirmation_status: "confirmed",
        lifecycle_status: "active", enabled: true, ...(append && bindCursor ? { cursor: bindCursor } : {}), limit: 20,
      });
      if (generation !== loreGeneration.current || requestProjectId !== projectId) return;
      setBindResults((previous) => append ? [...previous, ...result.items.filter((item) => !previous.some((old) => old.id === item.id))] : result.items);
      setBindCursor(result.next_cursor);
    } catch (cause) {
      if (generation !== loreGeneration.current || requestProjectId !== projectId) return;
      setError(message(cause));
    } finally {
      if (generation === loreGeneration.current && requestProjectId === projectId) setBindLoading(false);
    }
  }

  async function loadHistory() {
    if (!projectId || !detail) return;
    const generation = ++historyGeneration.current;
    const requestProjectId = projectId;
    const lifecycleId = detail.id;
    setHistoryLoading(true); setHistoryError("");
    try {
      const parsed = parseForeshadowHistory(await api.getForeshadowHistory(requestProjectId, lifecycleId), lifecycleId);
      if (generation !== historyGeneration.current || requestProjectId !== projectId || lifecycleId !== selectedLifecycleRef.current) return;
      setHistory(parsed);
    } catch (cause) {
      if (generation !== historyGeneration.current || requestProjectId !== projectId || lifecycleId !== selectedLifecycleRef.current) return;
      setHistoryError(message(cause));
    } finally {
      if (generation === historyGeneration.current && requestProjectId === projectId && lifecycleId === selectedLifecycleRef.current) setHistoryLoading(false);
    }
  }

  useEffect(() => {
    if (!confirmAction) return;
    window.setTimeout(() => confirmTitleRef.current?.focus(), 0);
    const containFocus = (event: KeyboardEvent) => {
      if (event.key === "Escape") { closeConfirm(); return; }
      if (event.key !== "Tab") return;
      const focusable = Array.from(confirmDialogRef.current?.querySelectorAll<HTMLElement>("button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex='-1'])") ?? []);
      if (focusable.length === 0) { event.preventDefault(); confirmTitleRef.current?.focus(); return; }
      const first = focusable[0]; const last = focusable[focusable.length - 1];
      if (!focusable.includes(document.activeElement as HTMLElement)) { event.preventDefault(); (event.shiftKey ? last : first).focus(); }
      else if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    window.addEventListener("keydown", containFocus);
    return () => window.removeEventListener("keydown", containFocus);
  }, [confirmAction]);

  useEffect(() => {
    if (!hasDraft) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [hasDraft]);

  useEffect(() => {
    if (error) window.setTimeout(() => errorRef.current?.focus(), 0);
  }, [error]);

  useEffect(() => {
    if (mobileDetail && detail && detail.id === selectedId) window.setTimeout(() => detailTitleRef.current?.focus(), 0);
  }, [mobileDetail, detail?.id, selectedId]);

  function openConfirm(action: ConfirmAction) {
    confirmReturnFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    setConfirmAction(action);
  }

  function closeConfirm() {
    setConfirmAction(null);
    window.setTimeout(() => confirmReturnFocus.current?.focus(), 0);
  }

  if (!projectId) return <div className="card empty-state" role="alert">项目地址无效。</div>;

  return (
    <div className="foreshadow-page foreshadow-page--studio" aria-busy={listLoading || busy} onClickCapture={(event) => {
      if (!hasDraft || !(event.target instanceof Element) || !event.target.closest("a")) return;
      if (!window.confirm("当前还有未提交的计划或事实表单。离开页面将放弃这些内容，是否继续？")) {
        event.preventDefault(); event.stopPropagation();
      }
    }}>
      <Link className="btn-back" to={`/project/${projectId}/plan/chapters`}>← 返回章节规划</Link>
      <header className="page-header foreshadow-header">
        <div><h1>伏笔管理</h1><p>安排未来位置，并单独记录作者已经确认的正文事实。</p></div>
        <Link className="btn btn-secondary" to={`/project/${projectId}/lore?type=foreshadow`}>打开设定仓库</Link>
      </header>
      {demoFixture?.state === "ready" && <div id="demo-foreshadow"><DemoGuide projectId={projectId!} current={4} chapterId={demoFixture.chapter_id} elementId={demoFixture.element_id} foreshadowLifecycleId={demoFixture.foreshadow_lifecycle_id} /></div>}

      <section className="foreshadow-boundary" aria-label="伏笔事实边界">
        <strong>计划表示作者未来安排，不代表正文已经发生。</strong>
        <span>作者确认由你手动记录；系统尚未核对、生成或修改正文。此页不会调用 AI，也不会产生费用。</span>
      </section>

      <div className="foreshadow-live" role="status" aria-live="polite">{notice}</div>
      {error && <div className="planning-notice is-error" role="alert" tabIndex={-1} ref={errorRef}><span>{error}{errorHint && <small className="planning-notice__hint">{errorHint}</small>}</span><span className="planning-notice__actions">{storageIssue === "corrupt" && <button className="btn btn-secondary" onClick={clearCorruptRecovery}>清除本机损坏线索</button>}{serverRejectedPending && <button className="btn btn-secondary" onClick={() => void abandonServerRejectedPending()}>放弃被服务端拒绝的本地恢复线索</button>}{(conflict || refreshRequired) && <button className="btn btn-secondary" onClick={() => void refreshAuthoritative(selectedId).then((ok) => { if (ok) { setConflict(false); setRefreshRequired(false); setError(""); setNotice("已核对服务器最新规划与伏笔资料。"); } })}>核对服务器最新状态</button>}</span></div>}
      {maintenance && <div className="planning-notice" role="status">项目正在维护；只读内容和原表单已保留，新写入已暂停。</div>}
      {foreignPending?.workspace === "planning" && <div className="planning-notice" role="alert"><span>章节规划中还有结果未确认的写入；伏笔写入已暂停。</span><Link className="btn btn-secondary" to={`/project/${projectId}/plan/chapters`}>返回章节规划核对</Link></div>}
      {foreignPending?.workspace === "generation_execution" && <div className="planning-notice" role="alert"><span>生成候选中还有结果未确认的模型调用；伏笔写入已暂停，且不会自动确认埋入或回收。</span><Link className="btn btn-secondary" to={foreignPending.chapterId ? `/project/${projectId}/plan/chapters?scope=chapter&target=${encodeURIComponent(foreignPending.chapterId)}` : `/project/${projectId}/plan/chapters`}>返回发起章节核对生成</Link></div>}
      {foreignPending?.workspace === "technical_demo_execution" && <div className="planning-notice" role="alert"><span>技术模拟中还有结果未确认的固定内容请求；伏笔写入已暂停，且不会自动确认埋入或回收。</span><Link className="btn btn-secondary" to={foreignPending.chapterId ? `/project/${projectId}/plan/chapters?scope=chapter&target=${encodeURIComponent(foreignPending.chapterId)}` : `/project/${projectId}/plan/chapters`}>返回技术模拟发起章节核对</Link></div>}
      {foreignPending?.workspace === "candidate_manual_edit" && <div className="planning-notice" role="alert"><span>候选版本还有手工另存结果未确认；伏笔写入已暂停，且不会自动确认埋入或回收。</span><Link className="btn btn-secondary" to={foreignPending.chapterId && foreignPending.runId && foreignPending.candidateId ? `/project/${projectId}/plan/chapters?scope=chapter&target=${encodeURIComponent(foreignPending.chapterId)}&generation_run=${encodeURIComponent(foreignPending.runId)}&candidate_version=${encodeURIComponent(foreignPending.candidateId)}` : `/project/${projectId}/plan/chapters`}>返回原章节核对候选版本</Link></div>}
      {foreignPending?.workspace === "candidate_selection" && <div className="planning-notice" role="alert"><span>候选采用还有结果未确认；伏笔写入已暂停，且不会自动确认埋入或回收。</span><Link className="btn btn-secondary" to={foreignPending.chapterId && foreignPending.runId && foreignPending.candidateId ? `/project/${projectId}/plan/chapters?scope=chapter&target=${encodeURIComponent(foreignPending.chapterId)}&generation_run=${encodeURIComponent(foreignPending.runId)}&candidate_version=${encodeURIComponent(foreignPending.candidateId)}` : `/project/${projectId}/plan/chapters`}>返回原章核对采用状态</Link></div>}
      {pending && <div className="planning-notice" role="status"><span>上次伏笔操作仍等待确认；已冻结新写入，避免重复记录。</span><span className="planning-notice__actions"><button className="btn btn-secondary" disabled={busy} onClick={() => void reconcilePending(pending)}>{recoveryState === "checking" ? "正在核对…" : "核对原操作结果"}</button>{recoveryState === "not_found" && <button className="btn btn-secondary" disabled={busy} onClick={() => void retryPending()}>使用原编号和内容重试</button>}</span></div>}

      <section className="foreshadow-overview" aria-label="伏笔状态概况">
        {(Object.keys(stateLabels) as ForeshadowState[]).map((key) => <button key={key} aria-pressed={state === key} onClick={() => changeStateFilter(state === key ? "all" : key)}><strong>{listLoading && !list ? "…" : currentCounts[key]}</strong><span>{stateLabels[key]}</span><small>{stateHelp[key]}</small></button>)}
      </section>

      <div className={`foreshadow-workspace${mobileDetail ? " show-detail" : ""}`}>
        <aside className="card foreshadow-list-panel">
          <div className="foreshadow-list-heading"><h2>伏笔列表</h2><button className="btn btn-secondary" disabled={writesDisabled} onClick={() => { setBindOpen(!bindOpen); if (!bindOpen && bindResults.length === 0) void searchLore(); }}>加入已有伏笔</button></div>
          <div className="foreshadow-filters" aria-label="列表筛选">
            <button aria-pressed={status === "active"} onClick={() => changeStatusFilter("active")}>活动伏笔</button>
            <button aria-pressed={status === "archived"} onClick={() => changeStatusFilter("archived")}>已归档</button>
            <button aria-pressed={state === "all"} onClick={() => changeStateFilter("all")}>全部状态</button>
          </div>
          {bindOpen && <BindPanel projectId={projectId} query={bindQuery} setQuery={setBindQuery} loading={bindLoading} results={bindResults} hasMore={!!bindCursor} disabled={writesDisabled} onSearch={() => void searchLore()} onMore={() => void searchLore(true)} onClose={() => setBindOpen(false)} onBind={(element) => {
            if (!plan || !user) return;
            const payload: ForeshadowBindInput = { operation_key: createForeshadowOperationKey("foreshadow_bind"), element_id: element.id, expected_structure_version: plan.structure_version, expected_element_lock_version: element.lock_version };
            void execute(operation({ userId: user.id, projectId, type: "foreshadow_bind", lifecycleId: null, resourceId: element.id, payload }), "伏笔已加入管理。");
          }} />}
          {listLoading && !list && <p className="foreshadow-empty">正在加载伏笔列表…</p>}
          {!listLoading && list?.items.length === 0 && <div className="foreshadow-empty"><strong>{state === "all" ? "还没有纳入管理的伏笔" : "当前筛选下没有伏笔"}</strong><span>{state === "all" ? "请从已确认的伏笔设定中选择。" : "可以清除状态筛选查看其他伏笔。"}</span>{state !== "all" && <button className="btn btn-secondary" onClick={() => setState("all")}>清除筛选</button>}</div>}
          <ul className="foreshadow-list">
            {list?.items.map((item) => <li key={item.id}><button ref={item.id === selectedId ? selectedButtonRef : undefined} className={`foreshadow-list-item${item.id === selectedId ? " is-selected" : ""}`} aria-current={item.id === selectedId ? "true" : undefined} onClick={() => selectLifecycle(item.id)}><span><strong>{item.element.name}</strong><small>{item.element.summary || "暂无摘要"}</small></span><span className="foreshadow-state-label">{stateLabels[item.state]}</span></button></li>)}
          </ul>
          {list?.next_cursor && <button className="btn btn-secondary" disabled={listMoreLoading} onClick={() => void loadList(true)}>{listMoreLoading ? "正在加载…" : "加载更多"}</button>}
        </aside>

        <section className="card foreshadow-detail-panel" aria-label="伏笔详情">
          <button className="btn btn-secondary foreshadow-mobile-back" onClick={returnToList}>← 返回伏笔列表</button>
          {!selectedId && <div className="foreshadow-empty"><strong>选择一个伏笔查看计划和作者确认事实</strong></div>}
          {detailLoading && <p>正在加载伏笔详情…</p>}
          {detail && selectedId === detail.id && <ForeshadowDetail
            lifecycle={detail} projectId={projectId} plan={plan} disabled={writesDisabled} setDraft={setHasDraft}
            committedAction={committedAction}
            titleRef={detailTitleRef} history={history} historyLoading={historyLoading} historyError={historyError}
            onLoadHistory={() => void loadHistory()}
            onPlan={(payload) => user && void execute(operation({ userId: user.id, projectId, type: "foreshadow_plan_create", lifecycleId: detail.id, resourceId: null, payload }), "未来计划已保存；正文和作者确认事实未改变。")}
            onPlanState={(item, action, payload) => user && void execute(operation({ userId: user.id, projectId, type: action === "cancel" ? "foreshadow_plan_cancel" : "foreshadow_plan_restore", lifecycleId: detail.id, resourceId: item.id, payload }), action === "cancel" ? "未来计划已取消；作者确认事实未改变。" : "未来计划已恢复。")}
            onFact={(payload) => openConfirm({ title: payload.fact_kind === "planted" ? "确认已经埋入" : "确认已经回收", body: "我确认该伏笔已在所选章节发生。系统尚未核对正文，也不会修改正文。", confirmLabel: "确认并记录", run: () => { closeConfirm(); if (user) void execute(operation({ userId: user.id, projectId, type: "foreshadow_fact_record", lifecycleId: detail.id, resourceId: null, payload }), "作者确认事实已记录；系统未修改正文。"); } })}
            onRetract={(fact, payload) => openConfirm({ title: "撤回作者确认事实", body: "撤回会保留原历史并重新计算状态，不会删除旧记录或修改正文。", confirmLabel: "确认撤回", run: () => { closeConfirm(); if (user) void execute(operation({ userId: user.id, projectId, type: "foreshadow_fact_retract", lifecycleId: detail.id, resourceId: fact.id, payload }), "作者确认事实已撤回，历史仍然保留。"); } })}
            onLifecycle={(action, payload) => {
              const body = action === "archive" ? "归档不会取消计划，也不会撤销作者确认事实；归档后仍可取消错误计划或撤销错误事实。" : "恢复时系统会重新检查 Lore、篇章、章节和顺序。";
              openConfirm({ title: action === "archive" ? "归档伏笔" : "恢复伏笔", body, confirmLabel: action === "archive" ? "确认归档" : "确认恢复", run: () => { closeConfirm(); if (user) void execute(operation({ userId: user.id, projectId, type: action === "archive" ? "foreshadow_archive" : "foreshadow_restore", lifecycleId: detail.id, resourceId: null, payload }), action === "archive" ? "伏笔已移至归档列表；计划和事实未被自动改变。" : "伏笔已恢复为活动状态。"); } });
            }}
          />}
        </section>
      </div>

      {confirmAction && <div className="foreshadow-dialog-backdrop"><section ref={confirmDialogRef} className="foreshadow-dialog" role="alertdialog" aria-modal="true" aria-labelledby="foreshadow-confirm-title"><h2 id="foreshadow-confirm-title" ref={confirmTitleRef} tabIndex={-1}>{confirmAction.title}</h2><p>{confirmAction.body}</p><div><button className="btn btn-secondary" onClick={closeConfirm}>取消</button><button className="btn btn-primary" onClick={confirmAction.run}>{confirmAction.confirmLabel}</button></div></section></div>}
    </div>
  );
}

function BindPanel({ projectId, query, setQuery, loading, results, hasMore, disabled, onSearch, onMore, onClose, onBind }: { projectId: string; query: string; setQuery: (value: string) => void; loading: boolean; results: LoreElementListItem[]; hasMore: boolean; disabled: boolean; onSearch: () => void; onMore: () => void; onClose: () => void; onBind: (element: LoreElementListItem) => void }) {
  return <section className="foreshadow-bind"><div className="foreshadow-list-heading"><h3>加入已有伏笔</h3><button className="btn btn-secondary" onClick={onClose}>关闭</button></div><p>只显示已确认、已启用且未归档的伏笔设定。本页不会复制创建设定。</p><form onSubmit={(event) => { event.preventDefault(); onSearch(); }}><label>搜索设定名称或摘要<input value={query} onChange={(event) => setQuery(event.target.value)} /></label><button className="btn btn-secondary" disabled={loading}>搜索</button></form>{!loading && results.length === 0 && <div className="foreshadow-empty"><span>暂无可加入的已确认伏笔。</span><Link to={`/project/${projectId}/lore?type=foreshadow`}>前往设定仓库创建或确认伏笔</Link></div>}<div className="foreshadow-bind-results">{results.map((element) => <article key={element.id}><span><strong>{element.name}</strong><small>{element.summary || "暂无摘要"}</small></span><button className="btn btn-secondary" disabled={disabled} onClick={() => onBind(element)}>加入管理</button></article>)}</div>{hasMore && <button className="btn btn-secondary" disabled={loading} onClick={onMore}>加载更多结果</button>}</section>;
}

function ForeshadowDetail({ lifecycle, projectId, plan, disabled, setDraft, committedAction, titleRef, history, historyLoading, historyError, onLoadHistory, onPlan, onPlanState, onFact, onRetract, onLifecycle }: { lifecycle: ForeshadowLifecycle; projectId: string; plan: NovelPlan | null; disabled: boolean; setDraft: (dirty: boolean) => void; committedAction: CommittedForeshadowAction | null; titleRef: React.RefObject<HTMLHeadingElement>; history: ForeshadowHistoryResponse | null; historyLoading: boolean; historyError: string; onLoadHistory: () => void; onPlan: (payload: ForeshadowPlanCreateInput) => void; onPlanState: (item: ForeshadowPlanItem, action: "cancel" | "restore", payload: ForeshadowPlanStateInput) => void; onFact: (payload: ForeshadowFactCreateInput) => void; onRetract: (fact: ForeshadowFact, payload: ForeshadowFactRetractInput) => void; onLifecycle: (action: "archive" | "restore", payload: ForeshadowLifecycleInput | ForeshadowRestoreInput) => void }) {
  const [planKind, setPlanKind] = useState<"plant" | "resolve">("plant");
  const [targetType, setTargetType] = useState<"part" | "chapter">("chapter");
  const [targetId, setTargetId] = useState("");
  const [condition, setCondition] = useState("");
  const [planNote, setPlanNote] = useState("");
  const [factKind, setFactKind] = useState<"planted" | "resolved">("planted");
  const [chapterId, setChapterId] = useState("");
  const [factNote, setFactNote] = useState("");
  const [retractReasons, setRetractReasons] = useState<Record<string, string>>({});
  useEffect(() => { setTargetId(""); setCondition(""); setPlanNote(""); setChapterId(""); setFactNote(""); setRetractReasons({}); setDraft(false); }, [lifecycle.id]);
  useEffect(() => {
    if (!committedAction || committedAction.lifecycleId !== lifecycle.id) return;
    if (committedAction.type === "foreshadow_plan_create") {
      setTargetId(""); setCondition(""); setPlanNote("");
    } else if (committedAction.type === "foreshadow_fact_record") {
      setChapterId(""); setFactNote("");
    } else if (committedAction.type === "foreshadow_fact_retract" && committedAction.resourceId) {
      setRetractReasons((current) => {
        const next = { ...current }; delete next[committedAction.resourceId!]; return next;
      });
    }
  }, [committedAction?.sequence, lifecycle.id]);
  const dirty = !!targetId || !!condition || !!planNote || !!chapterId || !!factNote || Object.values(retractReasons).some((value) => value.trim());
  useEffect(() => { setDraft(dirty); return () => setDraft(false); }, [dirty, setDraft]);
  const parts = activeParts(plan); const chapters = activeChapters(plan);
  const activePlans = lifecycle.plans.filter((item) => item.status === "active");
  const cancelledPlans = lifecycle.plans.filter((item) => item.status === "cancelled");
  const activeFacts = lifecycle.facts.filter((item) => item.status === "active");
  const retractedFacts = lifecycle.facts.filter((item) => item.status === "retracted");
  const ordinaryDisabled = disabled || lifecycle.status === "archived";

  function submitPlan(event: FormEvent) {
    event.preventDefault();
    if (!plan || !targetId) return;
    const target = findTarget(plan, targetType, targetId);
    if (!target) return;
    onPlan({ operation_key: createForeshadowOperationKey("foreshadow_plan_create"), expected_lifecycle_version: lifecycle.lock_version, expected_structure_version: plan.structure_version, action_kind: planKind, target_type: targetType, target_id: targetId, expected_target_lock_version: target.lock_version, condition_text: condition.trim(), note: planNote.trim() });
  }

  function submitFact(event: FormEvent) {
    event.preventDefault();
    if (!plan || !chapterId) return;
    const chapter = chapters.find((item) => item.id === chapterId);
    if (!chapter) return;
    onFact({ operation_key: createForeshadowOperationKey("foreshadow_fact_record"), expected_lifecycle_version: lifecycle.lock_version, expected_structure_version: plan.structure_version, fact_kind: factKind, chapter_id: chapter.id, expected_chapter_lock_version: chapter.lock_version, note: factNote.trim() });
  }

  return <article className="foreshadow-detail">
    <header><div><h2 ref={titleRef} tabIndex={-1}>{lifecycle.element.name}</h2><p>{lifecycle.element.summary || "暂无摘要"}</p></div><span className="foreshadow-state-label">{lifecycle.status === "archived" ? "已归档" : stateLabels[lifecycle.state]}</span></header>
    <div className="foreshadow-meta"><span>设定：{lifecycle.element.enabled ? "已启用" : "已停用"}</span><span>内容版本 {lifecycle.element.content_version}</span><span>生命周期版本 {lifecycle.lock_version}</span></div>
    <p className="foreshadow-state-help"><strong>{stateLabels[lifecycle.state]}</strong>：{stateHelp[lifecycle.state]}</p>
    <Link to={`/project/${projectId}/lore?element=${lifecycle.element.id}`}>在设定仓库查看原始设定</Link>

    <section className="foreshadow-detail-section"><h3>未来计划</h3><p>计划表示作者未来安排，不代表正文已经发生。取消计划不会撤销作者确认事实。</p>{activePlans.length === 0 && <span>尚未安排未来位置。</span>}<div className="foreshadow-cards">{activePlans.map((item) => <PlanCard key={item.id} item={item} disabled={disabled} onAction={() => plan && onPlanState(item, "cancel", { operation_key: createForeshadowOperationKey("foreshadow_plan_cancel"), expected_lifecycle_version: lifecycle.lock_version, expected_structure_version: plan.structure_version, expected_item_lock_version: item.lock_version })} />)}</div>{lifecycle.status === "active" && <form className="foreshadow-form" onSubmit={submitPlan}><label>计划类型<select value={planKind} onChange={(event) => setPlanKind(event.target.value as "plant" | "resolve")}><option value="plant">计划埋入</option><option value="resolve">计划回收</option></select></label><label>目标层级<select value={targetType} onChange={(event) => { setTargetType(event.target.value as "part" | "chapter"); setTargetId(""); }}><option value="chapter">具体章节</option><option value="part">大篇章</option></select></label><label>目标位置<select value={targetId} onChange={(event) => setTargetId(event.target.value)}><option value="">请选择，不自动预选</option>{(targetType === "part" ? parts : chapters).map((target) => <option key={target.id} value={target.id}>{target.title}</option>)}</select></label>{planKind === "resolve" && <label>回收条件<textarea value={condition} maxLength={2000} required onChange={(event) => setCondition(event.target.value)} /></label>}<label>计划备注<textarea value={planNote} maxLength={2000} onChange={(event) => setPlanNote(event.target.value)} /></label><p>保存计划不会修改正文，也不会把伏笔标记为已发生。</p><button className="btn btn-primary" disabled={ordinaryDisabled || !targetId || (planKind === "resolve" && !condition.trim())}>保存未来计划</button></form>}{cancelledPlans.length > 0 && <details><summary>已取消计划（{cancelledPlans.length}）</summary><div className="foreshadow-cards">{cancelledPlans.map((item) => <PlanCard key={item.id} item={item} disabled={ordinaryDisabled} restore onAction={() => plan && onPlanState(item, "restore", { operation_key: createForeshadowOperationKey("foreshadow_plan_restore"), expected_lifecycle_version: lifecycle.lock_version, expected_structure_version: plan.structure_version, expected_item_lock_version: item.lock_version })} />)}</div></details>}</section>

    <section className="foreshadow-detail-section"><h3>作者确认事实</h3><p>这里只记录你的手动确认；系统尚未核对正文，也不会读取、生成或修改正文。</p>{activeFacts.length === 0 && <span>尚无作者确认事实。</span>}<div className="foreshadow-cards">{activeFacts.map((fact) => <FactCard key={fact.id} fact={fact} reason={retractReasons[fact.id] ?? ""} setReason={(value) => setRetractReasons((current) => ({ ...current, [fact.id]: value }))} disabled={disabled} onRetract={() => onRetract(fact, { operation_key: createForeshadowOperationKey("foreshadow_fact_retract"), expected_lifecycle_version: lifecycle.lock_version, expected_fact_lock_version: fact.lock_version, reason: (retractReasons[fact.id] ?? "").trim() })} />)}</div>{lifecycle.status === "active" && <form className="foreshadow-form" onSubmit={submitFact}><label>确认类型<select value={factKind} onChange={(event) => setFactKind(event.target.value as "planted" | "resolved")}><option value="planted">确认已在某章埋入</option><option value="resolved">确认已在某章回收</option></select></label><label>实际发生章节<select value={chapterId} onChange={(event) => setChapterId(event.target.value)}><option value="">请选择具体活动章节，不自动预选</option>{chapters.map((chapter) => <option key={chapter.id} value={chapter.id}>{chapter.title}</option>)}</select></label><label>事实备注<textarea value={factNote} maxLength={2000} onChange={(event) => setFactNote(event.target.value)} /></label><button className="btn btn-primary" disabled={ordinaryDisabled || !chapterId}>继续确认</button></form>}{retractedFacts.length > 0 && <details><summary>已撤回事实（{retractedFacts.length}）</summary><div className="foreshadow-cards">{retractedFacts.map((fact) => <article key={fact.id}><strong>{fact.fact_kind === "planted" ? "曾确认埋入" : "曾确认回收"}</strong><span>{fact.chapter.title}</span><small>历史已保留</small></article>)}</div></details>}</section>

    <section className="foreshadow-detail-section"><details onToggle={(event) => { if ((event.currentTarget as HTMLDetailsElement).open && !history && !historyLoading) onLoadHistory(); }}><summary>操作历史</summary>{historyLoading && <p>正在加载历史…</p>}{historyError && <div role="alert"><span>{historyError}</span><button className="btn btn-secondary" onClick={onLoadHistory}>重新加载历史</button></div>}{history && <ol className="foreshadow-history">{history.items.map((item) => <li key={item.id}><strong>{eventLabels[item.event_kind]}</strong><span>{new Date(item.created_at).toLocaleString("zh-CN")}</span><small>生命周期版本 {item.previous_lifecycle_version} → {item.new_lifecycle_version}</small></li>)}</ol>}</details></section>

    <section className="foreshadow-detail-section foreshadow-danger"><h3>{lifecycle.status === "active" ? "归档伏笔" : "恢复伏笔"}</h3>{lifecycle.status === "archived" && <p>归档期间仍可取消错误计划或撤回错误事实；新增普通计划和事实保持禁用。</p>}{lifecycle.status === "active" ? <button className="btn btn-secondary" disabled={disabled} onClick={() => onLifecycle("archive", { operation_key: createForeshadowOperationKey("foreshadow_archive"), expected_lifecycle_version: lifecycle.lock_version })}>归档伏笔</button> : <button className="btn btn-primary" disabled={disabled || !plan} onClick={() => plan && onLifecycle("restore", { operation_key: createForeshadowOperationKey("foreshadow_restore"), expected_lifecycle_version: lifecycle.lock_version, expected_structure_version: plan.structure_version, expected_element_lock_version: lifecycle.element.lock_version })}>恢复伏笔</button>}</section>
  </article>;
}

function PlanCard({ item, disabled, restore = false, onAction }: { item: ForeshadowPlanItem; disabled: boolean; restore?: boolean; onAction: () => void }) {
  return <article><strong>{item.action_kind === "plant" ? "计划埋入" : "计划回收"}</strong><span>{item.target.target_type === "part" ? "篇章" : "章节"}：{item.target.title}</span>{item.condition_text && <p>条件：{item.condition_text}</p>}{item.note && <p>备注：{item.note}</p>}<button className="btn btn-secondary" disabled={disabled} onClick={onAction}>{restore ? "恢复原计划" : "取消未来计划"}</button></article>;
}

function FactCard({ fact, reason, setReason, disabled, onRetract }: { fact: ForeshadowFact; reason: string; setReason: (value: string) => void; disabled: boolean; onRetract: () => void }) {
  return <article><strong>{fact.fact_kind === "planted" ? "作者确认已埋入" : "作者确认已回收"}</strong><span>章节：{fact.chapter.title}</span>{fact.note && <p>备注：{fact.note}</p>}<label>撤回原因<textarea value={reason} maxLength={2000} onChange={(event) => setReason(event.target.value)} /></label><button className="btn btn-secondary" disabled={disabled || !reason.trim()} onClick={onRetract}>撤回并保留历史</button></article>;
}
