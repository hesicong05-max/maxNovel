import { useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { ApiError } from "@/services/api";
import {
  CandidateVersionContractError,
  candidateMatchesManualEditParent,
  clearCandidateManualEditDraft,
  clearCorruptCandidateManualEditDraft,
  clearPendingCandidateManualEdit,
  createCandidateManualEditOperationKey,
  listCandidateVersions,
  loadCandidateManualEditDraft,
  loadPendingCandidateManualEdit,
  parseCandidateManualEditInput,
  readCandidateManualEditByKey,
  readCandidateVersion,
  replaceCandidateManualEditDraft,
  requestCandidateManualEdit,
  saveCandidateManualEditDraft,
  savePendingCandidateManualEdit,
  type CandidateVersionIdentity,
  type CandidateManualEditDraft,
  type PendingCandidateManualEdit,
} from "@/services/candidateVersionOperations";
import { readGenerationCandidateAudit } from "@/services/generationExecution";
import type {
  GenerationCandidateAuditResponse,
  GenerationCandidateVersionDetail,
  GenerationCandidateVersionListItem,
  GenerationCandidateVersionListResponse,
  GenerationRunResponse,
} from "@/types/generation";

interface Props {
  userId: string;
  projectId: string;
  chapterId: string;
  chapterTitle: string;
  run: GenerationRunResponse;
  initialCandidateId: string;
  disabledReason?: string;
  onLockChange?: (locked: boolean) => void;
}

function message(error: unknown): string {
  if (error instanceof ApiError) return error.detail;
  if (error instanceof CandidateVersionContractError) return error.message;
  return error instanceof Error ? error.message : "候选版本暂时无法处理。";
}

function exactNotFound(error: unknown): error is ApiError {
  return error instanceof ApiError && error.status === 404
    && error.code === "GENERATION_CANDIDATE_MANUAL_EDIT_NOT_FOUND"
    && error.retryable
    && error.recommendedAction === "retry_original_candidate_manual_edit";
}

type ManualEditConflict = "content_unchanged" | "parent_changed" | "context_changed"
  | "operation_conflict" | "version_conflict" | "unknown";

function manualEditConflict(error: ApiError): ManualEditConflict {
  if (error.status !== 409) return "unknown";
  if (error.code === "GENERATION_CANDIDATE_CONTENT_UNCHANGED"
    && !error.retryable
    && error.recommendedAction === "edit_candidate_content") return "content_unchanged";
  if (error.code === "GENERATION_CANDIDATE_PARENT_CHANGED"
    && !error.retryable
    && error.recommendedAction === "reload_generation_candidate_versions") return "parent_changed";
  if (error.code === "GENERATION_CONTEXT_CHECKSUM_CONFLICT"
    && !error.retryable
    && error.recommendedAction === "reload_generation_candidate_versions") return "context_changed";
  if (error.code === "GENERATION_CANDIDATE_OPERATION_CONFLICT"
    && !error.retryable
    && error.recommendedAction === "start_new_candidate_manual_edit") return "operation_conflict";
  if (error.code === "GENERATION_CANDIDATE_VERSION_CONFLICT"
    && error.retryable && error.recommendedAction === "retry_candidate_manual_edit") {
    return "version_conflict";
  }
  return "unknown";
}

function rootLabel(item: GenerationCandidateVersionListItem): string {
  return item.root_origin_kind === "technical_demo"
    ? "根来源：固定技术模拟候选"
    : "根来源：模型生成候选";
}

function sourceLabel(item: GenerationCandidateVersionListItem): string {
  if (item.origin_kind === "generated") return "模型生成候选";
  if (item.origin_kind === "technical_demo") return "固定技术模拟候选";
  return `作者手工另存｜基于版本 ${item.parent_version_no}`;
}

function costLabel(item: GenerationCandidateVersionListItem): string {
  if (item.origin_kind === "generated") return "模型已调用；费用与用量以生成执行收据为准";
  if (item.origin_kind === "technical_demo") return "未调用模型、无模型费用";
  return "本次未调用模型、无新增模型费用";
}

function listConfirmsDetail(
  item: GenerationCandidateVersionListItem,
  detail: GenerationCandidateVersionDetail
): boolean {
  return Object.entries(item).every(([key, value]) =>
    detail[key as keyof GenerationCandidateVersionDetail] === value
  );
}

function mergeCandidatePage(
  current: GenerationCandidateVersionListItem[],
  page: GenerationCandidateVersionListResponse,
  beforeVersionNo: number
): GenerationCandidateVersionListItem[] {
  const tail = current.at(-1);
  if (!tail || tail.version_no !== beforeVersionNo
    || page.items.some((item) => item.version_no >= beforeVersionNo)) {
    throw new CandidateVersionContractError("更多候选版本未严格位于当前页尾之后，已保留现有列表。");
  }
  const ids = new Set(current.map((item) => item.id));
  const versions = new Set(current.map((item) => item.version_no));
  if (page.items.some((item) => ids.has(item.id) || versions.has(item.version_no))) {
    throw new CandidateVersionContractError("更多候选版本与已展示列表重复，已保留现有列表。");
  }
  const merged = [...current, ...page.items];
  if (merged.some((item, index) => index > 0 && merged[index - 1].version_no <= item.version_no)) {
    throw new CandidateVersionContractError("合并后的候选版本顺序无效，已保留现有列表。");
  }
  return merged;
}

function draftFor(
  identity: CandidateVersionIdentity,
  parent: GenerationCandidateVersionDetail,
  contextChecksum: string,
  content: string
): CandidateManualEditDraft {
  return {
    schema_version: 1,
    workspace: "candidate_manual_edit_draft",
    user_id: identity.userId,
    project_id: identity.projectId,
    chapter_id: identity.chapterId,
    run_id: identity.runId,
    parent_candidate_id: parent.id,
    parent_version_no: parent.version_no,
    parent_checksum: parent.content_checksum,
    context_checksum: contextChecksum,
    content,
    updated_at: new Date().toISOString(),
  };
}

export default function CandidateVersionWorkspace({
  userId,
  projectId,
  chapterId,
  chapterTitle,
  run,
  initialCandidateId,
  disabledReason = "",
  onLockChange,
}: Props) {
  const [searchParams, setSearchParams] = useSearchParams();
  const [items, setItems] = useState<GenerationCandidateVersionListItem[]>([]);
  const [listBusy, setListBusy] = useState(true);
  const [listError, setListError] = useState("");
  const [hasMore, setHasMore] = useState(false);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [pageBusy, setPageBusy] = useState(false);
  const [pageError, setPageError] = useState("");
  const [selected, setSelected] = useState<GenerationCandidateVersionDetail | null>(null);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [pending, setPending] = useState<PendingCandidateManualEdit | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [originalRetryReason, setOriginalRetryReason] = useState<"not_found" | "version_conflict" | null>(null);
  const [audit, setAudit] = useState<GenerationCandidateAuditResponse | null>(null);
  const [auditBusy, setAuditBusy] = useState(false);
  const [auditError, setAuditError] = useState("");
  const [storageBlocked, setStorageBlocked] = useState(false);
  const [recoveryBusy, setRecoveryBusy] = useState(true);
  const [pointerBusy, setPointerBusy] = useState(false);
  const [listNotice, setListNotice] = useState("");
  const [storedDraft, setStoredDraft] = useState<CandidateManualEditDraft | null>(null);
  const [draftIssue, setDraftIssue] = useState<{
    kind: "foreign" | "corrupt" | "stale" | "restored";
    draft?: CandidateManualEditDraft;
  } | null>(null);
  const requestGeneration = useRef(0);
  const listGeneration = useRef(0);
  const listController = useRef<AbortController | null>(null);
  const pageGeneration = useRef(0);
  const pageController = useRef<AbortController | null>(null);
  const pointerGeneration = useRef(0);
  const pointerController = useRef<AbortController | null>(null);
  const pointerTransition = useRef<string | null>(null);
  const acceptedPointer = useRef<string | null>(null);
  const auditGeneration = useRef(0);
  const auditController = useRef<AbortController | null>(null);
  const identityRef = useRef({ userId, projectId, chapterId, runId: run.id, contextChecksum: run.context_checksum });
  const selectedRef = useRef<GenerationCandidateVersionDetail | null>(null);
  const workspaceHeadingRef = useRef<HTMLHeadingElement | null>(null);
  const acceptedHeadingRef = useRef<HTMLHeadingElement | null>(null);
  const editorRef = useRef<HTMLTextAreaElement | null>(null);
  const editButtonRef = useRef<HTMLButtonElement | null>(null);
  const restoredHeadingRef = useRef<HTMLHeadingElement | null>(null);
  const listTitleRef = useRef<HTMLHeadingElement | null>(null);
  const pageErrorRef = useRef<HTMLDivElement | null>(null);
  const versionRowRefs = useRef(new Map<string, HTMLButtonElement>());
  const errorRef = useRef<HTMLDivElement | null>(null);
  const focusFrame = useRef<number | null>(null);
  const lastFocusedError = useRef("");

  const identity: CandidateVersionIdentity = {
    userId,
    projectId,
    chapterId,
    runId: run.id,
    chapterTitle,
  };
  identityRef.current = { userId, projectId, chapterId, runId: run.id, contextChecksum: run.context_checksum };
  selectedRef.current = selected;
  const locked = recoveryBusy || pointerBusy || editing || !!pending || storageBlocked;
  const versionBusy = busy || pointerBusy;

  const scheduleCandidateFocus = useCallback((candidate: GenerationCandidateVersionDetail) => {
    if (focusFrame.current !== null) window.cancelAnimationFrame(focusFrame.current);
    const expected = { userId, projectId, chapterId, runId: run.id, candidateId: candidate.id };
    focusFrame.current = window.requestAnimationFrame(() => {
      focusFrame.current = null;
      const currentIdentity = identityRef.current;
      if (currentIdentity.userId !== expected.userId || currentIdentity.projectId !== expected.projectId
        || currentIdentity.chapterId !== expected.chapterId || currentIdentity.runId !== expected.runId
        || selectedRef.current?.id !== expected.candidateId || !acceptedHeadingRef.current?.isConnected) return;
      acceptedHeadingRef.current.focus();
    });
  }, [userId, projectId, chapterId, run.id]);

  function scheduleEditorFocus() {
    if (focusFrame.current !== null) window.cancelAnimationFrame(focusFrame.current);
    const expected = { ...identityRef.current };
    focusFrame.current = window.requestAnimationFrame(() => {
      focusFrame.current = null;
      const current = identityRef.current;
      if (current.userId !== expected.userId || current.projectId !== expected.projectId
        || current.chapterId !== expected.chapterId || current.runId !== expected.runId
        || current.contextChecksum !== expected.contextChecksum || !editorRef.current?.isConnected) return;
      editorRef.current.focus();
    });
  }

  function scheduleEditButtonFocus() {
    if (focusFrame.current !== null) window.cancelAnimationFrame(focusFrame.current);
    const expected = { ...identityRef.current };
    focusFrame.current = window.requestAnimationFrame(() => {
      focusFrame.current = null;
      const current = identityRef.current;
      if (current.userId !== expected.userId || current.projectId !== expected.projectId
        || current.chapterId !== expected.chapterId || current.runId !== expected.runId
        || current.contextChecksum !== expected.contextChecksum) return;
      const editAction = editButtonRef.current;
      if (editAction?.isConnected && !editAction.disabled) {
        editAction.focus();
      } else if (acceptedHeadingRef.current?.isConnected) {
        acceptedHeadingRef.current.focus();
      } else if (workspaceHeadingRef.current?.isConnected) {
        workspaceHeadingRef.current.focus();
      }
    });
  }

  function scheduleRestoredDraftFocus(expectedDraft: CandidateManualEditDraft) {
    if (focusFrame.current !== null) window.cancelAnimationFrame(focusFrame.current);
    const expected = { ...identityRef.current, parentId: expectedDraft.parent_candidate_id };
    focusFrame.current = window.requestAnimationFrame(() => {
      focusFrame.current = null;
      const current = identityRef.current;
      if (current.userId !== expected.userId || current.projectId !== expected.projectId
        || current.chapterId !== expected.chapterId || current.runId !== expected.runId
        || current.contextChecksum !== expected.contextChecksum
        || selectedRef.current?.id !== expected.parentId || !restoredHeadingRef.current?.isConnected) return;
      restoredHeadingRef.current.focus();
    });
  }

  function scheduleVersionListFocus(candidateId?: string) {
    if (focusFrame.current !== null) window.cancelAnimationFrame(focusFrame.current);
    const expected = { ...identityRef.current, candidateId };
    focusFrame.current = window.requestAnimationFrame(() => {
      focusFrame.current = null;
      const current = identityRef.current;
      if (current.userId !== expected.userId || current.projectId !== expected.projectId
        || current.chapterId !== expected.chapterId || current.runId !== expected.runId
        || current.contextChecksum !== expected.contextChecksum) return;
      const row = expected.candidateId
        ? versionRowRefs.current.get(expected.candidateId)
        : undefined;
      const target = row?.isConnected && !row.disabled ? row : listTitleRef.current;
      if (target?.isConnected) target.focus();
    });
  }

  useEffect(() => {
    onLockChange?.(locked);
    return () => onLockChange?.(false);
  }, [locked, onLockChange]);

  useEffect(() => {
    if (!locked) return;
    const prevent = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", prevent);
    return () => window.removeEventListener("beforeunload", prevent);
  }, [locked]);

  useEffect(() => {
    if (!error) {
      lastFocusedError.current = "";
      return;
    }
    if (lastFocusedError.current === error) return;
    lastFocusedError.current = error;
    errorRef.current?.focus();
  }, [error]);

  useEffect(() => {
    if (pageError) pageErrorRef.current?.focus();
  }, [pageError]);

  const readAudit = useCallback(async (candidate: GenerationCandidateVersionDetail) => {
    const auditRequest = ++auditGeneration.current;
    auditController.current?.abort();
    const controller = new AbortController();
    auditController.current = controller;
    const expectedIdentity = {
      userId, projectId, chapterId, runId: run.id,
      contextChecksum: run.context_checksum,
      candidateId: candidate.id,
      versionNo: candidate.version_no,
      checksum: candidate.content_checksum,
    };
    setAudit(null);
    setAuditBusy(true);
    setAuditError("");
    try {
      const value = await readGenerationCandidateAudit({
        projectId,
        runId: run.id,
        chapterId,
        candidate,
        contextChecksum: run.context_checksum,
        targetWordCount: run.context_manifest.chapter.target_word_count,
        elements: run.context_manifest.elements.map((item) => ({
          elementId: item.element_id,
          typeKey: item.type.key,
          typeDisplayName: item.type.display_name,
          name: item.version.name,
          versionNo: item.version.version_no,
        })),
        relationCount: run.context_manifest.counts.relations,
        warnings: run.context_manifest.warnings,
      }, controller.signal);
      const currentIdentity = identityRef.current;
      const currentCandidate = selectedRef.current;
      if (auditRequest === auditGeneration.current && !controller.signal.aborted
        && currentIdentity.userId === expectedIdentity.userId
        && currentIdentity.projectId === expectedIdentity.projectId
        && currentIdentity.chapterId === expectedIdentity.chapterId
        && currentIdentity.runId === expectedIdentity.runId
        && currentIdentity.contextChecksum === expectedIdentity.contextChecksum
        && currentCandidate?.id === expectedIdentity.candidateId
        && currentCandidate.version_no === expectedIdentity.versionNo
        && currentCandidate.content_checksum === expectedIdentity.checksum) setAudit(value);
    } catch (cause) {
      if (auditRequest === auditGeneration.current && !controller.signal.aborted) {
        setAuditError(`${message(cause)} 只会重新读取检查，不会再次另存。`);
      }
    } finally {
      if (auditRequest === auditGeneration.current && !controller.signal.aborted) setAuditBusy(false);
    }
  }, [projectId, chapterId, run]);

  const refreshList = useCallback(async (request: number) => {
    const listRequest = ++listGeneration.current;
    listController.current?.abort();
    const controller = new AbortController();
    listController.current = controller;
    const expected = { ...identityRef.current };
    pageGeneration.current += 1;
    pageController.current?.abort();
    pageController.current = null;
    try {
      const value = await listCandidateVersions(identity, { limit: 50, signal: controller.signal });
      const current = identityRef.current;
      if (request !== requestGeneration.current || listRequest !== listGeneration.current
        || controller.signal.aborted || current.userId !== expected.userId
        || current.projectId !== expected.projectId || current.chapterId !== expected.chapterId
        || current.runId !== expected.runId || current.contextChecksum !== expected.contextChecksum) return null;
      setItems(value.items);
      setHasMore(value.has_more);
      setNextCursor(value.next_cursor);
      setListNotice("");
      setPageError("");
      setPageBusy(false);
      return value;
    } catch (cause) {
      if (controller.signal.aborted || listRequest !== listGeneration.current
        || request !== requestGeneration.current) return null;
      throw cause;
    }
  // The individual identity primitives are the stale-response boundary.
  }, [userId, projectId, chapterId, run.id, run.context_checksum, chapterTitle]);

  const accept = useCallback(async (
    candidate: GenerationCandidateVersionDetail,
    parent: GenerationCandidateVersionDetail,
    operation: PendingCandidateManualEdit,
    request: number
  ) => {
    if (request !== requestGeneration.current
      || candidate.project_id !== projectId
      || candidate.run_id !== run.id
      || candidate.planning_chapter_id !== chapterId) return;
    if (operation.payload.expected_context_checksum !== run.context_checksum
      || !candidateMatchesManualEditParent(candidate, parent, operation, identity)) {
      if (request === requestGeneration.current) {
        setStorageBlocked(true);
        setError("新候选与已确认父版本、根来源或冻结上下文不一致，已保留恢复线索并停止写入。");
      }
      return;
    }
    const list = await refreshList(request);
    const listed = list?.items.find((item) => item.id === candidate.id);
    if (!listed || !listConfirmsDetail(listed, candidate)) {
      if (request === requestGeneration.current) {
        setError("新候选已返回，但权威版本列表尚未确认它。已保留恢复线索。");
      }
      return;
    }
    if (!clearPendingCandidateManualEdit(operation)) {
      setError("新候选已确认，但本地恢复线索未能安全清除。");
      return;
    }
    setSelected(candidate);
    selectedRef.current = candidate;
    setPending(null);
    setEditing(false);
    const savedDraft = loadCandidateManualEditDraft(identity);
    if (savedDraft.status !== "missing"
      && (savedDraft.status !== "available"
        || savedDraft.draft.content !== operation.payload.content
        || !clearCandidateManualEditDraft(savedDraft.draft))) {
      setStorageBlocked(true);
      setError("新候选已确认，但本标签页草稿记录未能安全清除。已停止继续编辑。");
      return;
    }
    setDraft("");
    setStoredDraft(null);
    setDraftIssue(null);
    setOriginalRetryReason(null);
    setError("");
    const next = new URLSearchParams(searchParams);
    next.set("candidate_version", candidate.id);
    pointerTransition.current = candidate.id;
    acceptedPointer.current = candidate.id;
    setSearchParams(next, { replace: true });
    scheduleCandidateFocus(candidate);
    void readAudit(candidate);
  }, [projectId, run.id, run.context_checksum, chapterId, userId, refreshList, readAudit, searchParams, setSearchParams, scheduleCandidateFocus]);

  const reconcile = useCallback(async (
    operation: PendingCandidateManualEdit,
    parent: GenerationCandidateVersionDetail,
    request: number
  ) => {
    try {
      const response = await readCandidateManualEditByKey(identity, operation);
      await accept(response.candidate, parent, operation, request);
    } catch (cause) {
      if (request !== requestGeneration.current) return;
      if (exactNotFound(cause)) {
        setOriginalRetryReason("not_found");
        setError("服务端确认未找到这次另存。只有作者明确选择后，才会使用原编号和原正文重试。");
      } else {
        setError(`${message(cause)} 继续保留原恢复线索，系统不会自动重复另存。`);
      }
    }
  }, [identity.userId, identity.projectId, identity.chapterId, identity.runId, identity.chapterTitle, accept]);

  useEffect(() => {
    const request = ++requestGeneration.current;
    let disposed = false;
    setRecoveryBusy(true);
    setItems([]);
    setListBusy(true);
    setListError("");
    setListNotice("");
    setHasMore(false);
    setNextCursor(null);
    setPageBusy(false);
    setPageError("");
    listGeneration.current += 1;
    listController.current?.abort();
    listController.current = null;
    pageGeneration.current += 1;
    pageController.current?.abort();
    pageController.current = null;
    pointerGeneration.current += 1;
    pointerController.current?.abort();
    pointerController.current = null;
    setPointerBusy(false);
    pointerTransition.current = null;
    acceptedPointer.current = initialCandidateId;
    setSelected(null);
    selectedRef.current = null;
    setEditing(false);
    setDraft("");
    setAudit(null);
    setAuditError("");
    setError("");
    setNotice("");
    setPending(null);
    setOriginalRetryReason(null);
    setStorageBlocked(false);
    setStoredDraft(null);
    setDraftIssue(null);
    const loaded = loadPendingCandidateManualEdit(userId, projectId);
    if (loaded.status === "available") {
      const operation = loaded.operation;
      setPending(operation);
      setDraft(operation.payload.content);
      if (operation.chapter_id !== chapterId || operation.run_id !== run.id) {
        setStorageBlocked(true);
        setError("另一章还有未核对的候选另存，本页不会使用它写入。");
      }
    } else if (loaded.status === "foreign") {
      setStorageBlocked(true);
      setError("项目中还有其他类型的未决操作；候选编辑已停止且不会覆盖恢复记录。");
    } else if (loaded.status === "corrupt" || loaded.status === "unavailable") {
      setStorageBlocked(true);
      setError(loaded.status === "corrupt"
        ? "候选另存恢复记录损坏或身份不匹配，已停止新写入。"
        : "浏览器恢复存储不可用，已停止新写入。请修复浏览器会话存储设置后重新加载页面。");
    }
    // Draft ownership must be resolved synchronously before the first network read.
    // Any present or unreadable draft keeps the page locked until its exact parent is verified.
    const draftLoaded = loadCandidateManualEditDraft(identity);
    if (draftLoaded.status === "available") {
      setStorageBlocked(true);
      setStoredDraft(draftLoaded.draft);
      setDraft(draftLoaded.draft.content);
    } else if (draftLoaded.status !== "missing") {
      setStorageBlocked(true);
      setDraftIssue(draftLoaded.status === "foreign"
        ? { kind: "foreign", draft: draftLoaded.draft }
        : draftLoaded.status === "corrupt" ? { kind: "corrupt" } : null);
      const draftError = draftLoaded.status === "foreign"
        ? "另一章还有本标签页未另存草稿；请返回原章处理。"
        : draftLoaded.status === "corrupt"
          ? "本标签页候选草稿损坏，已停止写入。"
          : "本标签页草稿存储不可用，已停止写入。请修复浏览器会话存储设置后重新加载页面。";
      setError((current) => current ? `${current} ${draftError}` : draftError);
    }
    void (async () => {
      try {
        try {
          await refreshList(request);
        } catch (cause) {
          if (!disposed && request === requestGeneration.current) setListError(message(cause));
        } finally {
          if (!disposed && request === requestGeneration.current) setListBusy(false);
        }
        if (disposed || request !== requestGeneration.current) return;
        const candidateId = loaded.status === "available"
          ? loaded.operation.payload.parent_candidate_id
          : initialCandidateId;
        const detail = await readCandidateVersion({ ...identity, candidateId });
        if (disposed || request !== requestGeneration.current) return;
        setSelected(detail);
        selectedRef.current = detail;
        if (loaded.status === "available") {
          const operation = loaded.operation;
          if (operation.chapter_id !== chapterId || operation.run_id !== run.id
            || operation.payload.parent_candidate_id !== detail.id
            || operation.payload.expected_parent_version_no !== detail.version_no
            || operation.payload.expected_parent_checksum !== detail.content_checksum
            || operation.payload.expected_context_checksum !== run.context_checksum) {
            setStorageBlocked(true);
            setError("候选另存恢复记录与当前父版本或冻结上下文不一致，已保留线索并停止写入。");
            return;
          }
          await reconcile(operation, detail, request);
          return;
        }
        if (loaded.status !== "missing") return;
        if (draftLoaded.status === "available") {
          const saved = draftLoaded.draft;
          if (saved.parent_candidate_id !== detail.id
            || saved.parent_version_no !== detail.version_no
            || saved.parent_checksum !== detail.content_checksum
            || saved.context_checksum !== run.context_checksum) {
            setStorageBlocked(true);
            setStoredDraft(saved);
            setDraft(saved.content);
            setDraftIssue({ kind: "stale", draft: saved });
            setError("本标签页草稿与当前父版本或冻结上下文不一致，已停止恢复和写入。");
            return;
          }
          setStoredDraft(saved);
          setDraft(saved.content);
          setDraftIssue({ kind: "restored", draft: saved });
          scheduleRestoredDraftFocus(saved);
          void readAudit(detail);
        }
        if (loaded.status === "missing" && draftLoaded.status === "missing") {
          void readAudit(detail);
        }
      } catch (cause) {
        if (!disposed && request === requestGeneration.current) setError(message(cause));
      } finally {
        if (!disposed && request === requestGeneration.current) setRecoveryBusy(false);
      }
    })();
    return () => {
      disposed = true;
      listGeneration.current += 1;
      listController.current?.abort();
      pageGeneration.current += 1;
      pageController.current?.abort();
      pointerGeneration.current += 1;
      pointerController.current?.abort();
      auditGeneration.current += 1;
      auditController.current?.abort();
      if (focusFrame.current !== null) {
        window.cancelAnimationFrame(focusFrame.current);
        focusFrame.current = null;
      }
      if (request === requestGeneration.current) requestGeneration.current += 1;
    };
  }, [userId, projectId, chapterId, run.id, run.context_checksum, chapterTitle]);

  useEffect(() => {
    if (!initialCandidateId) return;
    if (pointerTransition.current === initialCandidateId) {
      pointerTransition.current = null;
      acceptedPointer.current = initialCandidateId;
      setPointerBusy(false);
      return;
    }
    pointerTransition.current = null;
    if (acceptedPointer.current === initialCandidateId) {
      setPointerBusy(false);
      return;
    }
    if (editing || pending || storageBlocked) {
      const accepted = acceptedPointer.current;
      if (accepted && accepted !== initialCandidateId) {
        const next = new URLSearchParams(searchParams);
        next.set("candidate_version", accepted);
        pointerTransition.current = accepted;
        setSearchParams(next, { replace: true });
      }
      setError("候选地址已变化，但当前还有未完成的草稿或恢复操作；已安全返回正在查看的候选，未读取或提交新内容。");
      return;
    }
    const request = ++pointerGeneration.current;
    pointerController.current?.abort();
    const controller = new AbortController();
    pointerController.current = controller;
    const expected = { ...identityRef.current, candidateId: initialCandidateId };
    setPointerBusy(true);
    setError("");
    void readCandidateVersion({ ...identity, candidateId: initialCandidateId }, controller.signal)
      .then((detail) => {
        const current = identityRef.current;
        if (request !== pointerGeneration.current || controller.signal.aborted
          || current.userId !== expected.userId || current.projectId !== expected.projectId
          || current.chapterId !== expected.chapterId || current.runId !== expected.runId
          || current.contextChecksum !== expected.contextChecksum) return;
        acceptedPointer.current = detail.id;
        setSelected(detail);
        selectedRef.current = detail;
        setEditing(false);
        setDraft("");
        setAudit(null);
        scheduleCandidateFocus(detail);
        void readAudit(detail);
      })
      .catch((cause) => {
        if (request === pointerGeneration.current && !controller.signal.aborted) {
          setError(`${message(cause)} 候选地址只会触发严格读取，不会提交或覆盖任何内容。`);
        }
      })
      .finally(() => {
        if (request === pointerGeneration.current && !controller.signal.aborted) setPointerBusy(false);
      });
    return () => {
      controller.abort();
      if (request === pointerGeneration.current) setPointerBusy(false);
    };
  // Pointer reconciliation is triggered only by a pointer or frozen-identity change.
  // Local editing/recovery state changes must not consume an internal transition
  // before React Router publishes its matching candidate_version value.
  }, [initialCandidateId, userId, projectId, chapterId, run.id, run.context_checksum, chapterTitle, searchParams, setSearchParams, readAudit, scheduleCandidateFocus]);

  async function selectVersion(item: GenerationCandidateVersionListItem) {
    if (versionBusy || pending || storageBlocked || (editing && !window.confirm("放弃当前未另存的编辑副本并切换版本吗？"))) return;
    if (editing && selected
      && (!storedDraft || !clearCandidateManualEditDraft(storedDraft))) {
      setStorageBlocked(true);
      setError("未能安全清除当前草稿，已停止切换版本。");
      return;
    }
    const request = ++requestGeneration.current;
    setBusy(true);
    setError("");
    try {
      const detail = await readCandidateVersion({ ...identity, candidateId: item.id });
      if (request === requestGeneration.current) {
        setSelected(detail);
        selectedRef.current = detail;
        setEditing(false);
        setDraft("");
        setAudit(null);
        const next = new URLSearchParams(searchParams);
        next.set("candidate_version", detail.id);
        pointerTransition.current = detail.id;
        acceptedPointer.current = detail.id;
        setSearchParams(next, { replace: true });
        scheduleCandidateFocus(detail);
        void readAudit(detail);
      }
    } catch (cause) {
      if (request === requestGeneration.current) setError(message(cause));
    } finally {
      if (request === requestGeneration.current) setBusy(false);
    }
  }

  function beginEdit() {
    if (!selected || versionBusy || pending || disabledReason || storageBlocked) return;
    const saved = draftFor(identity, selected, run.context_checksum, selected.content);
    if (!saveCandidateManualEditDraft(saved)) {
      setStorageBlocked(true);
      setError("无法保存本标签页草稿，已停止编辑和服务端写入。");
      return;
    }
    setDraft(selected.content);
    setStoredDraft(saved);
    setEditing(true);
    setError("");
    setNotice("");
    scheduleEditorFocus();
  }

  function changeDraft(content: string) {
    if (!selected || storageBlocked) return;
    const saved = draftFor(identity, selected, run.context_checksum, content);
    if (!saveCandidateManualEditDraft(saved)) {
      setStorageBlocked(true);
      setError("本标签页草稿保存失败，已停止编辑和服务端写入。");
      return;
    }
    setStoredDraft(saved);
    setDraft(content);
  }

  function cancelEdit() {
    if (!editing || !window.confirm("放弃这份未另存的编辑副本吗？")) return;
    if (!selected || !storedDraft || !clearCandidateManualEditDraft(storedDraft)) {
      setStorageBlocked(true);
      setError("未能安全清除本标签页草稿，已停止后续写入。");
      return;
    }
    setEditing(false);
    setDraft("");
    setStoredDraft(null);
    setError("");
    setNotice("");
    scheduleEditButtonFocus();
  }

  function clearCorruptDraft() {
    if (!draftIssue || draftIssue.kind !== "corrupt"
      || !window.confirm("仅清除这个浏览器标签页中损坏的草稿记录？不会修改服务端候选或共享恢复线索。")) return;
    if (!clearCorruptCandidateManualEditDraft(userId, projectId)) {
      setError("损坏草稿未能安全清除，仍保持禁写。");
      return;
    }
    setDraftIssue(null);
    setStorageBlocked(false);
    setError("");
    setNotice("已仅清除这个浏览器标签页的损坏草稿；服务端和共享恢复线索未改动。");
    scheduleEditButtonFocus();
  }

  function abandonStaleDraft() {
    const saved = draftIssue?.kind === "stale" ? draftIssue.draft : undefined;
    if (!saved || !window.confirm("放弃这份与当前权威父版本不一致的本地草稿？")) return;
    if (!clearCandidateManualEditDraft(saved)) {
      setError("旧草稿未能按完整身份安全清除，仍保持禁写。");
      return;
    }
    setDraftIssue(null);
    setStoredDraft(null);
    setDraft("");
    setStorageBlocked(false);
    setError("");
    setNotice("旧草稿已放弃；没有修改服务端候选。");
    scheduleEditButtonFocus();
  }

  function rebaseStaleDraft() {
    const saved = draftIssue?.kind === "stale" ? draftIssue.draft : undefined;
    if (!saved || !selected
      || !window.confirm("确认将保留的草稿正文明确重新绑定到页面已严格读取的权威父版本？此步不会提交服务端。")) return;
    const replacement = draftFor(identity, selected, run.context_checksum, saved.content);
    if (!replaceCandidateManualEditDraft(saved, replacement)) {
      setError("旧草稿已变化或无法安全重新绑定，仍保持禁写。");
      return;
    }
    setStoredDraft(replacement);
    setDraft(saved.content);
    setDraftIssue(null);
    setStorageBlocked(false);
    setEditing(true);
    setError("");
    setNotice("草稿已明确重新绑定到页面已严格读取的权威父版本；尚未另存或修改服务端。");
    scheduleEditorFocus();
  }

  async function copyStaleDraft() {
    const content = draftIssue?.kind === "stale" ? draftIssue.draft?.content : undefined;
    if (!content) return;
    try {
      await navigator.clipboard.writeText(content);
      setError("");
      setNotice("旧草稿正文已复制；没有修改任何记录。");
    } catch {
      setError("自动复制失败，请从下方只读草稿中手动复制。");
    }
  }

  function continueRestoredDraft() {
    const saved = draftIssue?.kind === "restored" ? draftIssue.draft : undefined;
    const current = loadCandidateManualEditDraft(identity);
    if (!saved || current.status !== "available" || JSON.stringify(current.draft) !== JSON.stringify(saved)) {
      setStorageBlocked(true);
      setError("草稿恢复记录已经变化，未进入编辑；请重新加载后核对。");
      return;
    }
    setStoredDraft(saved);
    setDraft(saved.content);
    setDraftIssue(null);
    setStorageBlocked(false);
    setEditing(true);
    setError("");
    setNotice("已恢复本标签页草稿；尚未另存或修改服务端。");
    scheduleEditorFocus();
  }

  function abandonRestoredDraft() {
    const saved = draftIssue?.kind === "restored" ? draftIssue.draft : undefined;
    if (!saved || !window.confirm("放弃这份尚未另存的本标签页草稿吗？不会修改服务端候选。")) return;
    if (!clearCandidateManualEditDraft(saved)) {
      setStorageBlocked(true);
      setError("草稿记录已变化或无法安全清除，继续保持禁写。");
      return;
    }
    setStoredDraft(null);
    setDraft("");
    setDraftIssue(null);
    setStorageBlocked(false);
    setError("");
    setNotice("本标签页草稿已放弃；没有修改服务端候选。");
    scheduleEditButtonFocus();
  }

  async function retryVersionList() {
    const ownerRequest = requestGeneration.current;
    setListBusy(true);
    setListError("");
    setListNotice("");
    try {
      const value = await refreshList(ownerRequest);
      if (!value) return;
      setListNotice(value.items.length > 0
        ? `版本列表已重新读取，共 ${value.items.length} 个版本。`
        : "版本列表已重新读取，当前没有候选版本。");
      scheduleVersionListFocus(value.items[0]?.id);
    } catch (cause) {
      if (ownerRequest === requestGeneration.current) setListError(message(cause));
    } finally {
      if (ownerRequest === requestGeneration.current) setListBusy(false);
    }
  }

  async function loadMoreVersions() {
    if (pageBusy || !hasMore || !nextCursor || items.length === 0) return;
    const beforeVersionNo = Number(nextCursor);
    if (!Number.isSafeInteger(beforeVersionNo) || beforeVersionNo < 1
      || items.at(-1)?.version_no !== beforeVersionNo) {
      setPageError("更多版本游标与当前严格页尾不一致，已保留现有列表。");
      return;
    }
    const request = ++pageGeneration.current;
    pageController.current?.abort();
    const controller = new AbortController();
    pageController.current = controller;
    const expected = { ...identityRef.current };
    const currentItems = items;
    setPageBusy(true);
    setPageError("");
    setListNotice("");
    try {
      const page = await listCandidateVersions(identity, {
        limit: 50,
        beforeVersionNo,
        signal: controller.signal,
      });
      const current = identityRef.current;
      if (request !== pageGeneration.current || controller.signal.aborted
        || current.userId !== expected.userId || current.projectId !== expected.projectId
        || current.chapterId !== expected.chapterId || current.runId !== expected.runId
        || current.contextChecksum !== expected.contextChecksum) return;
      const merged = mergeCandidatePage(currentItems, page, beforeVersionNo);
      setItems(merged);
      setHasMore(page.has_more);
      setNextCursor(page.next_cursor);
      setListNotice(page.items.length > 0
        ? `已加载 ${page.items.length} 个较早版本。${page.has_more ? "" : " 已显示全部候选版本。"}`
        : "没有更多候选版本，已显示全部候选版本。");
      scheduleVersionListFocus(page.items[0]?.id);
    } catch (cause) {
      if (request === pageGeneration.current && !controller.signal.aborted) setPageError(message(cause));
    } finally {
      if (request === pageGeneration.current && !controller.signal.aborted) setPageBusy(false);
    }
  }

  async function submit(operation: PendingCandidateManualEdit) {
    const request = ++requestGeneration.current;
    setBusy(true);
    setError("");
    setNotice("");
    setOriginalRetryReason(null);
    try {
      const response = await requestCandidateManualEdit(identity, operation.payload);
      const parent = await readCandidateVersion({ ...identity, candidateId: operation.payload.parent_candidate_id });
      if (request !== requestGeneration.current) return;
      await accept(response.candidate, parent, operation, request);
    } catch (cause) {
      if (request !== requestGeneration.current) return;
      if (cause instanceof ApiError && cause.status === 409) {
        const conflict = manualEditConflict(cause);
        if (conflict === "version_conflict") {
          setOriginalRetryReason("version_conflict");
          setError(`${cause.detail} 已保留原编号、草稿和恢复线索；只有作者明确确认才会按原编号重试。`);
          return;
        }
        if (["content_unchanged", "parent_changed", "context_changed", "operation_conflict"].includes(conflict)) {
          if (!clearPendingCandidateManualEdit(operation)) {
            setStorageBlocked(true);
            setError(`${cause.detail} 但旧恢复线索未能按完整身份安全清除，已保持禁写。`);
            return;
          }
          setPending(null);
          setOriginalRetryReason(null);
          if (conflict === "content_unchanged") {
            setError(`${cause.detail} 已确认本次零写入并清除旧恢复线索；草稿仍保留。`);
          } else {
            const loadedDraft = loadCandidateManualEditDraft(identity);
            const saved = storedDraft ?? (loadedDraft.status === "available" ? loadedDraft.draft : null);
            setStorageBlocked(true);
            if (saved) {
              setStoredDraft(saved);
              setDraft(saved.content);
              setDraftIssue({ kind: "stale", draft: saved });
            }
            setError(`${cause.detail} 已确认本次零写入并清除不可恢复的旧线索；草稿未丢失，不会自动换号或重提。`);
          }
        } else {
          setError(`${cause.detail} 冲突语义无法安全确认；已保留编辑副本和完整恢复线索，不会自动重试。`);
        }
      } else {
        setError("另存响应不确定，正在按原编号核对服务端；系统不会自动重复提交。");
        if (selected) await reconcile(operation, selected, request);
      }
    } finally {
      if (request === requestGeneration.current) setBusy(false);
    }
  }

  function saveDraft() {
    if (!selected || versionBusy || pending || disabledReason || storageBlocked) return;
    let payload;
    setNotice("");
    try {
      payload = parseCandidateManualEditInput({
        operation_key: createCandidateManualEditOperationKey(),
        parent_candidate_id: selected.id,
        expected_parent_version_no: selected.version_no,
        expected_parent_checksum: selected.content_checksum,
        expected_context_checksum: run.context_checksum,
        content: draft,
      }, selected.content);
    } catch (cause) {
      setError(message(cause));
      return;
    }
    const savedDraft = draftFor(identity, selected, run.context_checksum, draft);
    if (!saveCandidateManualEditDraft(savedDraft)) {
      setStorageBlocked(true);
      setError("无法确认本标签页草稿已保存，已停止提交，服务端不会收到本次另存。");
      return;
    }
    setStoredDraft(savedDraft);
    const operation: PendingCandidateManualEdit = {
      schema_version: 5,
      workspace: "candidate_manual_edit",
      user_id: userId,
      project_id: projectId,
      chapter_id: chapterId,
      run_id: run.id,
      operation_key: payload.operation_key,
      payload,
      created_at: new Date().toISOString(),
    };
    if (!savePendingCandidateManualEdit(operation)) {
      setError("无法在浏览器保存恢复线索，已停止提交，服务端不会收到本次另存。");
      return;
    }
    setPending(operation);
    void submit(operation);
  }

  function retryOriginal() {
    if (!pending || versionBusy || !originalRetryReason
      || !window.confirm("确认使用原编号、原父版本和原正文重试一次另存吗？")) return;
    void submit(pending);
  }

  return (
    <section className="candidate-version-workspace" aria-labelledby="candidate-version-title" aria-busy={versionBusy || recoveryBusy}>
      <header>
        <div>
          <h6 ref={workspaceHeadingRef} tabIndex={-1} id="candidate-version-title">候选版本</h6>
          <p>均为独立候选，不会覆盖原稿；选用功能尚未开放。</p>
        </div>
      </header>
      {error && <div ref={errorRef} className="planning-generation__error" role="alert" tabIndex={-1}>{error}</div>}
      {notice && <p className="candidate-version-notice" role="status" aria-live="polite">{notice}</p>}
      {(versionBusy || recoveryBusy) && <p role="status">正在核对候选版本…</p>}
      {draftIssue?.kind === "foreign" && draftIssue.draft && (
        <div className="planning-generation__warning candidate-version-recovery" role="alert">
          <span>这个标签页的草稿属于另一章或另一份生成准备；本页不会套用或覆盖它。</span>
          <a className="btn btn-secondary" href={`/project/${draftIssue.draft.project_id}/plan/chapters?scope=chapter&target=${draftIssue.draft.chapter_id}&generation_run=${draftIssue.draft.run_id}&candidate_version=${draftIssue.draft.parent_candidate_id}`}>
            返回原章处理草稿
          </a>
        </div>
      )}
      {draftIssue?.kind === "corrupt" && (
        <div className="planning-generation__warning candidate-version-recovery" role="alert">
          <span>本标签页的候选草稿损坏。只能由作者明确确认清除该浏览器草稿；不会触碰服务端。</span>
          <button className="btn btn-secondary" onClick={clearCorruptDraft}>确认仅清除浏览器损坏草稿</button>
        </div>
      )}
      {draftIssue?.kind === "stale" && draftIssue.draft && (
        <section className="planning-generation__warning candidate-version-recovery" aria-labelledby="stale-candidate-draft-title">
          <h6 id="stale-candidate-draft-title">待处理的旧草稿</h6>
          <p>草稿正文已保留，但父版本或冻结上下文已不一致。不会静默套用，也不会自动提交。</p>
          <textarea aria-label="待处理的旧草稿正文" readOnly value={draftIssue.draft.content} />
          <div className="planning-generation__actions">
            <button className="btn btn-secondary" onClick={() => void copyStaleDraft()}>复制旧草稿</button>
            <button className="btn btn-secondary" onClick={abandonStaleDraft}>放弃旧草稿</button>
            <button className="btn btn-primary" disabled={!selected} onClick={rebaseStaleDraft}>基于页面已严格读取的权威父版本重新开始</button>
          </div>
        </section>
      )}
      {draftIssue?.kind === "restored" && draftIssue.draft && (
        <section className="planning-generation__warning candidate-version-recovery" aria-labelledby="restored-candidate-draft-title">
          <h6 ref={restoredHeadingRef} tabIndex={-1} id="restored-candidate-draft-title">发现本标签页未另存草稿</h6>
          <p>草稿与页面已严格读取的权威父版本一致。请明确选择继续编辑或放弃；系统不会自动提交。</p>
          <p role="status">草稿恢复核对完成，等待你的选择。</p>
          <div className="planning-generation__actions">
            <button className="btn btn-primary" onClick={continueRestoredDraft}>继续编辑草稿</button>
            <button className="btn btn-secondary" onClick={abandonRestoredDraft}>放弃本地草稿</button>
          </div>
        </section>
      )}
      {pending && <div className="planning-generation__warning candidate-version-recovery" role="status"><span>已保留本地恢复线索。只按原编号核对，不会自动重复另存。</span><button className="btn btn-secondary" disabled={versionBusy || recoveryBusy || storageBlocked || !selected} onClick={() => { if (!selected) return; const request = ++requestGeneration.current; setBusy(true); void reconcile(pending, selected, request).finally(() => { if (request === requestGeneration.current) setBusy(false); }); }}>按原编号核对</button></div>}
      {originalRetryReason && <div className="planning-generation__warning candidate-version-recovery" role="alert"><span>{originalRetryReason === "not_found" ? "服务端精确确认未找到原记录。只有你明确确认才会使用原编号和原正文重试。" : "服务端确认版本并发冲突；原记录和草稿均已保留。只有你明确确认才会按原编号重试。"}</span><button className="btn btn-secondary" disabled={versionBusy} onClick={retryOriginal}>明确确认原请求重试</button></div>}

      <section className="candidate-version-list-panel" aria-labelledby="candidate-version-list-title" aria-busy={listBusy}>
        <h6 ref={listTitleRef} tabIndex={-1} id="candidate-version-list-title">版本列表</h6>
        {listNotice && <p role="status" aria-live="polite">{listNotice}</p>}
        {listBusy && <p role="status">正在读取版本列表…</p>}
        {listError && <div className="candidate-version-list-error" role="alert"><span>{listError}</span><button className="btn btn-secondary" disabled={listBusy} onClick={() => void retryVersionList()}>重新读取版本列表</button></div>}
        {!listBusy && !listError && items.length === 0 && <p className="candidate-version-list-empty" role="status">暂无可查看的候选版本。</p>}
        {items.length > 0 && <ol className="candidate-version-list" aria-label="候选版本列表">
          {items.map((item) => {
            const viewing = selected?.id === item.id;
            return (
              <li key={item.id}>
                <button ref={(node) => { if (node) versionRowRefs.current.set(item.id, node); else versionRowRefs.current.delete(item.id); }} className="candidate-version-row" aria-pressed={viewing} disabled={versionBusy || !!pending || storageBlocked} onClick={() => void selectVersion(item)}>
                  <strong>版本 {item.version_no}</strong>
                  <span>{sourceLabel(item)}</span>
                  <span>{costLabel(item)}</span>
                  <span>{rootLabel(item)}</span>
                  <span>{item.word_count} 字词 · {new Date(item.created_at).toLocaleString()}</span>
                  {viewing && <span className="candidate-version-row__viewing">正在查看</span>}
                </button>
              </li>
            );
          })}
        </ol>}
        {pageBusy && <p role="status">正在加载更多版本…</p>}
        {pageError && <div ref={pageErrorRef} tabIndex={-1} className="candidate-version-page-error" role="alert"><span>{pageError}</span><button className="btn btn-secondary" disabled={pageBusy} onClick={() => void loadMoreVersions()}>重试加载更多版本</button></div>}
        {hasMore && !pageError && <button className="btn btn-secondary candidate-version-load-more" disabled={pageBusy} onClick={() => void loadMoreVersions()}>{pageBusy ? "正在加载更多版本…" : "加载更多版本"}</button>}
      </section>

      {selected && (
        <article className="candidate-version-detail">
          <header>
            <div>
              <h6 ref={acceptedHeadingRef} tabIndex={-1}>{selected.title} · 候选版本 {selected.version_no}</h6>
              <span>{selected.word_count} 字词</span>
            </div>
            <strong>未覆盖原稿</strong>
          </header>
          <dl className="candidate-version-provenance">
            <div><dt>本版本来源</dt><dd>{sourceLabel(selected)}</dd></div>
            <div><dt>本版本费用</dt><dd>{costLabel(selected)}</dd></div>
            <div><dt>根来源</dt><dd>{rootLabel(selected).replace("根来源：", "")}</dd></div>
          </dl>
          {editing ? (
            <div className="candidate-version-editor">
              <label htmlFor="candidate-version-draft">编辑副本</label>
              <textarea ref={editorRef} id="candidate-version-draft" value={draft} onChange={(event) => changeDraft(event.target.value)} disabled={versionBusy || !!pending || storageBlocked} />
              <p>标题与版本号由服务端维护，不可在此编辑。</p>
              <div className="planning-generation__actions">
                <button className="btn btn-primary" disabled={versionBusy || !!pending || storageBlocked} onClick={saveDraft}>保存为新候选版本</button>
                <button className="btn btn-secondary" disabled={versionBusy || !!pending || storageBlocked} onClick={cancelEdit}>取消</button>
              </div>
            </div>
          ) : (
            <>
              <pre tabIndex={0} aria-label="正在查看的候选版本正文">{selected.content}</pre>
              <button ref={editButtonRef} className="btn btn-secondary" disabled={versionBusy || !!pending || !!disabledReason || storageBlocked} onClick={beginEdit}>基于此候选编辑</button>
              {disabledReason && <p role="status">{disabledReason}</p>}
            </>
          )}
          <section className="planning-generation__audit" aria-busy={auditBusy}>
            <h6>确定性检查</h6>
            <p>只核对内容完整性、目标字数与冻结上下文；不判断语义一致性，仍需作者判断。</p>
            {auditBusy && <p role="status">正在读取检查…</p>}
            {auditError && <div role="status" aria-live="polite"><span>{auditError}</span><button className="btn btn-secondary" disabled={auditBusy} onClick={() => { void readAudit(selected); }}>重新读取检查</button></div>}
            {audit && <p role="status" aria-live="polite">{audit.status === "review" ? "需要作者人工核对。" : "未发现确定性完整问题。"} 本次未自动确认任何伏笔状态。</p>}
          </section>
        </article>
      )}
    </section>
  );
}
