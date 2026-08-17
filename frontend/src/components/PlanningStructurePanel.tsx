import { useEffect, useRef, useState, type DragEvent } from "react";
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
  onDropChapter?: (input: {
    chapterId: string;
    partId: string;
    targetChapterId: string;
    placement: "before" | "after";
    expectedStructureVersion: number;
  }) => void;
  reorderDisabledReason?: string;
}

interface ChapterDragSnapshot {
  chapterId: string;
  partId: string;
  structureVersion: number;
  chapterIds: string[];
}

interface ChapterDropTarget {
  chapterId: string;
  partId: string;
  placement: "before" | "after";
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
  dragDisabled,
  dragging,
  dropPlacement,
  dropLabel,
  onDragStart,
  onDragOver,
  onDrop,
  onDragLeave,
  onDragEnd,
}: {
  chapter: PlanningChapter;
  selected: boolean;
  busy: boolean;
  isFirst: boolean;
  isLast: boolean;
  onSelect: () => void;
  onMove: (direction: -1 | 1) => void;
  dragDisabled: boolean;
  dragging: boolean;
  dropPlacement: "before" | "after" | null;
  dropLabel: string | null;
  onDragStart: (event: DragEvent<HTMLElement>) => void;
  onDragOver: (event: DragEvent<HTMLLIElement>) => void;
  onDrop: (event: DragEvent<HTMLLIElement>) => void;
  onDragLeave: (event: DragEvent<HTMLLIElement>) => void;
  onDragEnd: () => void;
}) {
  return (
    <li
      className={`planning-tree__chapter${dragging ? " is-dragging" : ""}${dropPlacement ? ` is-drop-${dropPlacement}` : ""}`}
      onDragOver={onDragOver}
      onDrop={onDrop}
      onDragLeave={onDragLeave}
    >
      {chapter.status === "active" && (
        <span
          className={`planning-tree__drag-handle${dragDisabled ? " is-disabled" : ""}`}
          draggable={!dragDisabled}
          aria-hidden="true"
          data-testid={`chapter-drag-handle-${chapter.id}`}
          onDragStart={onDragStart}
          onDragEnd={onDragEnd}
        >
          ⠿
        </span>
      )}
      <NodeButton selected={selected} label={chapter.title} status={chapter.status} nodeId={chapter.id} onClick={onSelect} />
      {chapter.status === "active" && (
        <span className="planning-tree__moves" aria-label={`${chapter.title}排序操作`}>
          <button type="button" disabled={busy || dragDisabled || isFirst} onClick={() => onMove(-1)} aria-label={`上移章节 ${chapter.title}`}>↑</button>
          <button type="button" disabled={busy || dragDisabled || isLast} onClick={() => onMove(1)} aria-label={`下移章节 ${chapter.title}`}>↓</button>
        </span>
      )}
      {dropPlacement && dropLabel && <span className="planning-tree__drop-label">{dropLabel}</span>}
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
  onDropChapter,
  reorderDisabledReason,
}: Props) {
  const dragSnapshot = useRef<ChapterDragSnapshot | null>(null);
  const dropTargetRef = useRef<ChapterDropTarget | null>(null);
  const [draggingChapterId, setDraggingChapterId] = useState<string | null>(null);
  const [dropTarget, setDropTarget] = useState<ChapterDropTarget | null>(null);
  const [dragAnnouncement, setDragAnnouncement] = useState("");
  const activeParts = plan.parts.filter((part) => part.status === "active");
  const archivedParts = plan.parts.filter((part) => part.status === "archived");
  const activeChapterCount = activeParts.reduce(
    (total, part) => total + part.chapters.filter((chapter) => chapter.status === "active").length,
    0
  );
  const dragDisabled = busy || !!reorderDisabledReason;

  function clearDrag(message?: string) {
    dragSnapshot.current = null;
    dropTargetRef.current = null;
    setDraggingChapterId(null);
    setDropTarget(null);
    if (message) setDragAnnouncement(message);
  }

  function clearDropPreview() {
    if (!dropTargetRef.current) return;
    dropTargetRef.current = null;
    setDropTarget(null);
  }

  useEffect(() => {
    if (!dragSnapshot.current) return;
    if (dragSnapshot.current.structureVersion !== plan.structure_version || dragDisabled) {
      clearDrag("排序已取消；章节结构或页面状态发生变化。");
    }
  }, [plan.structure_version, dragDisabled]);

  function beginDrag(event: DragEvent<HTMLElement>, part: PlanningPart, chapter: PlanningChapter) {
    if (dragDisabled || part.status !== "active" || chapter.status !== "active") {
      event.preventDefault();
      return;
    }
    const chapterIds = part.chapters
      .filter((item) => item.status === "active")
      .map((item) => item.id);
    dragSnapshot.current = {
      chapterId: chapter.id,
      partId: part.id,
      structureVersion: plan.structure_version,
      chapterIds,
    };
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", "planning-chapter");
    setDraggingChapterId(chapter.id);
    dropTargetRef.current = null;
    setDropTarget(null);
    setDragAnnouncement(`已开始拖动章节《${chapter.title}》。只能在当前篇章内调整。`);
  }

  function previewDrop(event: DragEvent<HTMLLIElement>, part: PlanningPart, chapter: PlanningChapter) {
    const snapshot = dragSnapshot.current;
    if (
      !snapshot || dragDisabled || part.status !== "active" || chapter.status !== "active"
      || snapshot.partId !== part.id || snapshot.chapterId === chapter.id
      || snapshot.structureVersion !== plan.structure_version
    ) {
      clearDropPreview();
      return;
    }
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
    const rect = event.currentTarget.getBoundingClientRect();
    const placement = event.clientY < rect.top + rect.height / 2 ? "before" : "after";
    const nextTarget = { chapterId: chapter.id, partId: part.id, placement } as const;
    dropTargetRef.current = nextTarget;
    if (
      dropTarget?.chapterId !== nextTarget.chapterId
      || dropTarget.partId !== nextTarget.partId
      || dropTarget.placement !== nextTarget.placement
    ) {
      setDropTarget(nextTarget);
      setDragAnnouncement(`将放在章节《${chapter.title}》${placement === "before" ? "之前" : "之后"}。`);
    }
  }

  function finishDrop(event: DragEvent<HTMLLIElement>, part: PlanningPart, chapter: PlanningChapter) {
    const snapshot = dragSnapshot.current;
    const previewTarget = dropTargetRef.current;
    const rect = event.currentTarget.getBoundingClientRect();
    const placement = event.clientY < rect.top + rect.height / 2 ? "before" : "after";
    clearDrag();
    if (
      !snapshot || !previewTarget || dragDisabled || previewTarget.chapterId !== chapter.id
      || snapshot.partId !== part.id || previewTarget.partId !== part.id
      || snapshot.structureVersion !== plan.structure_version
      || part.status !== "active" || chapter.status !== "active"
    ) return;
    event.preventDefault();
    const next = snapshot.chapterIds.filter((id) => id !== snapshot.chapterId);
    const targetIndex = next.indexOf(chapter.id);
    if (targetIndex < 0) return;
    next.splice(targetIndex + (placement === "after" ? 1 : 0), 0, snapshot.chapterId);
    if (next.every((id, index) => id === snapshot.chapterIds[index])) {
      setDragAnnouncement("章节仍在原位置，没有提交排序。");
      return;
    }
    setDragAnnouncement("正在保存章节顺序…");
    onDropChapter?.({
      chapterId: snapshot.chapterId,
      partId: snapshot.partId,
      targetChapterId: chapter.id,
      placement,
      expectedStructureVersion: snapshot.structureVersion,
    });
  }

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
              <button type="button" disabled={dragDisabled || index === 0} onClick={() => onMovePart(part.id, -1)} aria-label={`上移篇章 ${part.title}`}>↑</button>
              <button type="button" disabled={dragDisabled || index === activeParts.length - 1} onClick={() => onMovePart(part.id, 1)} aria-label={`下移篇章 ${part.title}`}>↓</button>
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
                dragDisabled={dragDisabled}
                dragging={draggingChapterId === chapter.id}
                dropPlacement={dropTarget?.chapterId === chapter.id ? dropTarget.placement : null}
                dropLabel={dropTarget?.chapterId === chapter.id
                  ? `放在《${chapter.title}》${dropTarget.placement === "before" ? "之前" : "之后"}` : null}
                onDragStart={(event) => beginDrag(event, part, chapter)}
                onDragOver={(event) => previewDrop(event, part, chapter)}
                onDrop={(event) => finishDrop(event, part, chapter)}
                onDragLeave={(event) => {
                  if (!event.currentTarget.contains(event.relatedTarget as Node | null)) clearDropPreview();
                }}
                onDragEnd={() => {
                  if (dragSnapshot.current) clearDrag("已取消章节拖动排序。");
                }}
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
                dragDisabled
                dragging={false}
                dropPlacement={null}
                dropLabel={null}
                onDragStart={(event) => event.preventDefault()}
                onDragOver={() => undefined}
                onDrop={() => undefined}
                onDragLeave={() => clearDropPreview()}
                onDragEnd={() => undefined}
              />
            ))}
          </ul>
        )}
      </li>
    );
  };

  return (
    <nav className="planning-tree" aria-label="篇章与章节结构">
      <p id="planning-drag-instructions" className="sr-only">
        桌面端可拖动把手调整同一篇章内的章节顺序。键盘或触屏请使用上移、下移按钮；跨篇章请使用章节详情中的移动菜单。
      </p>
      {dragAnnouncement && <p className="sr-only" role="status" aria-live="polite" aria-atomic="true">{dragAnnouncement}</p>}
      {reorderDisabledReason && <p className="planning-tree__reorder-disabled" role="status">{reorderDisabledReason}</p>}
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
