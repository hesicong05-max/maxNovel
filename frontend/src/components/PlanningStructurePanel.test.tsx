import { render, screen } from "@testing-library/react";
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
});
