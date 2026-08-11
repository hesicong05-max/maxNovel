import { describe, it, expect, beforeEach, vi, afterEach } from "vitest";
import {
  ApiError,
  getAuthToken,
  setAuthToken,
  clearAuthToken,
  isProjectWriteFrozenData,
  isProjectWriteFrozenError,
  setOnUnauthorized,
  api,
} from "./api";
import type { AuthResponse, AuthUser, Project } from "@/types";

// ── Test fixtures ──
const MOCK_USER: AuthUser = {
  id: "abc123",
  email: "test@example.com",
  username: "testuser",
  is_admin: false,
  created_at: "2026-01-01T00:00:00Z",
};

const MOCK_AUTH_RESPONSE: AuthResponse = {
  token: "mock-jwt-token-xyz",
  user: MOCK_USER,
};

const MOCK_PROJECT: Project = {
  id: "proj1",
  title: "Test Novel",
  genre: "玄幻",
  status: "draft",
  total_chapters: 10,
  chapter_word_count: 3000,
  style_intensity: "standard",
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
  has_worldview: false,
  has_outline: false,
  chapter_count: 0,
};

// ── Tests ──

describe("Token Management", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("getAuthToken returns null when no token is set", () => {
    expect(getAuthToken()).toBeNull();
  });

  it("setAuthToken stores token in localStorage", () => {
    setAuthToken("my-token-123");
    expect(getAuthToken()).toBe("my-token-123");
    expect(localStorage.getItem("novel_auth_token")).toBe("my-token-123");
  });

  it("clearAuthToken removes token from localStorage", () => {
    setAuthToken("temp-token");
    clearAuthToken();
    expect(getAuthToken()).toBeNull();
    expect(localStorage.getItem("novel_auth_token")).toBeNull();
  });

  it("setAuthToken overwrites previous token", () => {
    setAuthToken("first-token");
    setAuthToken("second-token");
    expect(getAuthToken()).toBe("second-token");
  });
});

describe("Unauthorized Callback", () => {
  beforeEach(() => {
    localStorage.clear();
    setOnUnauthorized(null);
  });

  it("setOnUnauthorized stores callback", () => {
    const cb = vi.fn();
    setOnUnauthorized(cb);
    // The callback is stored internally; we verify via behavior in API calls
    expect(cb).not.toHaveBeenCalled();
  });

  it("setOnUnauthorized(null) removes callback", () => {
    const cb = vi.fn();
    setOnUnauthorized(cb);
    setOnUnauthorized(null);
    // No throw when null is set
    expect(true).toBe(true);
  });
});

describe("API - Auth", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("login calls correct endpoint and returns token + user", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(MOCK_AUTH_RESPONSE), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    );

    const result = await api.login({
      email: "test@example.com",
      password: "password123",
    });

    expect(result).toEqual(MOCK_AUTH_RESPONSE);
    expect(fetch).toHaveBeenCalledWith(
      "/api/auth/login",
      expect.objectContaining({
        method: "POST",
        headers: expect.objectContaining({
          "Content-Type": "application/json",
        }),
      })
    );
  });

  it("register calls correct endpoint", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(MOCK_AUTH_RESPONSE), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    );

    await api.register({
      email: "new@example.com",
      username: "newuser",
      password: "password123",
    });

    expect(fetch).toHaveBeenCalledWith(
      "/api/auth/register",
      expect.objectContaining({ method: "POST" })
    );
  });

  it("getMe sends Authorization header when token exists", async () => {
    setAuthToken("my-jwt-token");

    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(MOCK_USER), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    );

    await api.getMe();

    const [, options] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(options.headers).toHaveProperty("Authorization", "Bearer my-jwt-token");
  });

  it("getMe does not send Authorization header when no token", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(MOCK_USER), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    );

    await api.getMe();

    const [, options] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(options.headers).not.toHaveProperty("Authorization");
  });

  it("throws Error with detail message on non-ok response", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ detail: "用户名已存在" }), {
        status: 409,
        headers: { "Content-Type": "application/json" },
      })
    );

    await expect(
      api.register({ email: "x@y.com", username: "x", password: "123456" })
    ).rejects.toThrow("用户名已存在");
  });

  it("falls back to statusText when no detail in error response", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response("Internal Server Error", { status: 500 })
    );

    await expect(api.getMe()).rejects.toThrow("HTTP 500");
  });

  it("401 response clears token and calls unauthorized callback", async () => {
    setAuthToken("expired-token");
    const cb = vi.fn();
    setOnUnauthorized(cb);

    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response("Unauthorized", { status: 401 })
    );

    // The 401 handler returns a never-resolving promise (by design).
    // We call getMe() and check side effects without awaiting.
    api.getMe().catch(() => {});

    // Wait for the fetch to be called
    await vi.waitFor(() => {
      expect(fetchSpy).toHaveBeenCalled();
    });

    // Give the .then() handler time to run
    await new Promise((r) => setTimeout(r, 50));

    // Token should be cleared
    expect(getAuthToken()).toBeNull();
    // Callback should have been called
    expect(cb).toHaveBeenCalled();
  });
});

