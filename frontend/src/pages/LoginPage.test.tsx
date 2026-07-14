import { describe, it, expect, beforeEach, vi, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AuthProvider } from "@/components/AuthContext";
import * as apiModule from "@/services/api";
import LoginPage from "./LoginPage";
import type { AuthResponse, AuthUser } from "@/types";

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

function renderLoginPage() {
  return render(
    <AuthProvider>
      <LoginPage />
    </AuthProvider>
  );
}

/** Helper: get the submit button (the one with class login-submit, not the tab button) */
function getSubmitButton(): HTMLElement {
  return document.querySelector(".login-submit") as HTMLElement;
}

/** Helper: get a tab button by its text */
function getTab(text: string): HTMLElement {
  const tabs = document.querySelectorAll(".login-tab");
  for (const tab of tabs) {
    if (tab.textContent === text) return tab as HTMLElement;
  }
  throw new Error(`Tab "${text}" not found`);
}

/** Helper: click the bottom switch text */
function getSwitchText(text: string): HTMLElement {
  const spans = document.querySelectorAll(".login-switch span");
  for (const span of spans) {
    if (span.textContent?.includes(text)) return span as HTMLElement;
  }
  throw new Error(`Switch text "${text}" not found`);
}

describe("LoginPage", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders login form with email and password fields", () => {
    renderLoginPage();

    expect(screen.getByText("满分小说")).toBeInTheDocument();
    expect(screen.getByPlaceholderText("you@example.com")).toBeInTheDocument();
    expect(screen.getByPlaceholderText("至少 6 个字符")).toBeInTheDocument();
    expect(getSubmitButton()).toBeInTheDocument();
  });

  it("shows login tab as active by default", () => {
    renderLoginPage();

    const loginTab = getTab("登录");
    expect(loginTab).toHaveClass("active");
  });

  it("switches to register mode when clicking register tab", async () => {
    renderLoginPage();

    await userEvent.click(getTab("注册"));

    // Register mode should show username field
    expect(screen.getByPlaceholderText("2-50 个字符")).toBeInTheDocument();
  });

  it("switches back to login mode", async () => {
    renderLoginPage();

    // Switch to register
    await userEvent.click(getTab("注册"));
    expect(screen.getByPlaceholderText("2-50 个字符")).toBeInTheDocument();

    // Switch back to login
    await userEvent.click(getTab("登录"));
    expect(screen.queryByPlaceholderText("2-50 个字符")).not.toBeInTheDocument();
  });

  it("shows loading state when submitting form", async () => {
    // Mock a slow API response
    vi.spyOn(apiModule, "api", "get").mockReturnValue({
      ...apiModule.api,
      login: vi.fn().mockImplementation(
        () => new Promise((resolve) => setTimeout(() => resolve(MOCK_AUTH_RESPONSE), 500))
      ),
    });

    renderLoginPage();

    const emailInput = screen.getByPlaceholderText("you@example.com");
    const passwordInput = screen.getByPlaceholderText("至少 6 个字符");

    await userEvent.type(emailInput, "test@example.com");
    await userEvent.type(passwordInput, "password123");
    await userEvent.click(getSubmitButton());

    // Should show loading state
    expect(getSubmitButton().textContent).toBe("处理中...");
    expect(getSubmitButton()).toBeDisabled();

    // Wait for completion
    await waitFor(() => {
      expect(getSubmitButton().textContent).toBe("登录");
    });
  });

  it("displays error message when login fails", async () => {
    vi.spyOn(apiModule, "api", "get").mockReturnValue({
      ...apiModule.api,
      login: vi.fn().mockRejectedValue(new Error("邮箱或密码错误")),
    });

    renderLoginPage();

    await userEvent.type(screen.getByPlaceholderText("you@example.com"), "test@example.com");
    await userEvent.type(screen.getByPlaceholderText("至少 6 个字符"), "wrongpass");
    await userEvent.click(getSubmitButton());

    await waitFor(() => {
      expect(screen.getByText("邮箱或密码错误")).toBeInTheDocument();
    });
  });

  it("switches mode via bottom switch text", async () => {
    renderLoginPage();

    // Click the bottom "还没有账号？点击注册"
    await userEvent.click(getSwitchText("还没有账号"));

    expect(screen.getByPlaceholderText("2-50 个字符")).toBeInTheDocument();

    // Switch back
    await userEvent.click(getSwitchText("已有账号"));

    expect(screen.queryByPlaceholderText("2-50 个字符")).not.toBeInTheDocument();
  });

  it("register form calls register API with username", async () => {
    const mockRegister = vi.fn().mockResolvedValue(MOCK_AUTH_RESPONSE);
    vi.spyOn(apiModule, "api", "get").mockReturnValue({
      ...apiModule.api,
      register: mockRegister,
    });

    renderLoginPage();

    // Switch to register mode via tab
    await userEvent.click(getTab("注册"));

    await userEvent.type(screen.getByPlaceholderText("you@example.com"), "new@example.com");
    await userEvent.type(screen.getByPlaceholderText("2-50 个字符"), "newuser");
    await userEvent.type(screen.getByPlaceholderText("至少 6 个字符"), "password123");

    // Click the submit button (not the tab button)
    await userEvent.click(getSubmitButton());

    await waitFor(() => {
      expect(mockRegister).toHaveBeenCalledWith({
        email: "new@example.com",
        username: "newuser",
        password: "password123",
      });
    });
  });
});
