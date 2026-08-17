import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "@/services/api";
import type { Project } from "@/types";
import { ApiError } from "@/services/api";
import { bootstrapDemoFixture, readDemoFixture } from "@/services/demoFixture";
import type { DemoFixtureCurrentResponse } from "@/types/demo";

const STATUS_LABELS: Record<string, string> = {
  draft: "草稿",
  worldview_set: "世界观已设定",
  outline_pending: "可继续写作",
  outline_confirmed: "可继续写作",
  writing: "写作中",
  completed: "已完成",
};

export default function ProjectList() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);
  const [demo, setDemo] = useState<DemoFixtureCurrentResponse | null>(null);
  const [demoError, setDemoError] = useState("");
  const [demoBusy, setDemoBusy] = useState(false);
  const navigate = useNavigate();

  useEffect(() => {
    const controller = new AbortController();
    void loadProjects();
    void readDemoFixture(controller.signal).then((current) => {
      setDemo(current);
      setDemoError("");
    }).catch((error) => {
      if (controller.signal.aborted || (error instanceof ApiError && error.status === 404)) return;
      setDemo(null);
      setDemoError("技术演示状态暂时无法核对；已隐藏演示入口，普通项目仍可继续使用。");
    });
    return () => controller.abort();
  }, []);

  async function loadProjects() {
    try {
      setProjects(await api.listProjects());
    } catch (e) {
      console.error("Failed to load projects:", e);
    } finally {
      setLoading(false);
    }
  }

  async function createDemo() {
    if (demo?.state !== "missing" || demoBusy) return;
    setDemoBusy(true); setDemoError("");
    try { const result = await bootstrapDemoFixture(); navigate(`/project/${result.project_id}`); }
    catch (error) {
      try {
        const current = await readDemoFixture();
        setDemo(current);
        if (current.state === "ready" && current.project_id) {
          navigate(`/project/${current.project_id}`);
        } else if (current.state === "missing") {
          setDemoError("服务端确认样例尚未建立。可以由你明确再次建立，系统不会自动重复提交。");
        } else {
          setDemoError("样例数据已发生变化，已保留现状且不再创建。");
        }
      } catch {
        setDemo(null);
        setDemoError(`${(error as Error).message || "技术演示建立请求响应不确定。"} 当前无法核对服务端状态，已隐藏建立入口，请稍后刷新。`);
      }
    }
    finally { setDemoBusy(false); }
  }

  async function handleDelete(id: string) {
    if (!confirm("确认删除这个项目？所有设定、历史规划和章节数据都会被删除。")) return;
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

      {demo && <section className={`card demo-entry is-${demo.state}`} aria-labelledby="demo-entry-title">
        <div><h2 id="demo-entry-title">五步技术演示</h2><p>{demo.state === "missing" ? "建立一份隔离的固定样例，体验设定、章节、伏笔和零 AI 技术模拟。" : demo.state === "ready" ? "固定样例已就绪，可以从上次的技术演示继续。" : "样例数据已发生变化。系统会保留现状，不覆盖、不修复，也不提供执行入口。"}</p></div>
        {demo.state === "missing" && <button className="btn btn-primary" disabled={demoBusy} onClick={() => void createDemo()}>{demoBusy ? "正在建立…" : "建立技术演示样例"}</button>}
        {demo.state === "ready" && demo.project_id && <Link className="btn btn-primary" to={`/project/${demo.project_id}`}>打开五步技术演示</Link>}
        {demo.state === "diverged" && <span className="tag tag-gray">已保留，不可执行</span>}
        {demoError && <p role="alert">{demoError}</p>}
      </section>}
      {!demo && demoError && <p className="card demo-entry" role="alert">{demoError}</p>}

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
                  {demo?.state === "ready" && demo.project_id === project.id && <span className="tag tag-gold">五步技术演示</span>}
                  <div style={{ display: "flex", gap: "0.625rem", alignItems: "center", flexWrap: "wrap" }}>
                    <span className="tag tag-gold">{project.genre}</span>
                    <span style={{ fontSize: "12px", color: "var(--text-3)" }}>
                      章节 {project.chapter_count}/{project.total_chapters}
                    </span>
                    <span className="tag tag-gray">{STATUS_LABELS[project.status] || project.status}</span>
                  </div>
                  <div style={{ marginTop: "0.5rem", display: "flex", gap: "0.375rem", flexWrap: "wrap" }}>
                    {project.has_worldview && <span className="tag tag-gold">世界观</span>}
                    {project.has_outline && <span className="tag tag-gold">历史章节可用</span>}
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
