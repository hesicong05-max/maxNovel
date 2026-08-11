import { useEffect, useRef, useState } from "react";
import { api } from "@/services/api";
import type {
  BatchStreamMessage,
  ChapterListItem,
  StreamMessage,
  WordCountConfig,
} from "@/types";

interface Props {
  projectId: string;
  totalChapters: number;
  onProgress: () => void;
  onBack: () => void;
}

// Word count validation constants
const MIN_WC = 500;
const MAX_WC = 10000;

type BatchStatus = "idle" | "running" | "done" | "error";

export default function ChapterWriter({ projectId, totalChapters, onProgress, onBack }: Props) {
  // === Chapter list state ===
  const [chapters, setChapters] = useState<ChapterListItem[]>([]);
  const [currentChapter, setCurrentChapter] = useState<number | null>(null);

  // === Single chapter generation state ===
  const [streamContent, setStreamContent] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [meta, setMeta] = useState<StreamMessage | null>(null);

  // === Editing state ===
  const [editing, setEditing] = useState(false);
  const [editTitle, setEditTitle] = useState("");
  const [editContent, setEditContent] = useState("");
  const contentRef = useRef<HTMLDivElement>(null);

  // === Word count config state ===
  const [wcConfig, setWcConfig] = useState<WordCountConfig | null>(null);
  const [wcTotal, setWcTotal] = useState<string>("");
  const [wcOverrides, setWcOverrides] = useState<Record<number, string>>({});
  const [wcSaving, setWcSaving] = useState(false);
  const [wcError, setWcError] = useState("");

  // === Batch generation state ===
  const [batchStatus, setBatchStatus] = useState<BatchStatus>("idle");
  const [batchProgress, setBatchProgress] = useState({ current: 0, total: 0 });
  const [batchCurrentCh, setBatchCurrentCh] = useState<number | null>(null);
  const [batchContent, setBatchContent] = useState("");
  const [batchLog, setBatchLog] = useState<{ chapter: number; status: "ok" | "fail"; wordCount: number }[]>([]);
  const [batchSkipExisting, setBatchSkipExisting] = useState(true);
  const batchContentRef = useRef<HTMLDivElement>(null);

  // === Show/hide settings panel ===
  const [showSettings, setShowSettings] = useState(false);

  // === AbortController for SSE streams ===
  const abortRef = useRef<AbortController | null>(null);
  const chapterSelectionRequestRef = useRef(0);

  // Cancel any active SSE stream on unmount
  useEffect(() => {
    return () => {
      if (abortRef.current) {
        abortRef.current.abort();
        abortRef.current = null;
      }
    };
  }, []);

  useEffect(() => {
    chapterSelectionRequestRef.current += 1;
    loadChapters();
    loadWordCounts();
  }, [projectId, totalChapters]);

  useEffect(() => {
    if (contentRef.current) contentRef.current.scrollTop = contentRef.current.scrollHeight;
  }, [streamContent]);

  useEffect(() => {
    if (batchContentRef.current) batchContentRef.current.scrollTop = batchContentRef.current.scrollHeight;
  }, [batchContent]);

  // === Load functions ===

  async function loadChapters() {
    try {
      const data = await api.listChapters(projectId);
      setChapters(data);
      if (data.length === 0) {
        setCurrentChapter(1);
      } else {
        const maxNum = Math.max(...data.map((c) => c.chapter_num));
        setCurrentChapter(maxNum < totalChapters ? maxNum + 1 : maxNum);
      }
    } catch (e) {
      console.error("Failed to load chapters:", e);
    }
  }

  async function loadWordCounts() {
    try {
      const config = await api.getWordCounts(projectId);
      setWcConfig(config);
      setWcTotal(config.total_word_count ? String(config.total_word_count) : "");
      const overrides: Record<number, string> = {};
      for (const ch of config.chapters) {
        if (ch.target_word_count) {
          overrides[ch.chapter_num] = String(ch.target_word_count);
        }
      }
      setWcOverrides(overrides);
    } catch (e) {
      console.error("Failed to load word counts:", e);
    }
  }

  // === Single chapter generation ===

  async function handleGenerate() {
    if (!currentChapter || streaming || batchStatus === "running") return;
    setStreaming(true);
    setStreamContent("");
    setMeta(null);
    // Create a new AbortController for this stream
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      let fullContent = "";
      let receivedTerminalEvent = false;
      for await (const msg of api.streamChapter(projectId, currentChapter, controller.signal)) {
        if (msg.type === "metadata") { setMeta(msg); }
        else if (msg.type === "content" && msg.text) { fullContent += msg.text; setStreamContent(fullContent); }
        else if (msg.type === "complete") {
          receivedTerminalEvent = true;
          await loadChapters();
          onProgress();
        }
        else if (msg.type === "error") {
          receivedTerminalEvent = true;
          alert("生成失败: " + msg.error);
          await loadChapters();
          break;
        }
      }
      if (!receivedTerminalEvent && !controller.signal.aborted) {
        alert("生成连接意外中断，未确认章节是否完成，请重试");
        await loadChapters();
      }
    } catch (e) {
      if (controller.signal.aborted) return; // Ignore abort errors
      alert("生成失败: " + (e as Error).message);
    } finally {
      setStreaming(false);
      abortRef.current = null;
    }
  }

  async function handleSelectChapter(chNum: number) {
    if (batchStatus === "running") return;
    const requestId = ++chapterSelectionRequestRef.current;
    setCurrentChapter(chNum);
    setStreamContent("");
    setMeta(null);
    setEditing(false);
    try {
      const ch = await api.getChapter(projectId, chNum);
      if (requestId !== chapterSelectionRequestRef.current) return;
      if (ch.content) { setStreamContent(ch.content); setEditTitle(ch.title); setEditContent(ch.content); }
    } catch { /* chapter doesn't exist yet */ }
  }

  async function handleSaveEdit() {
    if (!currentChapter) return;
    try {
      await api.updateChapter(projectId, currentChapter, { title: editTitle, content: editContent });
      setStreamContent(editContent);
      setEditing(false);
      await loadChapters();
      onProgress();
    } catch (e) {
      alert("保存失败: " + (e as Error).message);
    }
  }

  // === Word count config ===

  function handleApplyTotal() {
    const total = parseInt(wcTotal);
    if (!total || total < MIN_WC * totalChapters) {
      setWcError(`总字数不能少于 ${MIN_WC * totalChapters}（每章最少 ${MIN_WC} 字 × ${totalChapters} 章）`);
      return;
    }
    if (total > MAX_WC * totalChapters) {
      setWcError(`总字数不能超过 ${MAX_WC * totalChapters}（每章最多 ${MAX_WC} 字 × ${totalChapters} 章）`);
      return;
    }
    setWcError("");
    // Auto-distribute: fill each chapter with total / totalChapters
    const perCh = Math.floor(total / totalChapters);
    const remainder = total - perCh * totalChapters;
    const newOverrides: Record<number, string> = {};
    for (let i = 1; i <= totalChapters; i++) {
      newOverrides[i] = String(perCh + (i <= remainder ? 1 : 0));
    }
    setWcOverrides(newOverrides);
  }

  function handleOverrideChange(chNum: number, value: string) {
    setWcOverrides({ ...wcOverrides, [chNum]: value });
  }

  function validateOverride(value: string): string | null {
    const num = parseInt(value);
    if (!num) return null;
    if (num < MIN_WC) return `不能少于 ${MIN_WC} 字`;
    if (num > MAX_WC) return `不能超过 ${MAX_WC} 字`;
    return null;
  }

  async function handleSaveWordCounts() {
    setWcSaving(true);
    setWcError("");
    try {
      const chapters = [];
      for (let i = 1; i <= totalChapters; i++) {
        const val = wcOverrides[i];
        const num = val ? parseInt(val) : NaN;
        chapters.push({
          chapter_num: i,
          target_word_count: !isNaN(num) && num > 0 ? num : null,
        });
      }
      const totalNum = wcTotal ? parseInt(wcTotal) : NaN;
      await api.saveWordCounts(projectId, {
        total_word_count: !isNaN(totalNum) && totalNum > 0 ? totalNum : null,
        chapters,
      });
      await loadWordCounts();
      alert("字数设置已保存");
    } catch (e) {
      const err = e as Error;
      try {
        const detail = JSON.parse(err.message).detail || err.message;
        setWcError(detail);
      } catch {
        setWcError(err.message);
      }
    } finally {
      setWcSaving(false);
    }
  }

  // === Batch generation ===

  async function handleBatchGenerate() {
    if (batchStatus === "running" || streaming) return;
    setBatchStatus("running");
    setBatchContent("");
    setBatchLog([]);
    setBatchProgress({ current: 0, total: 0 });
    setBatchCurrentCh(null);

    // Create a new AbortController for this batch stream
    const controller = new AbortController();
    abortRef.current = controller;

    try {
      let currentChContent = "";
      let receivedBatchComplete = false;
      for await (const msg of api.streamBatchGenerate(projectId, batchSkipExisting, controller.signal)) {
        if (msg.type === "batch_start") {
          setBatchProgress({ current: 0, total: msg.total_to_generate || 0 });
        }
        else if (msg.type === "batch_progress") {
          setBatchProgress({ current: msg.current || 0, total: msg.total || 0 });
          setBatchCurrentCh(msg.chapter_num || null);
          currentChContent = "";
        }
        else if (msg.type === "metadata") {
          if (msg.chapter_num) setBatchCurrentCh(msg.chapter_num);
        }
        else if (msg.type === "content" && msg.text) {
          currentChContent += msg.text;
          setBatchContent(currentChContent);
        }
        else if (msg.type === "complete") {
          setBatchLog((prev) => [...prev, {
            chapter: msg.chapter_num || 0,
            status: "ok",
            wordCount: msg.word_count || 0,
          }]);
          currentChContent = "";
        }
        else if (msg.type === "error") {
          setBatchLog((prev) => [...prev, {
            chapter: msg.chapter_num || batchCurrentCh || 0,
            status: "fail",
            wordCount: 0,
          }]);
          currentChContent = "";
        }
        else if (msg.type === "batch_complete") {
          receivedBatchComplete = true;
          setBatchStatus("done");
        }
      }
      if (!receivedBatchComplete && !controller.signal.aborted) {
        setBatchStatus("error");
        alert("批量生成连接意外中断，请检查已保存章节后重试");
      }
      await loadChapters();
      onProgress();
    } catch (e) {
      if (controller.signal.aborted) { setBatchStatus("idle"); return; }
      setBatchStatus("error");
      alert("批量生成失败: " + (e as Error).message);
    } finally {
      abortRef.current = null;
    }
  }

  // === Derived values ===

  const generatedChapters = chapters.filter((c) => c.status !== "pending").length;
  const totalWords = chapters.reduce((sum, c) => sum + c.word_count, 0);
  const isBusy = streaming || batchStatus === "running";

  return (
    <div>
      <button className="btn-back" onClick={onBack}>← 返回世界观与设定</button>

      {/* === Word Count Settings Panel === */}
      <div className="card" style={{ marginBottom: "0.875rem" }}>
        <div
          style={{ display: "flex", justifyContent: "space-between", alignItems: "center", cursor: "pointer" }}
          onClick={() => setShowSettings(!showSettings)}
        >
          <span style={{ fontWeight: 700, fontSize: "14px", color: "var(--gold-dark)" }}>
            字数设置 {wcConfig && wcConfig.total_word_count ? `(总目标: ${wcConfig.total_word_count}字)` : ""}
          </span>
          <span style={{ fontSize: "12px", color: "var(--text-3)" }}>{showSettings ? "收起 ▲" : "展开 ▼"}</span>
        </div>

        {showSettings && (
          <div style={{ marginTop: "0.875rem" }}>
            {/* Total word count */}
            <div style={{ display: "flex", gap: "0.5rem", alignItems: "flex-end", marginBottom: "0.75rem" }}>
              <div style={{ flex: "0 0 200px" }}>
                <label className="form-label" style={{ fontSize: "12px" }}>全部章节总字数</label>
                <input
                  className="form-input"
                  type="number"
                  placeholder="如 60000"
                  value={wcTotal}
                  onChange={(e) => setWcTotal(e.target.value)}
                  disabled={isBusy}
                />
              </div>
              <button className="btn" onClick={handleApplyTotal} disabled={isBusy} style={{ marginBottom: "0" }}>
                自动分配
              </button>
              <span style={{ fontSize: "11px", color: "var(--text-3)", paddingBottom: "0.5rem" }}>
                设置总字数后自动分配到各章
              </span>
            </div>

            {/* Per-chapter word counts */}
            <div style={{ maxHeight: "240px", overflowY: "auto", borderTop: "1px solid var(--border)", paddingTop: "0.5rem" }}>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(140px, 1fr))", gap: "0.375rem" }}>
                {Array.from({ length: totalChapters }, (_, i) => i + 1).map((num) => {
                  const val = wcOverrides[num] || "";
                  const err = val ? validateOverride(val) : null;
                  const effective = wcConfig?.chapters.find((c) => c.chapter_num === num)?.effective_word_count;
                  return (
                    <div key={num} style={{ display: "flex", flexDirection: "column", gap: "2px" }}>
                      <label style={{ fontSize: "10px", color: "var(--text-3)" }}>第{num}章</label>
                      <input
                        className="form-input"
                        type="number"
                        placeholder={effective ? String(effective) : "默认"}
                        value={val}
                        onChange={(e) => handleOverrideChange(num, e.target.value)}
                        disabled={isBusy}
                        style={{
                          fontSize: "12px",
                          padding: "0.25rem 0.5rem",
                          borderColor: err ? "var(--red)" : "var(--border)",
                        }}
                      />
                      {err && <span style={{ fontSize: "9px", color: "var(--red)" }}>{err}</span>}
                    </div>
                  );
                })}
              </div>
            </div>

            {wcError && (
              <div style={{ marginTop: "0.5rem", padding: "0.375rem 0.625rem", background: "var(--red-light)", borderRadius: "var(--r-md)", fontSize: "12px", color: "var(--red)" }}>
                {wcError}
              </div>
            )}

            <div style={{ display: "flex", gap: "0.5rem", marginTop: "0.625rem" }}>
              <button className="btn btn-primary" onClick={handleSaveWordCounts} disabled={isBusy || wcSaving}>
                {wcSaving ? "保存中..." : "保存字数设置"}
              </button>
            </div>
          </div>
        )}
      </div>

      {/* === Batch Generation Panel === */}
      <div className="card" style={{ marginBottom: "0.875rem", padding: "0.75rem 0.875rem" }}>
        <div style={{ display: "flex", gap: "0.625rem", alignItems: "center", flexWrap: "wrap" }}>
          <button
            className="btn btn-primary btn-lg"
            onClick={handleBatchGenerate}
            disabled={isBusy}
            style={{ background: batchStatus === "running" ? "var(--text-3)" : undefined }}
          >
            {batchStatus === "running" ? "批量生成中..." : "一键生成所有章节"}
          </button>
          <label style={{ display: "flex", alignItems: "center", gap: "0.25rem", fontSize: "12px", color: "var(--text-2)", cursor: "pointer" }}>
            <input
              type="checkbox"
              checked={batchSkipExisting}
              onChange={(e) => setBatchSkipExisting(e.target.checked)}
              disabled={isBusy}
            />
            跳过已生成章节
          </label>
          <span style={{ fontSize: "12px", color: "var(--text-3)" }}>
            已生成: {generatedChapters}/{totalChapters} 章 · 共 {totalWords} 字
          </span>
        </div>

        {/* Batch progress bar */}
        {batchStatus === "running" && (
          <div style={{ marginTop: "0.625rem" }}>
            <div style={{ display: "flex", justifyContent: "space-between", fontSize: "12px", marginBottom: "0.25rem" }}>
              <span style={{ color: "var(--gold-dark)", fontWeight: 600 }}>
                正在生成第 {batchCurrentCh} 章...
              </span>
              <span style={{ color: "var(--text-3)" }}>
                {batchProgress.current} / {batchProgress.total}
              </span>
            </div>
            <div className="progress-bar">
              <div
                className="progress-fill progress-fill-gold"
                style={{ width: `${batchProgress.total > 0 ? (batchProgress.current / batchProgress.total) * 100 : 0}%` }}
              />
            </div>
            {/* Live content preview */}
            {batchContent && (
              <div
                ref={batchContentRef}
                className="stream-content"
                style={{ maxHeight: "200px", overflowY: "auto", marginTop: "0.5rem", fontSize: "13px", lineHeight: "1.8", opacity: 0.85 }}
              >
                {batchContent}
                <span className="cursor-blink" />
              </div>
            )}
          </div>
        )}

        {/* Batch result log */}
        {batchLog.length > 0 && batchStatus !== "running" && (
          <div style={{ marginTop: "0.625rem", maxHeight: "200px", overflowY: "auto" }}>
            {batchLog.map((log, i) => (
              <div key={i} style={{ display: "flex", alignItems: "center", gap: "0.5rem", padding: "0.25rem 0", borderBottom: "1px solid var(--border)", fontSize: "12px" }}>
                <span style={{ fontWeight: 600, color: log.status === "ok" ? "var(--gold-dark)" : "var(--red)", minWidth: "60px" }}>
                  第{log.chapter}章
                </span>
                <span style={{ color: log.status === "ok" ? "var(--text-2)" : "var(--red)" }}>
                  {log.status === "ok" ? `${log.wordCount} 字` : "生成失败"}
                </span>
              </div>
            ))}
          </div>
        )}

        {batchStatus === "done" && (
          <div style={{ marginTop: "0.5rem", padding: "0.375rem 0.625rem", background: "var(--gold-light)", borderRadius: "var(--r-md)", fontSize: "12px", color: "var(--gold-dark)" }}>
            批量生成完成 — 成功 {batchLog.filter((l) => l.status === "ok").length} 章，失败 {batchLog.filter((l) => l.status === "fail").length} 章
          </div>
        )}
      </div>

      {/* === Main layout: chapter list + content === */}
      <div style={{ display: "grid", gridTemplateColumns: "200px 1fr", gap: "0.875rem" }}>
        {/* Chapter list */}
        <div className="card" style={{ padding: "0.625rem" }}>
          <div style={{ fontWeight: 700, fontSize: "13px", marginBottom: "0.625rem", color: "var(--gold-dark)", display: "flex", justifyContent: "space-between" }}>
            <span>章节</span>
            <span style={{ color: "var(--text-3)" }}>{generatedChapters}/{totalChapters}</span>
          </div>
          <ul className="chapter-list">
            {Array.from({ length: totalChapters }, (_, i) => i + 1).map((num) => {
              const ch = chapters.find((c) => c.chapter_num === num);
              const isGenerated = ch && ch.status !== "pending";
              const isCurrent = currentChapter === num;
              return (
                <li key={num} onClick={() => handleSelectChapter(num)} style={{ background: isCurrent ? "var(--gold-light)" : undefined }}>
                  <div className={`chapter-num ${isGenerated ? "completed" : ""} ${isCurrent ? "current" : ""}`}>{num}</div>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontSize: "12px", fontWeight: isCurrent ? 600 : 400, color: isCurrent ? "var(--gold-dark)" : "var(--text-2)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {ch?.title || `第${num}章`}
                    </div>
                    {ch && (
                      <div style={{ fontSize: "10px", color: "var(--text-3)" }}>
                        {ch.word_count > 0 ? `${ch.word_count}字` : "未生成"}
                      </div>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        </div>

        {/* Chapter content area */}
        <div>
          {/* Metadata bar */}
          {meta && (
            <div className="card" style={{ padding: "0.625rem 0.875rem", marginBottom: "0.5rem", display: "flex", gap: "0.625rem", alignItems: "center", flexWrap: "wrap", fontSize: "12px" }}>
              <span style={{ fontWeight: 700, color: "var(--gold-dark)" }}>{meta.title || `第${currentChapter}章`}</span>
              {meta.phase_label && <span className={`tag tag-phase-${meta.phase}`}>{meta.phase_label}</span>}
              {meta.target_word_count && <span className="tag tag-gray">目标 {meta.target_word_count} 字</span>}
              {meta.elements_to_reveal && meta.elements_to_reveal.length > 0 && (
                <div style={{ display: "flex", gap: "0.25rem", flexWrap: "wrap", alignItems: "center" }}>
                  <span style={{ color: "var(--text-3)" }}>本章揭示:</span>
                  {meta.elements_to_reveal.map((el, i) => (
                    <span key={i} className="tag tag-gold">{el}</span>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* Action bar */}
          <div style={{ display: "flex", gap: "0.375rem", marginBottom: "0.5rem" }}>
            {streaming ? (
              <button className="btn" disabled>生成中... ({streamContent.length}字)</button>
            ) : (
              <>
                <button className="btn btn-primary" onClick={handleGenerate} disabled={!currentChapter || isBusy}>
                  {chapters.find((c) => c.chapter_num === currentChapter)?.status === "generated" || chapters.find((c) => c.chapter_num === currentChapter)?.status === "edited"
                    ? `重新生成第${currentChapter}章`
                    : `生成第${currentChapter}章`}
                </button>
                {streamContent && !editing && (
                  <button className="btn" onClick={() => { setEditing(true); setEditTitle(meta?.title || `第${currentChapter}章`); setEditContent(streamContent); }}>
                    编辑
                  </button>
                )}
                {streamContent && (
                  <button className="btn" onClick={async () => {
                    try {
                      const blob = await api.exportNovel(projectId, "txt");
                      const url = URL.createObjectURL(blob);
                      const a = document.createElement("a");
                      a.href = url;
                      a.download = `${projectId}.txt`;
                      a.click();
                      URL.revokeObjectURL(url);
                    } catch (e) {
                      alert(`导出失败: ${(e as Error).message}`);
                    }
                  }}>
                    导出全文
                  </button>
                )}
              </>
            )}
          </div>

          {/* Content display */}
          {editing ? (
            <div>
              <input className="form-input" style={{ marginBottom: "0.5rem", fontWeight: 600 }} value={editTitle} onChange={(e) => setEditTitle(e.target.value)} />
              <textarea
                className="form-textarea"
                style={{ minHeight: "500px", fontFamily: "var(--font-serif)", fontSize: "16px", lineHeight: "2.1" }}
                value={editContent}
                onChange={(e) => setEditContent(e.target.value)}
              />
              <div style={{ display: "flex", gap: "0.375rem", marginTop: "0.5rem" }}>
                <button className="btn btn-primary" onClick={handleSaveEdit}>保存修改</button>
                <button className="btn" onClick={() => setEditing(false)}>取消</button>
              </div>
            </div>
          ) : (
            <div ref={contentRef} className="stream-content" style={{ maxHeight: "600px", overflowY: "auto" }}>
              {streamContent || (
                <span style={{ color: "var(--text-3)" }}>
                  {isBusy ? "批量生成进行中..." : `点击「生成第${currentChapter}章」开始写作`}
                </span>
              )}
              {streaming && <span className="cursor-blink" />}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
