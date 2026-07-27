import { useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { api } from "@/services/api";
import type { Project } from "@/types";
import WorldviewEditor from "@/components/WorldviewEditor";
import OutlineReview from "@/components/OutlineReview";
import ChapterWriter from "@/components/ChapterWriter";
import ProgressPanel from "@/components/ProgressPanel";

type Step = "worldview" | "outline" | "writing";

export default function ProjectDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [project, setProject] = useState<Project | null>(null);
  const [loading, setLoading] = useState(true);
  const [activeStep, setActiveStep] = useState<Step>("worldview");

  useEffect(() => {
    if (id) loadProject(id);
  }, [id]);

  async function loadProject(projectId: string) {
    try {
      const data = await api.getProject(projectId);
      setProject(data);
      if (data.status === "draft" || data.status === "worldview_set") {
        setActiveStep("worldview");
      } else if (data.status === "outline_pending" || data.status === "outline_confirmed") {
        setActiveStep("outline");
      } else {
        setActiveStep("writing");
      }
    } catch (e) {
      console.error("Failed to load project:", e);
    } finally {
      setLoading(false);
    }
  }

  async function refreshProject() {
    if (id) await loadProject(id);
  }

  if (loading) return <div className="empty-state">加载中...</div>;
  if (!project) return <div className="empty-state">项目不存在</div>;

  const steps = [
    { key: "worldview" as Step, label: "世界观", num: 1 },
    { key: "outline" as Step, label: "大纲", num: 2 },
    { key: "writing" as Step, label: "章节生成", num: 3 },
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
      </div>

      {/* Workflow steps */}
      <div className="workflow-steps">
        {steps.map((step, i) => (
          <div
            key={step.key}
            className={`workflow-step ${i < currentStepIndex ? "completed" : i === currentStepIndex ? "active" : ""}`}
            onClick={() => setActiveStep(step.key)}
          >
            <div className="workflow-step-circle">{step.num}</div>
            <div className="workflow-step-label">{step.label}</div>
            {i < steps.length - 1 && <span className="workflow-step-arrow">›</span>}
          </div>
        ))}
      </div>

      {/* Progress panel at top of writing step */}
      {activeStep === "writing" && project.has_outline && (
        <ProgressPanel projectId={project.id} />
      )}

      {/* Step content */}
      {activeStep === "worldview" && (
        <WorldviewEditor
          projectId={project.id}
          hasWorldview={project.has_worldview}
          genre={project.genre}
          onComplete={async () => { await refreshProject(); setActiveStep("outline"); }}
          onBack={() => navigate("/")}
        />
      )}

      {activeStep === "outline" && (
        <OutlineReview
          projectId={project.id}
          hasOutline={project.has_outline}
          projectStatus={project.status}
          onComplete={async () => { await refreshProject(); setActiveStep("writing"); }}
          onBack={async () => { await refreshProject(); setActiveStep("worldview"); }}
        />
      )}

      {activeStep === "writing" && (
        <ChapterWriter
          projectId={project.id}
          totalChapters={project.total_chapters}
          onProgress={refreshProject}
          onBack={() => setActiveStep("outline")}
        />
      )}
    </div>
  );
}
