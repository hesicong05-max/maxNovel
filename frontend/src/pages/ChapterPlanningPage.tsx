import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useParams, useSearchParams } from "react-router-dom";
import PlanningStructurePanel, { type PlanningSelection } from "@/components/PlanningStructurePanel";
import PlanningLoreAssignments from "@/components/PlanningLoreAssignments";
import PlanningGenerationPreflight, { type GenerationRecoveryState } from "@/components/PlanningGenerationPreflight";
import DemoGuide from "@/components/DemoGuide";
import { readDemoFixture } from "@/services/demoFixture";
import ForeshadowPlanningSummary from "@/components/ForeshadowPlanningSummary";
import { useAuth } from "@/components/AuthContext";
import { ApiError, api } from "@/services/api";
import { generationRunContractError } from "@/services/generationRuns";
import { pendingProjectOperationKey } from "@/services/pendingProjectOperations";
import { loadPendingTechnicalDemoExecution } from "@/services/technicalDemoExecution";
import { loadPendingCandidateManualEdit, readCandidateVersion } from "@/services/candidateVersionOperations";
import {
  loadPendingCandidateSelection,
  readCandidateSelectionCurrent,
} from "@/services/candidateSelectionOperations";
import {
  clearPendingGenerationExecution,
  createGenerationExecutionKey,
  loadPendingGenerationExecution,
  readGenerationAttemptByKey,
  readGenerationCandidate,
  readGenerationCapability,
  requestGenerationAttempt,
  savePendingGenerationExecution,
  type PendingGenerationExecution,
} from "@/services/generationExecution";
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
import type { DemoFixtureCurrentResponse } from "@/types/demo";
import type {
  GenerationAttemptResponse,
  GenerationCandidateResponse,
  GenerationCandidateSelectionCurrentResponse,
  GenerationCapabilityResponse,
  GenerationRunPrepareInput,
  GenerationRunResponse,
} from "@/types/generation";
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

function canonicalJson(value: unknown): string {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  const record = value as Record<string, unknown>;
  return `{${Object.keys(record).sort().map((key) => `${JSON.stringify(key)}:${canonicalJson(record[key])}`).join(",")}}`;
}

