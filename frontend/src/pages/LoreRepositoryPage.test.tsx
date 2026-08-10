import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as apiModule from "@/services/api";
import { draftStorageKey, saveDraft, type DraftScope } from "@/services/maintenanceDrafts";
import type { LoreCandidateInboxResponse, LoreElementDetail, LoreListResponse, LoreOverview } from "@/types/lore";
import LoreRepositoryPage from "./LoreRepositoryPage";

vi.mock("@/components/AuthContext", () => ({
  useAuth: () => ({
    user: { id: "user-1", email: "user@example.com", username: "tester", is_admin: false, created_at: "2026-08-03T00:00:00Z" },
  }),
}));

const overview: LoreOverview = {
  formal_total: 1,
  confirmed_active: 1,
  pending_review: 2,
  needs_attention: 1,
  disabled: 0,
  archived: 0,
  review_pending: 0,
  migration_status: { storage_mode: "relational", state: "ready", read_only: false },
  capabilities: {
    candidate_review: true,
    candidate_accept: false,
    formal_create: false,
    formal_conflict_tracking: false,
    formal_merge_preview: false,
    formal_merge_commit: false,
    search_fields: ["name", "summary"],
  },
  count_definitions: {},
};

function formalResponse(name = "林渊"): LoreListResponse {
  return {
    items: [{
      id: `item-${name}`,
      type: { key: "character", display_name: "角色" },
      name,
      summary: "来自寒川城的追查者",
      confirmation_status: "confirmed",
      lifecycle_status: "active",
      enabled: true,
      generation_eligible: true,
      source_summary: "用户原文",
      current_version: 1,
      revision: 1,
      lock_version: 1,
      updated_at: "2026-08-03T00:00:00Z",
      relation_count: 2,
    }],
    next_cursor: null,
    has_more: false,
    total: 1,
    facets: {
      types: [{ key: "character", label: "角色", count: 1 }],
      confirmation_statuses: [{ key: "confirmed", label: "已确认", count: 1 }],
      sources: [{ key: "manual", label: "手动创建", count: 1 }],
      lifecycle_statuses: [{ key: "active", label: "使用中", count: 1 }],
      enabled_statuses: [],
      relation_statuses: [],
    },
    migration_status: { storage_mode: "relational", state: "ready", read_only: false },
  };
}

function writableFormalDetail(overrides: Partial<LoreElementDetail> = {}): LoreElementDetail {
  return {
    ...formalResponse().items[0],
    lock_version: 4,
    payload: { appearance: "黑发" },
    field_states: { appearance: "provided" },
    field_definitions: [{
      key: "appearance",
      label: "外貌",
      control: "textarea",
      value_type: "text",
      help: "角色外貌",
      order: 10,
      required: false,
    }],
    sources: [{ id: "source-1", kind: "manual", label: "manual", is_primary: true, created_at: "2026-08-03T00:00:00Z", excerpt: "林渊黑发。", reference: null }],
    version_count: 1,
    read_only: false,
    ...overrides,
  };
}

const candidateResponse: LoreCandidateInboxResponse = {
  items: [{
    id: "candidate-1",
    batch_id: "batch-1",
    ordinal: 1,
    type_key: "location",
    type_display_name: "地点",
    name: "寒川城",
    summary: "北境城邦",
    payload: { description: "北境城邦", significance: null, geography: null },
    field_states: { description: "provided", significance: "needs_confirmation", geography: "unknown" },
    relation_suggestions: [],
    duplicate_conflict_suggestions: [],
    suggestion_resolutions: {},
    user_overrides: {},
    status: "pending_review",
    needs_attention: true,
    disabled_reasons: ["fields_need_confirmation"],
    revision: 1,
    accepted_element_id: null,
    error_code: null,
    can_accept: false,
    actions: {
      can_edit: true,
      can_accept: false,
      can_reject: true,
      can_open_element: false,
      disabled_reasons: { edit: [], accept: ["fields_need_confirmation"], reject: [] },
    },
    created_at: "2026-08-03T00:00:00Z",
    updated_at: "2026-08-03T00:00:00Z",
    evidence: [{
      id: "evidence-1",
      field_key: "name",
      label: "名称",
      value: "寒川城",
      extracted_value: "寒川城",
      current_value: "寒川城",
      current_state: "provided" as const,
      value_origin: "ai_extraction" as const,
      state: "provided" as const,
      excerpt: "林渊进入寒川城。",
      locator: {},
      excerpt_hash: "hash",
      source_hash: "source-hash",
      is_name: true,
    }],
  }],
  next_cursor: null,
  has_more: false,
  total: 1,
  applied_filters: {},
  query_signature: "sig-1",
};

const writableOverview: LoreOverview = {
  ...overview,
  migration_status: { storage_mode: "relational", state: "ready", read_only: false },
  capabilities: { ...overview.capabilities, candidate_accept: true, formal_create: true },
};

const loreTypesResponse = {
  items: [{
    id: "type-location",
    key: "location",
    display_name: "地点",
    description: "地点设定",
    is_builtin: true,
    schema_revision: 1,
    status: "active" as const,
    field_schema: [
      { key: "description", label: "描述", control: "textarea", value_type: "text", help: "地点描述", order: 10, required: false },
      { key: "significance", label: "重要性", control: "textarea", value_type: "text", help: "故事中的作用", order: 20, required: false },
      { key: "geography", label: "地理特征", control: "textarea", value_type: "text", help: "地理信息", order: 30, required: false },
    ],
    created_at: "2026-08-03T00:00:00Z",
    updated_at: "2026-08-03T00:00:00Z",
  }, {
    id: "type-custom",
    key: "custom_secret",
    display_name: "自定义秘密",
    description: "暂不支持候选编辑",
    is_builtin: false,
    schema_revision: 1,
    status: "active" as const,
    field_schema: [],
    created_at: "2026-08-03T00:00:00Z",
    updated_at: "2026-08-03T00:00:00Z",
  }],
  total: 2,
};

function relationApiMocks() {
  return {
    listLoreRelationTypes: vi.fn().mockResolvedValue({
      items: [{
        key: "related_to",
        display_name: "关联",
        forward_label: "关联于",
        reverse_label: "关联于",
        symmetric: true,
        custom: false,
      }],
      total: 1,
    }),
    listLoreRelations: vi.fn().mockResolvedValue({
      items: [],
      next_cursor: null,
      has_more: false,
      total: 0,
    }),
    getLoreRelation: vi.fn(),
    createLoreRelation: vi.fn(),
    updateLoreRelation: vi.fn(),
    changeLoreRelationState: vi.fn(),
  };
}

function readyCandidateResponse(): LoreCandidateInboxResponse {
  const candidate = candidateResponse.items[0];
  return {
    ...candidateResponse,
    items: [{
      ...candidate,
      needs_attention: false,
      disabled_reasons: [],
      field_states: { description: "provided", significance: "unknown", geography: "unknown" },
      actions: {
        can_edit: true,
        can_accept: true,
        can_reject: true,
        can_open_element: false,
        disabled_reasons: { edit: [], accept: [], reject: [] },
      },
      can_accept: true,
    }],
  };
}

function renderPage(entry = "/project/project-1/lore") {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <Routes>
        <Route path="/project/:id/lore" element={<LoreRepositoryPage />} />
      </Routes>
    </MemoryRouter>
  );
}

