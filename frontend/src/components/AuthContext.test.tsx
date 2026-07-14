import { describe, it, expect, beforeEach, vi, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AuthProvider, useAuth } from "./AuthContext";
import * as apiModule from "@/services/api";
import type { AuthUser, AuthResponse } from "@/types";

// ── Test component that exposes auth state ──
function AuthConsumer() {
  const { user, loading, isAuthenticated, logout } = useAuth();
  return (
    <div>
      <div data-testid="loading">{String(loading)}</div>
      <div data-testid="authenticated">{String(isAuthenticated)}</div>
      <div data-testid="username">{user?.username || "none"}</div>
      <button onClick={logout} data-testid="logout-btn">
        Logout
      </button>
    </div>
  );
}

const MOCK_USER: AuthUser = {
  id: "user1",
  email: "test@example.com",
  username: "testuser",
  created_at: "2026-01-01T00:00:00Z",
};

const MOCK_AUTH_RESPONSE: AuthResponse = {
  token: "jwt-token-xyz",
  user: MOCK_USER,
};

// Helper: mock the api module's fetch function
function mockFetchResponse(response: Response) {
  return vi.spyOn(globalThis, "fetch").mockResolvedValue(response);
}

describe("AuthProvider", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("starts with loading=true, then becomes false when no token", async () => {
    render(
      <AuthProvider>
        <AuthConsumer />
      </AuthProvider>
    );

    // After effect runs, loading should be false (no token → no API call)
    await waitFor(() => {
      expect(screen.getByTestId("loading").textContent).toBe("false");
    });

    expect(screen.getByTestId("authenticated").textContent).toBe("false");
    expect(screen.getByTestId("username").textContent).toBe("none");
  });

  it("restores user when token exists in localStorage", async () => {
    // Pre-set a token
    localStorage.setItem("novel_auth_token", "existing-token");

    // Mock fetch to return user for getMe
    mockFetchResponse(
      new Response(JSON.stringify(MOCK_USER), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    );

    render(
      <AuthProvider>
        <AuthConsumer />
      </AuthProvider>
    );

    await waitFor(() => {
      expect(screen.getByTestId("loading").textContent).toBe("false");
    });

    expect(screen.getByTestId("authenticated").textContent).toBe("true");
    expect(screen.getByTestId("username").textContent).toBe("testuser");
  });

  it("clears token if getMe fails on restore", async () => {
    localStorage.setItem("novel_auth_token", "invalid-token");

    // Mock fetch to return 500 (will throw in fetchJSON, caught by .catch())
    mockFetchResponse(
      new Response(JSON.stringify({ detail: "Server error" }), {
        status: 500,
        headers: { "Content-Type": "application/json" },
      })
    );

    render(
      <AuthProvider>
        <AuthConsumer />
      </AuthProvider>
    );

    await waitFor(() => {
      expect(screen.getByTestId("loading").textContent).toBe("false");
    });

    // Token should be cleared (catch handler calls clearAuthToken)
    expect(localStorage.getItem("novel_auth_token")).toBeNull();
    expect(screen.getByTestId("authenticated").textContent).toBe("false");
  });

  it("logout clears auth state", async () => {
    // Start with a logged-in state
    localStorage.setItem("novel_auth_token", "token-123");

    mockFetchResponse(
      new Response(JSON.stringify(MOCK_USER), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    );

    render(
      <AuthProvider>
        <AuthConsumer />
      </AuthProvider>
    );

    // Wait for user to be loaded
    await waitFor(() => {
      expect(screen.getByTestId("authenticated").textContent).toBe("true");
    });

    // Click logout
    await userEvent.click(screen.getByTestId("logout-btn"));

    // Auth state should be cleared
    expect(screen.getByTestId("authenticated").textContent).toBe("false");
    expect(screen.getByTestId("username").textContent).toBe("none");
    expect(localStorage.getItem("novel_auth_token")).toBeNull();
  });
});

describe("useAuth outside AuthProvider", () => {
  it("throws error when used without AuthProvider", () => {
    // Suppress console.error from React
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});

    // We need to catch the error thrown during render
    function BrokenComponent() {
      useAuth();
      return null;
    }

    expect(() => render(<BrokenComponent />)).toThrow(
      "useAuth must be used within AuthProvider"
    );

    spy.mockRestore();
  });
});
