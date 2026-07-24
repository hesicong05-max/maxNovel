import { useEffect, useState, useRef } from "react";
import { api } from "@/services/api";
import type { OutlineData, OutlineChapter } from "@/types";

interface Props {
  projectId: string;
  hasOutline: boolean;
  projectStatus: string;
  onComplete: () => void;
  onBack: () => void;
}

export default function OutlineReview({ projectId, hasOutline, projectStatus, onComplete, onBack }: Props) {
  const [outline, setOutline] = useState<OutlineData | null>(null);
  const [loading, setLoading] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [editingChapter, setEditingChapter] = useState<number | null>(null);
  const [streamProgress, setStreamProgress] = useState<{ chunks: number; chars: number } | null>(null);
  const abortControllerRef = useRef<AbortController | null>(null);

  useEffect(() => {
    if (hasOutline) loadOutline();
  }, [hasOutline]);

  // Cleanup: abort any ongoing stream when component unmounts
  useEffect(() => {
    return () => {
      abortControllerRef.current?.abort();
    };
  }, []);

  async function loadOutline() {
    try {
      const data = await api.getOutline(projectId);
      setOutline(data);
    } catch (e) {
      console.error("Failed to load outline:", e);
    }
  }

  async function handleGenerate() {
    setGenerating(true);
    setStreamProgress(null);
    abortControllerRef.current = new AbortController();

    // Set a 5-minute timeout — the SSE connection keeps the proxy alive,
    // but we still want a safety net
    const timeoutId = setTimeout(() => {
      abortControllerRef.current?.abort();
    }, 5 * 60 * 1000);

    try {
      let gotComplete = false;

      for await (const msg of api.generateOutlineStream(projectId, abortControllerRef.current.signal)) {
        switch (msg.type) {
          case "start":
            setStreamProgress({ chunks: 0, chars: 0 });
            break;
          case "progress":
            setStreamProgress({ chunks: msg.chunks || 0, chars: msg.chars || 0 });
            break;
          case "complete":
            if (msg.outline) {
              setOutline(msg.outline);
              gotComplete = true;
            }
            break;
          case "error":
            throw new Error(msg.message || "生成失败");
        }
      }

      if (!gotComplete) {
        throw new Error("大纲生成完成但未返回有效数据，请重试");
      }
    } catch (e) {
      if (e instanceof DOMException && e.name === "AbortError") {
        alert("大纲生成超时（5分钟），请检查网络连接后重试");
      } else {
        alert("生成失败: " + (e as Error).message);
      }
    } finally {
      clearTimeout(timeoutId);
      abortControllerRef.current = null;
      setGenerating(false);
      setStreamProgress(null);
    }
  }

  function handleCancel() {
    abortControllerRef.current?.abort();
  }

  async function handleSave() {
    if (!outline) return;
    setLoading(true);
    try {
      await api.updateOutline(projectId, { story_arc: outline.story_arc, chapters: outline.chapters });
      alert("大纲已保存");
    } catch (e) {
      alert("保存失败: " + (e as Error).message);
    } finally {
      setLoading(false);
    }
  }

  async function handleConfirm() {
    try {
      await api.confirmOutline(projectId);
      onComplete();
    } catch (e) {
      alert("确认失败: " + (e as Error).message);
    }
  }

  function updateChapter(index: number, field: keyof OutlineChapter, value: string | string[]) {
    if (!outline) return;
    const chapters = [...outline.chapters];
    chapters[index] = { ...chapters[index], [field]: value };
    setOutline({ ...outline, chapters });
  }

  const isConfirmed = projectStatus === "outline_confirmed" || projectStatus === "writing";

  return (
    <div>
      {!outline && !generating && (
        <div className="card empty-state">
          <h3>还没有生成大纲</h3>
          <p>系统会根据你的世界观和网文类型，自动生成故事大纲和世界观揭示计划</p>
          <div style={{ marginTop: "1rem" }}>
            <button className="btn btn-primary btn-lg" onClick={handleGenerate}>生成故事大纲</button>
          </div>
        </div>
      )}

      {generating && (
        <div className="card empty-state">
          <h3>正在生成大纲...</h3>
          <p>AI 正在根据世界观和写作范式规划故事弧线和章节安排</p>
          {streamProgress && (
            <div style={{ marginTop: "0.75rem", fontSize: "13px", color: "var(--text-3)" }}>
              <p>已接收 {streamProgress.chars} 字符的数据流...</p>
              <div style={{
                marginTop: "0.5rem",
                height: "4px",
                background: "var(--border)",
                borderRadius: "2px",
                overflow: "hidden",
              }}>
                <div style={{
                  height: "100%",
                  background: "var(--gold)",
                  width: `${Math.min(100, (streamProgress.chars / 2000) * 100)}%`,
                  transition: "width 0.3s ease",
                }} />
              </div>
            </div>
          )}
          <div style={{ marginTop: "0.75rem" }}>
            <button className="btn btn-ghost btn-sm" onClick={handleCancel}>取消生成</button>
          </div>
        </div>
      )}

      {outline && (
        <div>
          {/* Story arc */}
          <div className="card">
            <label className="form-label">故事弧线</label>
            <textarea
              className="form-textarea"
              value={outline.story_arc}
              onChange={(e) => setOutline({ ...outline, story_arc: e.target.value })}
              disabled={isConfirmed}
              style={{ minHeight: "60px" }}
            />
          </div>

          {/* Reveal plan summary */}
          {outline.reveal_plan && outline.reveal_plan.length > 0 && (
            <div className="card">
              <div className="wv-section-title">世界观揭示计划</div>
              <div style={{ fontSize: "12px", maxHeight: "200px", overflowY: "auto" }}>
                {outline.reveal_plan.slice(0, 10).map((entry) => (
                  <div key={entry.chapter} style={{ padding: "0.375rem 0", borderBottom: "1px solid var(--border)", display: "flex", alignItems: "center", gap: "0.5rem" }}>
                    <span className={`tag tag-phase-${entry.phase}`}>
                      {entry.phase === "introduction" ? "引入" : entry.phase === "expansion" ? "展开" : "深入"}
                    </span>
                    <span style={{ color: "var(--text-2)" }}>第{entry.chapter}章: {entry.summary || "（无）"}</span>
                  </div>
                ))}
                {outline.reveal_plan.length > 10 && (
                  <div style={{ padding: "0.375rem 0", color: "var(--text-3)" }}>... 共 {outline.reveal_plan.length} 章</div>
                )}
              </div>
            </div>
          )}

          {/* Chapter outline */}
          <div className="card">
            <div className="wv-section-title">章节大纲 ({outline.chapters.length} 章)</div>
            {outline.chapters.map((ch, i) => (
              <div key={i} style={{ marginBottom: "0.625rem", padding: "0.625rem 0.75rem", background: "var(--bg)", borderRadius: "var(--r-md)" }}>
                <div style={{ display: "flex", gap: "0.5rem", marginBottom: "0.375rem", alignItems: "center" }}>
                  <span style={{ fontWeight: 700, fontSize: "13px", minWidth: "55px", color: "var(--gold-dark)" }}>第{ch.chapter_num}章</span>
                  <input
                    className="form-input"
                    placeholder="章节标题"
                    value={ch.title}
                    onChange={(e) => updateChapter(i, "title", e.target.value)}
                    disabled={isConfirmed || editingChapter !== i}
                    style={{ fontWeight: 500 }}
                  />
                  {!isConfirmed && (
                    <button className="btn btn-ghost btn-sm" onClick={() => setEditingChapter(editingChapter === i ? null : i)}>
                      {editingChapter === i ? "完成" : "编辑"}
                    </button>
                  )}
                </div>
                {editingChapter === i && !isConfirmed ? (
                  <textarea
                    className="form-textarea"
                    placeholder="章节概述"
                    value={ch.summary}
                    onChange={(e) => updateChapter(i, "summary", e.target.value)}
                    style={{ minHeight: "50px", fontSize: "13px" }}
                  />
                ) : (
                  <p style={{ fontSize: "13px", color: "var(--text-2)" }}>{ch.summary || "（无概述）"}</p>
                )}
                {ch.reveal_elements && ch.reveal_elements.length > 0 && (
                  <div style={{ marginTop: "0.375rem", display: "flex", gap: "0.25rem", flexWrap: "wrap" }}>
                    {ch.reveal_elements.map((el, j) => (
                      <span key={j} className="tag tag-gold">{el}</span>
                    ))}
                  </div>
                )}
                {ch.key_events && ch.key_events.length > 0 && (
                  <div style={{ marginTop: "0.25rem", fontSize: "11px", color: "var(--text-3)" }}>
                    关键事件: {ch.key_events.join(" / ")}
                  </div>
                )}
              </div>
            ))}
          </div>

          {/* Actions */}
          <div style={{ display: "flex", gap: "0.5rem" }}>
            <button className="btn" onClick={onBack}>← 返回世界观</button>
            {!isConfirmed ? (
              <>
                <button className="btn btn-primary" onClick={handleSave} disabled={loading}>
                  {loading ? "保存中..." : "保存修改"}
                </button>
                <button className="btn btn-danger btn-lg" onClick={handleConfirm}>
                  确认大纲，开始写作 →
                </button>
              </>
            ) : (
              <button className="btn btn-danger btn-lg" onClick={onComplete}>
                进入章节生成 →
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