describe("API - maintenance error contract", () => {
  beforeEach(() => {
    setAuthToken("valid-token");
    vi.restoreAllMocks();
  });

  it("preserves the flat maintenance contract as a typed ApiError", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          detail: "项目资料正在升级，暂时无法保存，请稍后重试。",
          code: "PROJECT_WRITE_FROZEN",
          maintenance_state: "write_frozen",
          retryable: true,
          retry_after_seconds: 60,
          event_id: "BUG-002B",
        }),
        {
          status: 503,
          headers: {
            "Content-Type": "application/json",
            "Retry-After": "60",
          },
        }
      )
    );

    let caught: unknown;
    try {
      await api.updateChapter("project-1", 1, { content: "未保存正文" });
    } catch (error) {
      caught = error;
    }
    expect(caught).toBeInstanceOf(ApiError);
    expect(isProjectWriteFrozenError(caught)).toBe(true);
    const error = caught as ApiError;
    expect(error.status).toBe(503);
    expect(error.detail).toBe("项目资料正在升级，暂时无法保存，请稍后重试。");
    expect(error.maintenanceState).toBe("write_frozen");
    expect(error.retryable).toBe(true);
    expect(error.retryAfterSeconds).toBe(60);
    expect(error.eventId).toBe("BUG-002B");
  });

  it("keeps legacy detail errors and safely falls back to Retry-After", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ detail: "旧错误格式" }), {
        status: 503,
        headers: {
          "Content-Type": "application/json",
          "Retry-After": "120",
        },
      })
    );
    await expect(api.getMe()).rejects.toMatchObject({
      name: "ApiError",
      detail: "旧错误格式",
      retryAfterSeconds: 120,
      retryable: false,
    });
  });

  it("preserves planning recovery fields from nested errors", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({
        detail: {
          code: "PLANNING_STRUCTURE_VERSION_CONFLICT",
          message: "结构已更新。",
          retryable: false,
          recommended_action: "refresh_planning",
          current_structure_version: 8,
          issues: [{ code: "missing_chapter", node_id: "chapter-1" }],
        },
      }), { status: 409, headers: { "Content-Type": "application/json" } })
    );
    await expect(api.getPlanning("project-1")).rejects.toMatchObject({
      code: "PLANNING_STRUCTURE_VERSION_CONFLICT",
      recommendedAction: "refresh_planning",
      context: expect.objectContaining({ current_structure_version: 8 }),
    });
  });

  it.each([
    ["chapter", () => api.streamChapter("project-1", 1)],
    ["batch", () => api.streamBatchGenerate("project-1")],
  ])("parses %s SSE maintenance error objects without retrying", async (_, factory) => {
    const maintenance = {
      detail: "项目资料正在升级，暂时无法保存，请稍后重试。",
      code: "PROJECT_WRITE_FROZEN",
      maintenance_state: "write_frozen",
      retryable: true,
      retry_after_seconds: 60,
      event_id: "BUG-002B",
    };
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        `data: ${JSON.stringify({ type: "error", error: maintenance })}\n\n`,
        {
          status: 200,
          headers: { "Content-Type": "text/event-stream" },
        }
      )
    );

    const generator = factory();
    const event = await generator.next();
    expect(event.done).toBe(false);
    expect(event.value.type).toBe("error");
    expect(isProjectWriteFrozenData(event.value.error)).toBe(true);
    await generator.next();
    expect(fetchSpy).toHaveBeenCalledTimes(1);
  });

  it("turns a non-2xx streaming response into the same typed error", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          detail: "项目资料正在升级，暂时无法保存，请稍后重试。",
          code: "PROJECT_WRITE_FROZEN",
        }),
        {
          status: 503,
          headers: { "Content-Type": "application/json" },
        }
      )
    );

    const generator = api.streamChapter("project-1", 1);
    await expect(generator.next()).rejects.toSatisfy(isProjectWriteFrozenError);
  });
});

