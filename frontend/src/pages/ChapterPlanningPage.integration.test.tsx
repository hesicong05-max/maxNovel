import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as apiModule from "@/services/api";
import { ApiError } from "@/services/api";
import type { LoreElementListItem } from "@/types/lore";
import type {
  NovelPlan,
  PlanningAssignmentCreateInput,
  PlanningAssignmentMutationReceipt,
  PlanningAssignmentScopeResponse,
  PlanningAssignmentSnapshot,
  PlanningAssignmentStateInput,
  PlanningChapterCreateInput,
  PlanningMutationReceipt,
  PlanningPartCreateInput,
  PlanningScopeSnapshot,
} from "@/types/planning";
import ChapterPlanningPage from "./ChapterPlanningPage";

vi.mock("@/components/AuthContext", () => ({
  useAuth: () => ({ user: { id: "user-1" } }),
}));

const loreItem: LoreElementListItem = {
  id: "element-1",
  type: { key: "character", display_name: "角色" },
  name: "林岚",
  summary: "谨慎的调查者",
  confirmation_status: "confirmed",
  lifecycle_status: "active",
  enabled: true,
  generation_eligible: true,
  source_summary: "世界观原稿",
  current_version: 4,
  revision: 1,
  lock_version: 1,
  updated_at: "2026-08-11T00:00:00Z",
  relation_count: 0,
};

const elementSnapshot = {
  id: loreItem.id,
  name: loreItem.name,
  summary: loreItem.summary,
  type: { id: "type-character", key: "character", display_name: "角色", status: "active" as const },
  confirmation_status: "confirmed" as const,
  lifecycle_status: "active" as const,
  enabled: true,
  merged_into_element_id: null,
};

