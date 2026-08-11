import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "@/services/api";
import { parseForeshadowList } from "@/services/foreshadowContracts";
import type { ForeshadowStateCounts } from "@/types/foreshadow";

const emptyCounts: ForeshadowStateCounts = { unplanted: 0, planted: 0, pending_resolution: 0, resolved: 0 };

export default function ForeshadowPlanningSummary({ projectId }: { projectId: string }) {
  const [counts, setCounts] = useState<ForeshadowStateCounts | null>(null);
  const [archived, setArchived] = useState(0);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    setCounts(null); setArchived(0); setFailed(false);
    Promise.all([
      api.listForeshadows(projectId, { status: "active", limit: 1 }, controller.signal),
      api.listForeshadows(projectId, { status: "archived", limit: 1 }, controller.signal),
    ]).then(([activeRaw, archivedRaw]) => {
      const active = parseForeshadowList(activeRaw, projectId);
      const archivedItems = parseForeshadowList(archivedRaw, projectId);
      setCounts(active.counts);
      setArchived(Object.values(archivedItems.counts).reduce((sum, value) => sum + value, 0));
    }).catch(() => {
      if (!controller.signal.aborted) setFailed(true);
    });
    return () => controller.abort();
  }, [projectId]);

  const values = counts ?? emptyCounts;
  return <section className="planning-foreshadow-summary" aria-busy={!counts && !failed} aria-label="伏笔概况">
    <div><h2>伏笔概况</h2><p>计划不会自动成为正文事实；作者确认需在伏笔管理中单独记录。</p></div>
    {failed ? <p>伏笔概况暂时无法读取，不影响章节规划。</p> : <div className="planning-foreshadow-summary__counts"><span>未埋入 <strong>{values.unplanted}</strong></span><span>已埋入 <strong>{values.planted}</strong></span><span>待回收 <strong>{values.pending_resolution}</strong></span><span>已回收 <strong>{values.resolved}</strong></span><span>已归档 <strong>{archived}</strong></span></div>}
    <Link className="btn btn-secondary" to={`/project/${projectId}/plan/foreshadows`}>管理伏笔</Link>
  </section>;
}
