import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "@/services/api";
import type { NovelGenre, StyleIntensity } from "@/types";

const GENRES: NovelGenre[] = ["玄幻", "都市", "科幻", "武侠", "仙侠", "悬疑", "言情"];

const GENRE_DESCRIPTIONS: Record<string, string> = {
  玄幻: "修真 · 异界 · 传承",
  都市: "异能 · 系统 · 重生",
  科幻: "星际 · 末世 · 赛博",
  武侠: "江湖 · 门派 · 侠义",
  仙侠: "修仙 · 渡劫 · 飞升",
  悬疑: "推理 · 冒险 · 惊悚",
  言情: "都市 · 古言 · 甜宠",
};

export default function NewProject() {
  const navigate = useNavigate();
  const [title, setTitle] = useState("");
  const [genre, setGenre] = useState<NovelGenre>("玄幻");
  const [totalChapters, setTotalChapters] = useState(30);
  const [chapterWordCount, setChapterWordCount] = useState(3000);
  const [styleIntensity, setStyleIntensity] = useState<StyleIntensity>("standard");
  const [loading, setLoading] = useState(false);

  async function handleSubmit() {
    if (!title.trim()) {
      alert("请输入项目标题");
      return;
    }
    setLoading(true);
    try {
      const project = await api.createProject({
        title: title.trim(),
        genre,
        total_chapters: totalChapters,
        chapter_word_count: chapterWordCount,
        style_intensity: styleIntensity,
      });
      navigate(`/project/${project.id}`);
    } catch (e) {
      alert("创建失败: " + (e as Error).message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div style={{ maxWidth: "640px" }}>
      <button className="btn-back" onClick={() => navigate("/")}>← 返回项目列表</button>

      <div className="page-header">
        <h1>新建项目</h1>
        <p>创建一个新的小说续写项目</p>
      </div>

      <div className="card">
        <div className="form-group">
          <label className="form-label">项目标题</label>
          <input
            className="form-input"
            type="text"
            placeholder="给你的小说起个名字..."
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            maxLength={100}
          />
        </div>

        <div className="form-group">
          <label className="form-label">网文类型</label>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(2, 1fr)", gap: "0.5rem" }}>
            {GENRES.map((g) => (
              <div
                key={g}
                onClick={() => setGenre(g)}
                style={{
                  padding: "0.625rem 0.75rem",
                  border: genre === g ? "2px solid var(--gold)" : "1px solid var(--border)",
                  borderRadius: "var(--r-md)",
                  cursor: "pointer",
                  background: genre === g ? "var(--gold-light)" : "var(--white)",
                  transition: "all var(--ease)",
                }}
              >
                <div style={{ fontWeight: 600, fontSize: "14px", color: genre === g ? "var(--gold-dark)" : "var(--text-1)" }}>{g}</div>
                <div style={{ fontSize: "11px", color: "var(--text-3)", marginTop: "2px" }}>{GENRE_DESCRIPTIONS[g]}</div>
              </div>
            ))}
          </div>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "1rem" }}>
          <div className="form-group">
            <label className="form-label">总章节数</label>
            <select className="form-select" value={totalChapters} onChange={(e) => setTotalChapters(Number(e.target.value))}>
              <option value={10}>10 章 · 短篇</option>
              <option value={30}>30 章 · 中篇</option>
              <option value={50}>50 章 · 长篇</option>
              <option value={100}>100 章 · 超长篇</option>
            </select>
          </div>
          <div className="form-group">
            <label className="form-label">单章字数</label>
            <select className="form-select" value={chapterWordCount} onChange={(e) => setChapterWordCount(Number(e.target.value))}>
              <option value={2000}>2000 字 · 精简</option>
              <option value={3000}>3000 字 · 标准</option>
              <option value={5000}>5000 字 · 丰富</option>
            </select>
          </div>
        </div>

        <div className="form-group">
          <label className="form-label">爽点强度</label>
          <select className="form-select" value={styleIntensity} onChange={(e) => setStyleIntensity(e.target.value as StyleIntensity)}>
            <option value="mild">温和 — 节奏舒缓，重氛围</option>
            <option value="standard">标准 — 爽点与过渡交替</option>
            <option value="intense">密集 — 高频高潮，节奏紧凑</option>
          </select>
        </div>

        <div style={{ display: "flex", gap: "0.5rem", marginTop: "1.25rem" }}>
          <button className="btn btn-primary btn-lg" onClick={handleSubmit} disabled={loading}>
            {loading ? "创建中..." : "创建项目"}
          </button>
          <button className="btn btn-lg" onClick={() => navigate("/")}>取消</button>
        </div>
      </div>
    </div>
  );
}