function copy<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function createStatefulPlanningApi() {
  let plan: NovelPlan = {
    id: "plan-1",
    project_id: "project-1",
    status: "active",
    structure_version: 1,
    assignment_version: 1,
    created_at: "2026-08-11T00:00:00Z",
    updated_at: "2026-08-11T00:00:00Z",
    parts: [],
  };
  const assignments = new Map<string, PlanningAssignmentSnapshot>();
  const structureExpectedVersions: number[] = [];
  const assignmentExpectedVersions: number[] = [];

  function scopeKey(scopeType: string, scopeTargetId: string): string {
    return `${scopeType}:${scopeTargetId}`;
  }

  function scope(scopeType: "novel" | "part" | "chapter", scopeTargetId: string): PlanningScopeSnapshot {
    if (scopeType === "novel") {
      return { scope_type: "novel", scope_target_id: plan.project_id, title: "整部小说", status: "active", part_id: null };
    }
    if (scopeType === "part") {
      const part = plan.parts.find((item) => item.id === scopeTargetId)!;
      return { scope_type: "part", scope_target_id: part.id, title: part.title, status: part.status, part_id: null };
    }
    const parent = plan.parts.find((item) => item.chapters.some((chapter) => chapter.id === scopeTargetId))!;
    const chapter = parent.chapters.find((item) => item.id === scopeTargetId)!;
    return { scope_type: "chapter", scope_target_id: chapter.id, title: chapter.title, status: chapter.status, part_id: parent.id };
  }

  function assignmentResponse(
    scopeType: "novel" | "part" | "chapter",
    scopeTargetId: string
  ): PlanningAssignmentScopeResponse {
    const currentScope = scope(scopeType, scopeTargetId);
    const direct = [...assignments.values()].filter((item) => (
      item.scope.scope_type === scopeType && item.scope.scope_target_id === scopeTargetId
    ));
    const activeSources = [...assignments.values()].filter((item) => {
      if (item.status !== "active") return false;
      if (item.scope.scope_type === "novel") return true;
      if (scopeType === "part") return item.scope.scope_type === "part" && item.scope.scope_target_id === scopeTargetId;
      if (scopeType === "chapter") {
        return (item.scope.scope_type === "chapter" && item.scope.scope_target_id === scopeTargetId)
          || (item.scope.scope_type === "part" && item.scope.scope_target_id === currentScope.part_id);
      }
      return false;
    });
    const directSources = activeSources.filter((item) => (
      item.scope.scope_type === scopeType && item.scope.scope_target_id === scopeTargetId
    ));
    const inheritedSources = activeSources.filter((item) => !directSources.includes(item));
    const source = (item: PlanningAssignmentSnapshot) => ({
      assignment_id: item.id,
      scope: item.scope,
      lock_version: item.lock_version,
      assigned_at_content_version: item.assigned_at_content_version,
    });
    return {
      scope: currentScope,
      assignment_version: plan.assignment_version,
      direct_assignments: copy(direct),
      effective_elements: activeSources.length > 0 ? [{
        element_id: loreItem.id,
        current_content_version: loreItem.current_version,
        content_changed_since_any_assignment: false,
        element: elementSnapshot,
        direct_assignments: directSources.map(source),
        inherited_from: inheritedSources.map(source),
        all_sources: activeSources.map(source),
        generation_eligible: true,
        ineligible_reasons: [],
      }] : [],
      counts: {
        direct: direct.length,
        direct_active: direct.filter((item) => item.status === "active").length,
        direct_removed: direct.filter((item) => item.status === "removed").length,
        effective: activeSources.length > 0 ? 1 : 0,
        generation_eligible: activeSources.length > 0 ? 1 : 0,
        ineligible: 0,
      },
    };
  }

  const getPlanning = vi.fn<typeof apiModule.api.getPlanning>(async () => copy(plan));
  const getPlanningLoreAssignments = vi.fn<typeof apiModule.api.getPlanningLoreAssignments>(async (
    _projectId: string,
    scopeType: "novel" | "part" | "chapter",
    scopeTargetId: string
  ) => copy(assignmentResponse(scopeType, scopeTargetId)));
  const createPlanningPart = vi.fn<typeof apiModule.api.createPlanningPart>(async (_projectId: string, body: PlanningPartCreateInput) => {
    structureExpectedVersions.push(body.expected_structure_version);
    expect(body).toEqual({
      operation_key: expect.any(String),
      expected_structure_version: plan.structure_version,
      title: "第一篇",
      description: "",
    });
    const previousVersion = plan.structure_version;
    plan = {
      ...plan,
      structure_version: previousVersion + 1,
      parts: [{
        id: "part-1", project_id: plan.project_id, plan_id: plan.id, title: body.title, description: "",
        position: 0, status: "active", lock_version: 1, created_at: "", updated_at: "", chapters: [],
      }],
    };
    return {
      receipt_kind: "structure", receipt_id: "receipt-part-1", operation_key: body.operation_key,
      operation_type: "part_create", replayed: false, changed: true, project_id: plan.project_id,
      plan_id: plan.id, previous_structure_version: previousVersion, new_structure_version: plan.structure_version,
      affected_node: { id: "part-1" }, placement: null, structure: null, created_at: "2026-08-11T00:00:00Z",
    } satisfies PlanningMutationReceipt;
  });
  const createPlanningChapter = vi.fn<typeof apiModule.api.createPlanningChapter>(async (
    _projectId: string,
    partId: string,
    body: PlanningChapterCreateInput
  ) => {
    structureExpectedVersions.push(body.expected_structure_version);
    expect(body).toEqual({
      operation_key: expect.any(String),
      expected_structure_version: plan.structure_version,
      title: "第一章",
      summary: "",
      target_word_count: null,
    });
    const previousVersion = plan.structure_version;
    const part = plan.parts.find((item) => item.id === partId)!;
    part.chapters.push({
      id: "chapter-1", project_id: plan.project_id, plan_id: plan.id, part_id: part.id,
      title: body.title, summary: "", target_word_count: null, position: 0, status: "active",
      lock_version: 1, created_at: "", updated_at: "",
    });
    plan = { ...plan, structure_version: previousVersion + 1, parts: [...plan.parts] };
    return {
      receipt_kind: "structure", receipt_id: "receipt-chapter-1", operation_key: body.operation_key,
      operation_type: "chapter_create", replayed: false, changed: true, project_id: plan.project_id,
      plan_id: plan.id, previous_structure_version: previousVersion, new_structure_version: plan.structure_version,
      affected_node: { id: "chapter-1" }, placement: null, structure: null, created_at: "2026-08-11T00:00:00Z",
    } satisfies PlanningMutationReceipt;
  });
  const createPlanningLoreAssignment = vi.fn<typeof apiModule.api.createPlanningLoreAssignment>(async (
    _projectId: string,
    body: PlanningAssignmentCreateInput
  ) => {
    assignmentExpectedVersions.push(body.expected_assignment_version);
    expect(body.expected_assignment_version).toBe(plan.assignment_version);
    expect(body).toEqual({
      operation_key: expect.any(String),
      expected_assignment_version: plan.assignment_version,
      element_id: loreItem.id,
      expected_element_content_version: loreItem.current_version,
      scope_type: body.scope_type,
      scope_target_id: body.scope_target_id,
    });
    const previousVersion = plan.assignment_version;
    const currentScope = scope(body.scope_type, body.scope_target_id);
    const key = scopeKey(body.scope_type, body.scope_target_id);
    const assignment: PlanningAssignmentSnapshot = {
      id: `assignment-${assignments.size + 1}`,
      element_id: loreItem.id,
      scope: currentScope,
      status: "active",
      lock_version: 1,
      assigned_at_content_version: loreItem.current_version,
      current_content_version: loreItem.current_version,
      content_changed_since_assignment: false,
      element: elementSnapshot,
      generation_eligible: true,
      ineligible_reasons: [],
      created_at: "",
      updated_at: "",
    };
    assignments.set(key, assignment);
    plan = { ...plan, assignment_version: previousVersion + 1 };
    return {
      receipt_kind: "assignment", receipt_id: `receipt-${assignment.id}`, operation_key: body.operation_key,
      operation_type: "assignment_create", replayed: false, changed: true, project_id: plan.project_id,
      plan_id: plan.id, previous_assignment_version: previousVersion, new_assignment_version: plan.assignment_version,
      assignment: copy(assignment), event_id: `event-${assignment.id}`, created_at: "2026-08-11T00:00:00Z",
    } satisfies PlanningAssignmentMutationReceipt;
  });
  const changePlanningLoreAssignmentState = vi.fn<typeof apiModule.api.changePlanningLoreAssignmentState>(async (
    _projectId: string,
    assignmentId: string,
    action: "remove" | "restore",
    body: PlanningAssignmentStateInput
  ) => {
    assignmentExpectedVersions.push(body.expected_assignment_version);
    expect(body.expected_assignment_version).toBe(plan.assignment_version);
    const entry = [...assignments.entries()].find(([, item]) => item.id === assignmentId)!;
    expect(body).toEqual({
      operation_key: expect.any(String),
      expected_assignment_version: plan.assignment_version,
      expected_lock_version: entry[1].lock_version,
      scope_type: entry[1].scope.scope_type,
      scope_target_id: entry[1].scope.scope_target_id,
    });
    const previousVersion = plan.assignment_version;
    const assignment: PlanningAssignmentSnapshot = {
      ...entry[1],
      status: action === "remove" ? "removed" : "active",
      lock_version: entry[1].lock_version + 1,
      generation_eligible: action === "restore",
      ineligible_reasons: action === "remove" ? ["assignment_removed"] : [],
    };
    assignments.set(entry[0], assignment);
    plan = { ...plan, assignment_version: previousVersion + 1 };
    return {
      receipt_kind: "assignment", receipt_id: `receipt-${action}-${assignment.id}`, operation_key: body.operation_key,
      operation_type: action === "remove" ? "assignment_remove" : "assignment_restore",
      replayed: false, changed: true, project_id: plan.project_id, plan_id: plan.id,
      previous_assignment_version: previousVersion, new_assignment_version: plan.assignment_version,
      assignment: copy(assignment), event_id: `event-${action}-${assignment.id}`, created_at: "2026-08-11T00:00:00Z",
    } satisfies PlanningAssignmentMutationReceipt;
  });

  const statefulApi = {
    ...apiModule.api,
    getPlanning,
    getPlanningLoreAssignments,
    getPlanningOperation: vi.fn<typeof apiModule.api.getPlanningOperation>().mockRejectedValue(new ApiError(404, { detail: "not found" })),
    createPlanningPart,
    createPlanningChapter,
    createPlanningLoreAssignment,
    changePlanningLoreAssignmentState,
    listLoreElements: vi.fn<typeof apiModule.api.listLoreElements>().mockResolvedValue({
      items: [loreItem], total: 1, next_cursor: null, has_more: false,
      facets: { types: [], confirmation_statuses: [], sources: [], lifecycle_statuses: [], enabled_statuses: [], relation_statuses: [] },
      migration_status: { storage_mode: "relational", state: "ready", read_only: false },
    }),
  } satisfies typeof apiModule.api;

  return {
    api: statefulApi,
    structureExpectedVersions,
    assignmentExpectedVersions,
  };
}