async function sha256Hex(value: string): Promise<string> {
  const digest = await globalThis.crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

async function receiptMatchesPending(
  receipt: PlanningOperationReceipt,
  operation: PendingPlanningOperation,
  projectId: string,
  planId?: string
): Promise<boolean> {
  const assignment = operation.action.startsWith("assignment_");
  const baseMatches = receipt.project_id === projectId
    && receipt.operation_key === operation.operation_key
    && receipt.operation_type === operation.action
    && receipt.receipt_kind === (assignment ? "assignment" : "structure");
  if (!baseMatches || operation.action !== "structure_reorder" || receipt.receipt_kind !== "structure") {
    return baseMatches;
  }
  const payload = operation.payload as Partial<PlanningReorderInput>;
  if (!planId || receipt.plan_id !== planId || !Number.isInteger(payload.expected_structure_version) || !Array.isArray(payload.parts)) {
    return false;
  }
  const structure = receipt.structure;
  if (!structure || typeof structure !== "object" || Array.isArray(structure)) return false;
  const typed = structure as Record<string, unknown>;
  const chapterCount = payload.parts.reduce((total, part) => total + part.chapter_ids.length, 0);
  if (
    receipt.changed !== true
    || receipt.previous_structure_version !== payload.expected_structure_version
    || receipt.new_structure_version !== payload.expected_structure_version + 1
    || typed.part_count !== payload.parts.length
    || typed.chapter_count !== chapterCount
    || typeof typed.digest !== "string"
  ) return false;
  try {
    return typed.digest === await sha256Hex(canonicalJson(payload.parts));
  } catch {
    return false;
  }
}

function replaceFailedGenerationPending(
  previous: PendingGenerationExecution,
  next: PendingGenerationExecution
): boolean {
  try {
    const key = pendingProjectOperationKey(previous.user_id, previous.project_id);
    const raw = sessionStorage.getItem(key);
    if (!raw || JSON.stringify(JSON.parse(raw)) !== JSON.stringify(previous)) return false;
    sessionStorage.setItem(key, JSON.stringify(next));
    return true;
  } catch {
    return false;
  }
}

export default function ChapterPlanningPage() {
  const { id } = useParams<{ id: string }>();
  const { user } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [searchParams, setSearchParams] = useSearchParams();
  const [plan, setPlan] = useState<NovelPlan | null>(null);
  const [loadState, setLoadState] = useState<LoadState>("loading");
  const [error, setError] = useState("");
  const [errorHint, setErrorHint] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [maintenance, setMaintenance] = useState(false);
  const [pending, setPending] = useState<PendingPlanningOperation | null>(null);
  const [reorderRecoveryState, setReorderRecoveryState] = useState<"idle" | "checking" | "not_found" | "unknown">("idle");
  const [mobileDetail, setMobileDetail] = useState(() => !!searchParams.get("target"));
  const [conflict, setConflict] = useState(false);
  const [assignmentConflict, setAssignmentConflict] = useState(false);
  const [assignmentResponse, setAssignmentResponse] = useState<PlanningAssignmentScopeResponse | null>(null);
  const [assignmentLoading, setAssignmentLoading] = useState(false);
  const [assignmentError, setAssignmentError] = useState("");
  const [assignmentRefreshRequired, setAssignmentRefreshRequired] = useState(false);
  const [assignmentSearchRefreshToken, setAssignmentSearchRefreshToken] = useState(0);
  const [pendingStorageIssue, setPendingStorageIssue] = useState<"corrupt" | "unavailable" | null>(null);
  const [foreignPending, setForeignPending] = useState<{ workspace: "foreshadow" | "generation_execution" | "technical_demo_execution" | "candidate_manual_edit" | "candidate_selection"; chapterId: string | null } | null>(null);
  const [serverSyncToken, setServerSyncToken] = useState(0);
  const [focusTarget, setFocusTarget] = useState<string | null>(null);
  const [assignmentFocusTarget, setAssignmentFocusTarget] = useState<{ elementId: string; scopeIdentity: string } | null>(null);
  const [refreshRequired, setRefreshRequired] = useState(false);
  const [hasUnsavedStructureDraft, setHasUnsavedStructureDraft] = useState(false);
  const [hasUnsavedPartCreationDraft, setHasUnsavedPartCreationDraft] = useState(false);
  const [generationRun, setGenerationRun] = useState<GenerationRunResponse | null>(null);
  const [generationBusy, setGenerationBusy] = useState(false);
  const [generationLoadingSaved, setGenerationLoadingSaved] = useState(false);
  const [generationError, setGenerationError] = useState("");
  const [generationFeedbackChapterId, setGenerationFeedbackChapterId] = useState<string | null>(null);
  const [generationRecoveryState, setGenerationRecoveryState] = useState<GenerationRecoveryState>("idle");
  const [generationRecovered, setGenerationRecovered] = useState(false);
  const [generationFocusToken, setGenerationFocusToken] = useState(0);
  const [generationFeedbackFocusToken, setGenerationFeedbackFocusToken] = useState(0);
  const [generationCapability, setGenerationCapability] = useState<GenerationCapabilityResponse | null>(null);
  const [generationAttempt, setGenerationAttempt] = useState<GenerationAttemptResponse | null>(null);
  const [generationCandidate, setGenerationCandidate] = useState<GenerationCandidateResponse | null>(null);
  const [generationExecutionPending, setGenerationExecutionPending] = useState<PendingGenerationExecution | null>(null);
  const [generationExecutionBusy, setGenerationExecutionBusy] = useState(false);
  const [generationCandidateLoading, setGenerationCandidateLoading] = useState(false);
  const [generationExecutionError, setGenerationExecutionError] = useState("");
  const [generationConfirmationOpen, setGenerationConfirmationOpen] = useState(false);
  const [generationConfirmationKind, setGenerationConfirmationKind] = useState<"new_attempt" | "original_retry" | null>(null);
  const [generationConfirmationIdentity, setGenerationConfirmationIdentity] = useState<{ runId: string; chapterId: string; contextChecksum: string; structureVersion: number; assignmentVersion: number; chapterLockVersion: number; operationKey: string | null } | null>(null);
  const [generationOriginalRetryAllowed, setGenerationOriginalRetryAllowed] = useState(false);
  const [generationExecutionRequiresNewPreflight, setGenerationExecutionRequiresNewPreflight] = useState(false);
  const [demoDescriptor, setDemoDescriptor] = useState<DemoFixtureCurrentResponse | null>(null);
  const [demoDescriptorStatus, setDemoDescriptorStatus] = useState<"loading" | "known" | "unknown">("loading");
  const [technicalDemoLocked, setTechnicalDemoLocked] = useState(false);
  const [candidateWorkspaceLocked, setCandidateWorkspaceLocked] = useState(true);
  const [candidateManualEditLocked, setCandidateManualEditLocked] = useState(false);
  const [candidateSelectionLocked, setCandidateSelectionLocked] = useState(false);
  const [candidateSelectionRecoveryRevision, setCandidateSelectionRecoveryRevision] = useState(0);
  const [candidateVersionRecoveryId, setCandidateVersionRecoveryId] = useState<string | null>(null);
  const [candidateSelectionCurrent, setCandidateSelectionCurrent] = useState<GenerationCandidateSelectionCurrentResponse | null>(null);
  const [candidateSelectionLoading, setCandidateSelectionLoading] = useState(false);
  const [candidateSelectionError, setCandidateSelectionError] = useState("");
  const conflictRef = useRef<HTMLDivElement | null>(null);
  const assignmentConflictRef = useRef<HTMLDivElement | null>(null);
  const globalGenerationFeedbackRef = useRef<HTMLDivElement | null>(null);
  const requestGeneration = useRef(0);
  const assignmentGeneration = useRef(0);
  const generationRunRequest = useRef(0);
  const generationExecutionRequest = useRef(0);
  const generationCandidateRequest = useRef(0);
  const generationExecutionPendingRef = useRef<PendingGenerationExecution | null>(null);
  const generationRunRef = useRef<GenerationRunResponse | null>(null);
  const generationCandidateRef = useRef<GenerationCandidateResponse | null>(null);
  const corruptRecoverySnapshot = useRef<string | null>(null);
  const generationIdentityRef = useRef({ projectId: id ?? "", userId: user?.id ?? "" });
  const acceptedGenerationRunId = useRef<string | null>(null);
  const generationPointerTransition = useRef<string | null>(null);
  const previousSelectionIdentity = useRef<string | null>(null);
  const technicalDemoHashFocusIdentity = useRef<string | null>(null);
  const candidateSelectionRequest = useRef(0);
  const candidateSelectionController = useRef<AbortController | null>(null);

  useEffect(() => {
    if (!id) return;
    const controller = new AbortController();
    setDemoDescriptorStatus("loading"); setDemoDescriptor(null);
    void readDemoFixture(controller.signal).then((value) => {
      if (controller.signal.aborted) return;
      setDemoDescriptor(value); setDemoDescriptorStatus("known");
    }).catch(() => {
      if (controller.signal.aborted) return;
      setDemoDescriptor(null); setDemoDescriptorStatus("unknown");
    });
    return () => controller.abort();
  }, [id]);
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
  generationExecutionPendingRef.current = generationExecutionPending;
  generationRunRef.current = generationRun;
  generationCandidateRef.current = generationCandidate;
  generationIdentityRef.current = { projectId: id ?? "", userId: user?.id ?? "" };

  const refreshCandidateSelection = useCallback(async () => {
    const selected = selectionRef.current;
    if (!id || !user || selected.kind !== "chapter") {
      throw new Error("当前未定位可读取的章节采用状态。");
    }
    const request = ++candidateSelectionRequest.current;
    candidateSelectionController.current?.abort();
    const controller = new AbortController();
    candidateSelectionController.current = controller;
    const expected = { userId: user.id, projectId: id, chapterId: selected.id };
    setCandidateSelectionLoading(true);
    setCandidateSelectionError("");
    try {
      const value = await readCandidateSelectionCurrent(expected, controller.signal);
      if (value.state === "selected") {
        const rawRun = await api.getGenerationRun(id, value.run_id, controller.signal);
        const contractError = generationRunContractError(rawRun, {
          projectId: id,
          chapterId: selected.id,
          runId: value.run_id,
        });
        if (contractError) throw new Error(contractError);
        if (rawRun.context_checksum !== value.context_checksum) {
          throw new Error("章节采用状态与对应检查记录的冻结上下文不一致。");
        }
        const detail = await readCandidateVersion({
          userId: user.id,
          projectId: id,
          chapterId: selected.id,
          runId: value.run_id,
          chapterTitle: rawRun.context_manifest.chapter.title,
          candidateId: value.candidate.id,
        }, controller.signal);
        if (!Object.entries(value.candidate).every(([key, item]) =>
          detail[key as keyof typeof detail] === item
        )) throw new Error("章节采用版本与权威候选详情不一致。");
      }
      const currentSelection = selectionRef.current;
      if (request !== candidateSelectionRequest.current || controller.signal.aborted
        || generationIdentityRef.current.projectId !== expected.projectId
        || generationIdentityRef.current.userId !== expected.userId
        || currentSelection.kind !== "chapter" || currentSelection.id !== expected.chapterId) {
        throw new Error("章节已变化，已忽略迟到的采用状态。");
      }
      setCandidateSelectionCurrent(value);
      return value;
    } catch (cause) {
      if (!controller.signal.aborted && request === candidateSelectionRequest.current) {
        setCandidateSelectionError(`${errorMessage(cause)} 只会重新读取采用状态，不会提交候选。`);
      }
      throw cause;
    } finally {
      if (!controller.signal.aborted && request === candidateSelectionRequest.current) {
        setCandidateSelectionLoading(false);
      }
    }
  }, [id, user?.id]);

  useEffect(() => {
    candidateSelectionRequest.current += 1;
    candidateSelectionController.current?.abort();
    setCandidateSelectionCurrent(null);
    setCandidateSelectionError("");
    setCandidateSelectionLoading(false);
    if (!id || !user || loadState !== "ready" || selection.kind !== "chapter" || !located.chapter) return;
    void refreshCandidateSelection().catch(() => {});
    return () => {
      candidateSelectionRequest.current += 1;
      candidateSelectionController.current?.abort();
    };
  }, [id, user?.id, loadState, selection.kind, selection.id, located.chapter?.id, refreshCandidateSelection]);

  useEffect(() => {
    const expectedPath = id ? `/project/${id}/plan/chapters` : "";
    if (location.pathname !== expectedPath || location.hash !== "#demo-technical-generation") {
      technicalDemoHashFocusIdentity.current = null;
      return;
    }
    if (
      !id || !user || loadState !== "ready"
      || demoDescriptor?.state !== "ready" || demoDescriptor.project_id !== id
      || selection.kind !== "chapter" || selection.id !== demoDescriptor.chapter_id
      || located.chapter?.id !== selection.id || !located.part
    ) return;
    const identity = `${user.id}:${id}:${selection.id}`;
    if (technicalDemoHashFocusIdentity.current === identity) return;
    const target = document.getElementById("demo-technical-generation");
    if (!target) return;
    const frame = window.requestAnimationFrame(() => {
      if (
        generationIdentityRef.current.projectId !== id
        || generationIdentityRef.current.userId !== user.id
        || selectionRef.current.kind !== "chapter" || selectionRef.current.id !== selection.id
        || !target.isConnected || document.getElementById("demo-technical-generation") !== target
      ) return;
      technicalDemoHashFocusIdentity.current = identity;
      target.scrollIntoView({ block: "start", behavior: "auto" });
      target.focus({ preventScroll: true });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [id, user?.id, loadState, demoDescriptor, selection.kind, selection.id, located.chapter, located.part, location.pathname, location.hash]);
  const generationFeedbackInlineVisible = !!generationFeedbackChapterId
    && selection.kind === "chapter"
    && selection.id === generationFeedbackChapterId
    && located.chapter?.status === "active"
    && located.part?.status === "active";
  const globalGenerationFeedbackVisible = !!generationError && !generationFeedbackInlineVisible;
  const candidateVersionLocked = candidateWorkspaceLocked
    || candidateManualEditLocked || candidateSelectionLocked;
  const planningWriteDisabled = busy || generationBusy || generationExecutionBusy || technicalDemoLocked || candidateVersionLocked || !!generationExecutionPending || !!pending || maintenance || conflict || assignmentConflict
    || refreshRequired || assignmentRefreshRequired || !!pendingStorageIssue || !!foreignPending;
  const structureReorderDisabledReason = hasUnsavedStructureDraft || hasUnsavedPartCreationDraft
    ? "请先保存或放弃当前修改，再调整顺序。"
    : undefined;

  const handleTechnicalDemoLockChange = useCallback((locked: boolean) => {
    if (locked) {
      setTechnicalDemoLocked(true);
      return;
    }
    if (!id || !user) return;
    const current = loadPendingPlanningOperation(user.id, id);
    if (current.status === "foreign" && current.workspace === "technical_demo_execution") {
      setTechnicalDemoLocked(true);
      return;
    }
    setTechnicalDemoLocked(false);
    setForeignPending((value) => value?.workspace === "technical_demo_execution" ? null : value);
  }, [id, user]);
  const handleCandidateVersionLockChange = useCallback((locked: boolean) => {
    setCandidateWorkspaceLocked(locked);
    if (!id || !user) return;
    const manualCurrent = loadPendingCandidateManualEdit(user.id, id);
    const selectionCurrent = loadPendingCandidateSelection(user.id, id);
    setCandidateManualEditLocked(manualCurrent.status === "available"
      || manualCurrent.status === "corrupt" || manualCurrent.status === "unavailable");
    setCandidateSelectionLocked(selectionCurrent.status === "available"
      || selectionCurrent.status === "corrupt" || selectionCurrent.status === "unavailable");
    if (!locked && manualCurrent.status === "missing" && selectionCurrent.status === "missing") {
      setCandidateVersionRecoveryId(null);
      setForeignPending((value) => value?.workspace === "candidate_manual_edit"
        || value?.workspace === "candidate_selection" ? null : value);
    }
  }, [id, user]);
  const assignmentWriteDisabled = planningWriteDisabled || assignmentLoading || !!assignmentError;
  const generationStale = useMemo(() => {
    if (!generationRun || !plan || selection.kind !== "chapter" || !located.chapter || !assignmentResponse) return false;
    if (
      generationRun.planning_chapter_id !== located.chapter.id ||
      generationRun.structure_version !== plan.structure_version ||
      generationRun.assignment_version !== plan.assignment_version ||
      generationRun.chapter_lock_version !== located.chapter.lock_version
    ) return true;
    const currentVersions = new Map(
      assignmentResponse.effective_elements
        .filter((item) => item.generation_eligible)
        .map((item) => [item.element_id, item.current_content_version])
    );
    if (currentVersions.size !== generationRun.context_manifest.elements.length) return true;
    return generationRun.context_manifest.elements.some(
      (item) => currentVersions.get(item.element_id) !== item.version.version_no
    );
  }, [generationRun, plan, selection.kind, located.chapter, assignmentResponse]);

  const generationDisabledReason = useMemo(() => {
    if (selection.kind !== "chapter" || !located.chapter) return "请选择一个活动章节。";
    if (located.chapter.status !== "active" || located.part?.status !== "active") return "归档章节不能创建新的检查记录。";
    if (hasUnsavedStructureDraft) return "当前章节或目标篇章有未保存修改；请先保存或恢复，再检查服务端权威内容。";
    if (pendingStorageIssue) return "浏览器恢复存储不可用；为避免丢失结果，已停止新的检查。";
    if (busy || generationBusy) return "当前操作尚未结束，请等待结果确认。";
    if (pending?.action === "generation_prepare") return "上次生成前检查仍等待结果确认；请先核对原操作编号。";
    if (pending) return "当前还有一项规划操作等待确认；请先完成恢复。";
    if (maintenance || refreshRequired || assignmentRefreshRequired || conflict || assignmentConflict) return "当前资料尚未完成权威同步，请先刷新并解决冲突。";
    if (assignmentLoading || !assignmentResponse) return "正在读取本章规划与设定。";
    if (assignmentError) return "本章设定暂时无法读取，请先重新加载。";
    if (assignmentResponse.scope.scope_type !== "chapter" || assignmentResponse.scope.scope_target_id !== located.chapter.id) return "本章设定范围尚未完成同步。";
    if (assignmentResponse.assignment_version !== plan?.assignment_version) return "规划与设定分配版本尚未同步。";
    if (assignmentResponse.counts.ineligible > 0) return "本章上下文包含已停用、未确认或冲突的设定；请先处理全部失效设定。";
    if (assignmentResponse.counts.generation_eligible < 1) return "本章及上级范围没有可用于生成的设定，请先管理本章设定。";
    return "";
  }, [selection.kind, located.chapter, located.part, hasUnsavedStructureDraft, pendingStorageIssue, busy, generationBusy, pending, maintenance, refreshRequired, assignmentRefreshRequired, conflict, assignmentConflict, assignmentLoading, assignmentResponse, assignmentError, plan?.assignment_version]);

  const generationExecutionDisabledReason = useMemo(() => {
    if (!generationRun) return "请先完成并保存生成前上下文检查。";
    if (generationExecutionRequiresNewPreflight) return "模型能力已变化，必须重新检查最新上下文。";
    if (generationStale) return "当前检查记录已过期，请先重新检查最新上下文。";
    if (generationExecutionPending) return "上次生成尚未完成可验证恢复，只能按原编号核对。";
    if (generationBusy || busy || generationExecutionBusy) return "当前操作尚未结束。";
    if (generationDisabledReason) return generationDisabledReason;
    return "";
  }, [generationRun, generationExecutionRequiresNewPreflight, generationStale, generationExecutionPending, generationBusy, busy, generationExecutionBusy, generationDisabledReason]);

  const candidateSelectionDisabledReason = useMemo(() => {
    if (selection.kind !== "chapter" || !located.chapter || !located.part) return "请选择要采用候选的章节。";
    if (located.chapter.status !== "active" || located.part.status !== "active") return "归档章节不能修改采用版本；请先恢复章节。";
    if (pendingStorageIssue) return "浏览器恢复存储尚未恢复，不能提交候选采用。";
    if (maintenance) return "项目正在维护，候选采用暂不可提交。";
    if (refreshRequired || assignmentRefreshRequired || conflict || assignmentConflict) return "当前规划或设定尚未完成权威同步，不能提交候选采用。";
    if (candidateSelectionLoading || !candidateSelectionCurrent) return "正在读取章节权威采用状态。";
    if (candidateSelectionError) return "章节权威采用状态读取失败，请先重新读取。";
    return "";
  }, [selection.kind, located.chapter, located.part, pendingStorageIssue, maintenance,
    refreshRequired, assignmentRefreshRequired, conflict, assignmentConflict,
    candidateSelectionLoading, candidateSelectionCurrent, candidateSelectionError]);

  const generationRunActionsDisabledReason = candidateVersionLocked
    ? "候选版本另存、草稿恢复或严格校验尚未完成；不能重新检查或关闭记录。"
    : technicalDemoLocked
    ? "技术模拟恢复或候选校验尚未完成；不能重新检查或关闭记录。"
    : generationExecutionPending || (generationAttempt && !generationCandidate)
    ? "生成执行收据仍在处理中；只能核对或处理当前生成，不能重新检查或关闭记录。"
    : "";

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
    setForeignPending(null);
    assignmentGeneration.current += 1;
    setRefreshRequired(false);
    setBusy(false);
    setAssignmentFocusTarget(null);
    setHasUnsavedStructureDraft(false);
    setHasUnsavedPartCreationDraft(false);
    setGenerationRun(null);
    setGenerationBusy(false);
    setGenerationLoadingSaved(false);
    setGenerationError("");
    setGenerationFeedbackChapterId(null);
    setGenerationRecoveryState("idle");
    setGenerationRecovered(false);
    setGenerationCapability(null);
    setGenerationAttempt(null);
    setGenerationCandidate(null);
    generationExecutionPendingRef.current = null;
    setGenerationExecutionPending(null);
    setGenerationExecutionBusy(false);
    setGenerationCandidateLoading(false);
    setGenerationExecutionError("");
    setGenerationConfirmationOpen(false);
    setGenerationConfirmationKind(null);
    setGenerationConfirmationIdentity(null);
    setGenerationOriginalRetryAllowed(false);
    setGenerationExecutionRequiresNewPreflight(false);
    setTechnicalDemoLocked(false);
    setCandidateWorkspaceLocked(true);
    setCandidateManualEditLocked(false);
    setCandidateSelectionLocked(false);
    setCandidateVersionRecoveryId(null);
    corruptRecoverySnapshot.current = null;
    generationExecutionRequest.current += 1;
    generationCandidateRequest.current += 1;
    generationRunRequest.current += 1;
    acceptedGenerationRunId.current = null;
    generationPointerTransition.current = null;
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
    const runId = searchParams.get("generation_run");
    if (!runId && generationPointerTransition.current) return;
    if (runId && generationPointerTransition.current === runId) generationPointerTransition.current = null;
    if (generationExecutionPending) {
      generationRunRequest.current += 1;
      setGenerationLoadingSaved(false);
      return;
    }
    if (pending?.action === "generation_prepare") {
      generationRunRequest.current += 1;
      setGenerationLoadingSaved(false);
      return;
    }
    if (!id || loadState !== "ready" || selection.kind !== "chapter" || !runId) {
      generationRunRequest.current += 1;
      setGenerationLoadingSaved(false);
      if (!runId || selection.kind !== "chapter") {
        setGenerationRun(null);
        setGenerationRecovered(false);
      }
      return;
    }
    if (acceptedGenerationRunId.current === runId || (generationRun?.id === runId && generationRun.planning_chapter_id === selection.id)) return;
    const controller = new AbortController();
    const request = ++generationRunRequest.current;
    setGenerationLoadingSaved(true);
    setGenerationError("");
    setGenerationFeedbackChapterId(selection.id);
    void api.getGenerationRun(id, runId, controller.signal)
      .then((value) => {
        if (request !== generationRunRequest.current) return;
        const contractError = generationRunContractError(value, { projectId: id, chapterId: selection.id, runId });
        if (contractError) {
          setGenerationRun(null);
          setGenerationRecoveryState("corrupt");
          setGenerationError(contractError);
          return;
        }
        setGenerationRun(value);
        acceptedGenerationRunId.current = value.id;
        setGenerationRecovered(true);
        setGenerationRecoveryState("idle");
      })
      .catch((cause) => {
        if (controller.signal.aborted || request !== generationRunRequest.current) return;
        setGenerationRun(null);
        setGenerationRecoveryState("saved_unavailable");
        setGenerationError(cause instanceof ApiError && cause.status === 404
          ? "这条已保存检查记录不存在或当前账号无权查看；可以关闭无效记录指针。"
          : errorMessage(cause));
      })
      .finally(() => {
        if (request === generationRunRequest.current) setGenerationLoadingSaved(false);
      });
    return () => controller.abort();
  }, [id, loadState, selection.kind, selection.id, searchParams.get("generation_run"), pending?.action, generationExecutionPending?.operation_key]);

  useEffect(() => {
    const attemptId = searchParams.get("generation_attempt");
    const candidateId = searchParams.get("generation_candidate");
    if (
      !id || !user || !generationRun || generationExecutionPending
      || selection.kind !== "chapter" || !located.chapter
      || !attemptId || !candidateId
    ) return;
    if (generationCandidate?.id === candidateId) return;
    const controller = new AbortController();
    const request = ++generationCandidateRequest.current;
    const expectedIdentity = {
      projectId: id,
      userId: user.id,
      chapterId: selection.id,
      runId: generationRun.id,
      contextChecksum: generationRun.context_checksum,
      attemptId,
      candidateId,
    };
    setGenerationCandidateLoading(true);
    setGenerationExecutionError("");
    void readGenerationCandidate({
      projectId: id,
      runId: generationRun.id,
      chapterId: selection.id,
      attemptId,
      candidateId,
      userId: user.id,
      chapterTitle: located.chapter.title,
    }, controller.signal).then((value) => {
      const currentRun = generationRunRef.current;
      if (
        request !== generationCandidateRequest.current
        || generationIdentityRef.current.projectId !== expectedIdentity.projectId
        || generationIdentityRef.current.userId !== expectedIdentity.userId
        || selectionRef.current.kind !== "chapter"
        || selectionRef.current.id !== expectedIdentity.chapterId
        || currentRun?.id !== expectedIdentity.runId
        || currentRun.context_checksum !== expectedIdentity.contextChecksum
      ) return;
      setGenerationCandidate(value);
    }).catch((cause) => {
      if (controller.signal.aborted || request !== generationCandidateRequest.current) return;
      setGenerationCandidate(null);
      setGenerationExecutionError(`${errorMessage(cause)} 候选指针只用于读取；系统不会因此发起生成。`);
    }).finally(() => {
      if (request === generationCandidateRequest.current) setGenerationCandidateLoading(false);
    });
    return () => {
      controller.abort();
      if (request === generationCandidateRequest.current) generationCandidateRequest.current += 1;
    };
  }, [id, user?.id, generationRun?.id, generationExecutionPending?.operation_key, selection.kind, selection.id, located.chapter?.title, searchParams.get("generation_attempt"), searchParams.get("generation_candidate")]);

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
    if (globalGenerationFeedbackVisible && generationFeedbackFocusToken > 0) {
      globalGenerationFeedbackRef.current?.focus();
    }
  }, [globalGenerationFeedbackVisible, generationFeedbackFocusToken]);

  useEffect(() => {
    const current = scopeIdentity(selection.kind, selection.kind === "novel" ? id ?? selection.id : selection.id);
    const previous = previousSelectionIdentity.current;
    previousSelectionIdentity.current = current;
    if (
      previous &&
      previous !== current &&
      pending?.action !== "generation_prepare" &&
      generationFeedbackChapterId &&
      (selection.kind !== "chapter" || selection.id !== generationFeedbackChapterId)
    ) {
      setGenerationError("");
      setGenerationFeedbackChapterId(null);
      setGenerationRecoveryState("idle");
    }
  }, [id, selection.kind, selection.id, pending?.action, generationFeedbackChapterId]);

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

  async function acceptGenerationRun(
    value: GenerationRunResponse,
    operation: PendingPlanningOperation<GenerationRunPrepareInput>,
    generation: number,
    recovered: boolean,
    focusResult: boolean
  ): Promise<boolean> {
    if (!id || !user || generation !== requestGeneration.current) return false;
    if (!recovered && (selectionRef.current.kind !== "chapter" || selectionRef.current.id !== operation.target_id)) {
      setGenerationRecoveryState("unknown");
      setGenerationError("检查响应返回时当前章节已经变化；已保留原操作编号，请先核对结果。");
      setGenerationFeedbackChapterId(operation.target_id);
      if (focusResult) setGenerationFeedbackFocusToken((token) => token + 1);
      return false;
    }
    const contractError = generationRunContractError(value, {
      projectId: id,
      chapterId: operation.target_id ?? "",
      operationKey: operation.operation_key,
      payload: operation.payload,
    });
    if (contractError) {
      setGenerationRun(null);
      setGenerationRecoveryState("corrupt");
      setGenerationError(contractError);
      setGenerationFeedbackChapterId(operation.target_id);
      if (focusResult) setGenerationFeedbackFocusToken((token) => token + 1);
      return false;
    }
    acceptedGenerationRunId.current = value.id;
    generationPointerTransition.current = value.id;
    const params = new URLSearchParams(searchParams);
    params.set("scope", "chapter");
    params.set("target", operation.target_id!);
    params.set("generation_run", value.id);
    setSearchParams(params, { replace: true });
    setGenerationRun(value);
    setGenerationExecutionRequiresNewPreflight(false);
    setGenerationRecovered(recovered || value.replayed);
    setGenerationError("");
    setGenerationFeedbackChapterId(null);
    setGenerationRecoveryState("idle");
    if (focusResult) setGenerationFocusToken((token) => token + 1);
    if (!clearPendingPlanningOperation(user.id, id)) {
      setPendingStorageIssue("unavailable");
      setGenerationError("检查记录已在服务端保存，但浏览器无法清除原恢复线索；继续保持禁写，请重新加载页面后核对。");
      setGenerationFeedbackChapterId(operation.target_id);
      setGenerationFeedbackFocusToken((token) => token + 1);
      return false;
    }
    setPending(null);
    selectionRef.current = { kind: "chapter", id: operation.target_id! };
    const refreshed = await refreshPlanningAndAssignmentScope("chapter", operation.target_id!, generation);
    if (generation !== requestGeneration.current) return false;
    if (!refreshed) {
      setAssignmentRefreshRequired(true);
      setGenerationError("检查记录已保存，但权威规划与设定尚未完整读取；已保持禁写，请重新读取后再继续。");
      setGenerationFeedbackChapterId(operation.target_id);
      setGenerationFeedbackFocusToken((token) => token + 1);
      return false;
    }
    setNotice(recovered || value.replayed
      ? "已找回上次保存的生成前检查记录，并重新载入权威规划与设定。"
      : "生成前检查记录已保存并完成权威同步；尚未调用 AI，也未创建或修改正文。");
    return true;
  }

  async function reconcileGenerationPending(
    operation: PendingPlanningOperation<GenerationRunPrepareInput>,
    generation = requestGeneration.current,
    focusResult = false
  ): Promise<boolean> {
    if (!id || generation !== requestGeneration.current) return false;
    setGenerationRecoveryState("checking");
    setGenerationError("");
    setGenerationFeedbackChapterId(operation.target_id);
    try {
      const value = await api.getGenerationRunByKey(id, operation.operation_key);
      if (generation !== requestGeneration.current) return false;
      const accepted = await acceptGenerationRun(value, operation, generation, true, focusResult);
      return accepted;
    } catch (cause) {
      if (generation !== requestGeneration.current) return false;
      if (
        cause instanceof ApiError &&
        cause.status === 404 &&
        cause.code === "GENERATION_RUN_NOT_FOUND" &&
        cause.retryable &&
        cause.recommendedAction === "retry_original_prepare"
      ) {
        setGenerationRecoveryState("not_found");
        setGenerationError("");
      } else {
        setGenerationRecoveryState("unknown");
        setGenerationError(cause instanceof ApiError && cause.status === 404
          ? "服务端未给出可安全重试原请求的确定答复；已保留原操作编号并停止创建新记录。"
          : errorMessage(cause));
        if (cause instanceof ApiError && cause.status === 503) setMaintenance(true);
      }
      if (focusResult) setGenerationFeedbackFocusToken((token) => token + 1);
      return false;
    }
  }

  useEffect(() => {
    if (!id || !user || loadState !== "ready") return;
    const sharedGate = loadPendingPlanningOperation(user.id, id);
    const candidateGate = loadPendingCandidateManualEdit(user.id, id);
    const selectionGate = loadPendingCandidateSelection(user.id, id);
    setCandidateWorkspaceLocked(false);
    setCandidateManualEditLocked(candidateGate.status === "available"
      || candidateGate.status === "corrupt" || candidateGate.status === "unavailable");
    setCandidateSelectionLocked(selectionGate.status === "available"
      || selectionGate.status === "corrupt" || selectionGate.status === "unavailable");
    const executionLoaded = loadPendingGenerationExecution(user.id, id);
    if (executionLoaded.status === "available") {
      const operation = executionLoaded.operation;
      setPending(null);
      setForeignPending(null);
      setPendingStorageIssue(null);
      generationExecutionPendingRef.current = operation;
      setGenerationExecutionPending(operation);
      setGenerationExecutionError("");
      const target = planRef.current ? locate(planRef.current, { kind: "chapter", id: operation.chapter_id }) : { chapter: null };
      if (!target.chapter) {
        setGenerationExecutionError("生成恢复记录对应的章节当前不存在；已保留原编号并停止新生成。");
        return;
      }
      const params = new URLSearchParams(searchParams);
      params.set("scope", "chapter");
      params.set("target", operation.chapter_id);
      params.set("generation_run", operation.run_id);
      setSearchParams(params, { replace: true });
      setMobileDetail(true);
      const request = ++generationExecutionRequest.current;
      setGenerationExecutionBusy(true);
      void Promise.all([
        api.getGenerationRun(id, operation.run_id),
        readGenerationAttemptByKey({
          projectId: id,
          runId: operation.run_id,
          chapterId: operation.chapter_id,
          operationKey: operation.operation_key,
          contextChecksum: operation.payload.expected_context_checksum,
          capabilityChecksum: operation.payload.expected_capability_checksum,
        }),
      ]).then(async ([savedRun, savedAttempt]) => {
        if (request !== generationExecutionRequest.current) return;
        const contractError = generationRunContractError(savedRun, {
          projectId: id,
          chapterId: operation.chapter_id,
          runId: operation.run_id,
        });
        if (contractError) throw new Error(contractError);
        setGenerationRun(savedRun);
        await acceptGenerationAttempt(savedAttempt, operation, savedRun);
      }).catch((cause) => {
        if (request !== generationExecutionRequest.current) return;
        const safeMissing = cause instanceof ApiError
          && cause.status === 404
          && cause.code === "GENERATION_ATTEMPT_NOT_FOUND"
          && cause.retryable
          && cause.recommendedAction === "retry_original_execute";
        setGenerationOriginalRetryAllowed(safeMissing);
        setGenerationExecutionError(safeMissing
          ? "服务端明确未找到原生成尝试。如需继续，必须由你显式使用原编号和原载荷重试。"
          : `${errorMessage(cause)} 已保留原编号，不会自动再次调用模型。`);
      }).finally(() => {
        if (request === generationExecutionRequest.current) setGenerationExecutionBusy(false);
      });
      return;
    }
    if (executionLoaded.status === "corrupt" || executionLoaded.status === "unavailable") {
      corruptRecoverySnapshot.current = executionLoaded.status === "corrupt"
        ? sessionStorage.getItem(pendingProjectOperationKey(user.id, id))
        : null;
      generationExecutionPendingRef.current = null;
      setGenerationExecutionPending(null);
      setPendingStorageIssue(executionLoaded.status);
      setGenerationExecutionError("");
      setError(executionLoaded.status === "corrupt"
        ? "检测到损坏或身份不匹配的浏览器恢复记录，已安全停止全部写入。"
        : "浏览器恢复存储不可用；已安全停止全部写入。");
      return;
    }
    generationExecutionPendingRef.current = null;
    setGenerationExecutionPending(null);
    setGenerationAttempt(null);
    setGenerationCandidate(null);
    setGenerationExecutionError("");
    const loaded = sharedGate;
    if (loaded.status === "missing") {
      corruptRecoverySnapshot.current = null;
      setPending(null); setPendingStorageIssue(null); setForeignPending(null);
      setCandidateWorkspaceLocked(false);
      setCandidateManualEditLocked(false);
      setCandidateSelectionLocked(false);
      setCandidateSelectionRecoveryRevision((value) => value + 1);
      setCandidateVersionRecoveryId(null);
      return;
    }
    if (loaded.status === "foreign") {
      setPending(null);
      setPendingStorageIssue(null);
      if (loaded.workspace !== "candidate_manual_edit" && loaded.workspace !== "candidate_selection") {
        setCandidateWorkspaceLocked(false);
        setCandidateManualEditLocked(false);
        setCandidateSelectionLocked(false);
        setCandidateVersionRecoveryId(null);
      }
      const generationPending = loaded.workspace === "generation_execution"
        ? loadPendingGenerationExecution(user.id, id)
        : null;
      const technicalPending = loaded.workspace === "technical_demo_execution"
        ? loadPendingTechnicalDemoExecution(user.id, id)
        : null;
      const candidatePending = loaded.workspace === "candidate_manual_edit"
        ? loadPendingCandidateManualEdit(user.id, id)
        : null;
      const selectionPending = loaded.workspace === "candidate_selection"
        ? loadPendingCandidateSelection(user.id, id)
        : null;
      if (technicalPending?.status === "corrupt" || technicalPending?.status === "unavailable") {
        corruptRecoverySnapshot.current = technicalPending.status === "corrupt"
          ? sessionStorage.getItem(pendingProjectOperationKey(user.id, id))
          : null;
        setForeignPending(null);
        setTechnicalDemoLocked(true);
        setPendingStorageIssue(technicalPending.status);
        setError(technicalPending.status === "corrupt"
          ? "检测到损坏或身份不匹配的技术模拟恢复记录，已安全停止全部写入。"
          : "浏览器恢复存储不可用；已安全停止全部写入。");
        return;
      }
      if (candidatePending?.status === "corrupt" || candidatePending?.status === "unavailable") {
        corruptRecoverySnapshot.current = candidatePending.status === "corrupt"
          ? sessionStorage.getItem(pendingProjectOperationKey(user.id, id))
          : null;
        setForeignPending(null);
        setCandidateManualEditLocked(true);
        setPendingStorageIssue(candidatePending.status);
        setError(candidatePending.status === "corrupt"
          ? "检测到损坏或身份不匹配的候选版本恢复记录，已安全停止全部写入。"
          : "浏览器恢复存储不可用；已安全停止全部写入。");
        return;
      }
      if (selectionPending?.status === "corrupt" || selectionPending?.status === "unavailable") {
        corruptRecoverySnapshot.current = selectionPending.status === "corrupt"
          ? sessionStorage.getItem(pendingProjectOperationKey(user.id, id))
          : null;
        setForeignPending(null);
        setCandidateSelectionLocked(true);
        setPendingStorageIssue(selectionPending.status);
        setError(selectionPending.status === "corrupt"
          ? "检测到损坏或身份不匹配的候选采用恢复记录，已安全停止全部写入。"
          : "浏览器恢复存储不可用；已安全停止全部写入。");
        return;
      }
      setForeignPending({
        workspace: loaded.workspace,
        chapterId: generationPending?.status === "available" ? generationPending.operation.chapter_id
          : technicalPending?.status === "available" ? technicalPending.operation.chapter_id
            : candidatePending?.status === "available" ? candidatePending.operation.chapter_id
              : selectionPending?.status === "available" ? selectionPending.operation.chapter_id : null,
      });
      if (technicalPending?.status === "available") {
        setTechnicalDemoLocked(true);
        const operation = technicalPending.operation;
        const params = new URLSearchParams(searchParams);
        params.set("scope", "chapter");
        params.set("target", operation.chapter_id);
        params.set("generation_run", operation.run_id);
        setSearchParams(params, { replace: true });
        setMobileDetail(true);
      }
      if (candidatePending?.status === "available") {
        setCandidateManualEditLocked(true);
        const operation = candidatePending.operation;
        setCandidateVersionRecoveryId(operation.payload.parent_candidate_id);
        const params = new URLSearchParams(searchParams);
        params.set("scope", "chapter");
        params.set("target", operation.chapter_id);
        params.set("generation_run", operation.run_id);
        params.set("candidate_version", operation.payload.parent_candidate_id);
        setSearchParams(params, { replace: true });
        setMobileDetail(true);
      }
      if (selectionPending?.status === "available") {
        setCandidateSelectionLocked(true);
        const operation = selectionPending.operation;
        setCandidateVersionRecoveryId(operation.expected_target.id);
        const params = new URLSearchParams(searchParams);
        params.set("scope", "chapter");
        params.set("target", operation.chapter_id);
        params.set("generation_run", operation.run_id);
        params.set("candidate_version", operation.expected_target.id);
        setSearchParams(params, { replace: true });
        setMobileDetail(true);
      }
      return;
    }
    if (loaded.status === "corrupt" || loaded.status === "unavailable") {
      corruptRecoverySnapshot.current = loaded.status === "corrupt"
        ? sessionStorage.getItem(pendingProjectOperationKey(user.id, id))
        : null;
      setPending(null);
      setPendingStorageIssue(loaded.status);
      setError(loaded.status === "corrupt"
        ? "检测到损坏或不受支持的规划恢复记录，已安全停止全部规划写入。"
        : "浏览器会话存储当前不可用，无法保证写入可恢复；已安全停止全部规划写入。");
      return;
    }
    const stored = loaded.operation;
    setForeignPending(null);
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
    if (stored.action === "structure_reorder") {
      void reconcileStructureReorderPending(stored, generation);
      return;
    }
    if (stored.action === "generation_prepare") {
      generationRunRequest.current += 1;
      setGenerationLoadingSaved(false);
      void reconcileGenerationPending(
        stored as unknown as PendingPlanningOperation<GenerationRunPrepareInput>,
        generation,
        false
      );
      return;
    }
    void api.getPlanningOperation(id, stored.operation_key)
      .then(async (receipt) => {
        if (generation !== requestGeneration.current) return;
        if (!await receiptMatchesPending(receipt, stored, id, planRef.current?.id)) {
          corruptRecoverySnapshot.current = sessionStorage.getItem(pendingProjectOperationKey(user.id, id));
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
    if (
      changingScope &&
      pending?.action === "generation_prepare" &&
      (next.kind !== "chapter" || next.id !== pending.target_id)
    ) {
      setNotice("上次生成前检查仍等待确认；请先返回发起章节核对原操作编号。");
      return;
    }
    if (
      changingScope && generationExecutionPending
      && (next.kind !== "chapter" || next.id !== generationExecutionPending.chapter_id)
    ) {
      setNotice("生成执行仍等待核对；已保持在发起章节，避免把执行收据显示到其他范围。");
      return;
    }
    if (changingScope && technicalDemoLocked) {
      setNotice("技术模拟仍在按原编号恢复；已保持在发起章节，避免把候选显示到其他范围。");
      return;
    }
    if (changingScope && candidateVersionLocked) {
      setNotice("候选版本另存或草稿仍在恢复；已保持在原章节，避免把候选显示到其他范围。");
      return;
    }
    if (changingScope && !confirmEditorUnload()) return;
    if (changingScope) {
      selectionRef.current = next;
      generationRunRequest.current += 1;
      generationCandidateRequest.current += 1;
      acceptedGenerationRunId.current = null;
      generationPointerTransition.current = null;
      setGenerationRun(null);
      setGenerationRecovered(false);
      setGenerationLoadingSaved(false);
      setGenerationCapability(null);
      setGenerationAttempt(null);
      setGenerationCandidate(null);
      setGenerationCandidateLoading(false);
      setGenerationExecutionError("");
    }
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
    const expectedPlanId = planRef.current?.id;
    if (!savePendingPlanningOperation(operation)) {
      setPendingStorageIssue("unavailable");
      setError("浏览器无法安全保存操作恢复信息，已停止写入。请检查会话存储设置。");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const result = await request(payload) as PlanningOperationReceipt & { affected_node?: { id?: string } | null };
      if (generation !== requestGeneration.current) return;
      const matchesReorderReceipt = action !== "structure_reorder"
        || await receiptMatchesPending(result, operation as PendingPlanningOperation, id, expectedPlanId);
      if (generation !== requestGeneration.current) return;
      if (!matchesReorderReceipt) {
        corruptRecoverySnapshot.current = sessionStorage.getItem(pendingProjectOperationKey(user.id, id));
        setPending(operation as PendingPlanningOperation);
        setPendingStorageIssue("corrupt");
        setError("服务器返回的排序收据与本地全量结构不一致；已保留恢复记录并停止全部规划写入。");
        return;
      }
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
        if (action === "structure_reorder") setReorderRecoveryState("unknown");
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

  async function executeGenerationPrepare(
    existing?: PendingPlanningOperation<GenerationRunPrepareInput>
  ) {
    if (!id || !user || generationBusy) return;
    if (!existing && (!plan || selection.kind !== "chapter" || !located.chapter)) return;
    if (!existing && generationDisabledReason) return;
    const body: GenerationRunPrepareInput = existing?.payload ?? {
      operation_key: createPlanningOperationKey("generation_prepare"),
      expected_structure_version: plan!.structure_version,
      expected_assignment_version: plan!.assignment_version,
      expected_chapter_lock_version: located.chapter!.lock_version,
    };
    const operation: PendingPlanningOperation<GenerationRunPrepareInput> = existing ?? {
      schema_version: 1,
      user_id: user.id,
      project_id: id,
      operation_key: body.operation_key,
      action: "generation_prepare",
      target_id: located.chapter!.id,
      payload: body,
      created_at: new Date().toISOString(),
    };
    if (
      operation.user_id !== user.id ||
      operation.project_id !== id ||
      !operation.target_id ||
      operation.operation_key !== operation.payload.operation_key
    ) {
      setGenerationRecoveryState("corrupt");
      setGenerationError("恢复请求与当前项目或章节不一致，已停止重试。");
      setGenerationFeedbackChapterId(operation.target_id);
      return;
    }
    if (!savePendingPlanningOperation(operation)) {
      setPendingStorageIssue("unavailable");
      setGenerationError("浏览器无法安全保存检查恢复信息，已停止请求。请检查会话存储设置。");
      setGenerationFeedbackChapterId(operation.target_id);
      setGenerationFeedbackFocusToken((token) => token + 1);
      return;
    }
    const generation = requestGeneration.current;
    setPending(operation as unknown as PendingPlanningOperation);
    setGenerationBusy(true);
    setGenerationError("");
    setGenerationFeedbackChapterId(operation.target_id);
    setGenerationRecoveryState("idle");
    try {
      const value = await api.prepareGenerationRun(id, operation.target_id, body);
      if (generation !== requestGeneration.current) return;
      await acceptGenerationRun(value, operation, generation, !!existing, true);
    } catch (cause) {
      if (generation !== requestGeneration.current) return;
      const apiError = cause instanceof ApiError ? cause : null;
      const keepPending = shouldKeepPlanningOperation(cause) || apiError?.status === 503;
      let clearFailed = false;
      if (!keepPending) {
        if (clearPendingPlanningOperation(user.id, id)) {
          setPending(null);
          setGenerationRecoveryState("idle");
        } else {
          clearFailed = true;
          setPending(operation as unknown as PendingPlanningOperation);
          setPendingStorageIssue("unavailable");
          setGenerationRecoveryState("corrupt");
          setGenerationError("服务端已确定拒绝本次检查，但浏览器无法清除恢复线索；继续保持禁写，请重新加载页面。");
        }
      } else {
        setPending(operation as unknown as PendingPlanningOperation);
      }
      if (!clearFailed) setGenerationError(errorMessage(cause));
      setGenerationFeedbackChapterId(operation.target_id);
      if (clearFailed) {
        // The server outcome is known, but the browser recovery clue could not be cleared.
        // Keep the in-memory pending lock and the storage-specific message above.
      } else if (apiError?.status === 503) {
        setMaintenance(true);
        setGenerationRecoveryState("unknown");
      } else if (keepPending) {
        await reconcileGenerationPending(operation, generation, false);
      } else if (apiError?.code === "GENERATION_LORE_INELIGIBLE") {
        const refreshed = await refreshPlanningAndAssignmentScope("chapter", operation.target_id, generation);
        if (!refreshed) setAssignmentRefreshRequired(true);
        setGenerationError(refreshed
          ? `${apiError.detail} 请在下方处理全部失效设定。`
          : "检查发现失效设定，且最新设定分配读取失败；已保持禁写，请重新读取。");
        setAssignmentFocusTarget({ elementId: "", scopeIdentity: scopeIdentity("chapter", operation.target_id) });
      } else if (apiError?.status === 409 || apiError?.code?.includes("VERSION_CONFLICT")) {
        const refreshed = await refreshPlanningAndAssignmentScope("chapter", operation.target_id, generation);
        if (!refreshed) {
          setAssignmentRefreshRequired(true);
          setGenerationError("检查时资料版本发生变化，且最新规划与设定读取失败；已保持禁写。 ");
        } else {
          setGenerationError(`${apiError.detail} 已载入最新规划与设定，请核对后重新检查。`);
        }
      } else if (apiError?.code === "GENERATION_CONTEXT_EMPTY") {
        setGenerationError(`${apiError.detail} 请先为本章、所属篇章或整部小说分配可用设定。`);
      }
      setGenerationFeedbackFocusToken((token) => token + 1);
    } finally {
      if (generation === requestGeneration.current) setGenerationBusy(false);
    }
  }

  async function reconcileStructureReorderPending(
    operation = pending,
    generation = requestGeneration.current
  ) {
    if (!id || !user || !operation || operation.action !== "structure_reorder") return;
    const expectedPlanId = planRef.current?.id;
    setReorderRecoveryState("checking");
    setError("");
    try {
      const receipt = await api.getPlanningOperation(id, operation.operation_key);
      if (generation !== requestGeneration.current) return;
      const matchesReceipt = await receiptMatchesPending(receipt, operation, id, expectedPlanId);
      if (generation !== requestGeneration.current) return;
      if (!matchesReceipt) {
        corruptRecoverySnapshot.current = sessionStorage.getItem(pendingProjectOperationKey(user.id, id));
        setPendingStorageIssue("corrupt");
        setError("服务器返回的排序收据与本地全量结构不一致；已保留恢复记录并停止全部规划写入。");
        setReorderRecoveryState("unknown");
        return;
      }
      clearPendingPlanningOperation(user.id, id);
      setPending(null);
      const refreshed = await loadPlan(false, generation);
      if (generation !== requestGeneration.current) return;
      if (!refreshed) {
        setRefreshRequired(true);
        setError("排序结果已确认，但权威规划尚未完整读取；已暂停新的写入。");
        setReorderRecoveryState("unknown");
        return;
      }
      setReorderRecoveryState("idle");
      setNotice("已按原编号找回章节排序，并重新载入权威结构。");
    } catch (cause) {
      if (generation !== requestGeneration.current) return;
      const safelyMissing = cause instanceof ApiError
        && cause.status === 404
        && cause.code === "PLANNING_OPERATION_NOT_FOUND"
        && cause.recommendedAction === "retry_original_request";
      setReorderRecoveryState(safelyMissing ? "not_found" : "unknown");
      setNotice(safelyMissing
        ? "服务端明确未找到原排序。如需继续，必须显式使用原编号和原全量结构重试。"
        : "原排序结果暂时无法确认；只允许继续按原编号核对，不会自动重复提交。");
    }
  }

  async function retryPending() {
    if (!id || !user || !pending || busy || generationBusy || refreshRequired) return;
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
    const expectedPlanId = planRef.current?.id;
    const action = pending.action;
    const target = pending.target_id;
    if (action === "generation_prepare") {
      await executeGenerationPrepare(pending as unknown as PendingPlanningOperation<GenerationRunPrepareInput>);
      return;
    }
    if (action === "structure_reorder" && reorderRecoveryState !== "not_found") return;
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
      const result = await handler();
      if (generation !== requestGeneration.current) return;
      const matchesReorderReceipt = action !== "structure_reorder"
        || await receiptMatchesPending(result as PlanningOperationReceipt, pending, id, expectedPlanId);
      if (generation !== requestGeneration.current) return;
      if (!matchesReorderReceipt) {
        corruptRecoverySnapshot.current = sessionStorage.getItem(pendingProjectOperationKey(user.id, id));
        setPendingStorageIssue("corrupt");
        setError("服务器返回的排序收据与本地全量结构不一致；已保留恢复记录并停止全部规划写入。");
        setReorderRecoveryState("unknown");
        return;
      }
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
      if (action === "structure_reorder") setReorderRecoveryState("idle");
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
        if (action === "structure_reorder") setReorderRecoveryState("idle");
      }
    } finally {
      if (generation === requestGeneration.current) setBusy(false);
    }
  }

  function reorder(parts: PlanningReorderInput["parts"], success: string, focusId: string) {
    if (!plan || planningWriteDisabled || structureReorderDisabledReason) return;
    const body: PlanningReorderInput = {
      operation_key: createPlanningOperationKey("structure_reorder"),
      expected_structure_version: plan.structure_version,
      parts,
    };
    setReorderRecoveryState("idle");
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

  function dropChapter(input: {
    chapterId: string;
    partId: string;
    targetChapterId: string;
    placement: "before" | "after";
    expectedStructureVersion: number;
  }) {
    if (!plan || planningWriteDisabled || structureReorderDisabledReason) return;
    if (plan.structure_version !== input.expectedStructureVersion || input.chapterId === input.targetChapterId) return;
    const sourceLocation = locate(plan, { kind: "chapter", id: input.chapterId });
    const targetLocation = locate(plan, { kind: "chapter", id: input.targetChapterId });
    if (
      !sourceLocation.part || !sourceLocation.chapter || !targetLocation.part || !targetLocation.chapter
      || sourceLocation.part.id !== input.partId || targetLocation.part.id !== input.partId
      || sourceLocation.part.status !== "active" || targetLocation.part.status !== "active"
      || sourceLocation.chapter.status !== "active" || targetLocation.chapter.status !== "active"
    ) return;
    const parts = activeReorder(plan);
    const part = parts.find((item) => item.part_id === input.partId);
    if (!part) return;
    const previous = [...part.chapter_ids];
    part.chapter_ids = part.chapter_ids.filter((chapterId) => chapterId !== input.chapterId);
    const targetIndex = part.chapter_ids.indexOf(input.targetChapterId);
    if (targetIndex < 0) return;
    part.chapter_ids.splice(targetIndex + (input.placement === "after" ? 1 : 0), 0, input.chapterId);
    if (part.chapter_ids.every((chapterId, index) => chapterId === previous[index])) return;
    const allChapterIds = parts.flatMap((item) => item.chapter_ids);
    if (new Set(allChapterIds).size !== allChapterIds.length) return;
    reorder(parts, "章节顺序已更新。", input.chapterId);
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
    const storageKey = pendingProjectOperationKey(user.id, id);
    let currentRaw: string | null;
    try {
      currentRaw = sessionStorage.getItem(storageKey);
    } catch {
      setError("浏览器会话存储仍不可用，无法核对或清除损坏记录；继续保持禁写。");
      return;
    }
    if (!corruptRecoverySnapshot.current || currentRaw !== corruptRecoverySnapshot.current) {
      setError("恢复记录状态已经变化，未执行清除；请重新载入后核对。");
      return;
    }
    const shared = loadPendingPlanningOperation(user.id, id);
    const corruptWorkspace = shared.status === "foreign" ? shared.workspace : "planning";
    const stillCorrupt = corruptWorkspace === "candidate_manual_edit"
      ? loadPendingCandidateManualEdit(user.id, id).status === "corrupt"
      : corruptWorkspace === "candidate_selection"
        ? loadPendingCandidateSelection(user.id, id).status === "corrupt"
      : corruptWorkspace === "technical_demo_execution"
        ? loadPendingTechnicalDemoExecution(user.id, id).status === "corrupt"
        : corruptWorkspace === "generation_execution"
          ? loadPendingGenerationExecution(user.id, id).status === "corrupt"
          : shared.status === "corrupt";
    if (!stillCorrupt) {
      setError("恢复记录状态已经变化，未执行清除；请重新载入后核对。");
      return;
    }
    if (clearPendingPlanningOperation(user.id, id)) {
      const confirmedMissing = loadPendingPlanningOperation(user.id, id).status === "missing"
        && loadPendingGenerationExecution(user.id, id).status === "missing"
        && loadPendingTechnicalDemoExecution(user.id, id).status === "missing"
        && loadPendingCandidateManualEdit(user.id, id).status === "missing"
        && loadPendingCandidateSelection(user.id, id).status === "missing";
      if (!confirmedMissing) {
        setError("清理后无法确认浏览器恢复槽为空；继续保持禁写。");
        return;
      }
      corruptRecoverySnapshot.current = null;
      setPendingStorageIssue(null);
      setCandidateManualEditLocked(false);
      setCandidateSelectionLocked(false);
      setCandidateSelectionRecoveryRevision((value) => value + 1);
      if (corruptWorkspace === "technical_demo_execution") {
        setTechnicalDemoLocked(false);
        setForeignPending((value) => value?.workspace === "technical_demo_execution" ? null : value);
      } else if (corruptWorkspace === "candidate_manual_edit") {
        setCandidateManualEditLocked(false);
        setCandidateVersionRecoveryId(null);
        setForeignPending((value) => value?.workspace === "candidate_manual_edit" ? null : value);
      } else if (corruptWorkspace === "candidate_selection") {
        setCandidateSelectionLocked(false);
        setCandidateVersionRecoveryId(null);
        setForeignPending((value) => value?.workspace === "candidate_selection" ? null : value);
      } else if (corruptWorkspace === "generation_execution") {
        setForeignPending((value) => value?.workspace === "generation_execution" ? null : value);
      }
      setError("");
      setGenerationError("");
      setGenerationFeedbackChapterId(null);
      setGenerationRecoveryState("idle");
      setNotice("损坏的本地恢复记录已清除，可以重新开始操作。");
    } else {
      setError("浏览器会话存储仍不可用，无法清除损坏记录；继续保持禁写。");
    }
  }

  function clearGenerationRunPointer() {
    generationRunRequest.current += 1;
    acceptedGenerationRunId.current = null;
    generationPointerTransition.current = null;
    setGenerationLoadingSaved(false);
    const params = new URLSearchParams(searchParams);
    params.delete("generation_run");
    params.delete("generation_attempt");
    params.delete("generation_candidate");
    setSearchParams(params, { replace: true });
    setGenerationRun(null);
    setGenerationAttempt(null);
    setGenerationCandidate(null);
    setGenerationConfirmationOpen(false);
    setGenerationConfirmationKind(null);
    setGenerationConfirmationIdentity(null);
    setGenerationExecutionRequiresNewPreflight(false);
    setGenerationRecovered(false);
    setGenerationError("");
    setGenerationFeedbackChapterId(null);
    setGenerationRecoveryState("idle");
    setNotice("已关闭本页的检查记录视图；服务端耐久记录未被删除。");
  }

  async function readCandidateForAttempt(
    attempt: GenerationAttemptResponse,
    operation: PendingGenerationExecution,
    runValue: GenerationRunResponse
  ): Promise<boolean> {
    if (!id || !user || attempt.status !== "succeeded" || !attempt.candidate_id) return false;
    if (generationExecutionPendingRef.current && generationExecutionPendingRef.current.operation_key !== operation.operation_key) return false;
    const chapterLocation = planRef.current
      ? locate(planRef.current, { kind: "chapter", id: operation.chapter_id })
      : { chapter: null };
    if (!chapterLocation.chapter) {
      setGenerationExecutionError("无法确认生成候选的章节身份；已保留恢复线索且不会重新调用模型。");
      return false;
    }
    const request = ++generationCandidateRequest.current;
    setGenerationCandidateLoading(true);
    setGenerationExecutionError("");
    try {
      const value = await readGenerationCandidate({
        projectId: id,
        runId: operation.run_id,
        chapterId: operation.chapter_id,
        attemptId: attempt.id,
        candidateId: attempt.candidate_id,
        userId: user.id,
        chapterTitle: chapterLocation.chapter.title,
      });
      if (request !== generationCandidateRequest.current) return false;
      if (generationExecutionPendingRef.current && generationExecutionPendingRef.current.operation_key !== operation.operation_key) return false;
      const params = new URLSearchParams(searchParams);
      params.set("scope", "chapter");
      params.set("target", operation.chapter_id);
      params.set("generation_run", runValue.id);
      params.set("generation_attempt", attempt.id);
      params.set("generation_candidate", value.id);
      setSearchParams(params, { replace: true });
      setGenerationCandidate(value);
      if (!clearPendingGenerationExecution(user.id, id, operation.operation_key)) {
        setGenerationExecutionError("候选已经过严格校验，但浏览器无法按原编号清除恢复线索；继续保持禁写。");
        return false;
      }
      generationExecutionPendingRef.current = null;
      setGenerationExecutionPending(null);
      setGenerationOriginalRetryAllowed(false);
      setNotice("生成候选已保存并通过完整性校验；未覆盖原稿，也未自动确认伏笔。");
      return true;
    } catch (cause) {
      if (request !== generationCandidateRequest.current) return false;
      setGenerationExecutionError(`${errorMessage(cause)} 只会重新读取已保存候选，不会重新调用模型。`);
      return false;
    } finally {
      if (request === generationCandidateRequest.current) setGenerationCandidateLoading(false);
    }
  }

  async function acceptGenerationAttempt(
    value: GenerationAttemptResponse,
    operation: PendingGenerationExecution,
    runValue = generationRun
  ): Promise<void> {
    if (generationExecutionPendingRef.current && generationExecutionPendingRef.current.operation_key !== operation.operation_key) return;
    const operationChapter = planRef.current
      ? locate(planRef.current, { kind: "chapter", id: operation.chapter_id }).chapter
      : null;
    if (operationChapter && (selectionRef.current.kind !== "chapter" || selectionRef.current.id !== operation.chapter_id)) return;
    setGenerationAttempt(value);
    setGenerationCapability(value.capability);
    setGenerationOriginalRetryAllowed(false);
    setGenerationExecutionError("");
    if (value.status === "succeeded") {
      if (!runValue) {
        setGenerationExecutionError("执行已成功，但生成前检查记录尚未读取；只允许继续读取，不会再次生成。");
        return;
      }
      await readCandidateForAttempt(value, operation, runValue);
    }
  }

  async function reconcileGenerationExecution(
    operation: PendingGenerationExecution,
    request = ++generationExecutionRequest.current
  ): Promise<void> {
    if (!id) return;
    setGenerationExecutionBusy(true);
    setGenerationOriginalRetryAllowed(false);
    try {
      const value = await readGenerationAttemptByKey({
        projectId: id,
        runId: operation.run_id,
        chapterId: operation.chapter_id,
        operationKey: operation.operation_key,
        contextChecksum: operation.payload.expected_context_checksum,
        capabilityChecksum: operation.payload.expected_capability_checksum,
      });
      if (request !== generationExecutionRequest.current) return;
      await acceptGenerationAttempt(value, operation);
    } catch (cause) {
      if (request !== generationExecutionRequest.current) return;
      const safeMissing = cause instanceof ApiError
        && cause.status === 404
        && cause.code === "GENERATION_ATTEMPT_NOT_FOUND"
        && cause.retryable
        && cause.recommendedAction === "retry_original_execute";
      setGenerationOriginalRetryAllowed(safeMissing);
      setGenerationExecutionError(safeMissing
        ? "服务端明确未找到原生成尝试。如需继续，必须显式使用原编号和原载荷重试。"
        : `${errorMessage(cause)} 结果仍不确定；可能已被服务商受理并产生费用，系统不会自动重复调用。`);
    } finally {
      if (request === generationExecutionRequest.current) setGenerationExecutionBusy(false);
    }
  }

  async function executeSavedGeneration(operation: PendingGenerationExecution) {
    if (!id) return;
    const request = ++generationExecutionRequest.current;
    setGenerationExecutionBusy(true);
    setGenerationOriginalRetryAllowed(false);
    setGenerationExecutionError("");
    try {
      const value = await requestGenerationAttempt(id, operation.run_id, operation.chapter_id, operation.payload);
      if (request !== generationExecutionRequest.current) return;
      await acceptGenerationAttempt(value, operation);
    } catch {
      if (request !== generationExecutionRequest.current) return;
      await reconcileGenerationExecution(operation, request);
    } finally {
      if (request === generationExecutionRequest.current) setGenerationExecutionBusy(false);
    }
  }

  async function openGenerationConfirmation() {
    if (!id || !generationRun || generationExecutionDisabledReason || generationExecutionBusy) return;
    const expectedRunId = generationRun.id;
    const expectedChapterId = generationRun.planning_chapter_id;
    const request = ++generationExecutionRequest.current;
    setGenerationExecutionBusy(true);
    setGenerationExecutionError("");
    try {
      const value = await readGenerationCapability(id);
      if (
        request !== generationExecutionRequest.current
        || generationRun?.id !== expectedRunId
        || selectionRef.current.kind !== "chapter"
        || selectionRef.current.id !== expectedChapterId
      ) return;
      setGenerationCapability(value);
      setGenerationConfirmationIdentity({
        runId: generationRun.id,
        chapterId: generationRun.planning_chapter_id,
        contextChecksum: generationRun.context_checksum,
        structureVersion: generationRun.structure_version,
        assignmentVersion: generationRun.assignment_version,
        chapterLockVersion: generationRun.chapter_lock_version,
        operationKey: null,
      });
      setGenerationConfirmationKind("new_attempt");
      setGenerationConfirmationOpen(true);
    } catch (cause) {
      if (request !== generationExecutionRequest.current) return;
      setGenerationCapability(null);
      setGenerationConfirmationIdentity(null);
      setGenerationConfirmationKind(null);
      setGenerationConfirmationOpen(false);
      setGenerationExecutionError(errorMessage(cause));
    } finally {
      if (request === generationExecutionRequest.current) setGenerationExecutionBusy(false);
    }
  }

  function cancelGenerationConfirmation() {
    if (generationExecutionBusy) return;
    setGenerationConfirmationOpen(false);
    setGenerationConfirmationKind(null);
    setGenerationConfirmationIdentity(null);
  }

  async function confirmGenerationExecution() {
    const retryingOriginal = generationConfirmationKind === "original_retry"
      && generationExecutionPending
      && generationOriginalRetryAllowed
      && generationConfirmationIdentity?.operationKey === generationExecutionPending.operation_key
      ? generationExecutionPending
      : null;
    const replacingFailed = generationExecutionPending
      && generationConfirmationKind === "new_attempt"
      && generationAttempt?.status === "failed"
      ? generationExecutionPending
      : null;
    if (
      !id || !user || !generationRun || !generationCapability || !generationConfirmationIdentity
      || !generationConfirmationOpen || !generationConfirmationKind
      || (generationExecutionPending && !replacingFailed && !retryingOriginal)
      || generationExecutionBusy || selectionRef.current.kind !== "chapter"
      || selectionRef.current.id !== generationRun.planning_chapter_id
    ) return;
    if (
      generationStale
      || !!generationDisabledReason
      || generationConfirmationIdentity.runId !== generationRun.id
      || generationConfirmationIdentity.chapterId !== generationRun.planning_chapter_id
      || generationConfirmationIdentity.contextChecksum !== generationRun.context_checksum
      || generationConfirmationIdentity.structureVersion !== generationRun.structure_version
      || generationConfirmationIdentity.assignmentVersion !== generationRun.assignment_version
      || generationConfirmationIdentity.chapterLockVersion !== generationRun.chapter_lock_version
      || (retryingOriginal && generationCapability.capability_checksum !== retryingOriginal.payload.expected_capability_checksum)
    ) {
      setGenerationConfirmationOpen(false);
      setGenerationConfirmationIdentity(null);
      setGenerationConfirmationKind(null);
      setGenerationExecutionError("确认期间规划、设定分配或章节版本已变化；本次模型请求未发送，请重新检查上下文。");
      return;
    }
    if (retryingOriginal) {
      setGenerationConfirmationOpen(false);
      setGenerationConfirmationIdentity(null);
      setGenerationConfirmationKind(null);
      await executeSavedGeneration(retryingOriginal);
      return;
    }
    const operationKey = createGenerationExecutionKey();
    const payload = {
      operation_key: operationKey,
      expected_context_checksum: generationRun.context_checksum,
      expected_capability_checksum: generationCapability.capability_checksum,
      confirm_model_call: true as const,
    };
    const operation: PendingGenerationExecution = {
      schema_version: 3,
      workspace: "generation_execution",
      user_id: user.id,
      project_id: id,
      chapter_id: generationRun.planning_chapter_id,
      run_id: generationRun.id,
      operation_key: operationKey,
      payload,
      created_at: new Date().toISOString(),
    };
    const pendingSaved = replacingFailed
      ? replaceFailedGenerationPending(replacingFailed, operation)
      : savePendingGenerationExecution(operation);
    if (!pendingSaved) {
      setPendingStorageIssue("unavailable");
      setGenerationExecutionError("浏览器无法原子保存新生成恢复信息；原失败线索保持不变，本次模型请求未发送。");
      return;
    }
    generationExecutionPendingRef.current = operation;
    setGenerationExecutionPending(operation);
    setGenerationAttempt(null);
    setGenerationCandidate(null);
    setGenerationExecutionRequiresNewPreflight(false);
    setGenerationConfirmationOpen(false);
    setGenerationConfirmationIdentity(null);
    setGenerationConfirmationKind(null);
    await executeSavedGeneration(operation);
  }

  async function retryOriginalGenerationExecution() {
    if (!id || !generationExecutionPending || !generationOriginalRetryAllowed || generationExecutionBusy) return;
    if (!generationRun) {
      setGenerationExecutionError("原章节或上下文检查记录当前不可用，无法安全确认付费重试；本次请求未发送。请恢复章节后重新检查上下文。");
      return;
    }
    const operation = generationExecutionPending;
    const request = ++generationExecutionRequest.current;
    setGenerationExecutionBusy(true);
    setGenerationExecutionError("");
    try {
      const value = await readGenerationCapability(id);
      if (
        request !== generationExecutionRequest.current
        || generationExecutionPendingRef.current?.operation_key !== operation.operation_key
        || generationRun.id !== operation.run_id
        || generationRun.context_checksum !== operation.payload.expected_context_checksum
      ) return;
      if (value.capability_checksum !== operation.payload.expected_capability_checksum) {
        if (user && clearPendingGenerationExecution(user.id, id, operation.operation_key)) {
          generationExecutionPendingRef.current = null;
          setGenerationExecutionPending(null);
          setGenerationAttempt(null);
          setGenerationOriginalRetryAllowed(false);
          setGenerationExecutionRequiresNewPreflight(true);
          setGenerationExecutionError("模型能力信息已变化，原确认已失效；本次请求未发送。请重新检查最新上下文后再确认生成。");
        } else {
          setGenerationExecutionError("模型能力信息已变化，且原恢复线索无法安全清除；本次请求未发送。请刷新后核对。");
        }
        return;
      }
      setGenerationCapability(value);
      setGenerationConfirmationIdentity({
        runId: generationRun.id,
        chapterId: generationRun.planning_chapter_id,
        contextChecksum: generationRun.context_checksum,
        structureVersion: generationRun.structure_version,
        assignmentVersion: generationRun.assignment_version,
        chapterLockVersion: generationRun.chapter_lock_version,
        operationKey: operation.operation_key,
      });
      setGenerationConfirmationKind("original_retry");
      setGenerationConfirmationOpen(true);
    } catch (cause) {
      if (request === generationExecutionRequest.current) setGenerationExecutionError(`${errorMessage(cause)} 原编号请求未发送。`);
    } finally {
      if (request === generationExecutionRequest.current) setGenerationExecutionBusy(false);
    }
  }

  async function checkGenerationExecution() {
    if (!generationExecutionPending || generationExecutionBusy) return;
    await reconcileGenerationExecution(generationExecutionPending);
  }

  async function startNewGenerationAfterFailure() {
    if (
      !id || !user || !generationRun || !generationExecutionPending
      || generationAttempt?.status !== "failed" || generationExecutionBusy
      || generationStale || located.chapter?.status !== "active" || located.part?.status !== "active"
    ) return;
    const expectedRun = generationRun;
    const request = ++generationExecutionRequest.current;
    setGenerationExecutionBusy(true);
    setGenerationExecutionError("");
    try {
      const value = await readGenerationCapability(id);
      if (
        request !== generationExecutionRequest.current
        || generationStale
        || generationRun?.id !== expectedRun.id
        || selectionRef.current.kind !== "chapter"
        || selectionRef.current.id !== expectedRun.planning_chapter_id
      ) {
        setGenerationExecutionError("重新确认前上下文已变化；本次模型请求未发送。");
        return;
      }
      setGenerationCapability(value);
      setGenerationConfirmationIdentity({
        runId: expectedRun.id,
        chapterId: expectedRun.planning_chapter_id,
        contextChecksum: expectedRun.context_checksum,
        structureVersion: expectedRun.structure_version,
        assignmentVersion: expectedRun.assignment_version,
        chapterLockVersion: expectedRun.chapter_lock_version,
        operationKey: null,
      });
      setGenerationConfirmationKind("new_attempt");
      setGenerationConfirmationOpen(true);
    } catch (cause) {
      if (request === generationExecutionRequest.current) setGenerationExecutionError(errorMessage(cause));
    } finally {
      if (request === generationExecutionRequest.current) setGenerationExecutionBusy(false);
    }
  }

  async function rereadGenerationCandidate() {
    if (!generationExecutionPending || !generationAttempt || !generationRun) return;
    await readCandidateForAttempt(generationAttempt, generationExecutionPending, generationRun);
  }

  function returnToPendingGenerationChapter() {
    if (!pending?.target_id || !planRef.current) return;
    const target = locate(planRef.current, { kind: "chapter", id: pending.target_id });
    if (!target.chapter) {
      setGenerationError("原检查对应章节当前无法在规划中找到；已保留恢复线索并停止新检查。");
      setGenerationFeedbackChapterId(pending.target_id);
      setGenerationFeedbackFocusToken((token) => token + 1);
      return;
    }
    const params = new URLSearchParams(searchParams);
    params.set("scope", "chapter");
    params.set("target", pending.target_id);
    params.delete("generation_run");
    setSearchParams(params);
    setMobileDetail(true);
  }

  function abandonGenerationPending() {
    if (!id || !user || pending?.action !== "generation_prepare") return;
    if (!window.confirm("确定放弃这条浏览器恢复线索？这不会删除服务器上可能已经保存的检查记录，之后也不会再自动找回它。")) return;
    if (!clearPendingPlanningOperation(user.id, id)) {
      setPendingStorageIssue("unavailable");
      setGenerationError("浏览器会话存储不可用，无法放弃原恢复线索；继续保持禁写。");
      setGenerationFeedbackChapterId(pending.target_id);
      setGenerationFeedbackFocusToken((token) => token + 1);
      return;
    }
    setPending(null);
    setGenerationError("");
    setGenerationFeedbackChapterId(null);
    setGenerationRecoveryState("idle");
    setNotice("已放弃本浏览器中的原检查恢复线索；服务器上可能存在的检查记录未被删除。");
  }

  function focusPlanningAssignments() {
    document.querySelector<HTMLElement>(".planning-assignments h3")?.focus();
  }

  if (!id) return <div className="card empty-state" role="alert">项目地址无效。</div>;

  return (
    <div className="planning-page planning-page--studio" aria-busy={loadState === "loading" || busy}>
      <button className="btn-back planning-page__back" onClick={() => confirmEditorUnload(undefined, true) && navigate(`/project/${id}`)}>← 返回项目</button>
      <header className="page-header planning-header">
        <div className="planning-header__copy"><span className="planning-header__eyebrow">Story architecture</span><h1>章节规划</h1><p>先组织故事结构与设定范围，再进入生成、候选与采用流程。</p></div>
        <span className="planning-header-actions"><Link className="btn btn-secondary" to={`/project/${id}/plan/foreshadows`} onClick={(event) => { if (!confirmEditorUnload(undefined, true)) event.preventDefault(); }}>管理伏笔</Link><Link className="btn btn-secondary" to={`/project/${id}/lore`} onClick={(event) => { if (!confirmEditorUnload(undefined, true)) event.preventDefault(); }}>打开设定仓库</Link></span>
      </header>
      {demoDescriptor?.state === "ready" && demoDescriptor.project_id === id && <div id="demo-planning"><DemoGuide projectId={id} current={location.hash === "#demo-technical-generation" ? 5 : 3} chapterId={demoDescriptor.chapter_id} elementId={demoDescriptor.element_id} foreshadowLifecycleId={demoDescriptor.foreshadow_lifecycle_id} /></div>}

      <div className="planning-status-stack">
        <div className="planning-live" aria-live="polite">{notice}</div>
      {error && <div className="planning-notice is-error" role="alert" tabIndex={-1} ref={conflictRef}><span>{error}{errorHint && <small className="planning-notice__hint">{errorHint}</small>}</span>{conflict ? <span className="planning-notice__actions"><button className="btn btn-secondary" onClick={() => { setServerSyncToken((value) => value + 1); setConflict(false); setError(""); setNotice("已载入服务器最新字段。"); }}>载入服务器最新值</button><button className="btn btn-secondary" onClick={() => { setConflict(false); setError(""); setNotice("旧草稿已保留；请与服务器最新值核对后再保存。"); }}>保留草稿并继续核对</button></span> : pendingStorageIssue === "corrupt" ? <button className="btn btn-secondary" onClick={clearCorruptRecoveryRecord}>确认清除损坏恢复记录</button> : (refreshRequired || assignmentRefreshRequired) ? <button className="btn btn-secondary" onClick={() => void reloadPlanningAndCurrentAssignments()}>重新读取规划与设定</button> : <button className="btn btn-secondary" onClick={() => loadPlan(false)}>刷新规划</button>}</div>}
      {globalGenerationFeedbackVisible && (
        <div className="planning-notice is-error" role="alert" tabIndex={-1} ref={globalGenerationFeedbackRef}>
          <span><strong>生成前检查未通过</strong><small className="planning-notice__hint">{generationError}</small></span>
        </div>
      )}
      {assignmentConflict && <div ref={assignmentConflictRef} className="planning-notice is-error" role="alert" tabIndex={-1}><span>{assignmentError || "分配状态已更新，请核对服务器最新结果。"}</span><button className="btn btn-secondary" onClick={() => { setAssignmentConflict(false); setAssignmentError(""); setNotice("已核对服务器最新分配，可以继续操作。"); }}>已核对最新分配</button></div>}
      {maintenance && <div className="planning-notice" role="status">项目资料正在维护；已保留当前只读内容并暂停写入。</div>}
      {foreignPending?.workspace === "foreshadow" && <div className="planning-notice" role="alert"><span>伏笔管理中还有结果未确认的写入；章节规划写入已暂停。</span><Link className="btn btn-secondary" to={`/project/${id}/plan/foreshadows`}>前往伏笔管理核对</Link></div>}
      {foreignPending?.workspace === "generation_execution" && <div className="planning-notice" role="alert"><span>生成候选中还有结果未确认的模型调用；章节规划写入已暂停，且不会自动重复生成。</span><Link className="btn btn-secondary" to={foreignPending.chapterId ? `/project/${id}/plan/chapters?scope=chapter&target=${encodeURIComponent(foreignPending.chapterId)}` : `/project/${id}/plan/chapters`}>返回发起章节核对生成</Link></div>}
      {foreignPending?.workspace === "technical_demo_execution" && <div className="planning-notice" role="alert"><span>技术模拟中还有结果未确认的固定内容请求；章节规划写入已暂停。它不调用 AI，也不会产生模型费用。</span><Link className="btn btn-secondary" to={foreignPending.chapterId ? `/project/${id}/plan/chapters?scope=chapter&target=${encodeURIComponent(foreignPending.chapterId)}` : `/project/${id}/plan/chapters`}>返回技术模拟发起章节核对</Link></div>}
      {foreignPending?.workspace === "candidate_manual_edit" && <div className="planning-notice" role="alert"><span>候选版本还有手工另存结果未确认；章节规划写入已暂停。</span><Link className="btn btn-secondary" to={foreignPending.chapterId ? `/project/${id}/plan/chapters?scope=chapter&target=${encodeURIComponent(foreignPending.chapterId)}` : `/project/${id}/plan/chapters`}>返回原章节核对候选版本</Link></div>}
      {foreignPending?.workspace === "candidate_selection" && <div className="planning-notice" role="alert"><span>候选采用还有结果未确认；章节规划写入已暂停，且不会自动重复提交。</span><Link className="btn btn-secondary" to={foreignPending.chapterId ? `/project/${id}/plan/chapters?scope=chapter&target=${encodeURIComponent(foreignPending.chapterId)}` : `/project/${id}/plan/chapters`}>返回原章核对采用状态</Link></div>}
      {pending && pending.action !== "generation_prepare" && pending.action !== "structure_reorder" && (
        <div className="planning-notice" role="alert">
          <span>检测到结果尚未确认的操作，已暂停新的写入。</span>
          <button className="btn btn-secondary" disabled={busy} onClick={retryPending}>使用原操作编号安全重试</button>
        </div>
      )}
      {pending?.action === "structure_reorder" && (
        <div className="planning-notice" role="alert">
          <span>章节排序结果尚未确认；系统不会自动重复提交。</span>
          <span className="planning-notice__actions">
            <button
              className="btn btn-secondary"
              disabled={busy || reorderRecoveryState === "checking" || !!pendingStorageIssue}
              onClick={() => void reconcileStructureReorderPending()}
            >
              {reorderRecoveryState === "checking" ? "正在核对原排序…" : "按原编号核对排序状态"}
            </button>
            {reorderRecoveryState === "not_found" && (
              <button className="btn btn-secondary" disabled={busy} onClick={() => void retryPending()}>
                使用原编号与全量结构重试
              </button>
            )}
          </span>
        </div>
      )}
      {pending?.action === "generation_prepare" && (
        <div className="planning-notice" role="status">
          <span>上次生成前检查仍等待确认；系统已冻结新的规划写入和新检查，避免重复记录。</span>
          <span className="planning-notice__actions">
            <button
              className="btn btn-secondary"
              disabled={generationBusy || generationRecoveryState === "checking"}
              onClick={() => void reconcileGenerationPending(pending as unknown as PendingPlanningOperation<GenerationRunPrepareInput>, requestGeneration.current, true)}
            >
              {generationRecoveryState === "checking" ? "正在核对原检查…" : "核对原检查结果"}
            </button>
            {generationRecoveryState === "not_found" && (
              <button className="btn btn-secondary" disabled={generationBusy} onClick={() => void retryPending()}>使用原编号与载荷重试</button>
            )}
            {(generationRecoveryState === "corrupt" || pendingStorageIssue === "unavailable") && (
              <button className="btn btn-secondary" disabled={generationBusy} onClick={abandonGenerationPending}>处理恢复线索</button>
            )}
          </span>
          {(selection.kind !== "chapter" || selection.id !== pending.target_id) && plan && locate(plan, { kind: "chapter", id: pending.target_id! }).chapter && (
            <button className="btn btn-secondary" onClick={returnToPendingGenerationChapter}>返回发起章节核对</button>
          )}
        </div>
      )}
      {generationExecutionPending && (!located.chapter || !located.part) && (
        <div className="planning-notice is-error" role="alert">
          <span><strong>生成恢复章节当前不可用</strong><small className="planning-notice__hint">{generationExecutionError || "已保留原操作编号。只允许继续核对服务端状态，不会创建新尝试。"}</small></span>
          <span className="planning-notice__actions">
            <button className="btn btn-secondary" disabled={generationExecutionBusy} onClick={() => void checkGenerationExecution()}>按原编号核对生成状态</button>
            {generationOriginalRetryAllowed && <small className="planning-notice__hint">服务端确认未找到原尝试，但原章节不可用，不能安全完成付费重试确认；请先恢复章节并重新检查上下文。</small>}
          </span>
        </div>
      )}
      </div>

      {loadState === "loading" && <section className="card empty-state planning-state-card" aria-label="章节规划详情">正在加载章节规划…</section>}
      {loadState === "error" && <section className="card empty-state planning-state-card" aria-label="章节规划详情"><h2>规划暂时无法加载</h2><button className="btn btn-primary" onClick={() => loadPlan()}>重新加载</button></section>}
      {loadState === "uninitialized" && (
        <section className="card empty-state planning-state-card" aria-label="章节规划详情"><h2>创建空白章节规划</h2><p>系统不会生成大纲，也不会覆盖现有正文。你可以自行建立篇章和章节。</p><button className="btn btn-primary" disabled={busy} onClick={initialize}>{busy ? "正在创建…" : "创建章节规划"}</button></section>
      )}
      {loadState === "migration" && (
        <section className="card empty-state planning-state-card" aria-label="章节规划详情"><h2>请先升级设定仓库</h2><p>章节规划只会引用已确认的模块化设定。</p><Link className="btn btn-primary" to={`/project/${id}/lore?migration=preview`}>打开设定仓库</Link></section>
      )}
      {loadState === "legacy" && (
        <section className="card empty-state planning-state-card" aria-label="章节规划详情"><h2>检测到历史章节资料</h2><p>系统不会自动迁移或覆盖旧大纲、章节正文和故事记忆。</p><Link className="btn btn-primary" to={`/project/${id}`}>返回项目继续兼容流程</Link></section>
      )}

      {loadState === "ready" && plan && (
        <div className={`planning-workspace planning-workspace--studio${mobileDetail ? " show-detail" : ""}`}>
          <aside className="card planning-workspace__tree">
            <div className="planning-section-heading"><h2>篇章结构</h2><CreatePartForm plan={plan} busy={planningWriteDisabled} onDirtyChange={setHasUnsavedPartCreationDraft} onCreate={(body) => execute("part_create", null, body, (value) => api.createPlanningPart(id, value), "篇章已创建。")} /></div>
            <PlanningStructurePanel
              plan={plan}
              selected={selection}
              busy={planningWriteDisabled}
              reorderDisabledReason={structureReorderDisabledReason}
              onSelect={selectScope}
              onMovePart={movePart}
              onMoveChapter={moveChapter}
              onDropChapter={dropChapter}
            />
          </aside>
          <section className="card planning-workspace__detail" aria-label="章节规划详情">
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
              <>
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
                {(located.chapter.status === "active" && located.part.status === "active"
                  || !!generationExecutionPending || !!generationAttempt || !!generationCandidate || !!candidateVersionRecoveryId) && (
                  <PlanningGenerationPreflight
                    plan={plan}
                    part={located.part}
                    chapter={located.chapter}
                    run={generationRun}
                    busy={generationBusy}
                    loadingSaved={generationLoadingSaved}
                    disabled={!!generationDisabledReason}
                    disabledReason={generationDisabledReason}
                    error={generationError}
                    recoveryState={generationRecoveryState}
                    stale={generationStale}
                    recovered={generationRecovered}
                    focusResultToken={generationFocusToken}
                    focusFeedbackToken={generationFeedbackFocusToken}
                    hasPendingRecovery={pending?.action === "generation_prepare"}
                    capability={generationCapability}
                    attempt={generationAttempt}
                    candidate={generationCandidate}
                    candidateAudit={null}
                    auditLoading={false}
                    auditError=""
                    executionBusy={generationExecutionBusy}
                    candidateLoading={generationCandidateLoading}
                    executionError={generationExecutionError}
                    executionDisabledReason={generationExecutionDisabledReason}
                    runActionsDisabledReason={generationRunActionsDisabledReason}
                    confirmationOpen={generationConfirmationOpen}
                    confirmationUsesOriginalRequest={generationConfirmationKind === "original_retry"}
                    originalRetryAllowed={generationOriginalRetryAllowed}
                    newAttemptDisabled={generationStale || located.chapter.status !== "active" || located.part.status !== "active" || busy || generationBusy || generationExecutionBusy}
                    executionMode={demoDescriptorStatus !== "known"
                      ? "hidden"
                      : demoDescriptor?.state === "ready" && demoDescriptor.project_id === id
                        ? demoDescriptor.chapter_id === located.chapter.id ? "technical" : "hidden"
                        : demoDescriptor?.state === "diverged" && demoDescriptor.project_id === id
                          ? "hidden" : "real"}
                    technicalDemoUserId={user?.id}
                    onTechnicalDemoLockChange={handleTechnicalDemoLockChange}
                    candidateVersionRecoveryId={candidateVersionRecoveryId ?? searchParams.get("candidate_version") ?? undefined}
                    onCandidateVersionLockChange={handleCandidateVersionLockChange}
                    candidateSelectionCurrent={candidateSelectionCurrent}
                    candidateSelectionLoading={candidateSelectionLoading}
                    candidateSelectionError={candidateSelectionError}
                    onRefreshCandidateSelection={refreshCandidateSelection}
                    selectionDisabledReason={candidateSelectionDisabledReason}
                    candidateSelectionRecoveryRevision={candidateSelectionRecoveryRevision}
                    onPrepare={() => void executeGenerationPrepare()}
                    onCheckPending={() => {
                      if (pending?.action === "generation_prepare") void reconcileGenerationPending(pending as unknown as PendingPlanningOperation<GenerationRunPrepareInput>, requestGeneration.current, true);
                    }}
                    onRetryOriginal={() => void retryPending()}
                    onFocusAssignments={focusPlanningAssignments}
                    onClearSavedPointer={clearGenerationRunPointer}
                    onAbandonPending={abandonGenerationPending}
                    onOpenGenerationConfirmation={() => void openGenerationConfirmation()}
                    onCancelGenerationConfirmation={cancelGenerationConfirmation}
                    onConfirmGeneration={() => void confirmGenerationExecution()}
                    onCheckGenerationAttempt={() => void checkGenerationExecution()}
                    onReadGenerationCandidate={() => void rereadGenerationCandidate()}
                    onReadGenerationCandidateAudit={() => {}}
                    onRetryOriginalGeneration={() => void retryOriginalGenerationExecution()}
                    onStartNewAfterFailure={() => void startNewGenerationAfterFailure()}
                  />
                )}
              </>
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
          </section>
        </div>
      )}

      <div className="planning-page__support">
        <ForeshadowPlanningSummary projectId={id} />
      </div>
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
