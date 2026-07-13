import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api } from "@/services/api";
import type { CommunityNovelDetail, CommunityNovelCreate, Project } from "@/types";

const GENRES = ["玄幻", "都市", "科幻", "武侠", "仙侠", "悬疑", "言情"];

export default function CommunityEdit() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const isEdit = Boolean(id);

  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(isEdit);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  // Form state
  const [title, setTitle] = useState("");
  const [authorName, setAuthorName] = useState("匿名作者");
  const [genre, setGenre] = useState("玄幻");
  const [projectId, setProjectId] = useState<string | null>(null);
  const [synopsis, setSynopsis] = useState("");
  const [storyOutline, setStoryOutline] = useState("");
  const [chapterNotes, setChapterNotes] = useState("");
  const [allowCocreation, setAllowCocreation] = useState(false);
  const [tags, setTags] = useState<string[]>([]);
  const [tagInput, setTagInput] = useState("");
  const [totalChapters, setTotalChapters] = useState(0);
  const [totalWords, setTotalWords] = useState(0);

  // ── Load existing novel data (edit mode) ──
  useEffect(() => {
    if (!id) return;
    api
      .getCommunityNovel(id)
      .then((data: CommunityNovelDetail) => {
        setTitle(data.title);
        setAuthorName(data.author_name);
        setGenre(data.genre);
        setProjectId(data.project_id);
        setSynopsis(data.synopsis);
        setStoryOutline(data.story_outline);
        setChapterNotes(data.chapter_notes);
        setAllowCocreation(data.allow_cocreation);
        setTags(data.tags);
        setTotalChapters(data.total_chapters);
        setTotalWords(data.total_words);
      })
      .catch((e) => setError("加载失败: " + e.message))
      .finally(() => setLoading(false));
  }, [id]);

  // ── Load projects (for new upload mode) ──
  useEffect(() => {
    if (isEdit) return;
    api
      .listProjects()
      .then(setProjects)
      .catch(() => {});
  }, [isEdit]);

  // ── Auto-fill from project stats ──
  function handleProjectSelect(pid: string) {
    if (!pid) {
      setProjectId(null);
      return;
    }
    setProjectId(pid);
    api
      .getProjectStats(pid)
      .then((stats) => {
        if (!title) setTitle(stats.title);
        setGenre(stats.genre);
        setTotalChapters(stats.chapter_count);
        setTotalWords(stats.total_words);
      })
      .catch(console.error);
  }

  // ── Tag management ──
  function handleAddTag() {
    const trimmed = tagInput.trim();
    if (!trimmed) return;
    if (tags.includes(trimmed)) {
      setTagInput("");
      return;
    }
    if (tags.length >= 10) {
      setError("最多 10 个标签");
      return;
    }
    if (trimmed.length > 20) {
      setError("单个标签最多 20 个字符");
      return;
    }
    setTags([...tags, trimmed]);
    setTagInput("");
    setError("");
  }

  function handleRemoveTag(tag: string) {
    setTags(tags.filter((t) => t !== tag));
  }

  function handleTagKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Enter" || e.key === ",") {
      e.preventDefault();
      handleAddTag();
    } else if (e.key === "Backspace" && !tagInput && tags.length > 0) {
      handleRemoveTag(tags[tags.length - 1]);
    }
  }

  // ── Save ──
  async function handleSave() {
    setError("");

    if (!title.trim()) {
      setError("请填写小说标题");
      return;
    }
    if (title.length > 200) {
      setError("标题不能超过 200 个字符");
      return;
    }
    if (authorName.length > 100) {
      setError("作者名不能超过 100 个字符");
      return;
    }
    if (synopsis.length > 5000) {
      setError("小说简介不能超过 5000 个字符");
      return;
    }
    if (storyOutline.length > 10000) {
      setError("故事梗概不能超过 10000 个字符");
      return;
    }
    if (chapterNotes.length > 10000) {
      setError("章节说明不能超过 10000 个字符");
      return;
    }

    setSaving(true);
    try {
      if (isEdit && id) {
        await api.updateCommunityNovel(id, {
          title: title.trim(),
          author_name: authorName.trim() || "匿名作者",
          genre,
          synopsis,
          story_outline: storyOutline,
          chapter_notes: chapterNotes,
          allow_cocreation: allowCocreation,
          tags,
        });
        navigate(`/community/novel/${id}`);
      } else {
        const payload: CommunityNovelCreate = {
          title: title.trim(),
          author_name: authorName.trim() || "匿名作者",
          genre,
          project_id: projectId,
          synopsis,
          story_outline: storyOutline,
          chapter_notes: chapterNotes,
          allow_cocreation: allowCocreation,
          tags,
          total_chapters: totalChapters,
          total_words: totalWords,
        };
        const result = await api.uploadCommunityNovel(payload);
        navigate(`/community/novel/${result.id}`);
      }
    } catch (e) {
      setError("保存失败: " + (e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  if (loading) return <div className="empty-state">加载中...</div>;

  return (
    <div style={{ maxWidth: "800px" }}>
      <button className="btn-back" onClick={() => navigate(isEdit ? `/community/novel/${id}` : "/community")}>
        ← 返回
      </button>

      <div className="page-header">
        <h1>{isEdit ? "编辑社区小说" : "上传小说到社区"}</h1>
        <p>{isEdit ? "修改小说信息和描述" : "将你的作品分享到社区，让更多人看到"}</p>
      </div>

      {error && (
        <div
          style={{
            background: "var(--red-light)",
            color: "var(--red)",
            padding: "0.625rem 1rem",
            borderRadius: "var(--r-md)",
            fontSize: "13px",
            marginBottom: "1rem",
            border: "1px solid #f5c6ce",
          }}
        >
          {error}
        </div>
      )}

      {/* ── Basic Info ── */}
      <div className="card">
        <div className="wv-section-title">基本信息</div>

        <div className="form-group">
          <label className="form-label">小说标题 *</label>
          <input
            className="form-input"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="输入小说标题"
            maxLength={200}
          />
        </div>

        <div className="form-group">
          <label className="form-label">作者名称</label>
          <input
            className="form-input"
            value={authorName}
            onChange={(e) => setAuthorName(e.target.value)}
            placeholder="匿名作者"
            maxLength={100}
          />
        </div>

        <div className="form-group">
          <label className="form-label">类型</label>
          <select className="form-select" value={genre} onChange={(e) => setGenre(e.target.value)}>
            {GENRES.map((g) => (
              <option key={g} value={g}>
                {g}
              </option>
            ))}
          </select>
        </div>

        {!isEdit && (
          <div className="form-group">
            <label className="form-label">关联项目（可选）</label>
            <select
              className="form-select"
              value={projectId || ""}
              onChange={(e) => handleProjectSelect(e.target.value)}
            >
              <option value="">不关联项目</option>
              {projects.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.title}（{p.genre} · {p.chapter_count}/{p.total_chapters} 章）
                </option>
              ))}
            </select>
            <p className="form-hint">
              关联后，系统将自动填充标题、类型、章节数和字数统计（仍可修改）
            </p>
          </div>
        )}

        {/* Stats preview (if linked) */}
        {(totalChapters > 0 || totalWords > 0) && (
          <div style={{ display: "flex", gap: "1rem", marginTop: "0.5rem" }}>
            <span className="tag tag-gray">{totalChapters} 章</span>
            <span className="tag tag-gray">{totalWords.toLocaleString()} 字</span>
          </div>
        )}
      </div>

      {/* ── Tags ── */}
      <div className="card">
        <div className="wv-section-title">标签分类</div>
        <div className="form-group">
          <label className="form-label">自定义标签（最多 10 个）</label>
          <div
            style={{
              display: "flex",
              gap: "0.375rem",
              flexWrap: "wrap",
              marginBottom: "0.5rem",
              minHeight: tags.length > 0 ? "32px" : "0",
            }}
          >
            {tags.map((tag) => (
              <span
                key={tag}
                className="tag tag-gold"
                style={{ cursor: "pointer", display: "flex", alignItems: "center", gap: "0.25rem" }}
                onClick={() => handleRemoveTag(tag)}
                title="点击移除"
              >
                {tag}
                <span style={{ opacity: 0.6 }}>×</span>
              </span>
            ))}
          </div>
          <input
            className="form-input"
            value={tagInput}
            onChange={(e) => setTagInput(e.target.value)}
            onKeyDown={handleTagKeyDown}
            placeholder="输入标签后按回车添加（如：修仙、末日、重生）"
            maxLength={20}
          />
          <p className="form-hint">
            按 Enter 或逗号添加标签，Backspace 删除最后一个。点击已添加的标签可移除。
          </p>
        </div>
      </div>

      {/* ── Detailed Description ── */}
      <div className="card">
        <div className="wv-section-title">详细描述</div>

        <div className="form-group">
          <label className="form-label">小说简介</label>
          <textarea
            className="form-textarea"
            value={synopsis}
            onChange={(e) => setSynopsis(e.target.value)}
            placeholder="用几句话介绍你的小说，吸引读者点击阅读..."
            maxLength={5000}
            style={{ minHeight: "100px" }}
          />
          <p className="form-hint">{synopsis.length} / 5000 字</p>
        </div>

        <div className="form-group">
          <label className="form-label">故事梗概</label>
          <textarea
            className="form-textarea"
            value={storyOutline}
            onChange={(e) => setStoryOutline(e.target.value)}
            placeholder="概述故事的起因、发展、高潮和结局。可以是粗略的骨架，也可以是详细的大纲..."
            maxLength={10000}
            style={{ minHeight: "150px" }}
          />
          <p className="form-hint">{storyOutline.length} / 10000 字</p>
        </div>

        <div className="form-group">
          <label className="form-label">章节说明</label>
          <textarea
            className="form-textarea"
            value={chapterNotes}
            onChange={(e) => setChapterNotes(e.target.value)}
            placeholder="对各章节的简要说明，例如：第一章介绍主角背景，第二章引入世界观..."
            maxLength={10000}
            style={{ minHeight: "120px" }}
          />
          <p className="form-hint">{chapterNotes.length} / 10000 字</p>
        </div>
      </div>

      {/* ── Co-creation ── */}
      <div className="card">
        <div className="wv-section-title">共创设置</div>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: "1rem",
            padding: "1rem",
            background: allowCocreation ? "var(--gold-light)" : "var(--bg-soft)",
            borderRadius: "var(--r-md)",
            border: `1px solid ${allowCocreation ? "var(--gold-border)" : "var(--border)"}`,
          }}
        >
          <div style={{ fontSize: "28px" }}>{allowCocreation ? "🔓" : "🔒"}</div>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: "14px", fontWeight: 600, color: "var(--text-1)", marginBottom: "0.25rem" }}>
              是否允许共创世界观
            </div>
            <div style={{ fontSize: "12px", color: "var(--text-3)", lineHeight: 1.5 }}>
              开启后，其他用户可以基于你的小说世界观创建新的续写项目，进行联合创作。
              <br />
              你的世界观设定（角色、地理、势力等）将被分享给共创者使用。
            </div>
          </div>
          <button
            onClick={() => setAllowCocreation(!allowCocreation)}
            style={{
              width: "48px",
              height: "26px",
              borderRadius: "13px",
              background: allowCocreation ? "var(--gold)" : "var(--border)",
              border: "none",
              cursor: "pointer",
              position: "relative",
              transition: "background 0.2s",
              flexShrink: 0,
            }}
          >
            <span
              style={{
                position: "absolute",
                top: "3px",
                left: allowCocreation ? "25px" : "3px",
                width: "20px",
                height: "20px",
                borderRadius: "50%",
                background: "var(--white)",
                transition: "left 0.2s",
                boxShadow: "0 1px 3px rgba(0,0,0,0.2)",
              }}
            />
          </button>
        </div>
      </div>

      {/* ── Actions ── */}
      <div style={{ display: "flex", gap: "0.75rem", marginTop: "1rem" }}>
        <button className="btn btn-primary btn-lg" onClick={handleSave} disabled={saving}>
          {saving ? "保存中..." : isEdit ? "保存修改" : "上传到社区"}
        </button>
        <button
          className="btn btn-lg"
          onClick={() => navigate(isEdit ? `/community/novel/${id}` : "/community")}
        >
          取消
        </button>
      </div>
    </div>
  );
}