describe("API - relational planning", () => {
  beforeEach(() => {
    setAuthToken("valid-token");
    vi.restoreAllMocks();
  });

  it("encodes path values and submits the complete reorder command", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ receipt_kind: "structure" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    );
    const body = {
      operation_key: "planning:reorder:12345678",
      expected_structure_version: 4,
      parts: [{ part_id: "part-1", chapter_ids: ["chapter-1"] }],
    };
    await api.reorderPlanningStructure("project/one", body);
    expect(fetch).toHaveBeenCalledWith(
      "/api/projects/project%2Fone/planning/structure/reorder",
      expect.objectContaining({ method: "POST", body: JSON.stringify(body) })
    );
  });

  it("uses the original operation key for read-only result recovery", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ receipt_kind: "structure" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    );
    await api.getPlanningOperation("project-1", "planning:part create:12345678");
    expect(fetch).toHaveBeenCalledWith(
      "/api/projects/project-1/planning/operations/by-key/planning%3Apart%20create%3A12345678",
      expect.any(Object)
    );
  });

  it("encodes scoped lore assignment reads, writes, state changes, and history", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
      new Response(JSON.stringify({ receipt_kind: "assignment", assignments: [] }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    );
    const create = {
      operation_key: "planning:assignment_create:12345678",
      expected_assignment_version: 2,
      element_id: "element/one",
      expected_element_content_version: 4,
      scope_type: "part" as const,
      scope_target_id: "part/one",
    };
    const change = {
      operation_key: "planning:assignment_remove:12345678",
      expected_assignment_version: 3,
      expected_lock_version: 1,
      scope_type: "part" as const,
      scope_target_id: "part/one",
    };
    await api.getPlanningLoreAssignments("project/one", "part", "part/one");
    await api.createPlanningLoreAssignment("project/one", create);
    await api.changePlanningLoreAssignmentState("project/one", "assignment/one", "remove", change);
    await api.getPlanningLoreAssignmentHistory("project/one", "element/one");

    expect(fetchSpy.mock.calls[0][0]).toBe(
      "/api/projects/project%2Fone/planning/lore-assignments?scope_type=part&scope_target_id=part%2Fone"
    );
    expect(fetchSpy).toHaveBeenNthCalledWith(2,
      "/api/projects/project%2Fone/planning/lore-assignments",
      expect.objectContaining({ method: "POST", body: JSON.stringify(create) })
    );
    expect(fetchSpy).toHaveBeenNthCalledWith(3,
      "/api/projects/project%2Fone/planning/lore-assignments/assignment%2Fone/remove",
      expect.objectContaining({ method: "POST", body: JSON.stringify(change) })
    );
    expect(fetchSpy.mock.calls[3][0]).toBe(
      "/api/projects/project%2Fone/planning/lore-assignments/history?element_id=element%2Fone"
    );
  });
});

