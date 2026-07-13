import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "@/services/api";
import type { Project } from "@/types";

const STATUS_LABELS: Record<string, string> = {
  draft: "草稿",
  worldview_set: "世界观已设定",
  outline_pending: "大纲待确认",
  outline_confirmed: "大纲已确认",
  writing: "写作中",
  completed: "已完成",
};

export default function ProjectList() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);
  const navigate = useNavigate();

  useEffect(() => {
    loadProjects();
  }, []);

  async function loadProjects() {
    try {
      const data = await api.listProjects();
      setProjects(data);
    } catch (e) {
      console.error("Failed to load projects:", e);
    } finally {
      setLoading(false);
    }
  }

  async function handleDelete(id: string) {
    if (!confirm("确认删除这个项目？所有世界观、大纲和章节数据都会被删除。")) return;
    try {
      await api.deleteProject(id);
      setProjects((prev) => prev.filter((p) => p.id !== id));
    } catch (e) {
      alert("删除失败: " + (e as Error).message);
    }
  }

  if (loading) return <div className="empty-state">加载中...</div>;

  return (
    <div>
      <div className="page-header">
        <h1>我的项目</h1>
        <p>管理你的小说世界观续写项目</p>
      </div>

      <div style={{ marginBottom: "1rem" }}>
        <Link to="/new">
          <button className="btn btn-primary btn-lg">+ 新建项目</button>
        </Link>
      </div>

      {projects.length === 0 ? (
        <div className="card empty-state">
          <h3>还没有项目</h3>
          <p>点击「新建项目」开始你的第一部小说</p>
        </div>
      ) : (
        <div>
          {projects.map((project) => (
            <div
              key={project.id}
              className="card card-hover"
              onClick={() => navigate(`/project/${project.id}`)}
            >
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
                <div style={{ flex: 1 }}>
                  <h3 style={{ fontSize: "16px", fontWeight: 600, marginBottom: "0.375rem", color: "var(--text-1)" }}>
                    {project.title}
                  </h3>
                  <div style={{ display: "flex", gap: "0.625rem", alignItems: "center", flexWrap: "wrap" }}>
                    <span className="tag tag-gold">{project.genre}</span>
                    <span style={{ fontSize: "12px", color: "var(--text-3)" }}>
                      章节 {project.chapter_count}/{project.total_chapters}
                    </span>
                    <span className="tag tag-gray">{STATUS_LABELS[project.status] || project.status}</span>
                  </div>
                  <div style={{ marginTop: "0.5rem", display: "flex", gap: "0.375rem", flexWrap: "wrap" }}>
                    {project.has_worldview && <span className="tag tag-gold">世界观</span>}
                    {project.has_outline && <span className="tag tag-gold">大纲</span>}
                    {project.chapter_count > 0 && <span className="tag tag-red">{project.chapter_count}章</span>}
                  </div>
                </div>
                <button
                  className="btn btn-ghost btn-sm"
                  onClick={(e) => { e.stopPropagation(); handleDelete(project.id); }}
                  style={{ color: "var(--red)" }}
                >
                  删除
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
