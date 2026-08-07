import { useEffect, useState } from "react";
import { Link, useParams, useNavigate } from "react-router-dom";
import { api } from "@/services/api";
import type { Project } from "@/types";
import WorldviewEditor from "@/components/WorldviewEditor";
import ChapterWriter from "@/components/ChapterWriter";
import ProgressPanel from "@/components/ProgressPanel";

type Step = "worldview" | "writing";

export default function ProjectDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [project, setProject] = useState<Project | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [activeStep, setActiveStep] = useState<Step>("worldview");

  useEffect(() => {
    if (id) loadProject(id);
  }, [id]);

  // Sync project status when tab becomes visible again (handles refresh / tab switch)
  useEffect(() => {
    const handleVisibilityChange = () => {
      if (!document.hidden && id) {
        refreshProject();
      }
    };
    document.addEventListener("visibilitychange", handleVisibilityChange);
    return () => document.removeEventListener("visibilitychange", handleVisibilityChange);
  }, [id]);

  async function loadProject(projectId: string, autoStep = true) {
    if (autoStep) setLoadError(null);
    try {
      const data = await api.getProject(projectId);
      setProject(data);
      if (autoStep) {
        setActiveStep(data.has_outline ? "writing" : "worldview");
      }
    } catch (e) {
      console.error("Failed to load project:", e);
      if (autoStep) {
        setProject(null);
        setLoadError((e as Error).message || "项目加载失败，请稍后重试。");
      }
    } finally {
      setLoading(false);
    }
  }

  async function refreshProject() {
    if (id) await loadProject(id, false);
  }

  async function goToStep(step: Step) {
    // Refresh project to get latest has_worldview / has_outline before switching
    // autoStep=false: don't override the step we're switching to
    await refreshProject();
    if (step === "writing" && !project?.has_outline) return;
    setActiveStep(step);
  }

  if (loading) return <div className="empty-state">加载中...</div>;
  if (loadError) {
    return (
      <div className="card empty-state" role="alert">
        <h2>项目暂时无法加载</h2>
        <p>{loadError}</p>
        <button className="btn btn-primary" onClick={() => id && loadProject(id)}>
          重新加载
        </button>
      </div>
    );
  }
  if (!project) return <div className="empty-state">项目不存在</div>;

  const steps = [
    { key: "worldview" as Step, label: "世界观与设定", num: 1 },
    ...(project.has_outline
      ? [{ key: "writing" as Step, label: "章节写作", num: 2 }]
      : []),
  ];

  const currentStepIndex = steps.findIndex((s) => s.key === activeStep);

  return (
    <div>
      <button className="btn-back" onClick={() => navigate("/")}>← 返回项目列表</button>

      <div className="page-header">
        <h1>{project.title}</h1>
        <p>
          {project.genre} · {project.total_chapters} 章 · 单章 {project.chapter_word_count} 字 · {project.style_intensity}
        </p>
        <Link className="btn btn-secondary project-lore-link" to={`/project/${project.id}/lore`}>
          打开设定仓库
        </Link>
      </div>

      {/* Workflow steps */}
      <div className="workflow-steps">
        {steps.map((step, i) => (
          <button
            type="button"
            key={step.key}
            className={`workflow-step ${i < currentStepIndex ? "completed" : i === currentStepIndex ? "active" : ""}`}
            onClick={() => goToStep(step.key)}
            aria-current={i === currentStepIndex ? "step" : undefined}
          >
            <div className="workflow-step-circle">{step.num}</div>
            <div className="workflow-step-label">{step.label}</div>
            {i < steps.length - 1 && <span className="workflow-step-arrow">›</span>}
          </button>
        ))}
      </div>

      {!project.has_outline && (
        <div className="card project-planning-notice" role="status">
          <h2>先完善设定仓库</h2>
          <p>自动大纲生成已经停止。新的篇章与章节规划将在第二阶段开放；现阶段请先保存世界观并整理独立设定模块。</p>
          <Link className="btn btn-primary" to={`/project/${project.id}/lore`}>
            打开设定仓库
          </Link>
        </div>
      )}

      {/* Progress panel — only show on writing step */}
      {activeStep === "writing" && project.has_outline && (
        <ProgressPanel projectId={project.id} />
      )}

      {activeStep === "worldview" && (
        <WorldviewEditor
          projectId={project.id}
          hasWorldview={project.has_worldview}
          genre={project.genre}
          onComplete={async () => {
            await refreshProject();
            navigate(`/project/${project.id}/lore`);
          }}
          onBack={() => navigate("/")}
        />
      )}

      {activeStep === "writing" && project.has_outline && (
        <ChapterWriter
          projectId={project.id}
          totalChapters={project.total_chapters}
          onProgress={refreshProject}
          onBack={() => setActiveStep("worldview")}
        />
      )}
    </div>
  );
}