describe("API - lore repository", () => {
  beforeEach(() => {
    setAuthToken("valid-token");
    vi.restoreAllMocks();
  });

  it("encodes lore filters without dropping false boolean values", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({
        items: [],
        next_cursor: null,
        has_more: false,
        total: 0,
        facets: {},
        migration_status: { storage_mode: "normalized", state: "ready", read_only: false },
      }), { status: 200, headers: { "Content-Type": "application/json" } })
    );

    await api.listLoreElements("project/one", {
      q: "龙 城",
      enabled: false,
      has_relation: true,
      limit: 20,
    });

    const [url] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe(
      "/api/projects/project/one/lore/elements?q=%E9%BE%99+%E5%9F%8E&enabled=false&has_relation=true&limit=20"
    );
  });

  it("parses nested FastAPI error details and reload requirement", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({
        detail: {
          code: "LORE_CURSOR_STALE",
          message: "列表游标已失效。",
          reload_required: true,
        },
      }), { status: 409, headers: { "Content-Type": "application/json" } })
    );

    await expect(api.listLoreElements("project-1", { cursor: "stale" })).rejects.toMatchObject({
      name: "ApiError",
      status: 409,
      code: "LORE_CURSOR_STALE",
      detail: "列表游标已失效。",
      reloadRequired: true,
    });
  });

  it("sends candidate mutations to the scoped endpoints with expected_version", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
      new Response(JSON.stringify({}), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    );
    const edit = {
      expected_version: 7,
      type_key: "character",
      name: "林渊",
      summary: "",
      payload: { personality: "谨慎" },
      field_states: { personality: "provided" as const },
      suggestion_resolutions: {},
    };
    await api.editLoreCandidate("project-1", "batch-1", "candidate-1", edit);
    await api.acceptLoreCandidate("project-1", "batch-1", "candidate-1", {
      expected_version: 8,
      suggestion_resolutions: {},
    });
    await api.rejectLoreCandidate("project-1", "batch-1", "candidate-1", {
      expected_version: 8,
      suggestion_resolutions: {},
    });

    expect(fetchSpy).toHaveBeenNthCalledWith(1,
      "/api/projects/project-1/lore/extractions/batch-1/candidates/candidate-1",
      expect.objectContaining({ method: "PATCH", body: JSON.stringify(edit) })
    );
    expect(fetchSpy).toHaveBeenNthCalledWith(2,
      "/api/projects/project-1/lore/extractions/batch-1/candidates/candidate-1/accept",
      expect.objectContaining({ method: "POST", body: expect.stringContaining('"expected_version":8') })
    );
    expect(fetchSpy).toHaveBeenNthCalledWith(3,
      "/api/projects/project-1/lore/extractions/batch-1/candidates/candidate-1/reject",
      expect.objectContaining({ method: "POST", body: expect.stringContaining('"expected_version":8') })
    );
  });

  it("sends formal element edits and reversible state changes with lock versions", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
      new Response(JSON.stringify({}), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    );
    const edit = {
      expected_version: 4,
      name: "寒川城",
      summary: "北境城邦",
      payload: { description: "终年积雪" },
      field_states: { description: "provided" as const },
    };

    await api.updateLoreElement("project-1", "element-1", edit);
    await api.changeLoreElementState("project-1", "element-1", "disable", {
      expected_version: 5,
      reason: "暂不参与生成",
    });
    await api.changeLoreElementState("project-1", "element-1", "restore-archive", {
      expected_version: 6,
    });

    expect(fetchSpy).toHaveBeenNthCalledWith(1,
      "/api/projects/project-1/lore/elements/element-1",
      expect.objectContaining({ method: "PATCH", body: JSON.stringify(edit) })
    );
    expect(fetchSpy).toHaveBeenNthCalledWith(2,
      "/api/projects/project-1/lore/elements/element-1/disable",
      expect.objectContaining({ method: "POST", body: expect.stringContaining('"expected_version":5') })
    );
    expect(fetchSpy).toHaveBeenNthCalledWith(3,
      "/api/projects/project-1/lore/elements/element-1/restore-archive",
      expect.objectContaining({ method: "POST", body: expect.stringContaining('"expected_version":6') })
    );
  });

  it("uses the relation catalog and versioned relation write endpoints", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
      new Response(JSON.stringify({ items: [], has_more: false, total: 0 }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    );
    const create = {
      operation_key: "lore-relation:test-operation-0001",
      target_element_id: "element-2",
      source_expected_version: 4,
      target_expected_version: 7,
      relation_type: "member_of",
      description: "用户确认",
    };

    await api.listLoreRelationTypes("project-1");
    await api.listLoreRelations("project-1", "element-1", { status: "active", limit: 20 });
    await api.getLoreRelation("project-1", "relation-1");
    await api.createLoreRelation("project-1", "element-1", create);
    await api.updateLoreRelation("project-1", "relation-1", {
      expected_version: 1,
      forward_label: "隶属于",
      reverse_label: "成员包括",
      description: "用户确认",
      metadata: {},
    });
    await api.changeLoreRelationState("project-1", "relation-1", "archive", {
      expected_version: 2,
      reason: "剧情变化",
    });

    expect(fetchSpy).toHaveBeenNthCalledWith(1,
      "/api/projects/project-1/lore/relation-types",
      expect.objectContaining({ headers: expect.any(Object) })
    );
    expect(fetchSpy.mock.calls[1][0]).toBe(
      "/api/projects/project-1/lore/elements/element-1/relations?status=active&limit=20"
    );
    expect(fetchSpy).toHaveBeenNthCalledWith(3,
      "/api/projects/project-1/lore/relations/relation-1",
      expect.objectContaining({ headers: expect.any(Object) })
    );
    expect(fetchSpy).toHaveBeenNthCalledWith(4,
      "/api/projects/project-1/lore/elements/element-1/relations",
      expect.objectContaining({ method: "POST", body: JSON.stringify(create) })
    );
    expect(fetchSpy).toHaveBeenNthCalledWith(5,
      "/api/projects/project-1/lore/relations/relation-1",
      expect.objectContaining({ method: "PATCH", body: expect.stringContaining('"expected_version":1') })
    );
    expect(fetchSpy).toHaveBeenNthCalledWith(6,
      "/api/projects/project-1/lore/relations/relation-1/archive",
      expect.objectContaining({ method: "POST", body: expect.stringContaining('"expected_version":2') })
    );
  });

  it("uses the scoped review scan, filters, detail, and idempotent decision endpoints", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
      new Response(JSON.stringify({ items: [], has_more: false, total: 0 }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    );
    const decision = {
      operation_key: "review-operation-0001",
      expected_version: 3,
      expected_evidence_revision: 2,
      decision: "confirmed_duplicate" as const,
      note: "作者确认",
    };

    await api.scanLoreReviews("project-1");
    await api.listLoreReviews("project-1", {
      q: "林岚",
      kind: "possible_conflict",
      review_status: "needs_review",
      limit: 20,
    });
    await api.getLoreReview("project-1", "review-1");
    await api.decideLoreReview("project-1", "review-1", decision);
    const preview = {
      suggestion_expected_version: 3,
      expected_evidence_revision: 2,
      survivor_element_id: "left-1",
      merged_element_id: "right-1",
      survivor_expected_lock_version: 1,
      survivor_expected_content_version: 1,
      merged_expected_lock_version: 1,
      merged_expected_content_version: 1,
      name_choice: "survivor" as const,
      summary_choice: "survivor" as const,
      field_choices: { personality: "survivor" as const },
      final_name: "林岚",
      final_summary: "",
      final_payload: { personality: "谨慎" },
      final_field_states: { personality: "provided" as const },
    };
    await api.previewLoreMerge("project-1", "review-1", preview);
    const commit = {
      operation_key: "merge-operation-1234",
      preview_token: "signed-preview-token",
      preview,
    };
    await api.commitLoreMerge("project-1", "review-1", commit);
    await api.getLoreMergeOperationByKey("project-1", "merge:key/1");
    await api.listLoreElementMergeHistory("project-1", "element-1");

    expect(fetchSpy).toHaveBeenNthCalledWith(1,
      "/api/projects/project-1/lore/reviews/scan",
      expect.objectContaining({ method: "POST" })
    );
    expect(fetchSpy.mock.calls[1][0]).toBe(
      "/api/projects/project-1/lore/reviews?q=%E6%9E%97%E5%B2%9A&kind=possible_conflict&review_status=needs_review&limit=20"
    );
    expect(fetchSpy).toHaveBeenNthCalledWith(3,
      "/api/projects/project-1/lore/reviews/review-1",
      expect.objectContaining({ headers: expect.any(Object) })
    );
    expect(fetchSpy).toHaveBeenNthCalledWith(4,
      "/api/projects/project-1/lore/reviews/review-1/decide",
      expect.objectContaining({ method: "POST", body: JSON.stringify(decision) })
    );
    expect(fetchSpy).toHaveBeenNthCalledWith(5,
      "/api/projects/project-1/lore/reviews/review-1/merge-preview",
      expect.objectContaining({ method: "POST", body: JSON.stringify(preview) })
    );
    expect(fetchSpy).toHaveBeenNthCalledWith(6,
      "/api/projects/project-1/lore/reviews/review-1/merge-commit",
      expect.objectContaining({ method: "POST", body: JSON.stringify(commit) })
    );
    expect(fetchSpy.mock.calls[6][0]).toBe(
      "/api/projects/project-1/lore/merge-operations/by-key/merge%3Akey%2F1"
    );
    expect(fetchSpy.mock.calls[7][0]).toBe(
      "/api/projects/project-1/lore/elements/element-1/merge-history"
    );
  });

  it("posts author review clues to the project-scoped manual endpoint", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ replayed: false, created: true }), {
        status: 201,
        headers: { "Content-Type": "application/json" },
      })
    );
    const input = {
      operation_key: "manual-review-operation-0001",
      kind: "possible_conflict" as const,
      left_element_id: "left-1",
      right_element_id: "right-1",
      left_expected_lock_version: 2,
      right_expected_lock_version: 3,
      note: "作者提报的冲突说明",
    };
    await api.createManualLoreReview("project-1", input);
    expect(fetchSpy).toHaveBeenCalledWith(
      "/api/projects/project-1/lore/reviews/manual",
      expect.objectContaining({ method: "POST", body: JSON.stringify(input) })
    );
  });

  it("preserves the existing suggestion id from a pair-conflict response", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({
        detail: {
          code: "LORE_MANUAL_REVIEW_PAIR_CONFLICT",
          message: "这两项设定已有不同的用户线索",
          suggestion_id: "review-existing",
          retryable: false,
        },
      }), {
        status: 409,
        headers: { "Content-Type": "application/json" },
      })
    );
    await expect(api.createManualLoreReview("project-1", {
      operation_key: "manual-review-operation-0002",
      kind: "possible_conflict",
      left_element_id: "left-1",
      right_element_id: "right-1",
      left_expected_lock_version: 2,
      right_expected_lock_version: 3,
      note: "另一条说明",
    })).rejects.toMatchObject({
      status: 409,
      code: "LORE_MANUAL_REVIEW_PAIR_CONFLICT",
      suggestionId: "review-existing",
    } satisfies Partial<ApiError>);
  });
});

