import { useEffect, useRef, useState } from "react";
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
  const [listError, setListError] = useState("");
  const [demo, setDemo] = useState<DemoFixtureCurrentResponse | null>(null);
  const [demoError, setDemoError] = useState("");
  const [demoBusy, setDemoBusy] = useState(false);
  const [listAnnouncement, setListAnnouncement] = useState("");
  const projectEntryRefs = useRef(new Map<string, HTMLAnchorElement>());
  const listTitleRef = useRef<HTMLHeadingElement>(null);
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
    setLoading(true);
    setListError("");
    try {
      setProjects(await api.listProjects());
    } catch (e) {
      console.error("Failed to load projects:", e);
      setListError((e as Error).message || "项目列表暂时无法加载，请稍后重试。");
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

  async function handleDelete(project: Project) {
    if (!confirm("确认删除这个项目？所有设定、历史规划和章节数据都会被删除。")) return;
    try {
      await api.deleteProject(project.id);
      setProjects((prev) => {
        const removedIndex = prev.findIndex((item) => item.id === project.id);
        const nextProjects = prev.filter((item) => item.id !== project.id);
        const nextFocusProject = nextProjects[removedIndex] ?? nextProjects[removedIndex - 1];
        requestAnimationFrame(() => {
          if (nextFocusProject) projectEntryRefs.current.get(nextFocusProject.id)?.focus();
          else listTitleRef.current?.focus();
        });
        return nextProjects;
      });
      setListAnnouncement(`已删除项目《${project.title}》。`);
    } catch (e) {
      alert("删除失败: " + (e as Error).message);
    }
  }

  if (loading) {
    return (
      <div className="project-hub project-hub--list" aria-busy="true">
        <div className="project-hub__state" role="status">
          <span className="project-hub__state-mark" aria-hidden="true" />
          <p>正在整理你的创作项目…</p>
        </div>
      </div>
    );
  }

  return (
    <div className="project-hub project-hub--list">
      <header className="project-hub__hero">
        <div className="project-hub__hero-copy">
          <p className="project-hub__eyebrow">Novel studio</p>
          <h1>让每个故事，都有清晰的下一步</h1>
          <p className="project-hub__lede">在同一个工作台管理设定、章节与持续写作进度。</p>
        </div>
        <Link className="btn btn-primary btn-lg project-hub__primary-action" to="/new">
          <span aria-hidden="true">＋</span> 新建项目
        </Link>
      </header>

      <section className="project-hub__demo-region" aria-label="技术演示">
        {demo && <div className={`project-hub__demo-card is-${demo.state}`}>
          <div className="project-hub__demo-copy">
            <p className="project-hub__section-kicker">Guided demo</p>
            <h2 id="demo-entry-title">五步技术演示</h2>
            <p>{demo.state === "missing" ? "建立一份隔离的固定样例，体验设定、章节、伏笔和零 AI 技术模拟。" : demo.state === "ready" ? "固定样例已就绪，可以从上次的技术演示继续。" : "样例数据已发生变化。系统会保留现状，不覆盖、不修复，也不提供执行入口。"}</p>
          </div>
          <div className="project-hub__demo-action">
            {demo.state === "missing" && <button className="btn btn-primary" disabled={demoBusy} onClick={() => void createDemo()}>{demoBusy ? "正在建立…" : "建立技术演示样例"}</button>}
            {demo.state === "ready" && demo.project_id && <Link className="btn btn-primary" to={`/project/${demo.project_id}`}>打开五步技术演示</Link>}
            {demo.state === "diverged" && <span className="tag tag-gray">已保留，不可执行</span>}
          </div>
          {demoError && <p className="project-hub__inline-error" role="alert">{demoError}</p>}
        </div>}
        {!demo && demoError && <p className="project-hub__demo-error" role="alert">{demoError}</p>}
      </section>

      <section className="project-hub__projects" aria-labelledby="project-list-title">
        <div className="project-hub__section-heading">
          <div>
            <p className="project-hub__section-kicker">Your stories</p>
            <h2 id="project-list-title" ref={listTitleRef} tabIndex={-1}>创作项目</h2>
          </div>
          {!listError && projects.length > 0 && <span className="project-hub__count">{projects.length} 个项目</span>}
        </div>
        <p className="project-hub__announcement" role="status" aria-live="polite">{listAnnouncement}</p>

        {listError ? (
          <div className="project-hub__state project-hub__state--error" role="alert">
            <h3>项目列表暂时无法加载</h3>
            <p>{listError}</p>
            <button className="btn btn-secondary" onClick={() => void loadProjects()}>重新加载项目</button>
          </div>
        ) : projects.length === 0 ? (
          <div className="project-hub__state project-hub__state--empty">
            <span className="project-hub__empty-symbol" aria-hidden="true">✦</span>
            <h3>第一部故事，正等你命名</h3>
            <p>创建项目后，从世界设定开始组织你的长篇小说。</p>
            <Link className="btn btn-secondary" to="/new">创建第一个项目</Link>
          </div>
        ) : (
          <ul className="project-hub__grid" aria-label="项目列表">
            {projects.map((project) => (
              <li key={project.id}>
                <article className="project-hub__project-card">
                  <div className="project-hub__project-topline">
                    <span className="project-hub__project-status">{STATUS_LABELS[project.status] || project.status}</span>
                    <button
                      type="button"
                      className="btn btn-ghost btn-sm project-hub__delete"
                      onClick={() => void handleDelete(project)}
                      aria-label={`删除项目《${project.title}》`}
                    >
                      删除
                    </button>
                  </div>
                  <Link
                    className="project-hub__project-entry"
                    to={`/project/${project.id}`}
                    aria-label={`打开项目《${project.title}》`}
                    ref={(node) => {
                      if (node) projectEntryRefs.current.set(project.id, node);
                      else projectEntryRefs.current.delete(project.id);
                    }}
                  >
                    <div className="project-hub__project-body">
                      {demo?.state === "ready" && demo.project_id === project.id && <span className="tag tag-gold">五步技术演示</span>}
                      <h3>{project.title}</h3>
                      <p>{project.genre} · 计划 {project.total_chapters} 章</p>
                    </div>
                    <div className="project-hub__project-meta">
                      <span>已写 {project.chapter_count} 章</span>
                      <span>{project.has_worldview ? "设定已建立" : "等待建立设定"}</span>
                      {project.has_outline && <span>历史章节可用</span>}
                    </div>
                    <span className="project-hub__open-link">
                      进入工作台 <span aria-hidden="true">↗</span>
                    </span>
                  </Link>
                </article>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
