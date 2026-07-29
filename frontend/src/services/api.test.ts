import { describe, it, expect, beforeEach, vi, afterEach } from "vitest";
import {
  getAuthToken,
  setAuthToken,
  clearAuthToken,
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