describe("ChapterPlanningPage integrated planning journey", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    sessionStorage.clear();
  });

  it("keeps structure and assignment versions authoritative through direct, inherited, remove, and restore transitions", async () => {
    const stateful = createStatefulPlanningApi();
    vi.spyOn(apiModule, "api", "get").mockReturnValue(stateful.api);
    render(
      <MemoryRouter initialEntries={["/project/project-1/plan/chapters"]}>
        <Routes><Route path="/project/:id/plan/chapters" element={<ChapterPlanningPage />} /></Routes>
      </MemoryRouter>
    );

    await userEvent.click(await screen.findByRole("button", { name: "新建篇章" }));
    await userEvent.type(screen.getByLabelText("篇章名称"), "第一篇");
    await userEvent.click(screen.getByRole("button", { name: "创建" }));
    await userEvent.click(await screen.findByRole("button", { name: "第一篇" }));
    await userEvent.type(screen.getByLabelText("新章节名称"), "第一章");
    await userEvent.click(screen.getByRole("button", { name: "添加章节" }));
    expect(await screen.findByRole("button", { name: "第一章" })).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "整部小说" }));
    await userEvent.click(await screen.findByRole("button", { name: "添加设定" }));
    await userEvent.click(await screen.findByRole("button", { name: "加入当前范围" }));
    expect(await screen.findByText("《林岚》已加入整部小说。")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "第一篇" }));
    await userEvent.click(await screen.findByRole("button", { name: "添加设定" }));
    await userEvent.click(await screen.findByRole("button", { name: "设为本范围直接" }));
    expect(await screen.findByText("《林岚》已加入《第一篇》。")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "第一章" }));
    await userEvent.click(await screen.findByRole("button", { name: "添加设定" }));
    await userEvent.click(await screen.findByRole("button", { name: "设为本范围直接" }));
    expect(await screen.findByText("《林岚》已加入《第一章》。")).toBeInTheDocument();
    let card = screen.getByRole("heading", { name: "林岚" }).closest("article")!;
    expect(within(card).getByText("本范围直接")).toBeInTheDocument();
    expect(within(card).getByText("来自整部小说")).toBeInTheDocument();
    expect(within(card).getByText("来自篇章《第一篇》")).toBeInTheDocument();

    await userEvent.click(within(card).getByRole("button", { name: "从本范围移除" }));
    await userEvent.click(within(card).getByRole("button", { name: "确认从本范围移除" }));
    expect(await screen.findByText(/已从《第一章》移除/)).toBeInTheDocument();
    card = screen.getByRole("heading", { name: "林岚" }).closest("article")!;
    expect(within(card).getByText("本范围直接记录已移除")).toBeInTheDocument();
    expect(screen.getByText("可用继承 1")).toBeInTheDocument();

    await userEvent.click(within(card).getByRole("button", { name: "恢复到本范围" }));
    expect(await screen.findByText("《林岚》已恢复为《第一章》的直接设定。")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("可用直接 1")).toBeInTheDocument());

    expect(stateful.structureExpectedVersions).toEqual([1, 2]);
    expect(stateful.assignmentExpectedVersions).toEqual([1, 2, 3, 4, 5]);
    expect(stateful.api.getPlanning).toHaveBeenCalledTimes(8);
    expect(stateful.api.getPlanningLoreAssignments.mock.calls.length).toBeGreaterThanOrEqual(10);
  });
});
