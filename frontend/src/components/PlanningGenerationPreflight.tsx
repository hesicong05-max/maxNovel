import { useEffect, useMemo, useRef } from "react";
import type { GenerationRunResponse } from "@/types/generation";
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
  onPrepare: () => void;
  onCheckPending: () => void;
  onRetryOriginal: () => void;
  onFocusAssignments: () => void;
  onClearSavedPointer: () => void;
  onAbandonPending: () => void;
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
  onPrepare,
  onCheckPending,
  onRetryOriginal,
  onFocusAssignments,
  onClearSavedPointer,
  onAbandonPending,
}: Props) {
  const resultHeadingRef = useRef<HTMLHeadingElement | null>(null);
  const feedbackRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (focusResultToken > 0) resultHeadingRef.current?.focus();
  }, [focusResultToken]);
  useEffect(() => {
    if (focusFeedbackToken > 0) feedbackRef.current?.focus();
  }, [focusFeedbackToken]);

  const elementNames = useMemo(() => new Map(
    run?.context_manifest.elements.map((item) => [item.element_id, item.version.name]) ?? []
  ), [run]);

  return (
    <section className="planning-generation" aria-busy={busy || loadingSaved}>
      <header className="planning-generation__heading">
        <div>
          <h3>生成前上下文检查</h3>
          <p>这里只检查并保存本章将使用的上下文。本操作不调用 AI、不产生模型费用，也不会创建或修改章节正文。</p>
        </div>
        <span className="planning-generation__zero-cost">零 AI · 零费用</span>
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

          <div className="planning-generation__guarantees" aria-label="本次检查边界">
            <span>AI 未调用</span><span>模型费用：无</span><span>正文：未创建或修改</span><span>状态：仅检查</span>
          </div>
          {stale && <div className="planning-generation__warning" role="status">当前规划、章节或设定分配已经变化；此处保留历史快照供核对，请按最新资料重新检查。</div>}

          <dl className="planning-generation__receipt">
            <div><dt>记录编号</dt><dd>{run.id}</dd></div>
            <div><dt>记录时间</dt><dd>{new Date(run.created_at).toLocaleString()}</dd></div>
            <div><dt>上下文校验值</dt><dd>{run.context_checksum}</dd></div>
            <div><dt>上下文大小</dt><dd>{run.context_size_bytes.toLocaleString()} / 65,536 字节</dd></div>
            <div><dt>结构 / 分配 / 章节版本</dt><dd>{run.structure_version} / {run.assignment_version} / {run.chapter_lock_version}</dd></div>
          </dl>

          <div className="planning-generation__counts">
            <div><strong>{run.context_manifest.counts.elements}</strong><span>设定（上限 100）</span></div>
            <div><strong>{run.context_manifest.counts.relations}</strong><span>关系（上限 300）</span></div>
            <div><strong>{run.context_manifest.counts.warnings}</strong><span>提醒</span></div>
          </div>

          {run.context_manifest.warnings.length > 0 && (
            <section className="planning-generation__warnings" aria-labelledby={`generation-warning-${run.id}`}>
              <h5 id={`generation-warning-${run.id}`}>检查提醒</h5>
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
                  <details><summary>查看完整内容快照与字段状态</summary><h6>内容快照</h6><JsonSnapshot value={item.version.payload} /><h6>字段状态</h6><JsonSnapshot value={item.version.field_states} />{item.version.source_id && <p>原始出处编号：{item.version.source_id}</p>}</details>
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
          <div className="planning-generation__actions">
            <button className="btn btn-primary" disabled={busy || disabled} onClick={onPrepare}>{busy ? "正在检查…" : stale ? "重新检查当前上下文" : "再次检查当前上下文"}</button>
            <button className="btn btn-secondary" disabled={busy} onClick={hasPendingRecovery ? onAbandonPending : onClearSavedPointer}>{hasPendingRecovery ? "处理未清除的恢复线索" : "关闭这条记录"}</button>
            {disabledReason && <p role="status">{disabledReason}</p>}
          </div>
        </div>
      )}
    </section>
  );
}
