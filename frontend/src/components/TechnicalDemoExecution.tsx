import { useCallback, useEffect, useId, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { ApiError } from "@/services/api";
import { readGenerationCandidateAudit } from "@/services/generationExecution";
import { clearPendingProjectOperationRecord } from "@/services/pendingProjectOperations";
import {
  clearPendingTechnicalDemoExecution,
  createTechnicalDemoOperationKey,
  loadPendingTechnicalDemoExecution,
  readTechnicalDemoCandidate,
  readTechnicalDemoCapability,
  readTechnicalDemoExecutionByKey,
  requestTechnicalDemoExecution,
  savePendingTechnicalDemoExecution,
  type PendingTechnicalDemoExecution,
  type TechnicalIdentity,
} from "@/services/technicalDemoExecution";
import type { GenerationCandidateAuditResponse, GenerationRunResponse } from "@/types/generation";
import type { TechnicalDemoCandidateResponse, TechnicalDemoCapabilityResponse, TechnicalDemoExecutionResponse } from "@/types/demo";

interface Props {
  userId: string;
  projectId: string;
  chapterId: string;
  chapterTitle: string;
  run: GenerationRunResponse;
  disabledReason?: string;
  onLockChange?: (locked: boolean) => void;
}

type ConfirmKind = "new" | "original" | null;

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.detail;
  return error instanceof Error ? error.message : "技术模拟暂时无法继续。";
}

function exactNotFound(error: unknown): error is ApiError {
  return error instanceof ApiError && error.status === 404
    && error.code === "TECHNICAL_DEMO_EXECUTION_NOT_FOUND"
    && error.retryable && error.recommendedAction === "retry_original_technical_demo";
}

function exactAdapterUnavailable(error: unknown): error is ApiError {
  return error instanceof ApiError && error.status === 503
    && error.code === "TECHNICAL_DEMO_ADAPTER_UNAVAILABLE"
    && error.retryable && error.recommendedAction === "start_new_confirmed_technical_demo";
}

