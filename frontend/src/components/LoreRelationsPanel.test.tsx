import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as apiModule from "@/services/api";
import type {
  LoreElementDetail,
  LoreElementListItem,
  LoreRelation,
  LoreRelationCreateInput,
} from "@/types/lore";
import LoreRelationsPanel from "./LoreRelationsPanel";

vi.mock("@/components/AuthContext", () => ({
  useAuth: () => ({
    user: { id: "user-1", email: "user@example.com", username: "tester" },
  }),
}));

const current: LoreElementDetail = {
  id: "element-current",
  type: { key: "character", display_name: "角色" },
  name: "林岚",
  summary: "追查者",
  confirmation_status: "confirmed",
  lifecycle_status: "active",
  enabled: true,
  generation_eligible: true,
  source_summary: "用户原文",
  current_version: 1,
  revision: 1,
  lock_version: 4,
  updated_at: "2026-08-06T00:00:00Z",
  relation_count: 1,
  payload: {},
  field_states: {},
  field_definitions: [],
  sources: [],
  version_count: 1,
  read_only: false,
};

const target: LoreElementListItem = {
  id: "element-target",
  type: { key: "faction", display_name: "阵营" },
  name: "星盟",
  summary: "北境组织",
  confirmation_status: "confirmed",
  lifecycle_status: "active",
  enabled: true,
  generation_eligible: true,
  source_summary: "用户原文",
  current_version: 1,
  revision: 1,
  lock_version: 7,
  updated_at: "2026-08-06T00:00:00Z",
  relation_count: 1,
};

const relation: LoreRelation = {
  id: "relation-1",
  source: {
    id: target.id,
    name: target.name,
    type: target.type,
    summary: target.summary,
    lifecycle_status: "active",
    enabled: true,
  },
  target: {
    id: current.id,
    name: current.name,
    type: current.type,
    summary: current.summary,
    lifecycle_status: "active",
    enabled: true,
  },
  relation_key: "member_of",
  forward_label: "成员包括",
  reverse_label: "隶属于",
  description: "用户确认的关系",
  metadata: {},
  status: "active",
  version_no: 1,
  lock_version: 1,
  created_at: "2026-08-06T00:00:00Z",
  updated_at: "2026-08-06T00:00:00Z",
};

function detailFrom(item: LoreElementListItem, lockVersion = item.lock_version): LoreElementDetail {
  return {
    ...item,
    lock_version: lockVersion,
    payload: {},
    field_states: {},
    field_definitions: [],
    sources: [],
    version_count: 1,
    read_only: false,
  };
}

function renderPanel(overrides: Partial<React.ComponentProps<typeof LoreRelationsPanel>> = {}) {
  return render(<LoreRelationsPanel
    projectId="project-1"
    element={current}
    writable
    onDirtyChange={vi.fn()}
    onBusyChange={vi.fn()}
    onMutationComplete={vi.fn()}
    onOpenElement={vi.fn()}
    {...overrides}
  />);
}

