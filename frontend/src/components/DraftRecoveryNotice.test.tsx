import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import DraftRecoveryNotice from "./DraftRecoveryNotice";

const savedAt = "2026-07-30T10:00:00.000Z";

describe("DraftRecoveryNotice", () => {
  it.each([
    ["available", "尚未保存到项目"],
    ["expired", "超过保留期限"],
    ["conflict", "人工核对"],
  ] as const)("shows the %s recovery state", (state, text) => {
    render(
      <DraftRecoveryNotice
        state={state}
        savedAt={savedAt}
        onRestore={vi.fn()}
        onCopy={vi.fn()}
        onDiscard={vi.fn()}
      />
    );

    expect(screen.getByRole("group", { name: "发现本地草稿" })).toHaveTextContent(text);
    expect(screen.getByRole("button", { name: "载入本地副本" })).toBeEnabled();
    for (const button of screen.getAllByRole("button")) {
      expect(button).toHaveClass("btn");
    }
    expect(screen.queryByText(savedAt)).toBeNull();
  });

  it("uses unique labels when more than one notice is present", () => {
    render(
      <>
        <DraftRecoveryNotice
          state="available"
          savedAt={savedAt}
          onRestore={vi.fn()}
          onCopy={vi.fn()}
          onDiscard={vi.fn()}
        />
        <DraftRecoveryNotice
          state="conflict"
          savedAt={savedAt}
          onRestore={vi.fn()}
          onCopy={vi.fn()}
          onDiscard={vi.fn()}
        />
      </>
    );

    const labelledBy = screen
      .getAllByRole("group", { name: "发现本地草稿" })
      .map((notice) => notice.getAttribute("aria-labelledby"));
    expect(new Set(labelledBy).size).toBe(2);
  });

  it("requires explicit confirmation before discarding only the local draft", async () => {
    const user = userEvent.setup();
    const discard = vi.fn();
    const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false);
    render(
      <DraftRecoveryNotice
        state="available"
        savedAt={savedAt}
        onRestore={vi.fn()}
        onCopy={vi.fn()}
        onDiscard={discard}
      />
    );

    await user.click(screen.getByRole("button", { name: "丢弃本地草稿" }));
    expect(confirm).toHaveBeenCalledWith(
      "只删除本地草稿，不影响项目中已保存的内容。确定继续吗？"
    );
    expect(discard).not.toHaveBeenCalled();

    confirm.mockReturnValueOnce(true);
    await user.click(screen.getByRole("button", { name: "丢弃本地草稿" }));
    expect(discard).toHaveBeenCalledOnce();
  });
});
