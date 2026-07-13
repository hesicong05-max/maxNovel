import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api } from "@/services/api";
import type { CommunityNovelDetail } from "@/types";

export default function CommunityNovelDetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [novel, setNovel] = useState<CommunityNovelDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [liked, setLiked] = useState(false);
  const [likeCount, setLikeCount] = useState(0);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!id) return;
    api
      .getCommunityNovel(id)
      .then((data) => {
        setNovel(data);
        setLikeCount(data.like_count);
      })
      .catch((e) => {
        console.error("Failed to load novel:", e);
      })
      .finally(() => setLoading(false));
  }, [id]);

  function handleLike() {
    if (!id || liked) return;
    api
      .likeCommunityNovel(id)
      .then((res) => {
        setLiked(true);
        setLikeCount(res.like_count);
      })
      .catch(console.error);
  }

  function handleEdit() {
    navigate(`/community/edit/${id}`);
  }

  function handleDelete() {
    if (!id || !novel) return;
    if (!confirm(`确认从社区移除《${novel.title}》？`)) return;
    api
      .deleteCommunityNovel(id)
      .then(() => navigate("/community"))
      .catch((e) => alert("删除失败: " + e.message));
  }

  function handleCoCreate() {
    if (!novel?.project_id) return;
    if (!confirm(`基于《${novel.title}》的世界观创建新的续写项目？\n这将复制该小说的世界观到你的新项目中。`)) return;
    navigate(`/new?from_novel=${novel.id}`);
  }

  if (loading) return <div className="empty-state">加载中...</div>;
  if (!novel)
    return (
      <div>
        <button className="btn-back" onClick={() => navigate("/community")}>
          ← 返回社区
        </button>
        <div className="empty-state">
          <h3>小说不存在</h3>
          <p>该作品可能已被作者移除</p>
        </div>
      </div>
    );

  return (
    <div style={{ maxWidth: "800px" }}>
      <button className="btn-back" onClick={() => navigate("/community")}>
        ← 返回社区
      </button>

      {/* ── Title Section ── */}
      <div className="card" style={{ marginBottom: "1rem" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: "1rem" }}>
          <div style={{ flex: 1 }}>
            <h1 style={{ fontSize: "24px", fontWeight: 700, color: "var(--text-1)", marginBottom: "0.5rem" }}>
              {novel.title}
            </h1>
            <div style={{ display: "flex", gap: "0.625rem", alignItems: "center", flexWrap: "wrap" }}>
              <span style={{ fontSize: "13px", color: "var(--text-3)" }}>by {novel.author_name}</span>
              <span className="tag tag-gold">{novel.genre}</span>
              {novel.allow_cocreation && <span className="tag tag-red">开放共创</span>}
            </div>
          </div>
          <div style={{ display: "flex", gap: "0.5rem", flexShrink: 0 }}>
            <button className="btn btn-sm" onClick={handleEdit}>编辑</button>
            <button className="btn btn-sm btn-ghost" style={{ color: "var(--red)" }} onClick={handleDelete}>
              删除
            </button>
          </div>
        </div>

        {/* Tags */}
        {novel.tags.length > 0 && (
          <div style={{ display: "flex", gap: "0.375rem", flexWrap: "wrap", marginTop: "0.75rem" }}>
            {novel.tags.map((tag, i) => (
              <span key={i} className="tag tag-gray">{tag}</span>
            ))}
          </div>
        )}

        {/* Stats */}
        <div style={{ display: "flex", gap: "1.5rem", marginTop: "1rem", paddingTop: "1rem", borderTop: "1px solid var(--border)" }}>
          <div>
            <div style={{ fontSize: "11px", color: "var(--text-3)", fontWeight: 600, textTransform: "uppercase" }}>阅读</div>
            <div style={{ fontSize: "18px", fontWeight: 700, color: "var(--text-1)" }}>{novel.view_count}</div>
          </div>
          <div>
            <div style={{ fontSize: "11px", color: "var(--text-3)", fontWeight: 600, textTransform: "uppercase" }}>点赞</div>
            <div style={{ fontSize: "18px", fontWeight: 700, color: "var(--text-1)" }}>{likeCount}</div>
          </div>
          <div>
            <div style={{ fontSize: "11px", color: "var(--text-3)", fontWeight: 600, textTransform: "uppercase" }}>章节</div>
            <div style={{ fontSize: "18px", fontWeight: 700, color: "var(--text-1)" }}>{novel.total_chapters}</div>
          </div>
          <div>
            <div style={{ fontSize: "11px", color: "var(--text-3)", fontWeight: 600, textTransform: "uppercase" }}>总字数</div>
            <div style={{ fontSize: "18px", fontWeight: 700, color: "var(--text-1)" }}>{novel.total_words.toLocaleString()}</div>
          </div>
        </div>

        {/* Actions */}
        <div style={{ display: "flex", gap: "0.625rem", marginTop: "1rem" }}>
          <button className={`btn ${liked ? "btn-danger" : "btn-primary"}`} onClick={handleLike}>
            {liked ? "❤ 已点赞" : "♡ 点赞"}
          </button>
          {novel.allow_cocreation && (
            <button className="btn" onClick={handleCoCreate}>
              基于世界观共创
            </button>
          )}
        </div>
      </div>

      {/* ── Synopsis ── */}
      {novel.synopsis && (
        <div className="card">
          <div className="wv-section-title">📖 小说简介</div>
          <p style={{ fontSize: "14px", lineHeight: 1.8, color: "var(--text-2)", whiteSpace: "pre-wrap" }}>
            {novel.synopsis}
          </p>
        </div>
      )}

      {/* ── Story Outline ── */}
      {novel.story_outline && (
        <div className="card">
          <div className="wv-section-title">🗺 故事梗概</div>
          <p style={{ fontSize: "14px", lineHeight: 1.8, color: "var(--text-2)", whiteSpace: "pre-wrap" }}>
            {novel.story_outline}
          </p>
        </div>
      )}

      {/* ── Chapter Notes ── */}
      {novel.chapter_notes && (
        <div className="card">
          <div className="wv-section-title">📝 章节说明</div>
          <p style={{ fontSize: "14px", lineHeight: 1.8, color: "var(--text-2)", whiteSpace: "pre-wrap" }}>
            {novel.chapter_notes}
          </p>
        </div>
      )}

      {/* ── Co-creation Info ── */}
      <div className="card" style={{ background: "var(--gold-light)", borderColor: "var(--gold-border)" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "0.625rem" }}>
          <span style={{ fontSize: "20px" }}>{novel.allow_cocreation ? "🔓" : "🔒"}</span>
          <div>
            <div style={{ fontSize: "14px", fontWeight: 600, color: "var(--text-1)" }}>
              {novel.allow_cocreation ? "已开放共创" : "未开放共创"}
            </div>
            <div style={{ fontSize: "12px", color: "var(--text-3)" }}>
              {novel.allow_cocreation
                ? "作者允许其他用户基于该世界观进行联合创作"
                : "作者未开放基于该世界观的联合创作"}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