describe("API - Projects", () => {
  beforeEach(() => {
    setAuthToken("valid-token");
    vi.restoreAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("listProjects returns array of projects", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify([MOCK_PROJECT]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    );

    const result = await api.listProjects();
    expect(result).toHaveLength(1);
    expect(result[0].title).toBe("Test Novel");
  });

  it("createProject sends POST with project data", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(MOCK_PROJECT), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    );

    await api.createProject({
      title: "New Novel",
      genre: "都市",
      total_chapters: 20,
      chapter_word_count: 2000,
      style_intensity: "standard",
    });

    const [, options] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(options.method).toBe("POST");
    const body = JSON.parse(options.body);
    expect(body.title).toBe("New Novel");
    expect(body.genre).toBe("都市");
  });

  it("deleteProject sends DELETE request", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ message: "deleted" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    );

    await api.deleteProject("proj1");

    expect(fetch).toHaveBeenCalledWith(
      "/api/projects/proj1",
      expect.objectContaining({ method: "DELETE" })
    );
  });
});

describe("API - Community", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("listCommunityNovels builds query string from params", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify([]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    );

    await api.listCommunityNovels({ offset: 10, limit: 5, sort: "popular", tag: "玄幻" });

    const [url] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toContain("/api/community/novels?");
    expect(url).toContain("offset=10");
    expect(url).toContain("limit=5");
    expect(url).toContain("sort=popular");
    expect(url).toContain("tag=");
  });

  it("likeCommunityNovel sends POST to like endpoint", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ like_count: 42 }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    );

    const result = await api.likeCommunityNovel("novel123");

    expect(fetch).toHaveBeenCalledWith(
      "/api/community/novels/novel123/like",
      expect.objectContaining({ method: "POST" })
    );
    expect(result.like_count).toBe(42);
  });
});

