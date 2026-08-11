import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as apiModule from "@/services/api";
import type { LoreElementListItem } from "@/types/lore";
import type { PlanningAssignedElementSnapshot, PlanningAssignmentScopeResponse } from "@/types/planning";
import PlanningLoreAssignments from "./PlanningLoreAssignments";

const novelScope = { scope_type: "novel" as const, scope_target_id: "project-1", title: "整部小说", status: "active" as const, part_id: null };
const chapterScope = { scope_type: "chapter" as const, scope_target_id: "chapter-1", title: "第一章", status: "active" as const, part_id: "part-1" };

function element(id: string, name: string): PlanningAssignedElementSnapshot {
  return {
    id, name, summary: `${name}摘要`,
    type: { id: `type-${id}`, key: "character", display_name: "角色", status: "active" },
    confirmation_status: "confirmed", lifecycle_status: "active", enabled: true, merged_into_element_id: null,
  };
}

const directElement = element("element-direct", "林岚");
const inheritedElement = element("element-inherited", "北境盟约");
const invalidElement = element("element-invalid", "封印法则");

const response: PlanningAssignmentScopeResponse = {
  scope: chapterScope,
  assignment_version: 3,
  direct_assignments: [
    {
      id: "assignment-direct", element_id: directElement.id, scope: chapterScope, status: "active", lock_version: 1,
      assigned_at_content_version: 1, current_content_version: 2, content_changed_since_assignment: true,
      element: directElement, generation_eligible: true, ineligible_reasons: [], created_at: "", updated_at: "",
    },
    {
      id: "assignment-removed", element_id: inheritedElement.id, scope: chapterScope, status: "removed", lock_version: 2,
      assigned_at_content_version: 1, current_content_version: 1, content_changed_since_assignment: false,
      element: inheritedElement, generation_eligible: true, ineligible_reasons: [], created_at: "", updated_at: "",
    },
    {
      id: "assignment-invalid", element_id: invalidElement.id, scope: chapterScope, status: "active", lock_version: 1,
      assigned_at_content_version: 1, current_content_version: 1, content_changed_since_assignment: false,
      element: invalidElement, generation_eligible: false, ineligible_reasons: ["element_disabled"], created_at: "", updated_at: "",
    },
  ],
  effective_elements: [
    {
      element_id: directElement.id, current_content_version: 2, content_changed_since_any_assignment: true, element: directElement,
      direct_assignments: [{ assignment_id: "assignment-direct", scope: chapterScope, lock_version: 1, assigned_at_content_version: 1 }],
      inherited_from: [{ assignment_id: "assignment-novel-direct", scope: novelScope, lock_version: 1, assigned_at_content_version: 1 }],
      all_sources: [
        { assignment_id: "assignment-novel-direct", scope: novelScope, lock_version: 1, assigned_at_content_version: 1 },
        { assignment_id: "assignment-direct", scope: chapterScope, lock_version: 1, assigned_at_content_version: 1 },
      ],
      generation_eligible: true, ineligible_reasons: [],
    },
    {
      element_id: inheritedElement.id, current_content_version: 1, content_changed_since_any_assignment: false, element: inheritedElement,
      direct_assignments: [], inherited_from: [{ assignment_id: "assignment-novel", scope: novelScope, lock_version: 1, assigned_at_content_version: 1 }],
      all_sources: [{ assignment_id: "assignment-novel", scope: novelScope, lock_version: 1, assigned_at_content_version: 1 }],
      generation_eligible: true, ineligible_reasons: [],
    },
    {
      element_id: invalidElement.id, current_content_version: 1, content_changed_since_any_assignment: false, element: invalidElement,
      direct_assignments: [{ assignment_id: "assignment-invalid", scope: chapterScope, lock_version: 1, assigned_at_content_version: 1 }],
      inherited_from: [], all_sources: [{ assignment_id: "assignment-invalid", scope: chapterScope, lock_version: 1, assigned_at_content_version: 1 }],
      generation_eligible: false, ineligible_reasons: ["element_disabled"],
    },
  ],
  counts: { direct: 3, direct_active: 2, direct_removed: 1, effective: 3, generation_eligible: 2, ineligible: 1 },
};

