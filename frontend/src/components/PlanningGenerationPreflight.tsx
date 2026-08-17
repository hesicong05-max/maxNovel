import { useEffect, useId, useMemo, useRef } from "react";
import TechnicalDemoExecution from "@/components/TechnicalDemoExecution";
import type {
  GenerationAttemptResponse,
  GenerationCandidateAuditResponse,
  GenerationCandidateResponse,
  GenerationCapabilityResponse,
  GenerationRunResponse,
} from "@/types/generation";
import type { NovelPlan, PlanningChapter, PlanningPart } from "@/types/planning";

export type GenerationRecoveryState =
  | "idle"
  | "checking"
  | "not_found"
  | "unknown"
  | "corrupt"
  | "saved_unavailable";

interface Props {
  plan: NovelPlan;
  part: PlanningPart;
  chapter: PlanningChapter;
  run: GenerationRunResponse | null;
  busy: boolean;
  loadingSaved: boolean;
  disabled: boolean;
  disabledReason: string;
  error: string;
  recoveryState: GenerationRecoveryState;
  stale: boolean;
  recovered: boolean;
  focusResultToken: number;
  focusFeedbackToken: number;
  hasPendingRecovery: boolean;
  capability: GenerationCapabilityResponse | null;
  attempt: GenerationAttemptResponse | null;
  candidate: GenerationCandidateResponse | null;
  candidateAudit: GenerationCandidateAuditResponse | null;
  auditLoading: boolean;
  auditError: string;
  executionBusy: boolean;
  candidateLoading: boolean;
  executionError: string;
  executionDisabledReason: string;
  runActionsDisabledReason: string;
  confirmationOpen: boolean;
  confirmationUsesOriginalRequest: boolean;
  originalRetryAllowed: boolean;
  newAttemptDisabled: boolean;
  executionMode?: "real" | "technical" | "hidden";
  technicalDemoUserId?: string;
  onTechnicalDemoLockChange?: (locked: boolean) => void;
  onPrepare: () => void;
  onCheckPending: () => void;
  onRetryOriginal: () => void;
  onFocusAssignments: () => void;
  onClearSavedPointer: () => void;
  onAbandonPending: () => void;
  onOpenGenerationConfirmation: () => void;
  onCancelGenerationConfirmation: () => void;
  onConfirmGeneration: () => void;
  onCheckGenerationAttempt: () => void;
  onReadGenerationCandidate: () => void;
  onReadGenerationCandidateAudit: () => void;
  onRetryOriginalGeneration: () => void;
  onStartNewAfterFailure: () => void;
}

const warningText: Record<string, string> = {
  CHAPTER_SUMMARY_EMPTY: "本章摘要为空；真正生成前建议先补充本章目标。",
  LORE_CHANGED_SINCE_ASSIGNMENT: "设定内容在分配后有更新；本记录使用检查时的当前版本。",
};

function scopeLabel(scopeType: "novel" | "part" | "chapter", title: string): string {
  if (scopeType === "novel") return "继承自整部小说";
  if (scopeType === "part") return `继承自篇章《${title}》`;
  return "本章节直接分配";
}

function providerLabel(providerName: string): string {
  const known: Record<string, string> = {
    openai: "OpenAI 兼容服务",
    deepseek: "DeepSeek",
  };
  return known[providerName.toLowerCase()] ?? "已配置的模型服务";
}

function JsonSnapshot({ value }: { value: Record<string, unknown> }) {
  const entries = Object.entries(value);
  if (entries.length === 0) return <p className="planning-generation-empty">无已确认字段。</p>;
  return (
    <dl className="planning-generation-fields">
      {entries.map(([key, item]) => (
        <div key={key}>
          <dt>{key}</dt>
          <dd>{typeof item === "string" ? item : JSON.stringify(item)}</dd>
        </div>
      ))}
    </dl>
  );
}

