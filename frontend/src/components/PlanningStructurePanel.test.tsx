import { createEvent, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { NovelPlan } from "@/types/planning";
import PlanningStructurePanel from "./PlanningStructurePanel";

const plan: NovelPlan = {
  id: "plan-1",
  project_id: "project-1",
  status: "active",
  structure_version: 4,
  assignment_version: 2,
  created_at: "2026-08-17T00:00:00Z",
  updated_at: "2026-08-17T00:00:00Z",
  parts: [
    {
      id: "part-1",
      project_id: "project-1",
      plan_id: "plan-1",
      title: "第一篇：雾港停摆与守夜人的长标题",
      description: "",
      position: 0,
      status: "active",
      lock_version: 1,
      created_at: "",
      updated_at: "",
      chapters: [
        { id: "chapter-1", project_id: "project-1", plan_id: "plan-1", part_id: "part-1", title: "第一章", summary: "", target_word_count: null, position: 0, status: "active", lock_version: 1, created_at: "", updated_at: "" },
        { id: "chapter-2", project_id: "project-1", plan_id: "plan-1", part_id: "part-1", title: "第二章", summary: "", target_word_count: null, position: 1, status: "active", lock_version: 1, created_at: "", updated_at: "" },
        { id: "chapter-archive", project_id: "project-1", plan_id: "plan-1", part_id: "part-1", title: "旧章节", summary: "", target_word_count: null, position: 2, status: "archived", lock_version: 1, created_at: "", updated_at: "" },
      ],
    },
    {
      id: "part-archive",
      project_id: "project-1",
      plan_id: "plan-1",
      title: "旧篇章",
      description: "",
      position: 1,
      status: "archived",
      lock_version: 1,
      created_at: "",
      updated_at: "",
      chapters: [],
    },
  ],
};

describe("PlanningStructurePanel", () => {
  it("shows active counts, archived labels, and the current chapter", async () => {
    render(
      <PlanningStructurePanel
        plan={plan}
        selected={{ kind: "chapter", id: "chapter-2" }}
        busy={false}
        onSelect={vi.fn()}
        onMovePart={vi.fn()}
        onMoveChapter={vi.fn()}
      />
    );

    expect(screen.getByText("1 个活动篇章")).toBeInTheDocument();
    expect(screen.getByText("2 个活动章节")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "第二章" })).toHaveAttribute("aria-current", "true");
    expect(screen.getByRole("button", { name: "旧章节，已归档" })).toHaveClass("is-archived");

    await userEvent.click(screen.getByText("已归档篇章（1）"));
    expect(screen.getByRole("button", { name: "旧篇章，已归档" })).toHaveClass("is-archived");
  });

  it("keeps selection and ordering callbacks exact", async () => {
    const onSelect = vi.fn();
    const onMoveChapter = vi.fn();
    render(
      <PlanningStructurePanel
        plan={plan}
        selected={{ kind: "novel", id: "project-1" }}
        busy={false}
        onSelect={onSelect}
        onMovePart={vi.fn()}
        onMoveChapter={onMoveChapter}
      />
    );

    expect(screen.getByRole("button", { name: "上移章节 第一章" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "下移章节 第二章" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "第一章" }));
    await userEvent.click(screen.getByRole("button", { name: "下移章节 第一章" }));

    expect(onSelect).toHaveBeenCalledWith({ kind: "chapter", id: "chapter-1" });
    expect(onMoveChapter).toHaveBeenCalledTimes(1);
    expect(onMoveChapter).toHaveBeenCalledWith("chapter-1", 1);
  });

  it("previews and submits one same-part chapter drop without mutating the plan", () => {
    const onDropChapter = vi.fn();
    render(
      <PlanningStructurePanel
        plan={plan}
        selected={{ kind: "novel", id: "project-1" }}
        busy={false}
        onSelect={vi.fn()}
        onMovePart={vi.fn()}
        onMoveChapter={vi.fn()}
        onDropChapter={onDropChapter}
      />
    );
    const source = screen.getByTestId("chapter-drag-handle-chapter-1");
    const target = screen.getByRole("button", { name: "第二章" }).closest("li")!;
    vi.spyOn(target, "getBoundingClientRect").mockReturnValue({
      top: 0, bottom: 200, left: 0, right: 200, width: 200, height: 200, x: 0, y: 0, toJSON: () => ({}),
    });
    const dataTransfer = { effectAllowed: "none", dropEffect: "none", setData: vi.fn() };

    fireEvent.dragStart(source, { dataTransfer });
    const dragOver = createEvent.dragOver(target, { dataTransfer });
    Object.defineProperty(dragOver, "clientY", { value: 75 });
    fireEvent(target, dragOver);
    expect(screen.getByText("放在《第二章》之前")).toBeInTheDocument();
    const drop = createEvent.drop(target, { dataTransfer });
    Object.defineProperty(drop, "clientY", { value: 175 });
    fireEvent(target, drop);
    fireEvent(target, drop);

    expect(dataTransfer.setData).toHaveBeenCalledWith("text/plain", "planning-chapter");
    expect(onDropChapter).toHaveBeenCalledTimes(1);
    expect(onDropChapter).toHaveBeenCalledWith({
      chapterId: "chapter-1",
      partId: "part-1",
      targetChapterId: "chapter-2",
      placement: "after",
      expectedStructureVersion: 4,
    });
    expect(plan.parts[0].chapters.map((chapter) => chapter.id)).toEqual(["chapter-1", "chapter-2", "chapter-archive"]);
  });

  it("keeps no-op, stale, and disabled dragging at zero submissions", () => {
    const onDropChapter = vi.fn();
    const props = {
      selected: { kind: "novel", id: "project-1" } as const,
      busy: false,
      onSelect: vi.fn(),
      onMovePart: vi.fn(),
      onMoveChapter: vi.fn(),
      onDropChapter,
    };
    const { rerender } = render(<PlanningStructurePanel {...props} plan={plan} />);
    const source = screen.getByTestId("chapter-drag-handle-chapter-2");
    const target = screen.getByRole("button", { name: "第一章" }).closest("li")!;
    vi.spyOn(target, "getBoundingClientRect").mockReturnValue({
      top: 0, bottom: 100, left: 0, right: 200, width: 200, height: 100, x: 0, y: 0, toJSON: () => ({}),
    });
    const dataTransfer = { effectAllowed: "none", dropEffect: "none", setData: vi.fn() };
    fireEvent.dragStart(source, { dataTransfer });
    fireEvent.dragOver(target, { dataTransfer, clientY: 75 });
    fireEvent.drop(target, { dataTransfer, clientY: 75 });
    expect(onDropChapter).not.toHaveBeenCalled();

    fireEvent.dragStart(screen.getByTestId("chapter-drag-handle-chapter-1"), { dataTransfer });
    rerender(<PlanningStructurePanel {...props} plan={{ ...plan, structure_version: 5 }} />);
    fireEvent.drop(target, { dataTransfer, clientY: 75 });
    expect(onDropChapter).not.toHaveBeenCalled();

    rerender(<PlanningStructurePanel {...props} plan={plan} reorderDisabledReason="请先保存或放弃当前修改，再调整顺序。" />);
    expect(screen.getByTestId("chapter-drag-handle-chapter-1")).toHaveAttribute("draggable", "false");
    expect(screen.queryByRole("button", { name: /拖动排序章节/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "下移章节 第一章" })).toBeDisabled();
    expect(screen.getByText("请先保存或放弃当前修改，再调整顺序。")).toBeInTheDocument();
  });

  it("rejects cross-part and archived drop targets without submitting", () => {
    const onDropChapter = vi.fn();
    const crossPartPlan: NovelPlan = {
      ...plan,
      parts: [
        plan.parts[0],
        {
          ...plan.parts[1],
          id: "part-2",
          title: "第二篇",
          status: "active",
          chapters: [{
            id: "chapter-3", project_id: "project-1", plan_id: "plan-1", part_id: "part-2",
            title: "第三章", summary: "", target_word_count: null, position: 0, status: "active",
            lock_version: 1, created_at: "", updated_at: "",
          }],
        },
      ],
    };
    render(
      <PlanningStructurePanel
        plan={crossPartPlan}
        selected={{ kind: "novel", id: "project-1" }}
        busy={false}
        onSelect={vi.fn()}
        onMovePart={vi.fn()}
        onMoveChapter={vi.fn()}
        onDropChapter={onDropChapter}
      />
    );
    const source = screen.getByTestId("chapter-drag-handle-chapter-1");
    const validTarget = screen.getByRole("button", { name: "第二章" }).closest("li")!;
    const target = screen.getByRole("button", { name: "第三章" }).closest("li")!;
    const dataTransfer = { effectAllowed: "none", dropEffect: "none", setData: vi.fn() };
    fireEvent.dragStart(source, { dataTransfer });
    vi.spyOn(validTarget, "getBoundingClientRect").mockReturnValue({
      top: 0, bottom: 100, left: 0, right: 200, width: 200, height: 100, x: 0, y: 0, toJSON: () => ({}),
    });
    fireEvent.dragOver(validTarget, { dataTransfer, clientY: 75 });
    expect(screen.getByText("放在《第二章》之后")).toBeInTheDocument();
    fireEvent.dragOver(target, { dataTransfer, clientY: 20 });
    fireEvent.drop(target, { dataTransfer, clientY: 20 });

    expect(onDropChapter).not.toHaveBeenCalled();
    expect(screen.queryByText(/放在《第三章》/)).not.toBeInTheDocument();
    expect(screen.queryByTestId("chapter-drag-handle-chapter-archive")).not.toBeInTheDocument();
  });
});
