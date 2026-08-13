import { useEffect, useState } from "react";
import { Link, useParams, useNavigate, useSearchParams } from "react-router-dom";
import { api } from "@/services/api";
import type { Project } from "@/types";
import WorldviewEditor from "@/components/WorldviewEditor";
import ChapterWriter from "@/components/ChapterWriter";
import ProgressPanel from "@/components/ProgressPanel";
import DemoGuide from "@/components/DemoGuide";
import { readDemoFixture } from "@/services/demoFixture";
import type { DemoFixtureCurrentResponse } from "@/types/demo";

type Step = "worldview" | "writing";

const MIGRATION_CATEGORIES = new Set([
  "characters", "geography", "factions", "power_system",
  "history", "conflicts", "special_settings",
]);

export default function ProjectDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const migrationFix = searchParams.get("migration_fix");
  const [migrationCategory, migrationIndexText, migrationItemFingerprint, migrationSourceChecksum] =
    migrationFix?.split(":") ?? [];
  const migrationIndex = Number(migrationIndexText);
  const isChecksum = (value: string | undefined) => /^[a-f0-9]{64}$/.test(value ?? "");
  const migrationTarget = typeof migrationCategory === "string" && MIGRATION_CATEGORIES.has(migrationCategory) &&
    Number.isInteger(migrationIndex) && migrationIndex >= 0 &&
    isChecksum(migrationItemFingerprint) && isChecksum(migrationSourceChecksum)
    ? {
        category: migrationCategory,
        index: migrationIndex,
        itemFingerprint: migrationItemFingerprint as string,
        sourceChecksum: migrationSourceChecksum as string,
      }
    : null;
  const migrationRequestInvalid = migrationFix !== null && migrationTarget === null;
  const [project, setProject] = useState<Project | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [activeStep, setActiveStep] = useState<Step>("worldview");
  const [demoFixture, setDemoFixture] = useState<DemoFixtureCurrentResponse | null>(null);

  useEffect(() => {
    if (id) loadProject(id);
  }, [id, migrationFix]);

  useEffect(() => {
    if (!id) return;
    const controller = new AbortController();
    void readDemoFixture(controller.signal).then((value) => setDemoFixture(value.state === "ready" && value.project_id === id ? value : null)).catch(() => setDemoFixture(null));
    return () => controller.abort();
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
        setActiveStep(migrationFix ? "worldview" : data.has_outline ? "writing" : "worldview");
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
        <Link className="btn btn-secondary project-planning-link" to={`/project/${project.id}/plan/chapters`}>
          打开章节规划
        </Link>
      </div>
      {demoFixture?.state === "ready" && <DemoGuide projectId={project.id} current={1} chapterId={demoFixture.chapter_id} elementId={demoFixture.element_id} foreshadowLifecycleId={demoFixture.foreshadow_lifecycle_id} />}

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
          <h2>建立篇章与章节结构</h2>
          <p>设定仓库准备完成后，可以自行创建篇章、章节并安全调整顺序；系统不会生成或覆盖旧大纲。</p>
          <Link className="btn btn-primary" to={`/project/${project.id}/plan/chapters`}>
            打开章节规划
          </Link>
        </div>
      )}

      {project.has_outline && activeStep === "writing" && (
        <p className="project-legacy-planning-note" role="status">
          已保留历史章节安排，可继续生成、编辑和导出章节；系统不会重新生成或覆盖历史规划。
        </p>
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
          migrationTarget={migrationTarget}
          migrationRequestInvalid={migrationRequestInvalid}
          onReturnToMigration={() => navigate(`/project/${project.id}/lore?migration=preview`)}
          onComplete={async () => {
            await refreshProject();
            navigate(`/project/${project.id}/lore`);
          }}
          onExtractionComplete={() => {
            navigate(`/project/${project.id}/lore?scope=review`);
          }}
          onBack={() => migrationTarget
            ? navigate(`/project/${project.id}/lore?migration=preview`)
            : navigate("/")}
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