export default function TechnicalDemoExecution({ userId, projectId, chapterId, chapterTitle, run, disabledReason = "", onLockChange }: Props) {
  const [searchParams, setSearchParams] = useSearchParams();
  const [pending, setPending] = useState<PendingTechnicalDemoExecution | null>(null);
  const [capability, setCapability] = useState<TechnicalDemoCapabilityResponse | null>(null);
  const [execution, setExecution] = useState<TechnicalDemoExecutionResponse | null>(null);
  const [candidate, setCandidate] = useState<TechnicalDemoCandidateResponse | null>(null);
  const [audit, setAudit] = useState<GenerationCandidateAuditResponse | null>(null);
  const [confirmKind, setConfirmKind] = useState<ConfirmKind>(null);
  const [busy, setBusy] = useState(false);
  const [candidateBusy, setCandidateBusy] = useState(false);
  const [auditBusy, setAuditBusy] = useState(false);
  const [error, setError] = useState("");
  const [auditError, setAuditError] = useState("");
  const [originalRetryAllowed, setOriginalRetryAllowed] = useState(false);
  const [newAttemptAllowed, setNewAttemptAllowed] = useState(false);
  const [storageRecovery, setStorageRecovery] = useState<"corrupt" | "unavailable" | null>(null);
  const requestGeneration = useRef(0);
  const dialogRef = useRef<HTMLDivElement | null>(null);
  const cancelRef = useRef<HTMLButtonElement | null>(null);
  const returnFocusRef = useRef<HTMLElement | null>(null);
  const candidateHeadingRef = useRef<HTMLHeadingElement | null>(null);
  const headingId = useId();

  const baseIdentity: TechnicalIdentity = { projectId, chapterId, runId: run.id, contextChecksum: run.context_checksum, userId, chapterTitle };

  const readAudit = useCallback(async (value: TechnicalDemoCandidateResponse, request: number) => {
    setAuditBusy(true); setAuditError("");
    try {
      const result = await readGenerationCandidateAudit({
        projectId,
        runId: run.id,
        chapterId,
        candidate: value,
        contextChecksum: run.context_checksum,
        targetWordCount: run.context_manifest.chapter.target_word_count,
        elements: run.context_manifest.elements.map((item) => ({ elementId: item.element_id, typeKey: item.type.key, typeDisplayName: item.type.display_name, name: item.version.name, versionNo: item.version.version_no })),
        relationCount: run.context_manifest.counts.relations,
        warnings: run.context_manifest.warnings,
      });
      if (request === requestGeneration.current) setAudit(result);
    } catch (cause) {
      if (request === requestGeneration.current) setAuditError(`${errorMessage(cause)} 候选仍保留只读，不会重新执行技术模拟。`);
    } finally {
      if (request === requestGeneration.current) setAuditBusy(false);
    }
  }, [projectId, chapterId, run]);

  const readCandidate = useCallback(async (receipt: TechnicalDemoExecutionResponse, operation: PendingTechnicalDemoExecution | null, request: number) => {
    setCandidateBusy(true); setError("");
    try {
      const value = await readTechnicalDemoCandidate({ ...baseIdentity, operationKey: receipt.operation_key, capabilityChecksum: receipt.capability_checksum, executionId: receipt.execution_id, candidateId: receipt.candidate_id });
      if (request !== requestGeneration.current) return;
      setCandidate(value);
      const next = new URLSearchParams(searchParams);
      next.set("technical_demo_run", run.id);
      next.set("technical_demo_execution", receipt.execution_id);
      next.set("technical_demo_candidate", receipt.candidate_id);
      setSearchParams(next, { replace: true });
      if (operation && clearPendingTechnicalDemoExecution(userId, projectId, operation.operation_key)) {
        setPending(null); onLockChange?.(false);
      } else if (operation) {
        setError("候选已严格校验，但本地恢复线索未能安全清除。请不要开始新操作。");
      }
      window.setTimeout(() => candidateHeadingRef.current?.focus(), 0);
      void readAudit(value, request);
    } catch (cause) {
      if (request === requestGeneration.current) setError(`${errorMessage(cause)} 只允许重新读取候选，不会再次执行技术模拟。`);
    } finally {
      if (request === requestGeneration.current) setCandidateBusy(false);
    }
  // searchParams is intentionally captured to preserve unrelated URL state at the accepted response boundary.
  }, [baseIdentity.projectId, baseIdentity.chapterId, baseIdentity.runId, baseIdentity.contextChecksum, baseIdentity.userId, baseIdentity.chapterTitle, userId, projectId, run.id, searchParams, setSearchParams, onLockChange, readAudit]);

  const acceptExecution = useCallback((value: TechnicalDemoExecutionResponse, operation: PendingTechnicalDemoExecution | null, request: number) => {
    if (request !== requestGeneration.current || value.project_id !== projectId || value.planning_chapter_id !== chapterId || value.run_id !== run.id) return;
    setExecution(value); setOriginalRetryAllowed(false); setNewAttemptAllowed(false);
    void readCandidate(value, operation, request);
  }, [projectId, chapterId, run.id, readCandidate]);

  const checkByKey = useCallback(async (operation: PendingTechnicalDemoExecution, request: number) => {
    try {
      const value = await readTechnicalDemoExecutionByKey({ ...baseIdentity, operationKey: operation.operation_key, capabilityChecksum: operation.payload.expected_capability_checksum });
      acceptExecution(value, operation, request);
    } catch (cause) {
      if (request !== requestGeneration.current) return;
      if (exactNotFound(cause)) {
        setOriginalRetryAllowed(true);
        setError("服务端确认尚未找到该记录。如需使用原编号和原载荷重试，必须重新核对能力并再次确认。");
      } else {
        setError(`${errorMessage(cause)} 请继续按原编号核对；系统不会自动重复提交。`);
      }
    }
  }, [baseIdentity.projectId, baseIdentity.chapterId, baseIdentity.runId, baseIdentity.contextChecksum, baseIdentity.userId, baseIdentity.chapterTitle, acceptExecution]);

  useEffect(() => {
    const request = ++requestGeneration.current;
    const cleanup = () => { if (request === requestGeneration.current) requestGeneration.current += 1; };
    setCandidate(null); setExecution(null); setAudit(null); setError(""); setAuditError("");
    const loaded = loadPendingTechnicalDemoExecution(userId, projectId);
    if (loaded.status === "available") {
      if (loaded.operation.chapter_id !== chapterId || loaded.operation.run_id !== run.id || loaded.operation.payload.expected_context_checksum !== run.context_checksum) {
        setError("已保存的技术模拟线索属于另一个章节或上下文，本页不会使用它执行。");
        onLockChange?.(true);
        return cleanup;
      }
      setPending(loaded.operation); onLockChange?.(true); setBusy(true);
      void checkByKey(loaded.operation, request).finally(() => { if (request === requestGeneration.current) setBusy(false); });
      return cleanup;
    }
    if (loaded.status === "corrupt" || loaded.status === "unavailable") {
      setStorageRecovery(loaded.status);
      setError(loaded.status === "corrupt" ? "本地技术模拟恢复线索已损坏，已停止执行。" : "当前无法读取本地恢复线索，已停止执行。");
      onLockChange?.(true);
      return cleanup;
    }
    if (loaded.status === "foreign") return cleanup;

    const pointerRun = searchParams.get("technical_demo_run");
    const pointerExecution = searchParams.get("technical_demo_execution");
    const pointerCandidate = searchParams.get("technical_demo_candidate");
    if (pointerRun === run.id && pointerExecution && pointerCandidate) {
      setCandidateBusy(true);
      void readTechnicalDemoCandidate({ ...baseIdentity, executionId: pointerExecution, candidateId: pointerCandidate }).then((value) => {
        if (request !== requestGeneration.current) return;
        setCandidate(value); window.setTimeout(() => candidateHeadingRef.current?.focus(), 0); void readAudit(value, request);
      }).catch((cause) => { if (request === requestGeneration.current) setError(`${errorMessage(cause)} 地址只用于只读恢复，不会触发技术模拟。`); }).finally(() => { if (request === requestGeneration.current) setCandidateBusy(false); });
    }
    return cleanup;
  // Identity fields are the recovery boundary; URL text changes are handled by explicit accepted writes.
  }, [userId, projectId, chapterId, run.id, run.context_checksum]);

  useEffect(() => {
    if (!confirmKind) return;
    const returnTarget = returnFocusRef.current;
    window.setTimeout(() => cancelRef.current?.focus(), 0);
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") { event.preventDefault(); setConfirmKind(null); return; }
      if (event.key !== "Tab" || !dialogRef.current) return;
      const items = Array.from(dialogRef.current.querySelectorAll<HTMLElement>("button:not([disabled]), [href], [tabindex]:not([tabindex='-1'])"));
      if (!items.length) return;
      if (event.shiftKey && document.activeElement === items[0]) { event.preventDefault(); items[items.length - 1].focus(); }
      else if (!event.shiftKey && document.activeElement === items[items.length - 1]) { event.preventDefault(); items[0].focus(); }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => { document.removeEventListener("keydown", onKeyDown); window.setTimeout(() => returnTarget?.focus(), 0); };
  }, [confirmKind]);

  async function openNewConfirmation(trigger: HTMLElement) {
    if (busy || disabledReason || pending) return;
    returnFocusRef.current = trigger;
    const request = ++requestGeneration.current;
    setBusy(true); setError("");
    try {
      const value = await readTechnicalDemoCapability(baseIdentity);
      if (request !== requestGeneration.current) return;
      setCapability(value); setConfirmKind("new");
    } catch (cause) { if (request === requestGeneration.current) setError(errorMessage(cause)); }
    finally { if (request === requestGeneration.current) setBusy(false); }
  }

  async function openOriginalConfirmation(trigger: HTMLElement) {
    if (!pending || busy) return;
    returnFocusRef.current = trigger;
    const request = ++requestGeneration.current;
    setBusy(true); setError("");
    try {
      const value = await readTechnicalDemoCapability(baseIdentity);
      if (request !== requestGeneration.current) return;
      if (value.capability_checksum !== pending.payload.expected_capability_checksum || value.context_checksum !== pending.payload.expected_context_checksum) {
        setOriginalRetryAllowed(false);
        setError("技术模拟能力或上下文已变化，原编号载荷不再允许提交。请保留记录并刷新当前上下文。");
        return;
      }
      setCapability(value); setConfirmKind("original");
    } catch (cause) { if (request === requestGeneration.current) setError(errorMessage(cause)); }
    finally { if (request === requestGeneration.current) setBusy(false); }
  }

  async function submit(operation: PendingTechnicalDemoExecution) {
    const request = ++requestGeneration.current;
    setBusy(true); setError(""); setOriginalRetryAllowed(false); setNewAttemptAllowed(false);
    try {
      const value = await requestTechnicalDemoExecution({ ...baseIdentity, operationKey: operation.operation_key, capabilityChecksum: operation.payload.expected_capability_checksum }, operation.payload);
      acceptExecution(value, operation, request);
    } catch (cause) {
      if (request !== requestGeneration.current) return;
      if (exactAdapterUnavailable(cause)) {
        if (clearPendingTechnicalDemoExecution(userId, projectId, operation.operation_key)) {
          setPending(null); onLockChange?.(false); setNewAttemptAllowed(true);
          setError("固定技术模拟适配器暂时不可用，服务端确认未保存执行或候选。如需重试，必须使用新编号并重新确认。");
        } else setError("适配器暂时不可用，且本地恢复线索未能安全清除。");
      } else {
        setError("提交响应不确定，正在按原编号核对服务端；系统不会自动重复提交。");
        await checkByKey(operation, request);
      }
    } finally { if (request === requestGeneration.current) setBusy(false); }
  }

  function confirm() {
    if (!capability || !confirmKind || capability.project_id !== projectId || capability.planning_chapter_id !== chapterId || capability.run_id !== run.id || capability.context_checksum !== run.context_checksum) {
      setConfirmKind(null); setError("当前章节或上下文已变化，本次确认已取消。"); return;
    }
    if (confirmKind === "original") {
      const original = pending;
      setConfirmKind(null);
      if (!original || capability.capability_checksum !== original.payload.expected_capability_checksum) { setError("原请求能力校验不再一致，已取消提交。"); return; }
      void submit(original); return;
    }
    const operationKey = createTechnicalDemoOperationKey();
    const operation: PendingTechnicalDemoExecution = {
      schema_version: 4, workspace: "technical_demo_execution", user_id: userId, project_id: projectId, chapter_id: chapterId, run_id: run.id, operation_key: operationKey,
      payload: { operation_key: operationKey, expected_context_checksum: run.context_checksum, expected_capability_checksum: capability.capability_checksum, fixture_version: 1, confirm_technical_demo: true }, created_at: new Date().toISOString(),
    };
    setConfirmKind(null);
    if (!savePendingTechnicalDemoExecution(operation)) { setError("无法在浏览器保存恢复线索，已停止提交，本次不会调用技术模拟。"); return; }
    setPending(operation); onLockChange?.(true); void submit(operation);
  }

  function reconcile() {
    if (!pending || busy) return;
    const request = ++requestGeneration.current; setBusy(true); setError("");
    void checkByKey(pending, request).finally(() => { if (request === requestGeneration.current) setBusy(false); });
  }

  function clearCorruptRecovery() {
    if (storageRecovery !== "corrupt" || !window.confirm("只清除本浏览器中损坏的共享恢复记录，不会删除服务器数据。确认继续吗？")) return;
    if (clearPendingProjectOperationRecord(userId, projectId)) {
      setStorageRecovery(null); setError(""); onLockChange?.(false);
    } else {
      setError("本机恢复记录清除失败，已继续锁定新执行。");
      onLockChange?.(true);
    }
  }

  return (
    <section className="planning-generation__execution technical-demo-execution" aria-labelledby={headingId} aria-busy={busy || candidateBusy}>
      <header><div><h5 id={headingId}>第五步：运行固定技术模拟</h5><p>这一步只验证安全落盘、恢复、候选读取和审计闭环，不调用 AI。</p></div><span className="planning-generation__zero-cost">固定内容 · 零 AI · 零模型费用</span></header>
      <div className="planning-generation__guarantees" aria-label="技术模拟边界"><span>不调用 AI</span><span>不产生模型费用</span><span>不覆盖现有原稿</span><span>伏笔仍为未埋入</span></div>
      {error && <div className="planning-generation__error" role="alert" tabIndex={-1}><strong>技术模拟需要处理</strong><span>{error}</span></div>}
      {storageRecovery === "corrupt" && <div className="planning-generation__actions"><button className="btn btn-secondary" onClick={clearCorruptRecovery}>确认清除损坏恢复记录</button></div>}
      {(busy || candidateBusy) && <p className="planning-generation__status" role="status">{candidateBusy ? "正在严格校验固定候选…" : "正在核对技术模拟状态…"}</p>}
      {!candidate && !execution && !pending && <div className="planning-generation__actions"><button className="btn btn-primary" disabled={busy || !!disabledReason} onClick={(event) => void openNewConfirmation(event.currentTarget)}>查看边界并确认技术模拟</button>{disabledReason && <p role="status">{disabledReason}</p>}</div>}
      {pending && !candidate && <div className="planning-generation__warning" role="status"><span>已保存一条本地恢复线索。只允许按原编号核对，不会自动重复执行。</span><button className="btn btn-secondary" disabled={busy} onClick={reconcile}>按原编号核对</button></div>}
      {originalRetryAllowed && <div className="planning-generation__warning" role="alert"><span>只有再次核对能力并弹出确认框后，才能使用原编号、原载荷重试。</span><button className="btn btn-secondary" disabled={busy} onClick={(event) => void openOriginalConfirmation(event.currentTarget)}>重新核对并确认原请求</button></div>}
      {newAttemptAllowed && <div className="planning-generation__actions"><button className="btn btn-secondary" disabled={busy} onClick={(event) => void openNewConfirmation(event.currentTarget)}>使用新编号重新确认</button></div>}
      {execution && !candidate && <div className="planning-generation__warning" role="alert"><span>服务端已完成技术模拟，但候选尚未通过本地严格校验。只允许重新读取候选。</span><button className="btn btn-secondary" disabled={candidateBusy} onClick={() => { const request = ++requestGeneration.current; void readCandidate(execution, pending, request); }}>重新读取固定候选</button></div>}
      {candidate && <article className="planning-generation__candidate technical-demo-candidate"><header><div><h6 ref={candidateHeadingRef} tabIndex={-1}>固定技术模拟候选已就绪：{candidate.title}</h6><span>独立候选版本 {candidate.version_no} · {candidate.word_count} 字词</span></div><strong>未覆盖原稿</strong></header><pre tabIndex={0} aria-label="固定技术模拟候选正文">{candidate.content}</pre><p>该内容由服务端固定适配器返回，不是 AI 生成，不产生模型费用，也没有自动确认伏笔。</p><section className="planning-generation__audit" aria-busy={auditBusy}><h6>确定性审计</h6><p>只核对内容完整性、目标字数、冻结设定和显式《》名称；不判断情节、人物或世界规则的语义一致性，仍需作者判断。</p>{auditBusy && <p role="status">正在读取审计…</p>}{auditError && <div role="alert"><span>{auditError}</span><button className="btn btn-secondary" onClick={() => void readAudit(candidate, requestGeneration.current)}>重新读取审计</button></div>}{audit && <><p role="status" aria-live="polite">{audit.status === "pass" ? "未发现确定性完整问题。" : "审计完成，需要作者人工核对。"} 伏笔动作为 0，仍未埋入。</p><dl className="planning-generation__receipt"><div><dt>目标字数</dt><dd>{audit.target_length.status === "not_applicable" ? "本章未设置目标字数" : `实际 ${audit.target_length.actual_word_count} 字词，建议范围 ${audit.target_length.minimum_word_count}–${audit.target_length.maximum_word_count}`}</dd></div><div><dt>冻结设定摘要</dt><dd>{audit.context_summary.element_count} 项设定 · {audit.context_summary.relation_count} 条关系</dd></div><div><dt>生成前提醒</dt><dd>{audit.preparation.warnings.length ? `${audit.preparation.warnings.length} 项需核对` : "无"}</dd></div></dl>{audit.context_summary.elements.length > 0 && <details><summary>查看本次冻结设定</summary><ul>{audit.context_summary.elements.map((item) => <li key={item.element_id}><strong>{item.name}</strong> · {item.type_display_name} · 内容版本 {item.version_no}</li>)}</ul></details>}{audit.preparation.warnings.length > 0 && <div className="planning-generation__warning"><strong>生成准备提醒</strong><ul>{audit.preparation.warnings.map((item, index) => <li key={`${item.code}-${item.element_id ?? index}`}>{item.code === "CHAPTER_SUMMARY_EMPTY" ? "本章摘要为空。" : "设定内容在分配后有更新。"}</li>)}</ul></div>}{audit.unrecognized_explicit_terms.items.length > 0 && <div className="planning-generation__warning"><strong>《》名称需人工核对</strong><p>以下名称未出现在本次冻结设定清单中，请判断是否需要纳入设定。</p><ul>{audit.unrecognized_explicit_terms.items.map((item) => <li key={`${item.start_offset}-${item.term}`}><strong>《{item.term}》</strong><blockquote>{item.excerpt}</blockquote></li>)}</ul></div>}</>}</section></article>}
      {confirmKind && capability && <div className="planning-generation-confirm-overlay" role="presentation"><div ref={dialogRef} className="planning-generation-confirm" role="alertdialog" aria-modal="true" aria-labelledby="technical-confirm-title" aria-describedby="technical-confirm-description"><h4 id="technical-confirm-title">确认运行固定技术模拟</h4><p id="technical-confirm-description">{confirmKind === "original" ? "确认后仅使用原编号和原载荷重试一次。" : "确认后会先保存本地恢复线索，再提交一次固定技术模拟。"} 本流程不调用 AI，不产生模型费用。</p><ul><li>内容由固定技术适配器返回。</li><li>候选独立保存，不覆盖原稿。</li><li>不会自动埋入、强化或回收伏笔。</li></ul><div className="planning-generation__actions"><button ref={cancelRef} className="btn btn-secondary" disabled={busy} onClick={() => setConfirmKind(null)}>取消，不执行</button><button className="btn btn-primary" disabled={busy} onClick={confirm}>{confirmKind === "original" ? "确认原请求重试" : "确认运行技术模拟"}</button></div></div></div>}
    </section>
  );
}
