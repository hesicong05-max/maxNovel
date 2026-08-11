import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import PlanningGenerationPreflight from "./PlanningGenerationPreflight";
import type { GenerationRunResponse } from "@/types/generation";
import type { NovelPlan, PlanningChapter, PlanningPart } from "@/types/planning";

const id = (value: string) => value.padEnd(32, value).slice(0, 32);
const projectId = id("project");
const planId = id("plan");
const partId = id("part");
const chapterId = id("chapter");
const firstId = id("first");
const secondId = id("second");
const typeId = id("type");
const now = "2026-08-11T05:00:00Z";

const chapter: PlanningChapter = { id: chapterId, project_id: projectId, plan_id: planId, part_id: partId, title: "第一章", summary: "雨夜相遇", target_word_count: 2000, position: 1, status: "active", lock_version: 4, created_at: now, updated_at: now };
const part: PlanningPart = { id: partId, project_id: projectId, plan_id: planId, title: "第一篇", description: "", position: 1, status: "active", lock_version: 1, created_at: now, updated_at: now, chapters: [chapter] };
const plan: NovelPlan = { id: planId, project_id: projectId, status: "active", structure_version: 2, assignment_version: 3, created_at: now, updated_at: now, parts: [part] };

function element(elementId: string, name: string, scope: "novel" | "part" | "chapter", scopeId: string, scopeTitle: string) {
  return {
    element_id: elementId,
    type: { id: typeId, key: "character", display_name: "角色", schema_revision: 1 },
    version: { id: id(`${name}-version`), element_id: elementId, type_id: typeId, version_no: 1, name, summary: `${name}摘要`, payload: { identity: `${name}身份`, long_text: "设定".repeat(80) }, field_states: { identity: "confirmed" }, source_id: id(`${name}-source`) },
    assignment_sources: [{ assignment_id: id(`${name}-assignment`), scope_type: scope, scope_target_id: scopeId, scope_title: scopeTitle, assignment_lock_version: 1, assigned_at_content_version: 1 }],
  };
}

const run: GenerationRunResponse = {
  id: id("run"), project_id: projectId, plan_id: planId, planning_chapter_id: chapterId,
  operation_key: "planning:generation_prepare:12345678", replayed: false,
  status: "prepared", execution_mode: "preflight_only", ai_invoked: false, billing_effect: "none",
  structure_version: 2, assignment_version: 3, chapter_lock_version: 4, context_schema_version: 1,
  context_checksum: "b".repeat(64), context_size_bytes: 2048, created_at: now, updated_at: now,
  context_manifest: {
    schema_version: 1, project_id: projectId, plan_id: planId,
    versions: { structure: 2, assignment: 3, chapter_lock: 4 },
    part: { id: partId, title: "第一篇", description: "", position: 1, lock_version: 1 },
    chapter: { id: chapterId, title: "第一章", summary: "雨夜相遇", target_word_count: 2000, position: 1, lock_version: 4 },
    elements: [element(firstId, "沈星", "novel", projectId, "整部小说"), element(secondId, "林夜", "chapter", chapterId, "第一章")],
    relations: [{ relation_id: id("relation"), version: { id: id("relation-version"), relation_id: id("relation"), version_no: 2, source_element_id: firstId, target_element_id: secondId, relation_key: "ally", forward_label: "信任", reverse_label: "信任", description: "共同调查", metadata: { since: "第一章" }, status: "active" } }],
    warnings: [{ code: "LORE_CHANGED_SINCE_ASSIGNMENT", element_id: firstId }],
    foreshadow_actions: { supported: false, items: [] }, counts: { elements: 2, relations: 1, warnings: 1 },
  },
};

function props(overrides: Record<string, unknown> = {}) {
  return {
    plan, part, chapter, run, busy: false, loadingSaved: false, disabled: false,
    disabledReason: "", error: "", recoveryState: "idle" as const, stale: false,
    recovered: false, focusResultToken: 0, focusFeedbackToken: 0, hasPendingRecovery: false,
    onPrepare: vi.fn(), onCheckPending: vi.fn(),
    onRetryOriginal: vi.fn(), onFocusAssignments: vi.fn(), onClearSavedPointer: vi.fn(),
    onAbandonPending: vi.fn(),
    ...overrides,
  };
}

describe("PlanningGenerationPreflight", () => {
  it("shows the exact zero-cost boundary, full sources, relationship, and warnings without a generation action", () => {
    render(<PlanningGenerationPreflight {...props()} />);
    expect(screen.getByText("检查记录已保存")).toBeInTheDocument();
    expect(screen.getByText("AI 未调用")).toBeInTheDocument();
    expect(screen.getByText("模型费用：无")).toBeInTheDocument();
    expect(screen.getByText("正文：未创建或修改")).toBeInTheDocument();
    expect(screen.queryByText(/开始生成|正在生成|生成完成/)).not.toBeInTheDocument();

    fireEvent.click(screen.getByText("查看 2 项设定"));
    fireEvent.click(screen.getAllByText("查看全部分配来源")[0]);
    expect(screen.getByText("继承自整部小说")).toBeInTheDocument();
    expect(screen.getByText("本章节直接分配")).toBeInTheDocument();
    fireEvent.click(screen.getByText("查看 1 条关系"));
    expect(screen.getByText("沈星 信任 林夜")).toBeInTheDocument();
    expect(screen.getByText(/设定内容在分配后有更新/)).toBeInTheDocument();
  });

  it("keeps a missing by-key result on the original-request retry path", () => {
    const onRetryOriginal = vi.fn();
    render(<PlanningGenerationPreflight {...props({ run: null, recoveryState: "not_found", onRetryOriginal })} />);
    expect(screen.getByText(/只能使用原操作编号和原版本载荷/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "使用原请求安全重试" }));
    expect(onRetryOriginal).toHaveBeenCalledOnce();
    expect(screen.queryByRole("button", { name: "检查生成上下文" })).not.toBeInTheDocument();
  });

  it("labels an older snapshot and disables recheck when the chapter has unsaved edits", () => {
    render(<PlanningGenerationPreflight {...props({ stale: true, disabled: true, disabledReason: "当前章节有未保存修改。" })} />);
    expect(screen.getByText("基于旧版本")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重新检查当前上下文" })).toBeDisabled();
    expect(screen.getByText("当前章节有未保存修改。")).toBeInTheDocument();
  });
});