describe("LoreRepositoryPage", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.spyOn(apiModule, "api", "get").mockReturnValue({
      ...apiModule.api,
      ...relationApiMocks(),
      getLoreOverview: vi.fn().mockResolvedValue(overview),
      listLoreElements: vi.fn().mockResolvedValue(formalResponse()),
      getLoreElement: vi.fn().mockResolvedValue({
        ...formalResponse().items[0],
        payload: { appearance: "黑发" },
        field_states: { appearance: "provided" },
        field_definitions: [{ key: "appearance", label: "外貌", order: 10 }],
        sources: [{ id: "source-1", kind: "manual", label: "manual", is_primary: true, created_at: "2026-08-03T00:00:00Z", excerpt: "林渊黑发。", reference: null }],
        version_count: 1,
        read_only: true,
      }),
      listLoreCandidates: vi.fn().mockResolvedValue(candidateResponse),
      listLoreTypes: vi.fn().mockResolvedValue({ items: [], total: 0 }),
    });
  });

  it("reopens a stored migration request after refresh and only checks its original key", async () => {
    const scope: DraftScope = {
      userId: "user-1",
      projectId: "project-1",
      kind: "lore-migration",
      objectId: "legacy-to-relational-v1",
    };
    const operationKey = "lore-migration:refresh-0001";
    saveDraft(scope, {
      version: 1,
      phase: "outcome_unknown",
      checkedAt: "2026-08-07T08:00:00Z",
      legacyTotal: 2,
      input: {
        operation_key: operationKey,
        preview_schema_version: 1,
        mapping_version: 1,
        expected_source_checksum: "a".repeat(64),
        expected_semantic_result_checksum: "b".repeat(64),
        confirm_legacy_retained_no_automatic_rollback: true,
      },
    }, null);
    const getOperation = vi.fn().mockResolvedValue({
      id: "operation-1",
      project_id: "project-1",
      operation_key: operationKey,
      status: "ready",
      source_checksum: "a".repeat(64),
      preview_schema_version: 1,
      mapping_version: 1,
      semantic_result_checksum: "b".repeat(64),
      result_checksum: "c".repeat(64),
      migration_id: "migration-1",
      error_code: null,
      counts: { elements: 2 },
      started_at: "2026-08-07T08:00:01Z",
      updated_at: "2026-08-07T08:00:02Z",
      completed_at: "2026-08-07T08:00:02Z",
      replayed: true,
    });
    const commit = vi.fn();
    vi.spyOn(apiModule, "api", "get").mockReturnValue({
      ...apiModule.api,
      getLoreOverview: vi.fn().mockResolvedValue(overview),
      listLoreElements: vi.fn().mockResolvedValue(formalResponse()),
      listLoreCandidates: vi.fn().mockResolvedValue(candidateResponse),
      listLoreTypes: vi.fn().mockResolvedValue({ items: [], total: 0 }),
      getLoreMigrationPreview: vi.fn().mockResolvedValue(null),
      getLoreMigrationOperationByKey: getOperation,
      commitLoreMigration: commit,
    });

    renderPage();

    expect(await screen.findByRole("heading", { name: "设定仓库升级完成" })).toBeInTheDocument();
    expect(getOperation).toHaveBeenCalledWith("project-1", operationKey);
    expect(commit).not.toHaveBeenCalled();
  });

  it("opens a separate formal-lore review scope without mixing candidate attention", async () => {
    const listLoreReviews = vi.fn().mockResolvedValue({
      items: [], next_cursor: null, has_more: false, total: 0,
    });
    vi.spyOn(apiModule, "api", "get").mockReturnValue({
      ...apiModule.api,
      ...relationApiMocks(),
      getLoreOverview: vi.fn().mockResolvedValue({
        ...writableOverview,
        review_pending: 3,
        capabilities: { ...writableOverview.capabilities, formal_conflict_tracking: true },
      }),
      listLoreElements: vi.fn().mockResolvedValue(formalResponse()),
      listLoreCandidates: vi.fn().mockResolvedValue(candidateResponse),
      listLoreTypes: vi.fn().mockResolvedValue(loreTypesResponse),
      listLoreReviews,
    });
    renderPage();
    expect((await screen.findAllByText("待审核提取")).length).toBeGreaterThan(0);
    expect(screen.getByText("需要关注")).toBeInTheDocument();
    expect(screen.getByText("待核对线索")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "重复与冲突" }));
    expect(await screen.findByRole("heading", { name: "重复与冲突" })).toBeInTheDocument();
    expect(screen.getByText("所有线索只用于待核对，不会自动认定、合并或改写设定。")).toBeInTheDocument();
    await waitFor(() => expect(listLoreReviews).toHaveBeenCalled());
  });

  it("does not expose manual creation when the capability is closed", async () => {
    renderPage();
    await screen.findByText("林渊");

    expect(screen.queryByRole("button", { name: "新建设定" })).not.toBeInTheDocument();
    expect(screen.getByText(/尚未开放安全新建入口/)).toBeInTheDocument();
  });

  it("returns focus to the empty-state trigger after cancelling creation", async () => {
    const empty = { ...formalResponse(), items: [], total: 0 };
    vi.spyOn(apiModule, "api", "get").mockReturnValue({
      ...apiModule.api,
      getLoreOverview: vi.fn().mockResolvedValue({ ...writableOverview, formal_total: 0 }),
      listLoreElements: vi.fn().mockResolvedValue(empty),
      getLoreElement: vi.fn(),
      listLoreCandidates: vi.fn(),
      listLoreTypes: vi.fn().mockResolvedValue(loreTypesResponse),
    });
    renderPage();
    const trigger = await screen.findByRole("button", { name: "创建第一项设定" });
    await userEvent.click(trigger);
    expect(await screen.findByRole("heading", { name: "新建设定模块" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "放弃草稿" }));

    await waitFor(() => expect(trigger).toHaveFocus());
  });

  it("clears the local create draft before leaving through the mobile back action", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    vi.spyOn(apiModule, "api", "get").mockReturnValue({
      ...apiModule.api,
      getLoreOverview: vi.fn().mockResolvedValue(writableOverview),
      listLoreElements: vi.fn().mockResolvedValue(formalResponse()),
      getLoreElement: vi.fn(),
      listLoreCandidates: vi.fn(),
      listLoreTypes: vi.fn().mockResolvedValue(loreTypesResponse),
    });
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: "新建设定" }));
    const createPanel = (await screen.findByRole("heading", { name: "新建设定模块" })).closest<HTMLElement>(".lore-candidate-review")!;
    await waitFor(() => expect(within(createPanel).getByRole("combobox", { name: "类型" })).toHaveValue("location"));
    await userEvent.type(await screen.findByRole("textbox", { name: "名称" }), "待放弃角色");
    const scope: DraftScope = { userId: "user-1", projectId: "project-1", kind: "lore-create", objectId: "new" };
    await waitFor(() => expect(
      JSON.parse(localStorage.getItem(draftStorageKey(scope))!).payload.draft.name
    ).not.toBe(""));
    await userEvent.click(screen.getByRole("button", { name: /返回设定列表/ }));

    expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining("从本机移除"));
    expect(localStorage.getItem(draftStorageKey(scope))).toBeNull();
    expect(screen.getByText("选择一项查看详情")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("heading", { name: "正式设定" })).toHaveFocus());
  });

  it("does not overwrite a corrupt create record unless the user confirms clearing it", async () => {
    const scope: DraftScope = { userId: "user-1", projectId: "project-1", kind: "lore-create", objectId: "new" };
    localStorage.setItem(draftStorageKey(scope), "{broken draft");
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    vi.spyOn(apiModule, "api", "get").mockReturnValue({
      ...apiModule.api,
      getLoreOverview: vi.fn().mockResolvedValue(writableOverview),
      listLoreElements: vi.fn().mockResolvedValue(formalResponse()),
      getLoreElement: vi.fn(),
      listLoreCandidates: vi.fn(),
      listLoreTypes: vi.fn().mockResolvedValue(loreTypesResponse),
    });
    renderPage();
    expect(await screen.findByRole("status")).toHaveTextContent("无法校验");
    await userEvent.click(screen.getByRole("button", { name: "新建设定" }));

    expect(confirm).toHaveBeenCalled();
    expect(screen.queryByRole("heading", { name: "新建设定模块" })).not.toBeInTheDocument();
    expect(localStorage.getItem(draftStorageKey(scope))).toBe("{broken draft");
  });

  it("restores an expired unknown outcome with the original key and never auto-posts", async () => {
    const scope: DraftScope = { userId: "user-1", projectId: "project-1", kind: "lore-create", objectId: "new" };
    const operationKey = "lore-create:expired123456789";
    saveDraft(scope, {
      operationKey,
      phase: "outcome_unknown",
      draft: {
        typeKey: "location",
        name: "过期但待核对",
        summary: "",
        payload: { description: "" },
        fieldStates: { description: "unknown" },
        sourceReference: "",
        sourceExcerpt: "",
      },
      frozenInput: {
        operation_key: operationKey,
        type_key: "location",
        name: "过期但待核对",
        summary: "",
        payload: { description: null },
        field_states: { description: "unknown" },
        sources: [{ kind: "manual", reference: null, is_primary: true }],
      },
    }, null, { now: 100, ttlMs: 50 });
    const create = vi.fn();
    vi.spyOn(apiModule, "api", "get").mockReturnValue({
      ...apiModule.api,
      getLoreOverview: vi.fn().mockResolvedValue(writableOverview),
      listLoreElements: vi.fn().mockResolvedValue(formalResponse()),
      getLoreElement: vi.fn(),
      listLoreCandidates: vi.fn(),
      listLoreTypes: vi.fn().mockResolvedValue(loreTypesResponse),
      createLoreElement: create,
    });
    renderPage();

    expect(await screen.findByRole("heading", { name: "新建设定模块" })).toBeInTheDocument();
    expect(screen.getByText(/已恢复一份超过七天/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "核对上次创建结果" })).toBeInTheDocument();
    expect(create).not.toHaveBeenCalled();
    const restored = JSON.parse(localStorage.getItem(draftStorageKey(scope))!);
    expect(restored.payload.operationKey).toBe(operationKey);
  });

  it("shows formal lore, source detail, and the read-only capability boundary", async () => {
    renderPage();

    expect(await screen.findByText("林渊")).toBeInTheDocument();
    expect(screen.getByText(/兼容资料模式/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /林渊/ }));

    expect(await screen.findByText("黑发")).toBeInTheDocument();
    expect(screen.getByText("林渊黑发。")).toBeInTheDocument();
  });

  it("labels unlocated migration snapshots and loads only the matching retained raw source", async () => {
    const sourceChecksum = "d".repeat(64);
    const detail = writableFormalDetail({
      sources: [{
        id: "migration-source-1",
        kind: "migration",
        label: "migration",
        is_primary: true,
        created_at: "2026-08-03T00:00:00Z",
        excerpt: "{\"name\":\"林渊\"}",
        reference: "worldviews:worldview-1",
        locator: {
          legacy_category: "characters",
          legacy_index: 2,
          source_checksum: sourceChecksum,
          exact_excerpt_available: false,
          author_confirmed_unlocated: true,
        },
      }],
    });
    const getWorldview = vi.fn().mockResolvedValue({
      id: "worldview-1",
      characters: [],
      geography: [],
      factions: [],
      power_system: [],
      history: [],
      conflicts: [],
      special_settings: [],
      parsed_elements: [],
      source: "imported",
      source_checksum: sourceChecksum,
      raw_text: "这是作者保留的完整世界观原文。",
    });
    vi.spyOn(apiModule, "api", "get").mockReturnValue({
      ...apiModule.api,
      ...relationApiMocks(),
      getLoreOverview: vi.fn().mockResolvedValue(writableOverview),
      listLoreElements: vi.fn().mockResolvedValue(formalResponse()),
      getLoreElement: vi.fn().mockResolvedValue(detail),
      listLoreCandidates: vi.fn().mockResolvedValue(candidateResponse),
      listLoreTypes: vi.fn().mockResolvedValue(loreTypesResponse),
      getWorldview,
    });
    renderPage();

    await userEvent.click(await screen.findByRole("button", { name: /林渊/ }));
    expect(await screen.findByText("旧世界观 › 角色 › 第 3 项")).toBeInTheDocument();
    expect(screen.getByText("作者已核对完整原文，但迁移时未精确定位到具体段落。")).toBeInTheDocument();
    expect(screen.getByText("迁移时保留的结构化快照（非精确原文摘录）")).toBeInTheDocument();
    expect(screen.queryByText("这是作者保留的完整世界观原文。")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "查看当前保留的完整原文" }));
    expect(await screen.findByText("这是作者保留的完整世界观原文。")).toBeInTheDocument();
    expect(getWorldview).toHaveBeenCalledWith("project-1");
  });

  it.each([
    ["missing", null, false],
    ["invalid", "not-a-checksum", false],
    ["mismatched", "e".repeat(64), true],
  ])("refuses %s migration source checksums before showing retained raw text", async (_case, locatorChecksum, shouldFetch) => {
    const currentChecksum = "d".repeat(64);
    const detail = writableFormalDetail({
      sources: [{
        id: "migration-source-unsafe",
        kind: "migration",
        label: "migration",
        is_primary: true,
        created_at: "2026-08-03T00:00:00Z",
        excerpt: "{\"name\":\"林渊\"}",
        reference: "worldviews:worldview-1",
        locator: {
          legacy_category: "characters",
          legacy_index: 0,
          ...(locatorChecksum === null ? {} : { source_checksum: locatorChecksum }),
          exact_excerpt_available: false,
          author_confirmed_unlocated: true,
        },
      }],
    });
    const getWorldview = vi.fn().mockResolvedValue({
      id: "worldview-1",
      characters: [], geography: [], factions: [], power_system: [], history: [], conflicts: [], special_settings: [],
      parsed_elements: [], source: "imported", source_checksum: currentChecksum,
      raw_text: "绝不能在版本未验证时展示的原文。",
    });
    vi.spyOn(apiModule, "api", "get").mockReturnValue({
      ...apiModule.api,
      ...relationApiMocks(),
      getLoreOverview: vi.fn().mockResolvedValue(writableOverview),
      listLoreElements: vi.fn().mockResolvedValue(formalResponse()),
      getLoreElement: vi.fn().mockResolvedValue(detail),
      listLoreCandidates: vi.fn().mockResolvedValue(candidateResponse),
      listLoreTypes: vi.fn().mockResolvedValue(loreTypesResponse),
      getWorldview,
    });
    renderPage();

    await userEvent.click(await screen.findByRole("button", { name: /林渊/ }));
    await userEvent.click(screen.getByRole("button", { name: "查看当前保留的完整原文" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      shouldFetch ? "版本与迁移时不一致" : "缺少可验证的迁移版本"
    );
    expect(screen.queryByText("绝不能在版本未验证时展示的原文。")).not.toBeInTheDocument();
    expect(getWorldview).toHaveBeenCalledTimes(shouldFetch ? 1 : 0);
  });

  it("edits a formal element with lock_version and reloads the filtered list", async () => {
    const original = writableFormalDetail();
    const updated = writableFormalDetail({ name: "林渊·修订", lock_version: 5, current_version: 2 });
    const updatedList = formalResponse("林渊·修订");
    updatedList.items[0].id = original.id;
    updatedList.items[0].lock_version = 5;
    const update = vi.fn().mockResolvedValue({});
    vi.spyOn(apiModule, "api", "get").mockReturnValue({
      ...apiModule.api,
      getLoreOverview: vi.fn().mockResolvedValue(overview),
      listLoreElements: vi.fn().mockResolvedValueOnce(formalResponse()).mockResolvedValue(updatedList),
      getLoreElement: vi.fn().mockResolvedValueOnce(original).mockResolvedValue(updated),
      listLoreCandidates: vi.fn(),
      listLoreTypes: vi.fn(),
      updateLoreElement: update,
    });
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: /林渊/ }));
    await userEvent.click(await screen.findByRole("button", { name: "编辑内容" }));
    const name = screen.getByLabelText("名称");
    await userEvent.clear(name);
    await userEvent.type(name, "林渊·修订");
    await userEvent.click(screen.getByRole("button", { name: "保存修改" }));

    expect(update).toHaveBeenCalledWith("project-1", original.id, expect.objectContaining({
      expected_version: 4,
      name: "林渊·修订",
      payload: { appearance: "黑发" },
      field_states: { appearance: "provided" },
    }));
    expect(await screen.findByText(/正式设定修改已保存/)).toBeInTheDocument();
  });

  it("preserves a formal edit on 409 and requires latest-state review before retry", async () => {
    const original = writableFormalDetail();
    const latest = writableFormalDetail({ name: "服务器版本", lock_version: 5 });
    const update = vi.fn().mockRejectedValue(new apiModule.ApiError(409, {
      detail: "设定已被其他操作更新，请重新加载后重试",
      code: "LORE_VERSION_CONFLICT",
    }));
    const get = vi.fn().mockResolvedValueOnce(original).mockResolvedValueOnce(latest);
    vi.spyOn(apiModule, "api", "get").mockReturnValue({
      ...apiModule.api,
      getLoreOverview: vi.fn().mockResolvedValue(overview),
      listLoreElements: vi.fn().mockResolvedValue(formalResponse()),
      getLoreElement: get,
      listLoreCandidates: vi.fn(),
      listLoreTypes: vi.fn(),
      updateLoreElement: update,
    });
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: /林渊/ }));
    await userEvent.click(await screen.findByRole("button", { name: "编辑内容" }));
    const name = screen.getByLabelText("名称");
    await userEvent.clear(name);
    await userEvent.type(name, "本地草稿");
    await userEvent.click(screen.getByRole("button", { name: "保存修改" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("其他操作更新");
    expect(name).toHaveValue("本地草稿");
    expect(screen.getByRole("button", { name: "保存修改" })).toBeDisabled();
    expect(update).toHaveBeenCalledTimes(1);
    await userEvent.click(screen.getByRole("button", { name: "核对最新状态" }));
    expect(await screen.findByText(/服务器当前内容与本次提交不一致/)).toBeInTheDocument();
    expect(screen.getByText(/服务器版本 · 内容版本 1/)).toBeInTheDocument();
    expect(name).toHaveValue("本地草稿");
    expect(update).toHaveBeenCalledTimes(1);
  });

  it("keeps a formal edit intact while repository writes are frozen", async () => {
    const original = writableFormalDetail();
    const update = vi.fn().mockRejectedValue(new apiModule.ApiError(503, {
      detail: "项目正在维护，写入暂时冻结",
      code: "PROJECT_WRITE_FROZEN",
    }));
    vi.spyOn(apiModule, "api", "get").mockReturnValue({
      ...apiModule.api,
      getLoreOverview: vi.fn().mockResolvedValue(overview),
      listLoreElements: vi.fn().mockResolvedValue(formalResponse()),
      getLoreElement: vi.fn().mockResolvedValue(original),
      listLoreCandidates: vi.fn(),
      listLoreTypes: vi.fn(),
      updateLoreElement: update,
    });
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: /林渊/ }));
    await userEvent.click(await screen.findByRole("button", { name: "编辑内容" }));
    const name = screen.getByLabelText("名称");
    await userEvent.clear(name);
    await userEvent.type(name, "维护中的本地设定");
    await userEvent.click(screen.getByRole("button", { name: "保存修改" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("正在维护");
    expect(name).toHaveValue("维护中的本地设定");
    expect(screen.getByRole("button", { name: "保存修改" })).toBeEnabled();
    expect(update).toHaveBeenCalledTimes(1);
  });

  it("reconciles an unknown edit result with GET instead of repeating the mutation", async () => {
    const original = writableFormalDetail();
    const saved = writableFormalDetail({ name: "已由服务器保存", lock_version: 5, current_version: 2 });
    const update = vi.fn().mockRejectedValue(new TypeError("Failed to fetch"));
    const listAfter = formalResponse("已由服务器保存");
    listAfter.items[0].id = original.id;
    vi.spyOn(apiModule, "api", "get").mockReturnValue({
      ...apiModule.api,
      getLoreOverview: vi.fn().mockResolvedValue(overview),
      listLoreElements: vi.fn().mockResolvedValueOnce(formalResponse()).mockResolvedValue(listAfter),
      getLoreElement: vi.fn().mockResolvedValueOnce(original).mockResolvedValue(saved),
      listLoreCandidates: vi.fn(),
      listLoreTypes: vi.fn(),
      updateLoreElement: update,
    });
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: /林渊/ }));
    await userEvent.click(await screen.findByRole("button", { name: "编辑内容" }));
    const name = screen.getByLabelText("名称");
    await userEvent.clear(name);
    await userEvent.type(name, "已由服务器保存");
    await userEvent.click(screen.getByRole("button", { name: "保存修改" }));

    expect(await screen.findByText(/服务器内容与本地提交一致/)).toBeInTheDocument();
    expect(update).toHaveBeenCalledTimes(1);
  });

  it("does not create a content version when the formal draft is unchanged", async () => {
    const update = vi.fn();
    vi.spyOn(apiModule, "api", "get").mockReturnValue({
      ...apiModule.api,
      getLoreOverview: vi.fn().mockResolvedValue(overview),
      listLoreElements: vi.fn().mockResolvedValue(formalResponse()),
      getLoreElement: vi.fn().mockResolvedValue(writableFormalDetail()),
      listLoreCandidates: vi.fn(),
      listLoreTypes: vi.fn(),
      updateLoreElement: update,
    });
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: /林渊/ }));
    await userEvent.click(await screen.findByRole("button", { name: "编辑内容" }));

    expect(screen.getByRole("button", { name: "保存修改" })).toBeDisabled();
    expect(update).not.toHaveBeenCalled();
  });

  it("treats leading and trailing whitespace as a no-op", async () => {
    const update = vi.fn();
    vi.spyOn(apiModule, "api", "get").mockReturnValue({
      ...apiModule.api,
      getLoreOverview: vi.fn().mockResolvedValue(overview),
      listLoreElements: vi.fn().mockResolvedValue(formalResponse()),
      getLoreElement: vi.fn().mockResolvedValue(writableFormalDetail()),
      listLoreCandidates: vi.fn(),
      listLoreTypes: vi.fn(),
      updateLoreElement: update,
    });
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: /林渊/ }));
    await userEvent.click(await screen.findByRole("button", { name: "编辑内容" }));
    const name = screen.getByLabelText("名称");
    await userEvent.clear(name);
    await userEvent.type(name, "  林渊  ");

    expect(screen.getByRole("button", { name: "保存修改" })).toBeDisabled();
    expect(update).not.toHaveBeenCalled();
  });

  it("confirms a reversible generation pause and sends the current lock version once", async () => {
    const original = writableFormalDetail();
    const changed = writableFormalDetail({ enabled: false, generation_eligible: false, lock_version: 5 });
    const listAfter = formalResponse();
    listAfter.items[0].enabled = false;
    listAfter.items[0].generation_eligible = false;
    listAfter.items[0].lock_version = 5;
    const changeState = vi.fn().mockResolvedValue({});
    vi.spyOn(apiModule, "api", "get").mockReturnValue({
      ...apiModule.api,
      getLoreOverview: vi.fn().mockResolvedValue(overview),
      listLoreElements: vi.fn().mockResolvedValueOnce(formalResponse()).mockResolvedValue(listAfter),
      getLoreElement: vi.fn().mockResolvedValueOnce(original).mockResolvedValue(changed),
      listLoreCandidates: vi.fn(),
      listLoreTypes: vi.fn(),
      changeLoreElementState: changeState,
    });
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: /林渊/ }));
    const trigger = await screen.findByRole("button", { name: "暂停用于生成" });
    await userEvent.click(trigger);
    const confirmation = screen.getByRole("alertdialog", { name: "确认暂停用于生成" });
    await waitFor(() => expect(confirmation).toHaveFocus());
    await userEvent.type(screen.getByLabelText("原因（可选）"), "暂不参与本卷");
    await userEvent.click(screen.getByRole("button", { name: "确认暂停用于生成" }));

    expect(changeState).toHaveBeenCalledTimes(1);
    expect(changeState).toHaveBeenCalledWith("project-1", original.id, "disable", {
      expected_version: 4,
      reason: "暂不参与本卷",
    });
    expect(await screen.findByText("该设定已暂停用于生成。")).toBeInTheDocument();
  });

  it("reconciles an unknown state result without claiming who changed the state", async () => {
    const original = writableFormalDetail();
    const changed = writableFormalDetail({ enabled: false, generation_eligible: false, lock_version: 5 });
    const listAfter = formalResponse();
    listAfter.items[0].enabled = false;
    listAfter.items[0].generation_eligible = false;
    listAfter.items[0].lock_version = 5;
    const changeState = vi.fn().mockRejectedValue(new TypeError("Failed to fetch"));
    vi.spyOn(apiModule, "api", "get").mockReturnValue({
      ...apiModule.api,
      getLoreOverview: vi.fn().mockResolvedValue(overview),
      listLoreElements: vi.fn().mockResolvedValueOnce(formalResponse()).mockResolvedValue(listAfter),
      getLoreElement: vi.fn().mockResolvedValueOnce(original).mockResolvedValue(changed),
      listLoreCandidates: vi.fn(),
      listLoreTypes: vi.fn(),
      changeLoreElementState: changeState,
    });
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: /林渊/ }));
    await userEvent.click(await screen.findByRole("button", { name: "暂停用于生成" }));
    await userEvent.click(screen.getByRole("button", { name: "确认暂停用于生成" }));

    expect(await screen.findByText(/目标状态已达成，但无法确认是否由本次请求产生/)).toBeInTheDocument();
    expect(changeState).toHaveBeenCalledTimes(1);
  });

  it("freezes formal actions after a state version conflict until latest data is reviewed", async () => {
    const original = writableFormalDetail();
    const latest = writableFormalDetail({ lock_version: 5 });
    const changeState = vi.fn().mockRejectedValue(new apiModule.ApiError(409, {
      detail: "设定已被其他操作更新，请重新加载后重试",
      code: "LORE_VERSION_CONFLICT",
    }));
    vi.spyOn(apiModule, "api", "get").mockReturnValue({
      ...apiModule.api,
      getLoreOverview: vi.fn().mockResolvedValue(overview),
      listLoreElements: vi.fn().mockResolvedValue(formalResponse()),
      getLoreElement: vi.fn().mockResolvedValueOnce(original).mockResolvedValue(latest),
      listLoreCandidates: vi.fn(),
      listLoreTypes: vi.fn(),
      changeLoreElementState: changeState,
    });
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: /林渊/ }));
    await userEvent.click(await screen.findByRole("button", { name: "暂停用于生成" }));
    await userEvent.click(screen.getByRole("button", { name: "确认暂停用于生成" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("其他操作更新");
    expect(screen.getByRole("button", { name: "编辑内容" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "暂停用于生成" })).toBeDisabled();
    expect(changeState).toHaveBeenCalledTimes(1);
    await userEvent.click(screen.getByRole("button", { name: "核对最新状态" }));
    expect(await screen.findByText(/服务器当前内容与本次提交不一致/)).toBeInTheDocument();
    expect(changeState).toHaveBeenCalledTimes(1);
  });

  it("explains the active-relation archive conflict without retrying the write", async () => {
    const original = writableFormalDetail({ relation_count: 0 });
    const changeState = vi.fn().mockRejectedValue(new apiModule.ApiError(409, {
      detail: "该设定仍有启用中的关系，请先归档相关关系",
      code: "LORE_ELEMENT_ACTIVE_RELATIONS",
    }));
    vi.spyOn(apiModule, "api", "get").mockReturnValue({
      ...apiModule.api,
      getLoreOverview: vi.fn().mockResolvedValue(overview),
      listLoreElements: vi.fn().mockResolvedValue(formalResponse()),
      getLoreElement: vi.fn().mockResolvedValueOnce(original).mockResolvedValue(writableFormalDetail({ relation_count: 1 })),
      listLoreCandidates: vi.fn(),
      listLoreTypes: vi.fn(),
      changeLoreElementState: changeState,
    });
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: /林渊/ }));
    const archiveTrigger = await screen.findByRole("button", { name: "归档设定" });
    await userEvent.click(archiveTrigger);
    await userEvent.click(screen.getByRole("button", { name: "取消" }));
    await waitFor(() => expect(archiveTrigger).toHaveFocus());
    await userEvent.click(archiveTrigger);
    await userEvent.click(screen.getByRole("button", { name: "确认归档设定" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("请先在下方“设定关系”中归档相关关系");
    expect(changeState).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("button", { name: "核对最新状态" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "归档设定" })).not.toBeInTheDocument();
  });

  it("blocks archive in advance until active relations are archived", async () => {
    vi.spyOn(apiModule, "api", "get").mockReturnValue({
      ...apiModule.api,
      getLoreOverview: vi.fn().mockResolvedValue(overview),
      listLoreElements: vi.fn().mockResolvedValue(formalResponse()),
      getLoreElement: vi.fn().mockResolvedValue(writableFormalDetail({ relation_count: 2 })),
      listLoreCandidates: vi.fn(),
      listLoreTypes: vi.fn(),
    });
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: /林渊/ }));

    expect(await screen.findByText(/该设定有 2 条使用中的关系/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "归档设定" })).not.toBeInTheDocument();
  });

  it("does not switch formal elements when an unsaved edit is kept", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    const list = formalResponse();
    list.items.push({ ...list.items[0], id: "item-另一人", name: "另一人" });
    vi.spyOn(apiModule, "api", "get").mockReturnValue({
      ...apiModule.api,
      getLoreOverview: vi.fn().mockResolvedValue(overview),
      listLoreElements: vi.fn().mockResolvedValue(list),
      getLoreElement: vi.fn().mockResolvedValue(writableFormalDetail()),
      listLoreCandidates: vi.fn(),
      listLoreTypes: vi.fn(),
    });
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: /林渊/ }));
    await userEvent.click(await screen.findByRole("button", { name: "编辑内容" }));
    await userEvent.type(screen.getByLabelText("名称"), "草稿");
    await userEvent.click(screen.getByRole("button", { name: /另一人/ }));

    expect(confirm).toHaveBeenCalled();
    expect(screen.getByLabelText("名称")).toHaveValue("林渊草稿");
  });

  it("opens the attention inbox from the overview without calling a write API", async () => {
    renderPage();
    await screen.findByText("林渊");

    await userEvent.click(screen.getByRole("button", { name: /1\s+需要关注/ }));

    expect(await screen.findByText("寒川城")).toBeInTheDocument();
    expect(apiModule.api.listLoreCandidates).toHaveBeenCalledWith(
      "project-1",
      expect.objectContaining({ needs_attention: true }),
      expect.any(AbortSignal)
    );
    expect(screen.queryByRole("button", { name: /接纳/ })).not.toBeInTheDocument();
  });

  it("sends source and confirmation filters and preserves the disabled count definition", async () => {
    renderPage();
    await screen.findByText("林渊");

    await userEvent.selectOptions(screen.getByLabelText("确认状态"), "confirmed");
    await waitFor(() => expect(apiModule.api.listLoreElements).toHaveBeenLastCalledWith(
      "project-1",
      expect.objectContaining({ confirmation_status: "confirmed" }),
      expect.any(AbortSignal)
    ));
    await userEvent.selectOptions(screen.getByLabelText("原始来源"), "manual");
    await waitFor(() => expect(apiModule.api.listLoreElements).toHaveBeenLastCalledWith(
      "project-1",
      expect.objectContaining({ confirmation_status: "confirmed", source_kind: "manual" }),
      expect.any(AbortSignal)
    ));

    await userEvent.click(screen.getByRole("button", { name: /0\s+暂停用于生成/ }));
    await waitFor(() => expect(apiModule.api.listLoreElements).toHaveBeenLastCalledWith(
      "project-1",
      expect.objectContaining({ enabled: false, lifecycle_status: "active" }),
      expect.any(AbortSignal)
    ));
  });

  it("ignores a stale list response after a newer search finishes", async () => {
    let resolveOld: (value: LoreListResponse) => void = () => {};
    const oldRequest = new Promise<LoreListResponse>((resolve) => { resolveOld = resolve; });
    const list = vi.fn().mockImplementation((_projectId: string, filters: { q?: string }) =>
      filters.q ? Promise.resolve(formalResponse("新结果")) : oldRequest
    );
    vi.spyOn(apiModule, "api", "get").mockReturnValue({
      ...apiModule.api,
      getLoreOverview: vi.fn().mockResolvedValue(overview),
      listLoreElements: list,
      getLoreElement: vi.fn(),
      listLoreCandidates: vi.fn(),
    });
    renderPage();

    await userEvent.type(screen.getByLabelText("搜索名称或摘要"), "新");
    await userEvent.click(screen.getByRole("button", { name: "搜索" }));
    expect(await screen.findByText("新结果")).toBeInTheDocument();

    await act(async () => resolveOld(formalResponse("旧结果")));
    await waitFor(() => expect(screen.queryByText("旧结果")).not.toBeInTheDocument());
    expect(screen.getByText("新结果")).toBeInTheDocument();
  });

  it("does not append a stale load-more page after filters change", async () => {
    let resolveMore: (value: LoreListResponse) => void = () => {};
    const moreRequest = new Promise<LoreListResponse>((resolve) => { resolveMore = resolve; });
    const first = { ...formalResponse("首页设定"), next_cursor: "cursor-1", has_more: true };
    const list = vi.fn().mockImplementation((_projectId: string, filters: { q?: string; cursor?: string }) => {
      if (filters.cursor) return moreRequest;
      if (filters.q) return Promise.resolve(formalResponse("筛选后设定"));
      return Promise.resolve(first);
    });
    vi.spyOn(apiModule, "api", "get").mockReturnValue({
      ...apiModule.api,
      getLoreOverview: vi.fn().mockResolvedValue(overview),
      listLoreElements: list,
      getLoreElement: vi.fn(),
      listLoreCandidates: vi.fn(),
    });
    renderPage();
    await screen.findByText("首页设定");

    await userEvent.click(screen.getByRole("button", { name: "加载更多" }));
    await userEvent.type(screen.getByLabelText("搜索名称或摘要"), "新");
    await userEvent.click(screen.getByRole("button", { name: "搜索" }));
    expect(await screen.findByText("筛选后设定")).toBeInTheDocument();

    await act(async () => resolveMore(formalResponse("过期分页设定")));
    await waitFor(() => expect(screen.queryByText("过期分页设定")).not.toBeInTheDocument());
  });

  it("keeps a detail failure local and retries the selected item", async () => {
    const successfulDetail = {
      ...formalResponse().items[0],
      payload: { appearance: "黑发" },
      field_states: { appearance: "provided" },
      field_definitions: [{ key: "appearance", label: "外貌", order: 10 }],
      sources: [],
      version_count: 1,
      read_only: true,
    };
    const getDetail = vi.fn()
      .mockRejectedValueOnce(new Error("详情网络中断"))
      .mockResolvedValueOnce(successfulDetail);
    vi.spyOn(apiModule, "api", "get").mockReturnValue({
      ...apiModule.api,
      getLoreOverview: vi.fn().mockResolvedValue(overview),
      listLoreElements: vi.fn().mockResolvedValue(formalResponse()),
      getLoreElement: getDetail,
      listLoreCandidates: vi.fn(),
    });
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: /林渊/ }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("详情网络中断");
    expect(screen.getByText("林渊")).toBeInTheDocument();
    await userEvent.click(within(alert).getByRole("button", { name: "重试" }));
    expect(await screen.findByText("黑发")).toBeInTheDocument();
    expect(getDetail).toHaveBeenCalledTimes(2);
  });

  it("recovers an invalid cursor from page one and keeps the explanation visible", async () => {
    const first = { ...formalResponse("首页设定"), next_cursor: "stale", has_more: true };
    const list = vi.fn().mockImplementation((_projectId: string, filters: { cursor?: string }) => {
      if (filters.cursor) {
        return Promise.reject(new apiModule.ApiError(409, {
          detail: "列表游标已失效。",
          code: "LORE_CURSOR_STALE",
          reload_required: true,
        }));
      }
      return Promise.resolve(first);
    });
    vi.spyOn(apiModule, "api", "get").mockReturnValue({
      ...apiModule.api,
      getLoreOverview: vi.fn().mockResolvedValue(overview),
      listLoreElements: list,
      getLoreElement: vi.fn(),
      listLoreCandidates: vi.fn(),
    });
    renderPage();
    await screen.findByText("首页设定");
    await userEvent.click(screen.getByRole("button", { name: "加载更多" }));

    expect(await screen.findByRole("status")).toHaveTextContent("已从第一页重新加载");
    expect(await screen.findByText("首页设定")).toBeInTheDocument();
    expect(list).toHaveBeenCalledTimes(3);
  });

  it("moves focus to the selected detail on a 390px viewport", async () => {
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      value: vi.fn().mockImplementation(() => ({ matches: true })),
    });
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: /林渊/ }));

    const detailRegion = screen.getByRole("complementary", { name: "设定详情" });
    await waitFor(() => expect(detailRegion).toHaveFocus());
    expect(screen.getByRole("button", { name: /返回设定列表/ })).toBeInTheDocument();
  });

  it("does not restore a late detail response after returning to the list", async () => {
    let resolveDetail: (value: object) => void = () => {};
    const pendingDetail = new Promise<object>((resolve) => { resolveDetail = resolve; });
    vi.spyOn(apiModule, "api", "get").mockReturnValue({
      ...apiModule.api,
      getLoreOverview: vi.fn().mockResolvedValue(overview),
      listLoreElements: vi.fn().mockResolvedValue(formalResponse()),
      getLoreElement: vi.fn().mockReturnValue(pendingDetail),
      listLoreCandidates: vi.fn(),
    });
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: /林渊/ }));
    await userEvent.click(screen.getByRole("button", { name: /返回设定列表/ }));

    await act(async () => resolveDetail({
      ...formalResponse().items[0],
      payload: { appearance: "过期详情" },
      field_states: { appearance: "provided" },
      field_definitions: [{ key: "appearance", label: "外貌", order: 10 }],
      sources: [],
      version_count: 1,
      read_only: true,
    }));
    await waitFor(() => expect(screen.queryByText("过期详情")).not.toBeInTheDocument());
    expect(screen.getByText("选择一项查看详情")).toBeInTheDocument();
  });

  it("does not show a late detail error after returning to the list", async () => {
    let rejectDetail: (reason: Error) => void = () => {};
    const pendingDetail = new Promise<object>((_resolve, reject) => { rejectDetail = reject; });
    vi.spyOn(apiModule, "api", "get").mockReturnValue({
      ...apiModule.api,
      getLoreOverview: vi.fn().mockResolvedValue(overview),
      listLoreElements: vi.fn().mockResolvedValue(formalResponse()),
      getLoreElement: vi.fn().mockReturnValue(pendingDetail),
      listLoreCandidates: vi.fn(),
    });
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: /林渊/ }));
    await userEvent.click(screen.getByRole("button", { name: /返回设定列表/ }));

    await act(async () => rejectDetail(new Error("过期详情错误")));
    await waitFor(() => expect(screen.queryByText("过期详情错误")).not.toBeInTheDocument());
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("focuses the review heading after the initial review list loads", async () => {
    vi.spyOn(apiModule, "api", "get").mockReturnValue({
      ...apiModule.api,
      getLoreOverview: vi.fn().mockResolvedValue(writableOverview),
      listLoreElements: vi.fn(),
      getLoreElement: vi.fn(),
      listLoreCandidates: vi.fn().mockResolvedValue(readyCandidateResponse()),
    });

    renderPage("/project/project-1/lore?scope=review");

    const heading = await screen.findByRole("heading", { level: 2, name: "待审核提取" });
    await waitFor(() => expect(heading).toHaveFocus());
  });

  it("focuses review after a scope change but does not steal focus on review filters", async () => {
    const listCandidates = vi.fn().mockResolvedValue(readyCandidateResponse());
    vi.spyOn(apiModule, "api", "get").mockReturnValue({
      ...apiModule.api,
      getLoreOverview: vi.fn().mockResolvedValue(writableOverview),
      listLoreElements: vi.fn().mockResolvedValue(formalResponse()),
      getLoreElement: vi.fn(),
      listLoreCandidates: listCandidates,
    });
    renderPage();
    await screen.findByRole("heading", { level: 2, name: "正式设定" });

    const scopeNavigation = screen.getByRole("navigation", { name: "设定范围" });
    await userEvent.click(within(scopeNavigation).getByRole("button", { name: "待审核提取" }));
    const reviewHeading = await screen.findByRole("heading", { level: 2, name: "待审核提取" });
    await waitFor(() => expect(reviewHeading).toHaveFocus());

    await userEvent.type(screen.getByRole("textbox", { name: "搜索名称或摘要" }), "寒川");
    await userEvent.click(screen.getByRole("button", { name: "搜索" }));
    await waitFor(() => expect(listCandidates).toHaveBeenCalledTimes(2));
    expect(reviewHeading).not.toHaveFocus();
  });

  it("edits a candidate with the authoritative schema and current revision", async () => {
    const updated = {
      ...readyCandidateResponse().items[0],
      name: "新寒川城",
      revision: 2,
    };
    const edit = vi.fn().mockResolvedValue(updated);
    const list = vi.fn()
      .mockResolvedValueOnce(readyCandidateResponse())
      .mockResolvedValue({ ...readyCandidateResponse(), items: [updated] });
    vi.spyOn(apiModule, "api", "get").mockReturnValue({
      ...apiModule.api,
      getLoreOverview: vi.fn().mockResolvedValue(writableOverview),
      listLoreElements: vi.fn(),
      getLoreElement: vi.fn(),
      listLoreCandidates: list,
      listLoreTypes: vi.fn().mockResolvedValue(loreTypesResponse),
      editLoreCandidate: edit,
    });
    renderPage("/project/project-1/lore?scope=review");
    await userEvent.click(await screen.findByRole("button", { name: /寒川城/ }));
    await waitFor(() => expect(apiModule.api.listLoreTypes).toHaveBeenCalled());
    await userEvent.click(screen.getByRole("button", { name: "编辑候选" }));
    expect(screen.queryByRole("option", { name: "自定义秘密" })).not.toBeInTheDocument();
    const name = screen.getByLabelText("名称");
    await userEvent.clear(name);
    await userEvent.type(name, "新寒川城");
    await userEvent.click(screen.getByRole("button", { name: "保存修改" }));

    await waitFor(() => expect(edit).toHaveBeenCalledWith(
      "project-1",
      "batch-1",
      "candidate-1",
      expect.objectContaining({
        expected_version: 1,
        type_key: "location",
        name: "新寒川城",
      })
    ));
    expect(await screen.findByText(/当前修订为 2/)).toBeInTheDocument();
  });

  it("keeps the local draft on a version conflict and reloads only after confirmation", async () => {
    const edit = vi.fn().mockRejectedValue(new apiModule.ApiError(409, {
      detail: "候选已被更新。",
      code: "LORE_CANDIDATE_VERSION_CONFLICT",
      reload_required: true,
    }));
    const latest = { ...readyCandidateResponse().items[0], name: "服务端寒川城", revision: 2 };
    vi.spyOn(window, "confirm").mockReturnValue(true);
    vi.spyOn(apiModule, "api", "get").mockReturnValue({
      ...apiModule.api,
      getLoreOverview: vi.fn().mockResolvedValue(writableOverview),
      listLoreElements: vi.fn(),
      getLoreElement: vi.fn(),
      listLoreCandidates: vi.fn().mockResolvedValue(readyCandidateResponse()),
      listLoreTypes: vi.fn().mockResolvedValue(loreTypesResponse),
      editLoreCandidate: edit,
      getLoreCandidate: vi.fn().mockResolvedValue(latest),
    });
    renderPage("/project/project-1/lore?scope=review");
    await userEvent.click(await screen.findByRole("button", { name: /寒川城/ }));
    await waitFor(() => expect(apiModule.api.listLoreTypes).toHaveBeenCalled());
    await userEvent.click(screen.getByRole("button", { name: "编辑候选" }));
    const name = screen.getByLabelText("名称");
    await userEvent.clear(name);
    await userEvent.type(name, "本地寒川城");
    await userEvent.click(screen.getByRole("button", { name: "保存修改" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("当前输入仍保留");
    expect(screen.getByLabelText("名称")).toHaveValue("本地寒川城");
    await userEvent.click(screen.getByRole("button", { name: "核对最新状态" }));
    expect(await screen.findByText(/仍为待审核状态/)).toBeInTheDocument();
    expect(screen.getByText("查看冲突前保留的本地草稿")).toBeInTheDocument();
  });

  it("retains candidate input while project writes are frozen", async () => {
    const edit = vi.fn().mockRejectedValue(new apiModule.ApiError(503, {
      detail: "项目资料正在升级。",
      code: "PROJECT_WRITE_FROZEN",
      maintenance_state: "write_frozen",
      retryable: true,
    }));
    vi.spyOn(apiModule, "api", "get").mockReturnValue({
      ...apiModule.api,
      getLoreOverview: vi.fn().mockResolvedValue(writableOverview),
      listLoreElements: vi.fn(),
      getLoreElement: vi.fn(),
      listLoreCandidates: vi.fn().mockResolvedValue(readyCandidateResponse()),
      listLoreTypes: vi.fn().mockResolvedValue(loreTypesResponse),
      editLoreCandidate: edit,
    });
    renderPage("/project/project-1/lore?scope=review");
    await userEvent.click(await screen.findByRole("button", { name: /寒川城/ }));
    await waitFor(() => expect(apiModule.api.listLoreTypes).toHaveBeenCalled());
    await userEvent.click(screen.getByRole("button", { name: "编辑候选" }));
    const name = screen.getByLabelText("名称");
    await userEvent.clear(name);
    await userEvent.type(name, "维护中的本地草稿");
    await userEvent.click(screen.getByRole("button", { name: "保存修改" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("正在维护");
    expect(screen.getByLabelText("名称")).toHaveValue("维护中的本地草稿");
    expect(screen.getByRole("button", { name: "保存修改" })).toBeEnabled();
  });

  it("does not discard an unsaved candidate when scope navigation is cancelled", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    vi.spyOn(apiModule, "api", "get").mockReturnValue({
      ...apiModule.api,
      getLoreOverview: vi.fn().mockResolvedValue(writableOverview),
      listLoreElements: vi.fn(),
      getLoreElement: vi.fn(),
      listLoreCandidates: vi.fn().mockResolvedValue(readyCandidateResponse()),
      listLoreTypes: vi.fn().mockResolvedValue(loreTypesResponse),
    });
    renderPage("/project/project-1/lore?scope=review");
    await userEvent.click(await screen.findByRole("button", { name: /寒川城/ }));
    await waitFor(() => expect(apiModule.api.listLoreTypes).toHaveBeenCalled());
    await userEvent.click(screen.getByRole("button", { name: "编辑候选" }));
    await userEvent.type(screen.getByLabelText("名称"), "草稿");
    await userEvent.click(within(screen.getByRole("navigation", { name: "设定范围" })).getByRole("button", { name: "正式设定" }));

    expect(confirm).toHaveBeenCalled();
    expect(screen.getByLabelText("名称")).toHaveValue("寒川城草稿");
    expect(screen.getByRole("button", { name: "保存修改" })).toBeInTheDocument();
  });

  it("labels a user override as an artificial revision instead of original text", async () => {
    const ready = readyCandidateResponse();
    const revised = {
      ...ready.items[0],
      evidence: [{
        ...ready.items[0].evidence[0],
        value_origin: "user_override" as const,
        current_value: "用户补充的名称",
      }],
    };
    vi.spyOn(apiModule, "api", "get").mockReturnValue({
      ...apiModule.api,
      getLoreOverview: vi.fn().mockResolvedValue(writableOverview),
      listLoreElements: vi.fn(),
      getLoreElement: vi.fn(),
      listLoreCandidates: vi.fn().mockResolvedValue({ ...ready, items: [revised] }),
      listLoreTypes: vi.fn().mockResolvedValue(loreTypesResponse),
    });
    const { container } = renderPage("/project/project-1/lore?scope=review");
    await userEvent.click(await screen.findByRole("button", { name: /寒川城/ }));

    expect(await screen.findByText(/名称 · 用户已补充/)).toBeInTheDocument();
    expect(screen.getByText(/当前人工确认值：用户补充的名称/)).toBeInTheDocument();
    expect(container).not.toHaveTextContent("原文已提供");
  });

  it("disables editing for a legacy candidate whose type cannot be safely repaired", async () => {
    const invalid = {
      ...candidateResponse.items[0],
      type_key: null,
      type_display_name: null,
      actions: { ...candidateResponse.items[0].actions, can_edit: true },
    };
    const legacyOverview = {
      ...overview,
      migration_status: { storage_mode: "legacy", state: "not_started", read_only: true },
    } satisfies LoreOverview;
    vi.spyOn(apiModule, "api", "get").mockReturnValue({
      ...apiModule.api,
      getLoreOverview: vi.fn().mockResolvedValue(legacyOverview),
      listLoreElements: vi.fn(),
      getLoreElement: vi.fn(),
      listLoreCandidates: vi.fn().mockResolvedValue({ ...candidateResponse, items: [invalid] }),
      listLoreTypes: vi.fn(),
    });
    renderPage("/project/project-1/lore?scope=review");
    await userEvent.click(await screen.findByRole("button", { name: /寒川城/ }));

    expect(screen.getByRole("button", { name: "编辑候选" })).toBeDisabled();
    expect(screen.getByText(/无法安全修正缺失或无效类型/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "拒绝并保留记录" })).toBeEnabled();
  });

  it("reapplies the active attention filter after an edit", async () => {
    const updated = {
      ...candidateResponse.items[0],
      name: "已确认寒川城",
      revision: 2,
      needs_attention: false,
      disabled_reasons: [],
    };
    const list = vi.fn()
      .mockResolvedValueOnce(candidateResponse)
      .mockResolvedValue({ ...candidateResponse, items: [], total: 0 });
    vi.spyOn(apiModule, "api", "get").mockReturnValue({
      ...apiModule.api,
      getLoreOverview: vi.fn().mockResolvedValue(writableOverview),
      listLoreElements: vi.fn(),
      getLoreElement: vi.fn(),
      listLoreCandidates: list,
      listLoreTypes: vi.fn().mockResolvedValue(loreTypesResponse),
      editLoreCandidate: vi.fn().mockResolvedValue(updated),
    });
    renderPage("/project/project-1/lore?scope=review&needs_attention=true");
    await userEvent.click(await screen.findByRole("button", { name: /寒川城/ }));
    await waitFor(() => expect(apiModule.api.listLoreTypes).toHaveBeenCalled());
    await userEvent.click(screen.getByRole("button", { name: "编辑候选" }));
    await userEvent.click(screen.getByRole("button", { name: "保存修改" }));

    await waitFor(() => expect(list).toHaveBeenCalledTimes(2));
    expect(await screen.findByText("没有匹配的设定")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /寒川城/ })).not.toBeInTheDocument();
  });

  it("persists an explicit duplicate decision before acceptance becomes available", async () => {
    const ready = readyCandidateResponse();
    const suggestion = {
      suggestion_id: "suggestion-1",
      kind: "possible_duplicate",
      target_element_id: "element-old",
      target_name: "旧寒川城",
      target_type_key: "location",
      differing_fields: [],
      resolution_status: "unresolved",
    };
    const unresolved = {
      ...ready.items[0],
      needs_attention: true,
      disabled_reasons: ["suggestions_unresolved"],
      duplicate_conflict_suggestions: [suggestion],
      actions: {
        ...ready.items[0].actions,
        can_accept: false,
        disabled_reasons: { edit: [], accept: ["suggestions_unresolved"], reject: [] },
      },
    };
    const updated = {
      ...unresolved,
      revision: 2,
      needs_attention: false,
      disabled_reasons: [],
      suggestion_resolutions: { "suggestion-1": "accept_as_new" as const },
      actions: ready.items[0].actions,
    };
    const edit = vi.fn().mockResolvedValue(updated);
    const list = vi.fn()
      .mockResolvedValueOnce({ ...ready, items: [unresolved] })
      .mockResolvedValue({ ...ready, items: [updated] });
    vi.spyOn(apiModule, "api", "get").mockReturnValue({
      ...apiModule.api,
      getLoreOverview: vi.fn().mockResolvedValue(writableOverview),
      listLoreElements: vi.fn(),
      getLoreElement: vi.fn(),
      listLoreCandidates: list,
      listLoreTypes: vi.fn().mockResolvedValue(loreTypesResponse),
      editLoreCandidate: edit,
    });
    renderPage("/project/project-1/lore?scope=review");
    await userEvent.click(await screen.findByRole("button", { name: /寒川城/ }));
    await waitFor(() => expect(apiModule.api.listLoreTypes).toHaveBeenCalled());
    expect(screen.getByRole("button", { name: "接纳为正式设定" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "编辑候选" }));
    await userEvent.selectOptions(screen.getByLabelText(/可能与已有设定重复：旧寒川城/), "accept_as_new");
    await userEvent.click(screen.getByRole("button", { name: "保存修改" }));

    await waitFor(() => expect(edit).toHaveBeenCalledWith(
      "project-1",
      "batch-1",
      "candidate-1",
      expect.objectContaining({ suggestion_resolutions: { "suggestion-1": "accept_as_new" } })
    ));
    expect(await screen.findByRole("button", { name: "接纳为正式设定" })).toBeEnabled();
  });

  it("returns focus to the action trigger when confirmation is cancelled", async () => {
    vi.spyOn(apiModule, "api", "get").mockReturnValue({
      ...apiModule.api,
      getLoreOverview: vi.fn().mockResolvedValue(writableOverview),
      listLoreElements: vi.fn(),
      getLoreElement: vi.fn(),
      listLoreCandidates: vi.fn().mockResolvedValue(readyCandidateResponse()),
      listLoreTypes: vi.fn().mockResolvedValue(loreTypesResponse),
    });
    renderPage("/project/project-1/lore?scope=review");
    await userEvent.click(await screen.findByRole("button", { name: /寒川城/ }));
    const acceptButton = screen.getByRole("button", { name: "接纳为正式设定" });
    await userEvent.click(acceptButton);
    await waitFor(() => expect(screen.getByRole("alertdialog")).toHaveFocus());
    await userEvent.click(screen.getByRole("button", { name: "取消" }));
    await waitFor(() => expect(acceptButton).toHaveFocus());
  });

  it("does not carry a preserved conflict draft into another candidate", async () => {
    const ready = readyCandidateResponse();
    const second = { ...ready.items[0], id: "candidate-2", name: "北境雪原", ordinal: 2 };
    vi.spyOn(window, "confirm").mockReturnValue(true);
    vi.spyOn(apiModule, "api", "get").mockReturnValue({
      ...apiModule.api,
      getLoreOverview: vi.fn().mockResolvedValue(writableOverview),
      listLoreElements: vi.fn(),
      getLoreElement: vi.fn(),
      listLoreCandidates: vi.fn().mockResolvedValue({ ...ready, items: [ready.items[0], second], total: 2 }),
      listLoreTypes: vi.fn().mockResolvedValue(loreTypesResponse),
      editLoreCandidate: vi.fn().mockRejectedValue(new apiModule.ApiError(409, {
        detail: "候选已被更新。",
        code: "LORE_CANDIDATE_VERSION_CONFLICT",
        reload_required: true,
      })),
    });
    renderPage("/project/project-1/lore?scope=review");
    await userEvent.click(await screen.findByRole("button", { name: /寒川城/ }));
    await waitFor(() => expect(apiModule.api.listLoreTypes).toHaveBeenCalled());
    await userEvent.click(screen.getByRole("button", { name: "编辑候选" }));
    await userEvent.type(screen.getByLabelText("名称"), "本地草稿");
    await userEvent.click(screen.getByRole("button", { name: "保存修改" }));
    expect(await screen.findByText("查看冲突前保留的本地草稿")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /北境雪原/ }));
    expect(screen.queryByText("查看冲突前保留的本地草稿")).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "北境雪原" })).toBeInTheDocument();
  });

  it("accepts a candidate once, sends expected_version, and refreshes the inbox", async () => {
    const ready = readyCandidateResponse();
    const empty = { ...ready, items: [], total: 0 };
    const list = vi.fn().mockResolvedValueOnce(ready).mockResolvedValue(empty);
    const accept = vi.fn().mockResolvedValue({
      candidate: { ...ready.items[0], status: "accepted", revision: 2, accepted_element_id: "element-1" },
      action_result: "accepted",
      replayed: false,
      accepted_element_id: "element-1",
      remaining_pending_count: 0,
      next_pending_candidate_id: null,
    });
    vi.spyOn(apiModule, "api", "get").mockReturnValue({
      ...apiModule.api,
      getLoreOverview: vi.fn().mockResolvedValue(writableOverview),
      listLoreElements: vi.fn(),
      getLoreElement: vi.fn(),
      listLoreCandidates: list,
      listLoreTypes: vi.fn().mockResolvedValue(loreTypesResponse),
      acceptLoreCandidate: accept,
    });
    renderPage("/project/project-1/lore?scope=review");
    await userEvent.click(await screen.findByRole("button", { name: /寒川城/ }));
    await userEvent.click(screen.getByRole("button", { name: "接纳为正式设定" }));
    expect(screen.getByRole("alertdialog")).toHaveTextContent("关系建议不会自动建立");
    await userEvent.click(screen.getByRole("button", { name: "确认接纳" }));

    await waitFor(() => expect(accept).toHaveBeenCalledWith(
      "project-1",
      "batch-1",
      "candidate-1",
      { expected_version: 1, suggestion_resolutions: {} }
    ));
    expect(await screen.findByRole("status")).toHaveTextContent("已接纳为正式设定");
    await waitFor(() => expect(list).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.getByRole("heading", { name: "待审核提取" })).toHaveFocus());
  });

  it("keeps legacy acceptance unavailable while allowing audited rejection", async () => {
    const legacyOverview = {
      ...overview,
      migration_status: { storage_mode: "legacy", state: "not_started", read_only: true },
    } satisfies LoreOverview;
    const reject = vi.fn().mockResolvedValue({
      candidate: { ...candidateResponse.items[0], status: "rejected", revision: 2 },
      action_result: "already_rejected",
      replayed: true,
      accepted_element_id: null,
      remaining_pending_count: 0,
      next_pending_candidate_id: null,
    });
    vi.spyOn(apiModule, "api", "get").mockReturnValue({
      ...apiModule.api,
      getLoreOverview: vi.fn().mockResolvedValue(legacyOverview),
      listLoreElements: vi.fn(),
      getLoreElement: vi.fn(),
      listLoreCandidates: vi.fn().mockResolvedValue(candidateResponse),
      listLoreTypes: vi.fn(),
      rejectLoreCandidate: reject,
    });
    renderPage("/project/project-1/lore?scope=review");
    await userEvent.click(await screen.findByRole("button", { name: /寒川城/ }));

    expect(screen.queryByRole("button", { name: "接纳为正式设定" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "拒绝并保留记录" }));
    expect(screen.getByRole("alertdialog")).toHaveTextContent("不支持撤销拒绝");
    await userEvent.click(screen.getByRole("button", { name: "确认拒绝" }));
    await waitFor(() => expect(reject).toHaveBeenCalledWith(
      "project-1",
      "batch-1",
      "candidate-1",
      { expected_version: 1, suggestion_resolutions: {} }
    ));
    expect(apiModule.api.listLoreTypes).not.toHaveBeenCalled();
  });
});
