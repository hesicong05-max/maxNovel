import { Link } from "react-router-dom";
import "@/styles/demo-guide.css";

interface Props { projectId: string; current: 1 | 2 | 3 | 4 | 5; chapterId?: string | null; elementId?: string | null; foreshadowLifecycleId?: string | null; }
const labels = ["项目总览", "设定仓库", "章节规划", "伏笔计划", "技术模拟"] as const;

export default function DemoGuide({ projectId, current, chapterId, elementId, foreshadowLifecycleId }: Props) {
  const hrefs = [
    `/project/${projectId}`,
    `/project/${projectId}/lore${elementId ? `?element=${encodeURIComponent(elementId)}` : ""}#demo-lore`,
    `/project/${projectId}/plan/chapters${chapterId ? `?scope=chapter&target=${encodeURIComponent(chapterId)}` : ""}#demo-planning`,
    `/project/${projectId}/plan/foreshadows${foreshadowLifecycleId ? `?lifecycle=${encodeURIComponent(foreshadowLifecycleId)}` : ""}#demo-foreshadow`,
    `/project/${projectId}/plan/chapters${chapterId ? `?scope=chapter&target=${encodeURIComponent(chapterId)}` : ""}#demo-technical-generation`,
  ];
  return (
    <nav className="demo-guide" aria-label="技术演示五步导览">
      <p><strong>普通用户技术演示</strong><span>固定样例 · 不调用 AI · 不产生模型费用</span></p>
      <ol>{labels.map((label, index) => {
        const step = index + 1;
        return <li key={label}><Link to={hrefs[index]} aria-current={step === current ? "step" : undefined}><span>{step}</span>{label}</Link></li>;
      })}</ol>
    </nav>
  );
}