function renderAssignments(overrides: Partial<React.ComponentProps<typeof PlanningLoreAssignments>> = {}, apiOverrides: Record<string, unknown> = {}) {
  const mocked = {
    ...apiModule.api,
    listLoreElements: vi.fn().mockResolvedValue({ items: [], total: 0, next_cursor: null, has_more: false, facets: {}, migration_status: {} }),
    getPlanningLoreAssignmentHistory: vi.fn().mockResolvedValue({ element_id: directElement.id, assignments: [] }),
    ...apiOverrides,
  };
  vi.spyOn(apiModule, "api", "get").mockReturnValue(mocked as typeof apiModule.api);
  const props = {
    projectId: "project-1",
    response,
    loading: false,
    error: "",
    writeDisabled: false,
    searchRefreshToken: 0,
    onReload: vi.fn(),
    onNavigateScope: vi.fn(),
    onOpenLore: vi.fn(() => true),
    onAssign: vi.fn(),
    onRemove: vi.fn(),
    onRestore: vi.fn(),
    ...overrides,
  };
  render(<MemoryRouter><PlanningLoreAssignments {...props} /></MemoryRouter>);
  return { props, mocked };
}

describe("PlanningLoreAssignments", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("puts every element in one section while preserving all source labels", () => {
    renderAssignments();
    const direct = screen.getByRole("heading", { name: "可用于生成 · 本范围直接" }).closest("section")!;
    const inherited = screen.getByRole("heading", { name: "可用于生成 · 从上级继承" }).closest("section")!;
    const invalid = screen.getByRole("heading", { name: "当前失效 · 不参与生成" }).closest("section")!;
    expect(within(direct).getByText("林岚")).toBeInTheDocument();
    expect(within(direct).getByText("来自整部小说")).toBeInTheDocument();
    expect(within(inherited).getByText("北境盟约")).toBeInTheDocument();
    expect(within(inherited).getByText("本范围直接记录已移除")).toBeInTheDocument();
    expect(within(screen.getByRole("heading", { name: "已从本范围移除" }).closest("section")!).queryByText("北境盟约")).not.toBeInTheDocument();
    expect(within(invalid).getByText("封印法则")).toBeInTheDocument();
    expect(within(invalid).getByText("设定已暂停用于生成")).toBeInTheDocument();
    expect(screen.getByText("可用直接 1")).toBeInTheDocument();
    expect(screen.getByText("可用继承 1")).toBeInTheDocument();
    expect(screen.getByText("仅移除记录 0")).toBeInTheDocument();
    expect(within(screen.getByText("林岚").closest("article")!).getByRole("button", { name: "前往整部小说调整" })).toBeInTheDocument();
  });

  it("confirms that remove affects only the current scope", async () => {
    const { props } = renderAssignments();
    const card = screen.getByText("林岚").closest("article")!;
    await userEvent.click(within(card).getByRole("button", { name: "从本范围移除" }));
    expect(within(card).getByRole("alertdialog", { name: "确认从本范围移除《林岚》？" })).toHaveAccessibleDescription(/只移除此章节的直接来源/);
    expect(within(card).getByText(/只移除此章节的直接来源/)).toBeInTheDocument();
    await userEvent.click(within(card).getByRole("button", { name: "确认从本范围移除" }));
    expect(props.onRemove).toHaveBeenCalledWith(expect.objectContaining({ id: "assignment-direct" }));
  });

  it("navigates inherited sources and restores the current scope's removed row", async () => {
    const { props } = renderAssignments();
    const card = screen.getByText("北境盟约").closest("article")!;
    await userEvent.click(within(card).getByRole("button", { name: "前往整部小说调整" }));
    expect(props.onNavigateScope).toHaveBeenCalledWith(novelScope);
    await userEvent.click(within(card).getByRole("button", { name: "恢复到本范围" }));
    expect(props.onRestore).toHaveBeenCalledWith(expect.objectContaining({ id: "assignment-removed" }));
  });

  it("keeps every inherited source navigable even when the element is also direct", async () => {
    const partScope = { scope_type: "part" as const, scope_target_id: "part-1", title: "北境篇", status: "active" as const, part_id: null };
    const multiSourceResponse: PlanningAssignmentScopeResponse = {
      ...response,
      effective_elements: response.effective_elements.map((item) => item.element_id === directElement.id ? {
        ...item,
        inherited_from: [
          ...item.inherited_from,
          { assignment_id: "assignment-part-direct", scope: partScope, lock_version: 1, assigned_at_content_version: 1 },
        ],
        all_sources: [
          ...item.all_sources,
          { assignment_id: "assignment-part-direct", scope: partScope, lock_version: 1, assigned_at_content_version: 1 },
        ],
      } : item),
    };
    const { props } = renderAssignments({ response: multiSourceResponse });
    const card = screen.getByText("林岚").closest("article")!;
    await userEvent.click(within(card).getByRole("button", { name: "前往整部小说调整" }));
    await userEvent.click(within(card).getByRole("button", { name: "前往篇章《北境篇》调整" }));
    expect(props.onNavigateScope).toHaveBeenNthCalledWith(1, novelScope);
    expect(props.onNavigateScope).toHaveBeenNthCalledWith(2, partScope);
  });

  it("searches formal lore and distinguishes new, inherited, direct, and ineligible candidates", async () => {
    const newItem: LoreElementListItem = {
      id: "element-new", type: { key: "place", display_name: "地点" }, name: "雾港", summary: "北方港口",
      confirmation_status: "confirmed", lifecycle_status: "active", enabled: true, generation_eligible: true,
      source_summary: "", current_version: 2, revision: 1, lock_version: 1, updated_at: "", relation_count: 0,
    };
    const invalidItem = { ...newItem, id: "element-search-invalid", name: "未确认遗迹", generation_eligible: false };
    const { props, mocked } = renderAssignments({}, {
      listLoreElements: vi.fn().mockResolvedValue({ items: [newItem, invalidItem], total: 2, next_cursor: null, has_more: false, facets: {}, migration_status: {} }),
    });
    await userEvent.click(screen.getByRole("button", { name: "添加设定" }));
    await waitFor(() => expect(mocked.listLoreElements).toHaveBeenCalledWith("project-1", expect.objectContaining({ confirmation_status: "confirmed", lifecycle_status: "active", enabled: true, limit: 20 })));
    await userEvent.click(await screen.findByRole("button", { name: "加入当前范围" }));
    expect(props.onAssign).toHaveBeenCalledWith(newItem);
    expect(screen.getByRole("button", { name: "当前不可分配" })).toBeDisabled();
  });

  it("loads history only when requested and never displays the internal actor id", async () => {
    const getHistory = vi.fn().mockResolvedValue({
      element_id: directElement.id,
      assignments: [{ id: "a", scope: chapterScope, status: "active", lock_version: 1, events: [{ id: "e", action: "assign", previous_status: null, new_status: "active", previous_lock_version: 0, new_lock_version: 1, element_content_version: 1, performed_by: "internal-user-id", created_at: "2026-08-11T00:00:00Z" }] }],
    });
    renderAssignments({}, { getPlanningLoreAssignmentHistory: getHistory });
    const card = screen.getByText("林岚").closest("article")!;
    expect(getHistory).not.toHaveBeenCalled();
    const historyToggle = within(card).getByRole("button", { name: "查看分配历史" });
    await userEvent.click(historyToggle);
    expect(await within(card).findByText("该设定的分配历史")).toBeInTheDocument();
    expect(within(card).getByRole("button", { name: "收起分配历史" })).toHaveAttribute("aria-expanded", "true");
    expect(getHistory).toHaveBeenCalledWith("project-1", "element-direct", expect.any(AbortSignal));
    expect(screen.queryByText("internal-user-id")).not.toBeInTheDocument();
  });

  it("keeps archived scopes readable while disabling add and restore", () => {
    renderAssignments({ response: { ...response, scope: { ...response.scope, status: "archived" } } });
    expect(screen.getByRole("button", { name: "添加设定" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "暂不能恢复" })).toBeDisabled();
    expect(screen.getByText(/当前范围已归档/)).toBeInTheDocument();
  });

  it("allows an otherwise eligible removed-only record to be restored", async () => {
    const removedOnly = {
      ...response.direct_assignments[1],
      generation_eligible: false,
      ineligible_reasons: ["assignment_removed"],
    };
    const removedResponse: PlanningAssignmentScopeResponse = {
      ...response,
      direct_assignments: [removedOnly],
      effective_elements: [],
      counts: { direct: 1, direct_active: 0, direct_removed: 1, effective: 0, generation_eligible: 0, ineligible: 0 },
    };
    const { props } = renderAssignments({ response: removedResponse });
    await userEvent.click(screen.getByRole("button", { name: "恢复到本范围" }));
    expect(props.onRestore).toHaveBeenCalledWith(removedOnly);
    expect(screen.queryByText("当前状态不允许参与生成")).not.toBeInTheDocument();
  });
});
