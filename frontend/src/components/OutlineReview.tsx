/**
 * Deprecated compatibility shell.
 *
 * Automatic outline generation was retired in DEV-003D1. This file remains
 * temporarily only because deleting a tracked core file requires a separate,
 * path-specific Trash approval under AGENTS.md. It has no runtime imports.
 */
export default function OutlineReview() {
  return (
    <section aria-labelledby="outline-retired-title" role="status">
      <h2 id="outline-retired-title">篇章与章节规划</h2>
      <p>该能力将在第二阶段开放。现阶段请先完善世界观和独立设定模块。</p>
    </section>
  );
}
