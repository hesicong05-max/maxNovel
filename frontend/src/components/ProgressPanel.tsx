import { useEffect, useState } from "react";
import { api } from "@/services/api";
import type { ProgressData } from "@/types";

interface Props {
  projectId: string;
}

export default function ProgressPanel({ projectId }: Props) {
  const [progress, setProgress] = useState<ProgressData | null>(null);

  useEffect(() => {
    loadProgress();
    const interval = setInterval(loadProgress, 5000);
    return () => clearInterval(interval);
  }, []);

  async function loadProgress() {
    try {
      const data = await api.getProgress(projectId);
      setProgress(data);
    } catch (e) {
      console.error("Failed to load progress:", e);
    }
  }

  if (!progress) return null;

  const revealPercent = progress.reveal_percentage;
  const chapterPercent = progress.total_chapters > 0
    ? Math.round((Math.min(progress.current_chapter, progress.total_chapters) / progress.total_chapters) * 100)
    : 0;

  return (
    <div className="card" style={{ marginBottom: "0.875rem" }}>
      <div className="stat-grid">
        {/* Worldview reveal progress */}
        <div className="stat-card">
          <div className="stat-label">世界观揭示</div>
          <div className="stat-value stat-value-gold">{progress.revealed_elements}<span style={{ fontSize: "14px", color: "var(--text-3)" }}>/{progress.total_elements}</span></div>
          <div className="progress-bar" style={{ marginTop: "0.375rem" }}>
            <div className="progress-fill progress-fill-gold" style={{ width: `${revealPercent}%` }} />
          </div>
          <div className="stat-sub">{revealPercent}%</div>
        </div>

        {/* Current phase */}
        <div className="stat-card">
          <div className="stat-label">当前阶段</div>
          <div className="stat-value stat-value-gold" style={{ fontSize: "16px" }}>{progress.current_phase}</div>
          <div className="stat-sub">第 {progress.current_chapter} / {progress.total_chapters} 章</div>
        </div>

        {/* Chapter progress */}
        <div className="stat-card">
          <div className="stat-label">章节进度</div>
          <div className="stat-value stat-value-red">{Math.min(progress.current_chapter, progress.total_chapters)}<span style={{ fontSize: "14px", color: "var(--text-3)" }}>/{progress.total_chapters}</span></div>
          <div className="progress-bar" style={{ marginTop: "0.375rem" }}>
            <div className="progress-fill progress-fill-red" style={{ width: `${chapterPercent}%` }} />
          </div>
          <div className="stat-sub">{chapterPercent}%</div>
        </div>

        {/* Pending foreshadows */}
        <div className="stat-card">
          <div className="stat-label">待回收伏笔</div>
          <div className="stat-value stat-value-red">{progress.pending_foreshadows}</div>
          <div className="stat-sub">{progress.pending_foreshadows > 5 ? "较多，注意回收" : "正常"}</div>
        </div>
      </div>
    </div>
  );
}
