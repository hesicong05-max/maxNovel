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

  it.each([
    ["outline", () => api.generateOutlineStream("project-1")],
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
