import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ApiError } from "@/services/api";
import MaintenanceNotice from "./MaintenanceNotice";

function maintenanceError(retryable = true) {
  return new ApiError(503, {
    detail: "服务端不可信的内部错误内容",
    code: "PROJECT_WRITE_FROZEN",
    maintenance_state: "write_frozen",
    retryable,
    retry_after_seconds: 60,
    event_id: "BUG-002B",
  });
}

describe("MaintenanceNotice", () => {
  it("shows safe copy and focuses the alert without exposing server detail", () => {
    render(
      <MaintenanceNotice
        error={maintenanceError()}
        draftStored
        onCopy={vi.fn()}
        focusOnMount
      />
    );

    const alert = screen.getByRole("alert");
    expect(alert).toHaveFocus();
    expect(screen.getByText("未保存的内容已保留在此设备。")).toBeInTheDocument();
    expect(screen.getByText(/BUG-002B/)).toBeInTheDocument();
    expect(
      screen.queryByText("服务端不可信的内部错误内容")
    ).not.toBeInTheDocument();
  });

  it("does not steal focus for a background maintenance update", () => {
    const existing = document.createElement("button");
    document.body.appendChild(existing);
    existing.focus();
    render(
      <MaintenanceNotice
        error={maintenanceError()}
        draftStored
        onCopy={vi.fn()}
      />
    );

    expect(existing).toHaveFocus();
  });

  it("warns clearly when the local draft could not be stored", () => {
    render(
      <MaintenanceNotice
        error={maintenanceError()}
        draftStored={false}
        onCopy={vi.fn()}
      />
    );

    expect(
      screen.getByText("本地草稿也未能保存，请立即复制内容，避免丢失。")
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "复制未保存内容" })).toHaveClass(
      "btn",
      "btn-primary"
    );
  });

  it("only offers manual retry for retryable errors", async () => {
    const user = userEvent.setup();
    const retry = vi.fn();
    const copy = vi.fn();
    const { rerender } = render(
      <MaintenanceNotice
        error={maintenanceError(false)}
        draftStored
        onCopy={copy}
        onRetry={retry}
      />
    );

    expect(screen.queryByRole("button", { name: "手动重试保存" })).toBeNull();
    expect(
      screen.queryByText("请稍后手动重试，系统不会自动重复提交。")
    ).toBeNull();
    await user.click(screen.getByRole("button", { name: "复制未保存内容" }));
    expect(copy).toHaveBeenCalledOnce();

    rerender(
      <MaintenanceNotice
        error={maintenanceError(true)}
        draftStored
        onCopy={copy}
        onRetry={retry}
      />
    );
    await user.click(screen.getByRole("button", { name: "手动重试保存" }));
    expect(retry).toHaveBeenCalledOnce();
    expect(screen.getByRole("button", { name: "手动重试保存" })).toHaveClass(
      "btn",
      "btn-primary"
    );
  });
});