describe("LoreRelationsPanel", () => {
  beforeEach(() => {
    vi.spyOn(apiModule, "api", "get").mockReturnValue({
      ...apiModule.api,
      listLoreRelationTypes: vi.fn().mockResolvedValue({
        items: [{
          key: "member_of",
          display_name: "隶属",
          forward_label: "隶属于",
          reverse_label: "成员包括",
          symmetric: false,
        }],
      }),
      listLoreRelations: vi.fn().mockResolvedValue({
        items: [relation],
        next_cursor: null,
        has_more: false,
        total: 1,
      }),
      listLoreElements: vi.fn().mockResolvedValue({
        items: [target],
        next_cursor: null,
        has_more: false,
        total: 1,
        facets: {
          types: [], confirmation_statuses: [], sources: [],
          lifecycle_statuses: [], enabled_statuses: [], relation_statuses: [],
        },
        migration_status: { storage_mode: "relational", state: "ready", read_only: false },
      }),
      getLoreElement: vi.fn().mockImplementation((_projectId: string, elementId: string) => (
        Promise.resolve(elementId === current.id ? current : detailFrom(target))
      )),
      getLoreRelation: vi.fn().mockResolvedValue(relation),
      createLoreRelation: vi.fn(),
      updateLoreRelation: vi.fn(),
      changeLoreRelationState: vi.fn(),
    });
  });

  it("renders the reverse label from the current element viewpoint", async () => {
    const onOpenElement = vi.fn();
    renderPanel({ onOpenElement });

    expect(await screen.findByText((_, node) => (
      node?.classList.contains("lore-relation-sentence") === true &&
      node.textContent?.includes("林岚 隶属于 星盟") === true
    ))).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "星盟" }));
    expect(onOpenElement).toHaveBeenCalledWith(target.id);
  });

  it("creates a relation with endpoint versions and the backend catalog key", async () => {
    const user = userEvent.setup();
    const search = vi.fn().mockResolvedValue({
      items: [target], next_cursor: null, has_more: false, total: 1,
      facets: {
        types: [], confirmation_statuses: [], sources: [],
        lifecycle_statuses: [], enabled_statuses: [], relation_statuses: [],
      },
      migration_status: { storage_mode: "relational", state: "ready", read_only: false },
    });
    const create = vi.fn().mockImplementation((
      _projectId: string,
      _elementId: string,
      input: LoreRelationCreateInput,
    ) => Promise.resolve({ ...relation, source: relation.target, target: relation.source, replayed: false, operation_key: input.operation_key }));
    vi.spyOn(apiModule, "api", "get").mockReturnValue({
      ...apiModule.api,
      createLoreRelation: create,
      listLoreElements: search,
    });
    renderPanel();

    await user.click(await screen.findByRole("button", { name: "添加关系" }));
    fireEvent.change(screen.getByRole("searchbox", { name: "搜索目标设定" }), { target: { value: "星盟" } });
    await waitFor(() => expect(search).toHaveBeenCalled(), { timeout: 3000 });
    await user.click(await screen.findByRole("button", { name: /星盟.*阵营/ }, { timeout: 3000 }));
    await user.click(screen.getByRole("button", { name: "创建关系" }));

    await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
    const [, sourceId, input] = create.mock.calls[0];
    expect(sourceId).toBe(current.id);
    expect(input).toMatchObject({
      target_element_id: target.id,
      source_expected_version: 4,
      target_expected_version: 7,
      relation_type: "member_of",
    });
    expect(input.operation_key).toMatch(/^lore-relation:/);
  });

  it("refreshes stale endpoint versions before allowing the same operation to retry", async () => {
    const user = userEvent.setup();
    const search = vi.fn().mockResolvedValue({
      items: [target], next_cursor: null, has_more: false, total: 1,
      facets: {
        types: [], confirmation_statuses: [], sources: [],
        lifecycle_statuses: [], enabled_statuses: [], relation_statuses: [],
      },
      migration_status: { storage_mode: "relational", state: "ready", read_only: false },
    });
    const create = vi.fn()
      .mockRejectedValueOnce(new apiModule.ApiError(409, {
        detail: "关系端点已变化",
        code: "LORE_RELATION_ENDPOINT_CHANGED",
      }))
      .mockResolvedValueOnce({ ...relation, replayed: false });
    const getElement = vi.fn().mockImplementation((_projectId: string, elementId: string) => (
      Promise.resolve(elementId === current.id ? { ...current, lock_version: 5 } : detailFrom(target, 8))
    ));
    vi.spyOn(apiModule, "api", "get").mockReturnValue({
      ...apiModule.api,
      createLoreRelation: create,
      getLoreElement: getElement,
      listLoreElements: search,
    });
    renderPanel();

    await user.click(await screen.findByRole("button", { name: "添加关系" }));
    const searchbox = screen.getByRole("searchbox", { name: "搜索目标设定" });
    fireEvent.change(searchbox, { target: { value: "星" } });
    await waitFor(() => expect(search).toHaveBeenCalled(), { timeout: 3000 });
    await user.click(await screen.findByRole("button", { name: /星盟.*阵营/ }));
    await user.click(screen.getByRole("button", { name: "创建关系" }));
    const refresh = await screen.findByRole("button", { name: "载入最新端点版本" });
    const firstInput = create.mock.calls[0][2] as LoreRelationCreateInput;

    await user.click(refresh);
    await user.click(await screen.findByRole("button", { name: "使用相同内容安全重试" }));
    await waitFor(() => expect(create).toHaveBeenCalledTimes(2));
    const secondInput = create.mock.calls[1][2] as LoreRelationCreateInput;
    expect(secondInput.operation_key).toBe(firstInput.operation_key);
    expect(secondInput).toMatchObject({ source_expected_version: 5, target_expected_version: 8 });
  });

  it("archives a relation only after explicit confirmation", async () => {
    const changeState = vi.fn().mockResolvedValue({ ...relation, status: "archived" });
    vi.spyOn(apiModule, "api", "get").mockReturnValue({
      ...apiModule.api,
      changeLoreRelationState: changeState,
    });
    renderPanel();

    await userEvent.click(await screen.findByRole("button", { name: "归档关系" }));
    expect(changeState).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "确认" }));
    await waitFor(() => expect(changeState).toHaveBeenCalledWith(
      "project-1",
      relation.id,
      "archive",
      { expected_version: 1, reason: "" },
    ));
  });
});
