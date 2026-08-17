import type { NovelPlan, PlanningChapter, PlanningPart } from "@/types/planning";

export type PlanningSelection =
  | { kind: "novel"; id: string }
  | { kind: "part"; id: string }
  | { kind: "chapter"; id: string };

interface Props {
  plan: NovelPlan;
  selected: PlanningSelection;
  busy: boolean;
  onSelect: (selection: PlanningSelection) => void;
  onMovePart: (partId: string, direction: -1 | 1) => void;
  onMoveChapter: (chapterId: string, direction: -1 | 1) => void;
}

function NodeButton({
  selected,
  label,
  status,
  nodeId,
  onClick,
}: {
  selected: boolean;
  label: string;
  status?: "active" | "archived";
  nodeId?: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      className={`planning-node${selected ? " is-selected" : ""}${status === "archived" ? " is-archived" : ""}`}
      aria-current={selected ? "true" : undefined}
      data-node-id={nodeId}
      aria-label={status === "archived" ? `${label}，已归档` : label}
      onClick={onClick}
    >
      <span>{label}</span>
      {status === "archived" && <small>已归档</small>}
    </button>
  );
}

function ChapterRow({
  chapter,
  selected,
  busy,
  isFirst,
  isLast,
  onSelect,
  onMove,
}: {
  chapter: PlanningChapter;
  selected: boolean;
  busy: boolean;
  isFirst: boolean;
  isLast: boolean;
  onSelect: () => void;
  onMove: (direction: -1 | 1) => void;
}) {
  return (
    <li className="planning-tree__chapter">
      <NodeButton selected={selected} label={chapter.title} status={chapter.status} nodeId={chapter.id} onClick={onSelect} />
      {chapter.status === "active" && (
        <span className="planning-tree__moves" aria-label={`${chapter.title}排序操作`}>
          <button type="button" disabled={busy || isFirst} onClick={() => onMove(-1)} aria-label={`上移章节 ${chapter.title}`}>↑</button>
          <button type="button" disabled={busy || isLast} onClick={() => onMove(1)} aria-label={`下移章节 ${chapter.title}`}>↓</button>
        </span>
      )}
    </li>
  );
}

export default function PlanningStructurePanel({
  plan,
  selected,
  busy,
  onSelect,
  onMovePart,
  onMoveChapter,
}: Props) {
  const activeParts = plan.parts.filter((part) => part.status === "active");
  const archivedParts = plan.parts.filter((part) => part.status === "archived");
  const activeChapterCount = activeParts.reduce(
    (total, part) => total + part.chapters.filter((chapter) => chapter.status === "active").length,
    0
  );

  const renderPart = (part: PlanningPart, index: number, active: boolean) => {
    const activeChapters = part.chapters.filter((chapter) => chapter.status === "active");
    const archivedChapters = part.chapters.filter((chapter) => chapter.status === "archived");
    return (
      <li key={part.id} className="planning-tree__part">
        <div className="planning-tree__part-row">
          <NodeButton
            selected={selected.kind === "part" && selected.id === part.id}
            label={part.title}
            status={part.status}
            nodeId={part.id}
            onClick={() => onSelect({ kind: "part", id: part.id })}
          />
          {active && (
            <span className="planning-tree__moves" aria-label={`${part.title}排序操作`}>
              <button type="button" disabled={busy || index === 0} onClick={() => onMovePart(part.id, -1)} aria-label={`上移篇章 ${part.title}`}>↑</button>
              <button type="button" disabled={busy || index === activeParts.length - 1} onClick={() => onMovePart(part.id, 1)} aria-label={`下移篇章 ${part.title}`}>↓</button>
            </span>
          )}
        </div>
        {part.chapters.length > 0 && (
          <ul className="planning-tree__chapters">
            {activeChapters.map((chapter, chapterIndex) => (
              <ChapterRow
                key={chapter.id}
                chapter={chapter}
                selected={selected.kind === "chapter" && selected.id === chapter.id}
                busy={busy}
                isFirst={chapterIndex === 0}
                isLast={chapterIndex === activeChapters.length - 1}
                onSelect={() => onSelect({ kind: "chapter", id: chapter.id })}
                onMove={(direction) => onMoveChapter(chapter.id, direction)}
              />
            ))}
            {archivedChapters.map((chapter) => (
              <ChapterRow
                key={chapter.id}
                chapter={chapter}
                selected={selected.kind === "chapter" && selected.id === chapter.id}
                busy={busy}
                isFirst
                isLast
                onSelect={() => onSelect({ kind: "chapter", id: chapter.id })}
                onMove={() => undefined}
              />
            ))}
          </ul>
        )}
      </li>
    );
  };

  return (
    <nav className="planning-tree" aria-label="篇章与章节结构">
      <p className="planning-tree__summary">
        <span>{activeParts.length} 个活动篇章</span>
        <span>{activeChapterCount} 个活动章节</span>
      </p>
      <NodeButton
        selected={selected.kind === "novel"}
        label="整部小说"
        nodeId={plan.project_id}
        onClick={() => onSelect({ kind: "novel", id: plan.project_id })}
      />
      {activeParts.length === 0 ? (
        <p className="planning-tree__empty">还没有篇章，请先新建第一个篇章。</p>
      ) : (
        <ol>{activeParts.map((part, index) => renderPart(part, index, true))}</ol>
      )}
      {archivedParts.length > 0 && (
        <details className="planning-tree__archived">
          <summary>已归档篇章（{archivedParts.length}）</summary>
          <ol>{archivedParts.map((part, index) => renderPart(part, index, false))}</ol>
        </details>
      )}
    </nav>
  );
}
