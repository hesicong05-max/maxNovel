import { describe, it, expect, beforeEach, vi, afterEach, afterAll } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import ErrorBoundary from "./ErrorBoundary";

// Suppress console.error from React's error handling in tests
const consoleSpy = vi.spyOn(console, "error").mockImplementation(() => {});

// ── Helper: a component that throws ──
function ThrowOnRender({ error }: { error: Error }): null {
  throw error;
}

// ── Helper: a component that renders normally ──
function NormalChild() {
  return <div data-testid="child">Normal Content</div>;
}

describe("ErrorBoundary", () => {
  beforeEach(() => {
    consoleSpy.mockClear();
  });

  afterEach(() => {
    consoleSpy.mockClear();
  });

  it("renders children normally when no error", () => {
    render(
      <ErrorBoundary>
        <NormalChild />
      </ErrorBoundary>
    );

    expect(screen.getByTestId("child")).toBeInTheDocument();
    expect(screen.queryByText("页面出错了")).not.toBeInTheDocument();
  });

  it("renders error UI when child throws", () => {
    render(
      <ErrorBoundary>
        <ThrowOnRender error={new Error("Test render error")} />
      </ErrorBoundary>
    );

    expect(screen.getByText("页面出错了")).toBeInTheDocument();
    expect(screen.queryByTestId("child")).not.toBeInTheDocument();
  });

  it("shows user-friendly error message", () => {
    render(
      <ErrorBoundary>
        <ThrowOnRender error={new Error("Something broke")} />
      </ErrorBoundary>
    );

    expect(
      screen.getByText("应用遇到了一个意外错误。你可以刷新页面重试，或者返回首页。")
    ).toBeInTheDocument();
  });

  it("provides reload and go home buttons", () => {
    render(
      <ErrorBoundary>
        <ThrowOnRender error={new Error("Crash")} />
      </ErrorBoundary>
    );

    expect(screen.getByText("刷新页面")).toBeInTheDocument();
    expect(screen.getByText("返回首页")).toBeInTheDocument();
  });

  it("logs error to console", async () => {
    // Set up spy fresh for this test
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    const testError = new Error("Logged error");

    render(
      <ErrorBoundary>
        <ThrowOnRender error={testError} />
      </ErrorBoundary>
    );

    // componentDidCatch runs synchronously after render in class components
    expect(spy).toHaveBeenCalled();

    // Find the call with our error message
    const errorCalls = spy.mock.calls.filter(
      (call) => call[0] === "[ErrorBoundary] Unhandled render error:"
    );
    expect(errorCalls.length).toBeGreaterThan(0);
    expect(errorCalls[0][1]).toBe(testError);

    spy.mockRestore();
  });

  it("shows error details in dev mode", () => {
    render(
      <ErrorBoundary>
        <ThrowOnRender error={new Error("Dev error message")} />
      </ErrorBoundary>
    );

    // Error details should be visible (summary element)
    expect(screen.getByText("错误详情")).toBeInTheDocument();
  });
});

afterAll(() => {
  consoleSpy.mockRestore();
});