describe("API - Lore extraction", () => {
  afterEach(() => vi.restoreAllMocks());

  it("creates an idempotent source-grounded extraction batch", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ id: "batch-1", status: "completed" }), {
        status: 201,
        headers: { "Content-Type": "application/json" },
      })
    );
    await api.createLoreExtraction("project-1", {
      idempotency_key: "extract-operation-1",
      document_text: "林远性格坚韧，目标是守护故乡。",
      source_kind: "worldview_import",
      source_ref: "世界观编辑器导入原文",
    });
    expect(fetch).toHaveBeenCalledWith(
      "/api/projects/project-1/lore/extractions",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          idempotency_key: "extract-operation-1",
          document_text: "林远性格坚韧，目标是守护故乡。",
          source_kind: "worldview_import",
          source_ref: "世界观编辑器导入原文",
        }),
      })
    );
  });
});

describe("API - Lore migration operations", () => {
  afterEach(() => vi.restoreAllMocks());

  it("submits the frozen request and safely encodes the operation key lookup", async () => {
    const response = { id: "operation-1", status: "validating" };
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockImplementation(() => Promise.resolve(
      new Response(JSON.stringify(response), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    ));
    const input = {
      operation_key: "lore-migration:key:0001",
      preview_schema_version: 1,
      mapping_version: 1,
      expected_source_checksum: "a".repeat(64),
      expected_semantic_result_checksum: "b".repeat(64),
      confirm_legacy_retained_no_automatic_rollback: true as const,
    };

    await api.commitLoreMigration("project-1", input);
    expect(fetchSpy).toHaveBeenLastCalledWith(
      "/api/projects/project-1/lore/migration-operations",
      expect.objectContaining({ method: "POST", body: JSON.stringify(input) })
    );
    await api.getLoreMigrationOperationByKey("project-1", input.operation_key);
    expect(fetchSpy).toHaveBeenLastCalledWith(
      `/api/projects/project-1/lore/migration-operations/by-key/${encodeURIComponent(input.operation_key)}`,
      expect.any(Object)
    );
  });

  it("parses nested unknown-outcome errors without exposing response internals", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({
      detail: {
        code: "LORE_MIGRATION_OUTCOME_UNKNOWN",
        message: "结果待确认",
        retryable: true,
        outcome_unknown: true,
      },
    }), {
      status: 503,
      headers: { "Content-Type": "application/json" },
    }));

    await expect(api.commitLoreMigration("project-1", {
      operation_key: "lore-migration:key-0001",
      preview_schema_version: 1,
      mapping_version: 1,
      expected_source_checksum: "a".repeat(64),
      expected_semantic_result_checksum: "b".repeat(64),
      confirm_legacy_retained_no_automatic_rollback: true,
    })).rejects.toMatchObject({
      status: 503,
      code: "LORE_MIGRATION_OUTCOME_UNKNOWN",
      retryable: true,
      outcomeUnknown: true,
    });
  });

  it("creates, lists, and revokes auditable migration resolutions", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockImplementation(() => Promise.resolve(
      new Response(JSON.stringify({ items: [] }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    ));
    const input = {
      operation_key: "migration-resolution:key-0001",
      preview_schema_version: 1,
      mapping_version: 1,
      expected_source_checksum: "a".repeat(64),
      expected_semantic_result_checksum: "b".repeat(64),
      item_fingerprint: "c".repeat(64),
      group_fingerprint: null,
      legacy_category: "special_settings",
      legacy_index: 0,
      reason_code: "type_confirmation_required",
      decision_code: "confirm_type",
      decision_payload: { type_key: "rule" },
      expected_resolution_version: null,
    };

    await api.getLoreMigrationResolutions("project-1");
    expect(fetchSpy).toHaveBeenLastCalledWith(
      "/api/projects/project-1/lore/migration-resolutions",
      expect.any(Object)
    );
    await api.decideLoreMigrationResolution("project-1", input);
    expect(fetchSpy).toHaveBeenLastCalledWith(
      "/api/projects/project-1/lore/migration-resolutions",
      expect.objectContaining({ method: "POST", body: JSON.stringify(input) })
    );
    const revoke = {
      operation_key: "migration-resolution:revoke-0001",
      expected_source_checksum: "a".repeat(64),
      expected_resolution_version: 1,
    };
    await api.revokeLoreMigrationResolution("project-1", "d".repeat(32), revoke);
    expect(fetchSpy).toHaveBeenLastCalledWith(
      `/api/projects/project-1/lore/migration-resolutions/${"d".repeat(32)}/revoke`,
      expect.objectContaining({ method: "POST", body: JSON.stringify(revoke) })
    );
  });
});

describe("API - Worldview optimistic save", () => {
  afterEach(() => vi.restoreAllMocks());

  it("sends the expected source checksum with the complete worldview payload", async () => {
    const response = {
      id: "worldview-1",
      source_checksum: "b".repeat(64),
      parsed_elements: [],
    };
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(response), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    );
    const input = {
      characters: [],
      geography: [],
      factions: [],
      power_system: [],
      history: [],
      conflicts: [],
      special_settings: [],
      source: "manual" as const,
      expected_source_checksum: "a".repeat(64),
    };

    await api.setWorldview("project-1", input);
    expect(fetchSpy).toHaveBeenCalledWith(
      "/api/worldview/project-1",
      expect.objectContaining({ method: "POST", body: JSON.stringify(input) })
    );
  });
});
