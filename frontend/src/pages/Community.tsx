import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "@/services/api";
import type { CommunityNovelBrief, CommunityTag } from "@/types";

const GENRE_COLORS: Record<string, string> = {
  玄幻: "#b8860b",
  都市: "#2563eb",
  科幻: "#7c3aed",
  武侠: "#dc2626",
  仙侠: "#059669",
  悬疑: "#475569",
  言情: "#ec4899",
};

export default function Community() {
  const navigate = useNavigate();

  const [novels, setNovels] = useState<CommunityNovelBrief[]>([]);
  const [tags, setTags] = useState<CommunityTag[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [hasMore, setHasMore] = useState(true);
  const [activeTag, setActiveTag] = useState<string | null>(null);
  const [sortBy, setSortBy] = useState<"latest" | "popular" | "random">("latest");

  const sentinelRef = useRef<HTMLDivElement>(null);
  const loadedIds = useRef<Set<string>>(new Set());
  const pageOffset = useRef(0);
  const isFirstLoad = useRef(true);

  // ── Load tags ──
  useEffect(() => {
    api.getCommunityTags().then(setTags).catch(() => {});
  }, []);

  // ── Load novels (reset when tag/sort changes) ──
  const loadNovels = useCallback(
    async (tag: string | null, sort: typeof sortBy) => {
      setLoading(true);
      loadedIds.current = new Set();
      pageOffset.current = 0;
      setHasMore(true);
      try {
        const data = await api.listCommunityNovels({
          offset: 0,
          limit: 12,
          tag: tag ?? undefined,
          sort,
        });
        setNovels(data);
        data.forEach((n) => loadedIds.current.add(n.id));
        pageOffset.current = data.length;  // Update offset for next page
        setHasMore(data.length >= 12);
      } catch (e) {
        console.error("Failed to load community novels:", e);
        setNovels([]);
      } finally {
        setLoading(false);
        isFirstLoad.current = false;
      }
    },
    []
  );

  useEffect(() => {
    loadNovels(activeTag, sortBy);
  }, [activeTag, sortBy, loadNovels]);

  // ── Load more (random batch, excluding already loaded) ──
  const loadMore = useCallback(async () => {
    if (loadingMore || !hasMore) return;
    setLoadingMore(true);
    try {
      let data: CommunityNovelBrief[];
      if (sortBy === "random" || activeTag) {
        // For random/tagged mode, use the regular list endpoint with offset
        data = await api.listCommunityNovels({
          offset: pageOffset.current,
          limit: 12,
          tag: activeTag ?? undefined,
          sort: sortBy,
        });
      } else {
        // For latest/popular, use random endpoint for more variety
        data = await api.getRandomNovels(6, Array.from(loadedIds.current));
      }

      if (data.length === 0) {
        setHasMore(false);
      } else {
        const newOnes = data.filter((n) => !loadedIds.current.has(n.id));
        if (newOnes.length === 0) {
          setHasMore(false);
        } else {
          setNovels((prev) => [...prev, ...newOnes]);
          newOnes.forEach((n) => loadedIds.current.add(n.id));
          pageOffset.current += data.length;
        }
      }
    } catch (e) {
      console.error("Failed to load more novels:", e);
    } finally {
      setLoadingMore(false);
    }
  }, [loadingMore, hasMore, sortBy, activeTag]);

  // ── Infinite scroll via IntersectionObserver ──
  useEffect(() => {
    const sentinel = sentinelRef.current;
    if (!sentinel) return;

    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0].isIntersecting) {
          loadMore();
        }
      },
      { rootMargin: "200px" }
    );
    observer.observe(sentinel);
    return () => observer.disconnect();
  }, [loadMore]);

  // ── Refresh (random reload) ──
  const handleRefresh = useCallback(() => {
    loadNovels(null, "random");
    setSortBy("random");
    setActiveTag(null);
  }, [loadNovels]);

  function formatDate(dateStr: string): string {
    const d = new Date(dateStr);
    const now = new Date();
    const diff = now.getTime() - d.getTime();
    const hours = diff / (1000 * 60 * 60);
    if (hours < 1) return "刚刚";
    if (hours < 24) return `${Math.floor(hours)}小时前`;
    if (hours < 168) return `${Math.floor(hours / 24)}天前`;
    return d.toLocaleDateString("zh-CN", { month: "short", day: "numeric" });
  }

  return (
    <div>
      {/* ── Page Header ── */}
      <div className="page-header" style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-end" }}>
        <div>
          <h1>社区</h1>
          <p>探索其他创作者上传的小说作品</p>
        </div>
        <button className="btn btn-primary" onClick={handleRefresh}>
          ↻ 随机刷新
        </button>
      </div>

      {/* ── Sort & Tag Filters ── */}
      <div style={{ display: "flex", gap: "0.75rem", marginBottom: "1rem", flexWrap: "wrap", alignItems: "center" }}>
        <div style={{ display: "flex", gap: "0.375rem" }}>
          {(["latest", "popular", "random"] as const).map((s) => (
            <button
              key={s}
              className={`btn btn-sm ${sortBy === s ? "btn-primary" : ""}`}
              onClick={() => setSortBy(s)}
            >
              {s === "latest" ? "最新" : s === "popular" ? "热门" : "随机"}
            </button>
          ))}
        </div>
        {tags.length > 0 && (
          <div style={{ display: "flex", gap: "0.375rem", flexWrap: "wrap" }}>
            <button
              className={`tag ${!activeTag ? "tag-gold" : "tag-gray"}`}
              style={{ cursor: "pointer", border: "1px solid", padding: "0.2rem 0.625rem" }}
              onClick={() => setActiveTag(null)}
            >
              全部
            </button>
            {tags.slice(0, 10).map((t) => (
              <button
                key={t.id}
                className={`tag ${activeTag === t.name ? "tag-gold" : "tag-gray"}`}
                style={{ cursor: "pointer", border: "1px solid", padding: "0.2rem 0.625rem" }}
                onClick={() => setActiveTag(activeTag === t.name ? null : t.name)}
              >
                {t.name} ({t.usage_count})
              </button>
            ))}
          </div>
        )}
      </div>

      {/* ── Novel Cards Grid ── */}
      {loading ? (
        <div className="empty-state">加载中...</div>
      ) : novels.length === 0 ? (
        <div className="card empty-state">
          <h3>社区还没有作品</h3>
          <p>成为第一个上传小说的创作者吧！</p>
          <button className="btn btn-primary" style={{ marginTop: "1rem" }} onClick={() => navigate("/community/upload")}>
            上传我的小说
          </button>
        </div>
      ) : (
        <>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fill, minmax(340px, 1fr))",
              gap: "1rem",
            }}
          >
            {novels.map((novel) => (
              <div
                key={novel.id}
                className="card card-hover"
                onClick={() => navigate(`/community/novel/${novel.id}`)}
                style={{ display: "flex", flexDirection: "column", gap: "0.625rem", cursor: "pointer" }}
              >
                {/* Title + Author */}
                <div>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: "0.5rem" }}>
                    <h3 style={{ fontSize: "16px", fontWeight: 700, color: "var(--text-1)", lineHeight: 1.4 }}>
                      {novel.title}
                    </h3>
                    <span
                      className="tag"
                      style={{
                        background: `${GENRE_COLORS[novel.genre] || "#b8860b"}15`,
                        color: GENRE_COLORS[novel.genre] || "#b8860b",
                        border: `1px solid ${GENRE_COLORS[novel.genre] || "#b8860b"}30`,
                        flexShrink: 0,
                      }}
                    >
                      {novel.genre}
                    </span>
                  </div>
                  <div style={{ fontSize: "12px", color: "var(--text-3)", marginTop: "0.25rem" }}>
                    by {novel.author_name} · {formatDate(novel.created_at)}
                  </div>
                </div>

                {/* Synopsis */}
                <p
                  style={{
                    fontSize: "13px",
                    color: "var(--text-2)",
                    lineHeight: 1.6,
                    overflow: "hidden",
                    display: "-webkit-box",
                    WebkitLineClamp: 3,
                    WebkitBoxOrient: "vertical",
                    minHeight: "3.5rem",
                  }}
                >
                  {novel.synopsis || "暂无简介"}
                </p>

                {/* Tags */}
                {novel.tags.length > 0 && (
                  <div style={{ display: "flex", gap: "0.375rem", flexWrap: "wrap" }}>
                    {novel.tags.slice(0, 5).map((tag, i) => (
                      <span key={i} className="tag tag-gray">
                        {tag}
                      </span>
                    ))}
                    {novel.tags.length > 5 && (
                      <span className="tag tag-gray">+{novel.tags.length - 5}</span>
                    )}
                  </div>
                )}

                {/* Stats */}
                <div
                  style={{
                    display: "flex",
                    gap: "1rem",
                    fontSize: "12px",
                    color: "var(--text-3)",
                    paddingTop: "0.5rem",
                    borderTop: "1px solid var(--border)",
                    alignItems: "center",
                  }}
                >
                  <span>📖 {novel.view_count} 阅读</span>
                  <span>❤ {novel.like_count} 赞</span>
                  <span>📝 {novel.total_chapters} 章</span>
                  {novel.allow_cocreation && (
                    <span className="tag tag-gold" style={{ marginLeft: "auto" }}>
                      开放共创
                    </span>
                  )}
                </div>
              </div>
            ))}
          </div>

          {/* ── Infinite scroll sentinel ── */}
          <div ref={sentinelRef} style={{ height: "60px", display: "flex", alignItems: "center", justifyContent: "center" }}>
            {loadingMore && <span style={{ color: "var(--text-3)", fontSize: "13px" }}>加载更多...</span>}
            {!hasMore && !loading && (
              <span style={{ color: "var(--text-3)", fontSize: "13px" }}>— 已经到底了 —</span>
            )}
          </div>
        </>
      )}
    </div>
  );
}