export default function PlanningGenerationPreflight({
  plan,
  part,
  chapter,
  run,
  busy,
  loadingSaved,
  disabled,
  disabledReason,
  error,
  recoveryState,
  stale,
  recovered,
  focusResultToken,
  focusFeedbackToken,
  hasPendingRecovery,
  capability,
  attempt,
  candidate,
  candidateAudit,
  auditLoading,
  auditError,
  executionBusy,
  candidateLoading,
  executionError,
  executionDisabledReason,
  runActionsDisabledReason,
  confirmationOpen,
  confirmationUsesOriginalRequest,
  originalRetryAllowed,
  newAttemptDisabled,
  executionMode = "real",
  technicalDemoUserId,
  onTechnicalDemoLockChange,
  onPrepare,
  onCheckPending,
  onRetryOriginal,
  onFocusAssignments,
  onClearSavedPointer,
  onAbandonPending,
  onOpenGenerationConfirmation,
  onCancelGenerationConfirmation,
  onConfirmGeneration,
  onCheckGenerationAttempt,
  onReadGenerationCandidate,
  onReadGenerationCandidateAudit,
  onRetryOriginalGeneration,
  onStartNewAfterFailure,
}: Props) {
  const resultHeadingRef = useRef<HTMLHeadingElement | null>(null);
  const feedbackRef = useRef<HTMLDivElement | null>(null);
  const dialogRef = useRef<HTMLDivElement | null>(null);
  const confirmationTriggerRef = useRef<HTMLButtonElement | null>(null);
  const cancelConfirmationRef = useRef<HTMLButtonElement | null>(null);
  const executionAlertRef = useRef<HTMLDivElement | null>(null);
  const candidateHeadingRef = useRef<HTMLHeadingElement | null>(null);
  const previousCandidateId = useRef<string | null>(null);
  const previousTerminalKey = useRef<string | null>(null);
  const warningHeadingId = useId();
  useEffect(() => {
    if (focusResultToken > 0) resultHeadingRef.current?.focus();
  }, [focusResultToken]);
  useEffect(() => {
    if (focusFeedbackToken > 0) feedbackRef.current?.focus();
  }, [focusFeedbackToken]);
  useEffect(() => {
    if (!confirmationOpen) return;
    const returnTarget = confirmationTriggerRef.current;
    window.setTimeout(() => cancelConfirmationRef.current?.focus(), 0);
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onCancelGenerationConfirmation();
        return;
      }
      if (event.key !== "Tab" || !dialogRef.current) return;
      const focusable = Array.from(dialogRef.current.querySelectorAll<HTMLElement>(
        "button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex='-1'])"
      ));
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      window.setTimeout(() => returnTarget?.focus(), 0);
    };
  }, [confirmationOpen, onCancelGenerationConfirmation]);
  useEffect(() => {
    if (candidate && candidate.id !== previousCandidateId.current) {
      previousCandidateId.current = candidate.id;
      window.setTimeout(() => candidateHeadingRef.current?.focus(), 0);
    }
  }, [candidate?.id]);
  useEffect(() => {
    const terminalKey = executionError
      ? `error:${executionError}`
      : attempt && (attempt.status === "failed" || attempt.status === "outcome_unknown")
        ? `${attempt.id}:${attempt.status}:${attempt.lock_version}`
        : null;
    if (terminalKey && terminalKey !== previousTerminalKey.current) {
      previousTerminalKey.current = terminalKey;
      window.setTimeout(() => executionAlertRef.current?.focus(), 0);
    }
  }, [executionError, attempt?.id, attempt?.status, attempt?.lock_version]);

  const elementNames = useMemo(() => new Map(
    run?.context_manifest.elements.map((item) => [item.element_id, item.version.name]) ?? []
  ), [run]);

  return (
    <section id="demo-technical-generation" className="planning-generation" tabIndex={-1} aria-busy={busy || loadingSaved}>
      <header className="planning-generation__heading">
        <div>
          <h3>{executionMode === "technical" ? `技术模拟前：冻结《${chapter.title}》上下文` : "生成前上下文检查"}</h3>
          <p>{executionMode === "technical"
            ? "先检查并保存本章将使用的上下文；之后可运行固定内容技术模拟，全程不调用 AI、不产生模型费用，也不会覆盖正文。"
            : executionMode === "hidden"
              ? "这里仅检查并保存本章将使用的上下文，不调用 AI、不产生模型费用，也不会创建或修改正文。"
              : "第一步只检查并保存本章将使用的上下文，不调用 AI、不产生模型费用，也不会创建或修改章节正文。下方“生成候选正文”是另一个需要单独付费确认的步骤。"}</p>
        </div>
        <span className="planning-generation__zero-cost">上下文检查：零 AI · 零费用</span>
      </header>

      <div className="planning-generation__chapter">
        <strong>{part.title} · {chapter.title}</strong>
        <span>{chapter.summary.trim() ? "已有章节摘要" : "章节摘要待补充"} · {chapter.target_word_count ? `目标 ${chapter.target_word_count} 字` : "未设置目标字数"}</span>
      </div>

      {(loadingSaved || busy || error || recoveryState !== "idle") && (
        <div className="planning-generation__feedback" ref={feedbackRef} tabIndex={-1}>
          {loadingSaved && <p className="planning-generation__status" role="status">正在读取已保存的检查记录…</p>}
          {busy && <p className="planning-generation__status" role="status">正在核对并保存本章权威上下文…</p>}
          {error && <div className="planning-generation__error" role="alert"><strong>上下文检查未通过</strong><span>{error}</span></div>}
          {(recoveryState === "unknown" || recoveryState === "checking") && (
            <div className="planning-generation__warning" role={recoveryState === "unknown" ? "alert" : "status"}>
              <span>{recoveryState === "checking" ? "正在使用原操作编号核对检查结果…" : "上次请求结果尚不确定。请先按原操作编号核对，避免重复创建记录。"}</span>
              <button className="btn btn-secondary" disabled={busy || recoveryState === "checking"} onClick={onCheckPending}>{recoveryState === "checking" ? "核对中…" : "核对上次检查"}</button>
            </div>
          )}
          {recoveryState === "not_found" && (
            <div className="planning-generation__warning" role="alert">
              <span>服务端尚未找到这条检查记录。只能使用原操作编号和原版本载荷安全重试。</span>
              <button className="btn btn-secondary" disabled={busy} onClick={onRetryOriginal}>使用原请求安全重试</button>
            </div>
          )}
          {recoveryState === "corrupt" && <div className="planning-generation__error" role="alert"><span>服务端检查记录的契约或身份无效，已停止部分展示和自动重试。</span><button className="btn btn-secondary" disabled={busy} onClick={hasPendingRecovery ? onAbandonPending : onClearSavedPointer}>{hasPendingRecovery ? "明确放弃原检查恢复线索" : "关闭无效记录指针"}</button></div>}
          {recoveryState === "saved_unavailable" && (
            <div className="planning-generation__warning" role="alert">
              <span>当前地址指向的已保存检查记录无法读取。请刷新页面重试，或只关闭本页记录视图。</span>
              <button className="btn btn-secondary" disabled={busy} onClick={onClearSavedPointer}>关闭无效记录指针</button>
            </div>
          )}
        </div>
      )}

      {!run && recoveryState === "idle" && !loadingSaved && (
        <div className="planning-generation__actions">
          <button className="btn btn-primary" disabled={disabled || busy} onClick={onPrepare}>
            {busy ? "正在检查…" : "检查生成上下文"}
          </button>
          {disabledReason && <p role="status">{disabledReason}</p>}
          {disabledReason.includes("设定") && <button className="btn btn-secondary" onClick={onFocusAssignments}>管理本章设定</button>}
        </div>
      )}

      {run && (
        <div className="planning-generation__result">
          <header>
            <div>
              <h4 ref={resultHeadingRef} tabIndex={-1}>检查记录已保存</h4>
              <p>{recovered || run.replayed ? "已找回服务端保存的检查记录。" : "已冻结本章本次检查使用的权威上下文。"}</p>
            </div>
            {stale && <strong className="planning-generation__stale">基于旧版本</strong>}
          </header>

          <div className="planning-generation__guarantees" aria-label="本次上下文检查收据边界">
            <span>本次检查：AI 未调用</span><span>本次检查费用：无</span><span>本次检查：正文未修改</span><span>状态：仅检查</span>
          </div>
          {stale && <div className="planning-generation__warning" role="status">当前规划、章节或设定分配已经变化；此处保留历史快照供核对，请按最新资料重新检查。</div>}

          <dl className="planning-generation__receipt">
            {executionMode !== "technical" && <div><dt>记录编号</dt><dd>{run.id}</dd></div>}
            <div><dt>记录时间</dt><dd>{new Date(run.created_at).toLocaleString()}</dd></div>
            <div><dt>上下文大小</dt><dd>{run.context_size_bytes.toLocaleString()} / 65,536 字节</dd></div>
            <div><dt>结构 / 分配 / 章节版本</dt><dd>{run.structure_version} / {run.assignment_version} / {run.chapter_lock_version}</dd></div>
          </dl>

          <div className="planning-generation__counts">
            <div><strong>{run.context_manifest.counts.elements}</strong><span>设定（上限 100）</span></div>
            <div><strong>{run.context_manifest.counts.relations}</strong><span>关系（上限 300）</span></div>
            <div><strong>{run.context_manifest.counts.warnings}</strong><span>提醒</span></div>
          </div>

          {run.context_manifest.warnings.length > 0 && (
            <section className="planning-generation__warnings" aria-labelledby={warningHeadingId}>
              <h5 id={warningHeadingId}>检查提醒</h5>
              <ul>{run.context_manifest.warnings.map((warning, index) => <li key={`${warning.code}-${warning.element_id ?? index}`}>{warningText[warning.code] ?? warning.code}{warning.element_id && elementNames.get(warning.element_id) ? `（${elementNames.get(warning.element_id)}）` : ""}</li>)}</ul>
            </section>
          )}

          <details className="planning-generation__details">
            <summary>查看 {run.context_manifest.elements.length} 项设定</summary>
            <div className="planning-generation__list">
              {run.context_manifest.elements.map((item) => (
                <article key={item.element_id} className="planning-generation__item">
                  <header><div><h5>{item.version.name}</h5><span>{item.type.display_name} · 内容版本 {item.version.version_no}</span></div><strong>{item.assignment_sources.length} 个来源</strong></header>
                  <p>{item.version.summary || "未提供摘要"}</p>
                  <details><summary>查看完整内容快照与字段状态</summary><h6>内容快照</h6><JsonSnapshot value={item.version.payload} /><h6>字段状态</h6><JsonSnapshot value={item.version.field_states} />{executionMode !== "technical" && item.version.source_id && <p>原始出处编号：{item.version.source_id}</p>}</details>
                  <details><summary>查看全部分配来源</summary><ul className="planning-generation__sources">{item.assignment_sources.map((source) => <li key={source.assignment_id}><strong>{scopeLabel(source.scope_type, source.scope_title)}</strong><span>分配时内容版本 {source.assigned_at_content_version} · 分配记录版本 {source.assignment_lock_version}</span></li>)}</ul></details>
                </article>
              ))}
            </div>
          </details>

          <details className="planning-generation__details">
            <summary>查看 {run.context_manifest.relations.length} 条关系</summary>
            {run.context_manifest.relations.length === 0 ? <p className="planning-generation-empty">本次上下文没有设定关系。</p> : <div className="planning-generation__list">{run.context_manifest.relations.map((item) => <article key={item.relation_id} className="planning-generation__item"><h5>{elementNames.get(item.version.source_element_id) ?? "未知设定"} {item.version.forward_label || "有关联"} {elementNames.get(item.version.target_element_id) ?? "未知设定"}</h5><p>{item.version.description || "未提供关系说明"}</p><span>关系版本 {item.version.version_no}</span><details><summary>查看关系附加信息</summary><JsonSnapshot value={item.version.metadata} /></details></article>)}</div>}
          </details>

          <p className="planning-generation__boundary">若后续另行启动生成，这些内容才会成为生成上下文；当前记录本身不是正文，也不是生成任务。本次不安排伏笔埋入、强化或回收。</p>
          {executionMode === "technical" && technicalDemoUserId ? (
            <TechnicalDemoExecution
              userId={technicalDemoUserId}
              projectId={plan.project_id}
              chapterId={chapter.id}
              chapterTitle={chapter.title}
              run={run}
              disabledReason={stale ? "当前检查记录已过期，请先重新检查上下文。" : ""}
              onLockChange={onTechnicalDemoLockChange}
            />
          ) : executionMode === "hidden" ? (
            <div className="planning-generation__boundary" role="status">当前项目不提供候选执行入口；上下文检查记录仍可只读核对。</div>
          ) : <section className="planning-generation__execution" aria-labelledby={`generation-execution-${run.id}`} aria-busy={executionBusy || candidateLoading}>
            <header>
              <div>
                <h5 id={`generation-execution-${run.id}`}>生成候选正文</h5>
                <p>生成结果只保存为独立候选，不会覆盖现有原稿，也不会自动确认任何伏笔状态。</p>
              </div>
              <span className="planning-generation__possible-cost">可能调用模型并产生费用</span>
            </header>

            {executionError && <div ref={executionAlertRef} tabIndex={-1} className="planning-generation__error" role="alert"><strong>生成状态需要处理</strong><span>{executionError}</span></div>}
            {originalRetryAllowed && <div className="planning-generation__warning" role="alert"><span>只能使用原操作编号和完全相同的确认载荷重试；系统不会自动发送。</span><button className="btn btn-secondary" disabled={executionBusy} onClick={onRetryOriginalGeneration}>使用原编号和原载荷重试</button></div>}
            {(executionBusy || candidateLoading) && <p className="planning-generation__status" role="status">{candidateLoading ? "正在校验并读取生成候选…" : "正在核对生成执行状态…"}</p>}

            {!attempt && !candidate && (
              <div className="planning-generation__actions">
                <button ref={confirmationTriggerRef} className="btn btn-primary" disabled={!!executionDisabledReason || executionBusy} onClick={onOpenGenerationConfirmation}>查看模型信息并确认生成</button>
                {executionDisabledReason && <p role="status">{executionDisabledReason}</p>}
              </div>
            )}

            {attempt && !candidate && (
              <div className="planning-generation__attempt">
                <dl className="planning-generation__receipt">
                  <div><dt>执行状态</dt><dd>{attempt.status === "reserved" ? "已预留，尚未确认结果" : attempt.status === "calling" ? "模型调用中" : attempt.status === "failed" ? "本次生成失败" : attempt.status === "outcome_unknown" ? "调用结果未知" : "候选已生成，等待读取"}</dd></div>
                  <div><dt>模型</dt><dd>{providerLabel(attempt.capability.provider_name)} / {attempt.model_name}</dd></div>
                  <div><dt>费用影响</dt><dd>{attempt.billing_effect === "possible" ? "可能已产生模型费用" : "未调用模型、无模型费用"}</dd></div>
                  <div><dt>用量</dt><dd>{attempt.usage.status === "reported" ? `输入 ${attempt.usage.input_tokens} / 输出 ${attempt.usage.output_tokens} / 合计 ${attempt.usage.total_tokens} tokens` : attempt.usage.status === "unknown" ? "暂时未知，不能按 0 展示" : "服务商未返回，不能按 0 展示"}</dd></div>
                </dl>
                {attempt.error && <div ref={executionAlertRef} tabIndex={-1} className="planning-generation__error" role="alert"><strong>{attempt.status === "outcome_unknown" ? "服务端无法确认生成结果" : "生成尝试失败"}</strong><span>{attempt.error.message}</span></div>}
                {(attempt.status === "reserved" || attempt.status === "calling" || attempt.status === "outcome_unknown") && <div className="planning-generation__warning" role={attempt.status === "outcome_unknown" ? "alert" : "status"}><span>{attempt.status === "outcome_unknown" ? "本次调用可能已被服务商受理并产生费用，但服务端暂时无法确认是否完成。系统不会再次发送生成请求，以避免重复生成或重复扣费。" : "系统只会按原操作编号读取状态，不会再次发送生成请求。"}</span><button className="btn btn-secondary" disabled={executionBusy} onClick={onCheckGenerationAttempt}>按原编号核对状态</button></div>}
                {attempt.status === "failed" && <div className="planning-generation__actions"><button className="btn btn-secondary" disabled={executionBusy || newAttemptDisabled} onClick={onStartNewAfterFailure}>重新获取模型信息并确认新尝试</button><p>{attempt.ai_invoked ? "本次已进入模型调用，可能产生费用。" : "本次在模型调用前失败，没有模型费用。"} 新尝试会使用新的操作编号；不会复用失败请求。{newAttemptDisabled ? " 当前章节或上下文不允许开始新尝试。" : ""}</p></div>}
                {attempt.status === "succeeded" && <div className="planning-generation__warning" role="alert"><span>服务端已生成候选，但候选内容尚未完成严格校验。只允许重新读取候选，不会再次请求模型。</span><button className="btn btn-secondary" disabled={candidateLoading} onClick={onReadGenerationCandidate}>重新读取生成候选</button></div>}
              </div>
            )}

            {candidate && (
              <article className="planning-generation__candidate">
                <header><div><h6 ref={candidateHeadingRef} tabIndex={-1}>候选正文已就绪：{candidate.title}</h6><span>独立候选版本 {candidate.version_no} · {candidate.word_count} 字词</span></div><strong>未覆盖原稿</strong></header>
                <pre tabIndex={0} aria-label="候选正文内容">{candidate.content}</pre>
                <dl className="planning-generation__receipt">
                  <div><dt>候选编号</dt><dd>{candidate.id}</dd></div>
                  <div><dt>模型用量</dt><dd>{!attempt ? "请以生成时的执行收据为准" : attempt.usage.status === "reported" ? `输入 ${attempt.usage.input_tokens} / 输出 ${attempt.usage.output_tokens} / 合计 ${attempt.usage.total_tokens} tokens` : attempt.usage.status === "unknown" ? "用量未知" : "服务商未返回用量"}</dd></div>
                </dl>
                <section className="planning-generation__audit" aria-labelledby={`generation-audit-${candidate.id}`} aria-busy={auditLoading}>
                  <header>
                    <div>
                      <h6 id={`generation-audit-${candidate.id}`}>确定性检查</h6>
                      <p>只核对候选完整性、目标字数和本次冻结上下文；不判断情节、人物或世界规则的语义一致性，仍需作者判断。</p>
                    </div>
                    {candidateAudit && <strong role="status" aria-live="polite">{candidateAudit.status === "review" ? "需要人工核对" : "未发现确定性问题"}</strong>}
                  </header>
                  {auditLoading && <p className="planning-generation__status" role="status">正在读取确定性检查结果…</p>}
                  {auditError && <div className="planning-generation__error" role="alert"><span>{auditError}</span><button className="btn btn-secondary" disabled={auditLoading} onClick={onReadGenerationCandidateAudit}>重新读取检查</button></div>}
                  {candidateAudit && (
                    <>
                      <dl className="planning-generation__receipt">
                        <div><dt>内容完整性</dt><dd>{candidateAudit.integrity.status === "pass" ? "校验值、字节数和字词数一致" : "内容到达存储边界，建议人工核对是否截断"}</dd></div>
                        <div><dt>目标字数</dt><dd>{candidateAudit.target_length.status === "not_applicable" ? "本章未设置目标字数" : candidateAudit.target_length.status === "pass" ? `位于 ${candidateAudit.target_length.minimum_word_count}–${candidateAudit.target_length.maximum_word_count} 字词范围` : `当前 ${candidateAudit.target_length.actual_word_count} 字词，不在 ${candidateAudit.target_length.minimum_word_count}–${candidateAudit.target_length.maximum_word_count} 范围`}</dd></div>
                        <div><dt>冻结上下文</dt><dd>{candidateAudit.context_summary.element_count} 项设定 · {candidateAudit.context_summary.relation_count} 条关系</dd></div>
                        <div><dt>伏笔动作</dt><dd>本次未自动确认埋入、强化或回收</dd></div>
                      </dl>
                      <details><summary>查看本次冻结设定</summary><ul>{candidateAudit.context_summary.elements.map((item) => <li key={item.element_id}><strong>{item.name}</strong> · {item.type_display_name} · 内容版本 {item.version_no}</li>)}</ul></details>
                      {candidateAudit.preparation.warnings.length > 0 && <div className="planning-generation__warning"><strong>生成准备阶段已有提醒</strong><ul>{candidateAudit.preparation.warnings.map((item, index) => <li key={`${item.code}-${item.element_id ?? index}`}>{warningText[item.code] ?? item.code}</li>)}</ul></div>}
                      {candidateAudit.unrecognized_explicit_terms.items.length > 0 && <div className="planning-generation__warning"><strong>发现需要核对的《》标记名称</strong><p>以下名称仅因《》标记且未出现在本次冻结清单中而被提示，请作者核对是否需要纳入设定。</p><ul>{candidateAudit.unrecognized_explicit_terms.items.map((item) => <li key={`${item.start_offset}-${item.term}`}><strong>《{item.term}》</strong><blockquote>{item.excerpt}</blockquote></li>)}</ul>{candidateAudit.unrecognized_explicit_terms.truncated && <p>提示已达到 20 条上限，请人工检查其余正文。</p>}</div>}
                    </>
                  )}
                </section>
              </article>
            )}
          </section>}
          <div className="planning-generation__actions">
            <button className="btn btn-primary" disabled={busy || disabled || !!runActionsDisabledReason} onClick={onPrepare}>{busy ? "正在检查…" : stale ? "重新检查当前上下文" : "再次检查当前上下文"}</button>
            <button className="btn btn-secondary" disabled={busy || !!runActionsDisabledReason} onClick={hasPendingRecovery ? onAbandonPending : onClearSavedPointer}>{hasPendingRecovery ? "处理未清除的恢复线索" : "关闭这条记录"}</button>
            {(runActionsDisabledReason || disabledReason) && <p role="status">{runActionsDisabledReason || disabledReason}</p>}
          </div>
        </div>
      )}

      {executionMode === "real" && confirmationOpen && capability && (
        <div className="planning-generation-confirm-overlay" role="presentation">
          <div ref={dialogRef} className="planning-generation-confirm" role="alertdialog" aria-modal="true" aria-labelledby="generation-confirm-title" aria-describedby="generation-confirm-description">
            <h4 id="generation-confirm-title">确认调用模型生成候选</h4>
            <p id="generation-confirm-description">{confirmationUsesOriginalRequest ? "再次确认后才会使用原操作编号和原载荷重试一次模型请求。" : "确认后将发送一次模型请求。"} 请求可能产生费用；网络中断时系统只核对原操作编号，不会自动重复调用。</p>
            <dl className="planning-generation__receipt">
              <div><dt>服务商 / 模型</dt><dd>{providerLabel(capability.provider_name)} / {capability.model_name}</dd></div>
              <div><dt>最大输出</dt><dd>{capability.max_output_tokens.toLocaleString()} tokens</dd></div>
              <div><dt>输入上限</dt><dd>服务商未提供，当前不可核实</dd></div>
              <div><dt>价格</dt><dd>当前不可核实；不能承诺具体金额，最终以服务商账单为准</dd></div>
            </dl>
            <ul>
              <li>结果保存为独立候选，不覆盖任何现有正文。</li>
              <li>不会自动新增或确认伏笔、世界观事实和人物状态。</li>
              <li>结果仍需人工审阅；模型调用可能产生费用。</li>
            </ul>
            <div className="planning-generation__actions">
              <button ref={cancelConfirmationRef} className="btn btn-secondary" disabled={executionBusy} onClick={onCancelGenerationConfirmation}>取消，暂不生成</button>
              <button className="btn btn-primary" disabled={executionBusy} onClick={onConfirmGeneration}>{executionBusy ? "正在提交…" : confirmationUsesOriginalRequest ? "确认并使用原编号重试" : "确认并生成一次候选"}</button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
