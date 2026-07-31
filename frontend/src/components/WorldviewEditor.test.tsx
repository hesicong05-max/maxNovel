import { describe, it, expect, beforeEach, vi, afterEach } from "vitest";
import { render, screen, waitFor, fireEvent, cleanup, act, within } from "@testing-library/react";
import WorldviewEditor from "./WorldviewEditor";
import { saveDraft, clearDraft, loadDraft, draftKey } from "./WorldviewDraftStorage";
import type { WorldviewData } from "@/types";

// Mock AuthContext — provides a stable user ID for draft isolation
vi.mock("@/components/AuthContext", () => ({
  useAuth: () => ({
    user: { id: "test-user-1", email: "test@test.com", username: "test", created_at: "" },
  }),
}));

// Mock API — avoids real fetch calls; each test configures its own behavior
vi.mock("@/services/api", () => ({
  api: {
    getWorldview: vi.fn(),
    setWorldview: vi.fn(),
    importWorldview: vi.fn(),
    uploadWorldviewFile: vi.fn(),
  },
}));

// Import after mock to get the mocked version
import { api } from "@/services/api";
const mockedApi = vi.mocked(api);

const EMPTY_DATA: WorldviewData = {
  characters: [],
  geography: [],
  factions: [],
  power_system: [],
  history: [],
  conflicts: [],
  special_settings: [],
};

const PROPS = {
  projectId: "proj-1",
  hasWorldview: false,
  genre: "玄幻",
  onComplete: vi.fn(),
  onBack: vi.fn(),
};

beforeEach(() => {
  localStorage.clear();
  vi.clearAllMocks();
  // Default: no existing worldview on server (404)
  mockedApi.getWorldview.mockRejectedValue(new Error("404 Not Found"));
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

// ═══════════════════════════════════════════════════
// P1-7: Accessibility
// ═══════════════════════════════════════════════════
describe("WorldviewEditor — accessibility", () => {
  it("renders a single polite live region for status", () => {
    render(<WorldviewEditor {...PROPS} />);
    const liveRegion = screen.getByRole("status");
    expect(liveRegion).toHaveAttribute("aria-live", "polite");
  });

  it("sets aria-pressed on the active mode button", () => {
    render(<WorldviewEditor {...PROPS} />);
    const manualBtn = screen.getByText("手动创建").closest("button")!;
    expect(manualBtn).toHaveAttribute("aria-pressed", "true");

    const importBtn = screen.getByText("导入文档").closest("button")!;
    expect(importBtn).toHaveAttribute("aria-pressed", "false");
  });

  it("updates aria-pressed when switching modes", () => {
    render(<WorldviewEditor {...PROPS} />);
    const importBtn = screen.getByText("导入文档").closest("button")!;
    fireEvent.click(importBtn);
    expect(importBtn).toHaveAttribute("aria-pressed", "true");

    const manualBtn = screen.getByText("手动创建").closest("button")!;
    expect(manualBtn).toHaveAttribute("aria-pressed", "false");
  });

  // P1-6: delete buttons must have accessible names with index
  it("gives delete buttons accessible names with index and name", () => {
    render(<WorldviewEditor {...PROPS} />);

    // Add two characters
    fireEvent.click(screen.getByText("+ 添加角色"));
    fireEvent.click(screen.getByText("+ 添加角色"));

    // Fill first character name
    const nameInputs = screen.getAllByPlaceholderText("姓名");
    fireEvent.change(nameInputs[0], { target: { value: "Alice" } });

    // Check aria-labels
    const deleteBtns = screen.getAllByLabelText(/删除角色/);
    expect(deleteBtns).toHaveLength(2);
    expect(deleteBtns[0]).toHaveAttribute("aria-label", "删除角色 1：Alice");
    expect(deleteBtns[1]).toHaveAttribute("aria-label", "删除角色 2");
  });
});

// ═══════════════════════════════════════════════════
// Draft recovery
// ═══════════════════════════════════════════════════
describe("WorldviewEditor — draft recovery", () => {
  it("shows recovery dialog when a valid draft exists", async () => {
    saveDraft("test-user-1", "proj-1", {
      data: { ...EMPTY_DATA, characters: [{ name: "Drafted Hero", personality: "", background: "", motivation: "", ability: "", relations: [] }] },
      importText: "",
      mode: "manual",
      source: "manual",
      savedAt: Date.now(),
      schemaVersion: 1,
    });

    render(<WorldviewEditor {...PROPS} />);
    expect(await screen.findByText("发现未保存的草稿")).toBeInTheDocument();
    expect(screen.getByText("恢复草稿")).toBeInTheDocument();
    expect(screen.getByText("丢弃草稿")).toBeInTheDocument();
  });

  it("restores draft data when clicking restore", async () => {
    saveDraft("test-user-1", "proj-1", {
      data: { ...EMPTY_DATA, characters: [{ name: "Restored Hero", personality: "", background: "", motivation: "", ability: "", relations: [] }] },
      importText: "",
      mode: "manual",
      source: "manual",
      savedAt: Date.now(),
      schemaVersion: 1,
    });

    render(<WorldviewEditor {...PROPS} />);
    await screen.findByText("发现未保存的草稿");
    fireEvent.click(screen.getByText("恢复草稿"));

    await waitFor(() => {
      expect(screen.getByText("角色 (1)")).toBeInTheDocument();
      expect(screen.getByDisplayValue("Restored Hero")).toBeInTheDocument();
    });
  });

  it("clears draft when clicking discard (valid draft)", async () => {
    saveDraft("test-user-1", "proj-1", {
      data: EMPTY_DATA,
      importText: "discard me",
      mode: "manual",
      source: "manual",
      savedAt: Date.now(),
      schemaVersion: 1,
    });

    render(<WorldviewEditor {...PROPS} />);
    await screen.findByText("发现未保存的草稿");
    fireEvent.click(screen.getByText("丢弃草稿"));

    await waitFor(() => {
      expect(screen.queryByText("发现未保存的草稿")).not.toBeInTheDocument();
    });
    // Draft should be cleared from localStorage
    expect(loadDraft("test-user-1", "proj-1").status).toBe("none");
  });

  it("shows only discard for corrupted drafts (no restore button)", async () => {
    localStorage.setItem("wv-draft:test-user-1:proj-1", "{corrupt json");

    render(<WorldviewEditor {...PROPS} />);
    expect(await screen.findByText("草稿已损坏")).toBeInTheDocument();
    expect(screen.queryByText("恢复草稿")).not.toBeInTheDocument();
    expect(screen.getByText("确认丢弃")).toBeInTheDocument();
  });

  // P0-5: overlay click does NOT discard corrupt draft
  it("does NOT discard corrupt draft when clicking overlay", async () => {
    localStorage.setItem("wv-draft:test-user-1:proj-1", "{corrupt json");

    render(<WorldviewEditor {...PROPS} />);
    await screen.findByText("草稿已损坏");

    // Click the overlay (the outer div)
    const overlay = document.querySelector(".draft-overlay") as HTMLElement;
    fireEvent.click(overlay);

    // Corrupt data should still be in localStorage
    expect(localStorage.getItem("wv-draft:test-user-1:proj-1")).toBe("{corrupt json");
  });

  // P0-5: ESC does NOT discard corrupt draft
  it("does NOT discard corrupt draft when pressing Escape", async () => {
    localStorage.setItem("wv-draft:test-user-1:proj-1", "{corrupt json");

    render(<WorldviewEditor {...PROPS} />);
    await screen.findByText("草稿已损坏");

    // Press Escape on the dialog
    const dialog = screen.getByRole("dialog");
    fireEvent.keyDown(dialog, { key: "Escape" });

    // Corrupt data should still be in localStorage
    expect(localStorage.getItem("wv-draft:test-user-1:proj-1")).toBe("{corrupt json");
  });

  // P0-5: confirm discard requires secondary confirmation
  it("requires secondary confirmation for corrupt draft discard", async () => {
    localStorage.setItem("wv-draft:test-user-1:proj-1", "{corrupt json");

    render(<WorldviewEditor {...PROPS} />);
    await screen.findByText("草稿已损坏");

    // Click "确认丢弃" — should show secondary confirmation, NOT clear yet
    fireEvent.click(screen.getByText("确认丢弃"));

    await waitFor(() => {
      expect(screen.getByText("确认永久丢弃")).toBeInTheDocument();
    });
    // Corrupt data should still be in localStorage
    expect(localStorage.getItem("wv-draft:test-user-1:proj-1")).toBe("{corrupt json");
  });

  // P0-5: actually discard after secondary confirmation
  it("clears corrupt draft only after secondary confirmation", async () => {
    localStorage.setItem("wv-draft:test-user-1:proj-1", "{corrupt json");

    render(<WorldviewEditor {...PROPS} />);
    await screen.findByText("草稿已损坏");

    // First "确认丢弃"
    fireEvent.click(screen.getByText("确认丢弃"));
    await screen.findByText("确认永久丢弃");

    // Secondary "确认丢弃"
    const confirmBtns = screen.getAllByText("确认丢弃");
    // The last one is in the secondary dialog
    fireEvent.click(confirmBtns[confirmBtns.length - 1]);

    await waitFor(() => {
      expect(screen.queryByText("确认永久丢弃")).not.toBeInTheDocument();
    });
    // Now corrupt data should be cleared
    expect(localStorage.getItem("wv-draft:test-user-1:proj-1")).toBeNull();
  });

  // P0-5: cancel secondary confirmation goes back to first dialog
  it("cancel on secondary confirmation returns to first dialog", async () => {
    localStorage.setItem("wv-draft:test-user-1:proj-1", "{corrupt json");

    render(<WorldviewEditor {...PROPS} />);
    await screen.findByText("草稿已损坏");

    fireEvent.click(screen.getByText("确认丢弃"));
    await screen.findByText("确认永久丢弃");

    // Click "取消"
    fireEvent.click(screen.getByText("取消"));

    // Should be back to first dialog (not showing secondary)
    await waitFor(() => {
      expect(screen.queryByText("确认永久丢弃")).not.toBeInTheDocument();
      expect(screen.getByText("草稿已损坏")).toBeInTheDocument();
    });
    // Corrupt data still in localStorage
    expect(localStorage.getItem("wv-draft:test-user-1:proj-1")).toBe("{corrupt json");
  });

  // P0-5: copy content available before discard
  it("provides copy content button in corrupt draft dialog", async () => {
    localStorage.setItem("wv-draft:test-user-1:proj-1", "{corrupt json");

    render(<WorldviewEditor {...PROPS} />);
    await screen.findByText("草稿已损坏");

    expect(screen.getByText("复制内容")).toBeInTheDocument();
  });
});

// ═══════════════════════════════════════════════════
// Error sanitization
// ═══════════════════════════════════════════════════
describe("WorldviewEditor — error sanitization", () => {
  it("does not leak backend detail on save failure", async () => {
    render(<WorldviewEditor {...PROPS} />);

    await waitFor(() => {
      expect(screen.getByText("手动创建")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("+ 添加角色"));

    const secretDetail = "Internal server error: database connection to 10.0.0.5:5432 failed";
    mockedApi.setWorldview.mockRejectedValue(new Error(secretDetail));

    fireEvent.click(screen.getByText("保存世界观"));

    await waitFor(() => {
      const liveRegion = screen.getByRole("status");
      expect(liveRegion.textContent).not.toContain(secretDetail);
      expect(liveRegion.textContent).toContain("保存失败");
    });

    expect(screen.getByText("复制当前内容")).toBeInTheDocument();
  });

  it("preserves auth error messages (login expired)", async () => {
    render(<WorldviewEditor {...PROPS} />);

    await waitFor(() => {
      expect(screen.getByText("手动创建")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("+ 添加角色"));

    mockedApi.setWorldview.mockRejectedValue(new Error("登录已过期，请重新登录"));

    fireEvent.click(screen.getByText("保存世界观"));

    await waitFor(() => {
      const liveRegion = screen.getByRole("status");
      expect(liveRegion.textContent).toContain("登录已过期");
    });
  });
});

// ═══════════════════════════════════════════════════
// P1-6: Back/next protection
// ═══════════════════════════════════════════════════
describe("WorldviewEditor — back/next protection", () => {
  it("shows confirmation when navigating back with unsaved changes", async () => {
    render(<WorldviewEditor {...PROPS} />);

    await waitFor(() => {
      expect(screen.getByText("手动创建")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("+ 添加角色"));
    fireEvent.click(screen.getByText("← 返回项目详情"));

    await waitFor(() => {
      expect(screen.getByText("未保存的修改")).toBeInTheDocument();
    });

    expect(PROPS.onBack).not.toHaveBeenCalled();
  });

  it("proceeds immediately when no unsaved changes", async () => {
    render(<WorldviewEditor {...PROPS} />);

    await waitFor(() => {
      expect(screen.getByText("手动创建")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("← 返回项目详情"));

    expect(PROPS.onBack).toHaveBeenCalled();
  });

  // P1-6: ESC cancels leave
  it("ESC cancels leave in unsaved confirm dialog", async () => {
    render(<WorldviewEditor {...PROPS} />);

    await waitFor(() => {
      expect(screen.getByText("手动创建")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("+ 添加角色"));
    fireEvent.click(screen.getByText("← 返回项目详情"));

    await screen.findByText("未保存的修改");

    const dialog = screen.getByRole("dialog");
    fireEvent.keyDown(dialog, { key: "Escape" });

    await waitFor(() => {
      expect(screen.queryByText("未保存的修改")).not.toBeInTheDocument();
    });
    expect(PROPS.onBack).not.toHaveBeenCalled();
  });

  // P1-6: overlay click cancels leave (not confirm)
  it("overlay click cancels leave in unsaved confirm dialog", async () => {
    render(<WorldviewEditor {...PROPS} />);

    await waitFor(() => {
      expect(screen.getByText("手动创建")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("+ 添加角色"));
    fireEvent.click(screen.getByText("← 返回项目详情"));

    await screen.findByText("未保存的修改");

    const overlay = document.querySelector(".draft-overlay") as HTMLElement;
    fireEvent.click(overlay);

    await waitFor(() => {
      expect(screen.queryByText("未保存的修改")).not.toBeInTheDocument();
    });
    expect(PROPS.onBack).not.toHaveBeenCalled();
  });

  // P1-6: confirm leave saves draft and navigates
  it("confirm leave saves draft and navigates", async () => {
    render(<WorldviewEditor {...PROPS} />);

    await waitFor(() => {
      expect(screen.getByText("手动创建")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("+ 添加角色"));
    const nameInput = screen.getByPlaceholderText("姓名");
    fireEvent.change(nameInput, { target: { value: "TestChar" } });

    fireEvent.click(screen.getByText("← 返回项目详情"));
    await screen.findByText("未保存的修改");

    fireEvent.click(screen.getByText("确认离开"));

    await waitFor(() => {
      expect(PROPS.onBack).toHaveBeenCalled();
    });

    // Draft should be saved
    const result = loadDraft("test-user-1", "proj-1");
    expect(result.status).toBe("ok");
    if (result.status === "ok") {
      expect(result.draft.data.characters[0].name).toBe("TestChar");
    }
  });
});

// ═══════════════════════════════════════════════════
// P0-2: Save race condition
// ═══════════════════════════════════════════════════
describe("WorldviewEditor — save race condition", () => {
  it("preserves new edits made during save request", async () => {
    let resolveSave!: (v: unknown) => void;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    mockedApi.setWorldview.mockImplementation(
      () => new Promise<any>((resolve) => { resolveSave = resolve; })
    );

    render(<WorldviewEditor {...PROPS} />);

    await waitFor(() => {
      expect(screen.getByText("手动创建")).toBeInTheDocument();
    });

    // Add first character (will be in the save request)
    fireEvent.click(screen.getByText("+ 添加角色"));
    const nameInputs = screen.getAllByPlaceholderText("姓名");
    fireEvent.change(nameInputs[0], { target: { value: "Submitted" } });

    // Start save
    fireEvent.click(screen.getByText("保存世界观"));

    // While save is in-flight, add another character (new edit)
    fireEvent.click(screen.getByText("+ 添加角色"));

    // Resolve the save
    await act(async () => {
      resolveSave({});
    });

    // The new character should still be in the editor (not overwritten by reload)
    await waitFor(() => {
      expect(screen.getByText("角色 (2)")).toBeInTheDocument();
    });
  });

  it("does not clear draft when new edits made during save", async () => {
    let resolveSave!: (v: unknown) => void;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    mockedApi.setWorldview.mockImplementation(
      () => new Promise<any>((resolve) => { resolveSave = resolve; })
    );

    render(<WorldviewEditor {...PROPS} />);

    await waitFor(() => {
      expect(screen.getByText("手动创建")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("+ 添加角色"));
    const nameInputs = screen.getAllByPlaceholderText("姓名");
    fireEvent.change(nameInputs[0], { target: { value: "First" } });

    fireEvent.click(screen.getByText("保存世界观"));

    // New edit during save
    fireEvent.click(screen.getByText("+ 添加角色"));

    await act(async () => {
      resolveSave({});
    });

    await waitFor(() => {
      expect(screen.getByText("角色 (2)")).toBeInTheDocument();
    });

    // Draft should NOT be cleared — new edits need preservation
    const draftResult = loadDraft("test-user-1", "proj-1");
    expect(draftResult.status).toBe("ok");
  });

  it("shows specific message when server saved older version but new edits exist", async () => {
    let resolveSave!: (v: unknown) => void;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    mockedApi.setWorldview.mockImplementation(
      () => new Promise<any>((resolve) => { resolveSave = resolve; })
    );

    render(<WorldviewEditor {...PROPS} />);

    await waitFor(() => {
      expect(screen.getByText("手动创建")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("+ 添加角色"));
    fireEvent.click(screen.getByText("保存世界观"));

    // New edit during save
    fireEvent.click(screen.getByText("+ 添加角色"));

    await act(async () => {
      resolveSave({});
    });

    await waitFor(() => {
      const liveRegion = screen.getByRole("status");
      expect(liveRegion.textContent).toContain("较早版本");
    });
  });

  it("clears draft and shows success when no new edits during save", async () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    mockedApi.setWorldview.mockResolvedValue({} as any);

    render(<WorldviewEditor {...PROPS} />);

    await waitFor(() => {
      expect(screen.getByText("手动创建")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("+ 添加角色"));
    const nameInputs = screen.getAllByPlaceholderText("姓名");
    fireEvent.change(nameInputs[0], { target: { value: "Saved" } });

    fireEvent.click(screen.getByText("保存世界观"));

    await waitFor(() => {
      const liveRegion = screen.getByRole("status");
      expect(liveRegion.textContent).toContain("保存成功");
    });

    // Draft should be cleared
    const draftResult = loadDraft("test-user-1", "proj-1");
    expect(draftResult.status).toBe("none");
  });
});

// ═══════════════════════════════════════════════════
// P0-3: Scope isolation — project/user switch
// ═══════════════════════════════════════════════════
describe("WorldviewEditor — scope isolation", () => {
  it("resets state when projectId changes", async () => {
    const { rerender } = render(<WorldviewEditor {...PROPS} />);

    await waitFor(() => {
      expect(screen.getByText("手动创建")).toBeInTheDocument();
    });

    // Add a character in proj-1
    fireEvent.click(screen.getByText("+ 添加角色"));
    expect(screen.getByText("角色 (1)")).toBeInTheDocument();

    // Switch to proj-2
    mockedApi.getWorldview.mockRejectedValue(new Error("404 Not Found"));
    rerender(<WorldviewEditor {...PROPS} projectId="proj-2" />);

    // State should be reset — no characters
    await waitFor(() => {
      expect(screen.queryByText("角色 (1)")).not.toBeInTheDocument();
    });
  });

  it("does not leak draft from old scope to new scope", async () => {
    // Save a draft in proj-1
    saveDraft("test-user-1", "proj-1", {
      data: { ...EMPTY_DATA, characters: [{ name: "Proj1Char", personality: "", background: "", motivation: "", ability: "", relations: [] }] },
      importText: "",
      mode: "manual",
      source: "manual",
      savedAt: Date.now(),
      schemaVersion: 1,
    });

    const { rerender } = render(<WorldviewEditor {...PROPS} />);

    // Should see recovery dialog for proj-1
    expect(await screen.findByText("发现未保存的草稿")).toBeInTheDocument();

    // Dismiss recovery
    fireEvent.click(screen.getByText("丢弃草稿"));

    // Switch to proj-2 (no draft)
    mockedApi.getWorldview.mockRejectedValue(new Error("404 Not Found"));
    rerender(<WorldviewEditor {...PROPS} projectId="proj-2" />);

    // Should NOT see recovery dialog for proj-2
    await waitFor(() => {
      expect(screen.queryByText("发现未保存的草稿")).not.toBeInTheDocument();
    });

    // proj-1 draft should be cleared (discarded), proj-2 should have no draft
    expect(loadDraft("test-user-1", "proj-1").status).toBe("none");
    expect(loadDraft("test-user-1", "proj-2").status).toBe("none");
  });

  it("ignores late getWorldview response from old scope", async () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    let resolveGetWorldview!: (v: any) => void;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    mockedApi.getWorldview.mockImplementation(
      () => new Promise<any>((resolve) => { resolveGetWorldview = resolve; })
    );

    const { rerender } = render(<WorldviewEditor {...PROPS} projectId="proj-1" />);

    // Switch to proj-2 before proj-1's getWorldview resolves
    mockedApi.getWorldview.mockRejectedValue(new Error("404 Not Found"));
    rerender(<WorldviewEditor {...PROPS} projectId="proj-2" />);

    // Now resolve the old proj-1 getWorldview with data
    await act(async () => {
      resolveGetWorldview({
        characters: [{ name: "OldScopeChar", personality: "", background: "", motivation: "", ability: "", relations: [] }],
        geography: [], factions: [], power_system: [], history: [], conflicts: [], special_settings: [],
      });
    });

    // The old scope data should NOT appear in the new scope
    await waitFor(() => {
      expect(screen.queryByDisplayValue("OldScopeChar")).not.toBeInTheDocument();
    });
  });

  it("ignores late save response from old scope", async () => {
    let resolveSave!: (v: unknown) => void;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    mockedApi.setWorldview.mockImplementation(
      () => new Promise<any>((resolve) => { resolveSave = resolve; })
    );

    const { rerender } = render(<WorldviewEditor {...PROPS} projectId="proj-1" />);

    await waitFor(() => {
      expect(screen.getByText("手动创建")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("+ 添加角色"));

    fireEvent.click(screen.getByText("保存世界观"));

    // Switch to proj-2 before save resolves
    mockedApi.getWorldview.mockRejectedValue(new Error("404 Not Found"));
    rerender(<WorldviewEditor {...PROPS} projectId="proj-2" />);

    // Now resolve the old save
    await act(async () => {
      resolveSave({});
    });

    // The new scope should not show "保存成功" from the old scope's save
    const liveRegion = screen.getByRole("status");
    expect(liveRegion.textContent).not.toContain("保存成功");
  });

  it("ignores late import response from old scope", async () => {
    let resolveImport!: (v: unknown) => void;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    mockedApi.importWorldview.mockImplementation(
      () => new Promise<any>((resolve) => { resolveImport = resolve; })
    );

    const { rerender } = render(<WorldviewEditor {...PROPS} projectId="proj-1" />);

    await waitFor(() => {
      expect(screen.getByText("手动创建")).toBeInTheDocument();
    });

    // Switch to import mode and trigger import
    fireEvent.click(screen.getByText("导入文档"));
    const textarea = screen.getByPlaceholderText(/在此粘贴世界观文档内容/);
    fireEvent.change(textarea, { target: { value: "这是一段足够长的世界观文档内容用于测试导入功能" } });
    fireEvent.click(screen.getByText("开始导入解析"));

    // Switch to proj-2 before import resolves
    mockedApi.getWorldview.mockRejectedValue(new Error("404 Not Found"));
    rerender(<WorldviewEditor {...PROPS} projectId="proj-2" />);

    // Now resolve the old import with data
    await act(async () => {
      resolveImport({
        characters: [{ name: "ImportedChar", personality: "", background: "", motivation: "", ability: "", relations: [] }],
        geography: [], factions: [], power_system: [], history: [], conflicts: [], special_settings: [],
        element_count: 1,
      });
    });

    // The old scope's imported data should NOT appear in the new scope
    await waitFor(() => {
      expect(screen.queryByDisplayValue("ImportedChar")).not.toBeInTheDocument();
    });
  });
});

// ═══════════════════════════════════════════════════
// P0-4: Import text always latest
// ═══════════════════════════════════════════════════
describe("WorldviewEditor — import text", () => {
  it("preserves import text as raw_text when switching to manual", async () => {
    render(<WorldviewEditor {...PROPS} />);

    await waitFor(() => {
      expect(screen.getByText("手动创建")).toBeInTheDocument();
    });

    // Switch to import mode
    fireEvent.click(screen.getByText("导入文档"));

    // Type some import text
    const textarea = screen.getByPlaceholderText(/在此粘贴世界观文档内容/);
    fireEvent.change(textarea, { target: { value: "Some worldview text content here" } });

    // Switch back to manual
    fireEvent.click(screen.getByText("手动创建"));

    // Add a character to trigger dirty, then save
    fireEvent.click(screen.getByText("+ 添加角色"));
    fireEvent.click(screen.getByText("保存世界观"));

    await waitFor(() => {
      expect(mockedApi.setWorldview).toHaveBeenCalled();
    });

    const [, body] = mockedApi.setWorldview.mock.calls[0];
    expect((body as { raw_text?: string }).raw_text).toBe("Some worldview text content here");
  });

  it("updates raw_text when importText modified after parse", async () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    mockedApi.importWorldview.mockResolvedValue({
      characters: [{ name: "Parsed", personality: "", background: "", motivation: "", ability: "", relations: [] }],
      geography: [], factions: [], power_system: [], history: [], conflicts: [], special_settings: [],
      element_count: 1,
    } as any);

    render(<WorldviewEditor {...PROPS} />);

    await waitFor(() => {
      expect(screen.getByText("手动创建")).toBeInTheDocument();
    });

    // Switch to hybrid mode
    fireEvent.click(screen.getByText("混合模式"));

    const textarea = screen.getByPlaceholderText(/在此粘贴世界观文档内容/);
    fireEvent.change(textarea, { target: { value: "原始世界观文档内容足够长用于测试" } });
    fireEvent.click(screen.getByText("开始导入解析"));

    await waitFor(() => {
      expect(screen.getByText("已提取 1 个要素")).toBeInTheDocument();
    });

    // Modify importText after parse
    fireEvent.change(textarea, { target: { value: "修改后的世界观文档内容足够长" } });

    // Switch to manual to trigger raw_text update
    fireEvent.click(screen.getByText("手动创建"));

    // Save and check payload uses latest importText
    fireEvent.click(screen.getByText("保存世界观"));

    await waitFor(() => {
      expect(mockedApi.setWorldview).toHaveBeenCalled();
    });

    const [, body] = mockedApi.setWorldview.mock.calls[0];
    expect((body as { raw_text?: string }).raw_text).toBe("修改后的世界观文档内容足够长");
  });

  it("always sends latest importText as raw_text in save payload", async () => {
    render(<WorldviewEditor {...PROPS} />);

    await waitFor(() => {
      expect(screen.getByText("手动创建")).toBeInTheDocument();
    });

    // Switch to import mode, type text
    fireEvent.click(screen.getByText("导入文档"));
    const textarea = screen.getByPlaceholderText(/在此粘贴世界观文档内容/);
    fireEvent.change(textarea, { target: { value: "Latest import text content" } });

    // Switch to manual (updates raw_text)
    fireEvent.click(screen.getByText("手动创建"));

    // Add character and save
    fireEvent.click(screen.getByText("+ 添加角色"));
    fireEvent.click(screen.getByText("保存世界观"));

    await waitFor(() => {
      expect(mockedApi.setWorldview).toHaveBeenCalled();
    });

    const [, body] = mockedApi.setWorldview.mock.calls[0];
    expect((body as { raw_text?: string }).raw_text).toBe("Latest import text content");
  });
});

// ═══════════════════════════════════════════════════
// P0-1: Autosave with real result checking
// ═══════════════════════════════════════════════════
describe("WorldviewEditor — autosave", () => {
  it("shows '已自动保存' on successful autosave", async () => {
    render(<WorldviewEditor {...PROPS} />);

    await waitFor(() => {
      expect(screen.getByText("手动创建")).toBeInTheDocument();
    });

    // Make a change to trigger dirty + autosave
    fireEvent.click(screen.getByText("+ 添加角色"));

    // Wait for debounced autosave (700ms)
    await waitFor(() => {
      const liveRegion = screen.getByRole("status");
      expect(liveRegion.textContent).toContain("已自动保存");
    }, { timeout: 2000 });
  });

  it("shows '未保存到本机' when localStorage.setItem fails", async () => {
    const origSetItem = localStorage.setItem;
    Object.defineProperty(localStorage, "setItem", {
      configurable: true,
      value: vi.fn(() => {
        throw new DOMException("Quota exceeded", "QuotaExceededError");
      }),
    });

    render(<WorldviewEditor {...PROPS} />);

    await waitFor(() => {
      expect(screen.getByText("手动创建")).toBeInTheDocument();
    });

    // Make a change to trigger dirty + autosave
    fireEvent.click(screen.getByText("+ 添加角色"));

    // Wait for debounced autosave
    await waitFor(() => {
      const liveRegion = screen.getByRole("status");
      expect(liveRegion.textContent).toContain("未保存到本机");
    }, { timeout: 2000 });

    // Copy button should appear
    expect(screen.getByText("复制当前内容")).toBeInTheDocument();

    // Restore
    Object.defineProperty(localStorage, "setItem", { configurable: true, value: origSetItem });
  });
});

// ═══════════════════════════════════════════════════
// P0-2: Save race — refresh recovery after new edits
// ═══════════════════════════════════════════════════
describe("WorldviewEditor — save race + refresh recovery", () => {
  it("preserves new edits in localStorage after save with concurrent edits", async () => {
    let resolveSave!: (v: unknown) => void;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    mockedApi.setWorldview.mockImplementation(
      () => new Promise<any>((resolve) => { resolveSave = resolve; })
    );

    render(<WorldviewEditor {...PROPS} />);

    await waitFor(() => {
      expect(screen.getByText("手动创建")).toBeInTheDocument();
    });

    // Add first character
    fireEvent.click(screen.getByText("+ 添加角色"));
    const nameInputs = screen.getAllByPlaceholderText("姓名");
    fireEvent.change(nameInputs[0], { target: { value: "First" } });

    // Start save
    fireEvent.click(screen.getByText("保存世界观"));

    // Add second character during save
    fireEvent.click(screen.getByText("+ 添加角色"));
    const nameInputs2 = screen.getAllByPlaceholderText("姓名");
    fireEvent.change(nameInputs2[1], { target: { value: "Second" } });

    // Resolve save
    await act(async () => {
      resolveSave({});
    });

    // Wait for state to settle
    await waitFor(() => {
      expect(screen.getByText("角色 (2)")).toBeInTheDocument();
    });

    // The draft in localStorage should contain BOTH characters (the latest state)
    const draftResult = loadDraft("test-user-1", "proj-1");
    expect(draftResult.status).toBe("ok");
    if (draftResult.status === "ok") {
      expect(draftResult.draft.data.characters).toHaveLength(2);
      expect(draftResult.draft.data.characters[0].name).toBe("First");
      expect(draftResult.draft.data.characters[1].name).toBe("Second");
    }
  });
});

// ═══════════════════════════════════════════════════
// P0-1: Deferred-promise — late GET doesn't overwrite user edits
// ═══════════════════════════════════════════════════
describe("WorldviewEditor — P0-1 deferred-promise race protection", () => {
  it("preserves user edits when initial GET returns late", async () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    let resolveGetWorldview!: (v: any) => void;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    mockedApi.getWorldview.mockImplementation(
      () => new Promise<any>((resolve) => { resolveGetWorldview = resolve; })
    );

    render(<WorldviewEditor {...PROPS} />);

    // Wait for mode buttons to appear (component mounted, GET is pending)
    await waitFor(() => {
      expect(screen.getByText("手动创建")).toBeInTheDocument();
    });

    // User edits BEFORE the GET returns — bumps editRevision
    fireEvent.click(screen.getByText("+ 添加角色"));
    const nameInput = screen.getByPlaceholderText("姓名");
    fireEvent.change(nameInput, { target: { value: "UserEdit" } });

    expect(screen.getByDisplayValue("UserEdit")).toBeInTheDocument();

    // Now the late GET returns with server data
    await act(async () => {
      resolveGetWorldview({
        characters: [{ name: "ServerChar", personality: "", background: "", motivation: "", ability: "", relations: [] }],
        geography: [], factions: [], power_system: [], history: [], conflicts: [], special_settings: [],
      });
    });

    // The user's edit should still be there — server data must NOT overwrite
    await waitFor(() => {
      expect(screen.getByDisplayValue("UserEdit")).toBeInTheDocument();
      expect(screen.queryByDisplayValue("ServerChar")).not.toBeInTheDocument();
    });

    // dirty should still be true (user edit preserved)
    // Verify by checking that back button triggers unsaved confirm
    fireEvent.click(screen.getByText("← 返回项目详情"));
    await waitFor(() => {
      expect(screen.getByText("未保存的修改")).toBeInTheDocument();
    });
  });

  it("preserves edits made after save when initial GET returns late", async () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    let resolveGetWorldview!: (v: any) => void;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    mockedApi.getWorldview.mockImplementation(
      () => new Promise<any>((resolve) => { resolveGetWorldview = resolve; })
    );
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    mockedApi.setWorldview.mockResolvedValue({} as any);

    render(<WorldviewEditor {...PROPS} />);

    await waitFor(() => {
      expect(screen.getByText("手动创建")).toBeInTheDocument();
    });

    // User adds a character and saves (POST resolves immediately)
    fireEvent.click(screen.getByText("+ 添加角色"));
    const nameInputs = screen.getAllByPlaceholderText("姓名");
    fireEvent.change(nameInputs[0], { target: { value: "First" } });
    fireEvent.click(screen.getByText("保存世界观"));

    await waitFor(() => {
      expect(screen.getByRole("status").textContent).toContain("保存成功");
    });

    // Draft should be cleared (save succeeded, no new edits)
    expect(loadDraft("test-user-1", "proj-1").status).toBe("none");

    // User edits again (before initial GET returns)
    fireEvent.click(screen.getByText("+ 添加角色"));
    const nameInputs2 = screen.getAllByPlaceholderText("姓名");
    fireEvent.change(nameInputs2[1], { target: { value: "Second" } });

    // Now the initial GET returns with server data
    await act(async () => {
      resolveGetWorldview({
        characters: [{ name: "ServerChar", personality: "", background: "", motivation: "", ability: "", relations: [] }],
        geography: [], factions: [], power_system: [], history: [], conflicts: [], special_settings: [],
      });
    });

    // The user's edits should be preserved — both characters
    await waitFor(() => {
      expect(screen.getByText("角色 (2)")).toBeInTheDocument();
      expect(screen.getByDisplayValue("First")).toBeInTheDocument();
      expect(screen.getByDisplayValue("Second")).toBeInTheDocument();
      expect(screen.queryByDisplayValue("ServerChar")).not.toBeInTheDocument();
    });

    // dirty should be true
    fireEvent.click(screen.getByText("← 返回项目详情"));
    await waitFor(() => {
      expect(screen.getByText("未保存的修改")).toBeInTheDocument();
    });

    // Draft should exist (from autosave or pagehide-like save)
    // The draft should contain the latest values
    // (autosave may or may not have fired, but the data is in the editor)
  });

  it("does not produce duplicate GETs when hasWorldview is true on mount", async () => {
    mockedApi.getWorldview.mockRejectedValue(new Error("404 Not Found"));

    render(<WorldviewEditor {...PROPS} hasWorldview={true} />);

    await waitFor(() => {
      expect(screen.getByText("手动创建")).toBeInTheDocument();
    });

    // getWorldview should be called exactly once (no duplicate from hasWorldview effect)
    expect(mockedApi.getWorldview).toHaveBeenCalledTimes(1);
  });
});

// ═══════════════════════════════════════════════════
// P0-2: Real save/cleanup/copy text — combo failure tests
// ═══════════════════════════════════════════════════
describe("WorldviewEditor — P0-2 combo failure text", () => {
  it("shows dual-failure message when server save AND setItem both fail", async () => {
    const origSetItem = localStorage.setItem;
    Object.defineProperty(localStorage, "setItem", {
      configurable: true,
      value: vi.fn(() => {
        throw new DOMException("Quota exceeded", "QuotaExceededError");
      }),
    });

    render(<WorldviewEditor {...PROPS} />);

    await waitFor(() => {
      expect(screen.getByText("手动创建")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("+ 添加角色"));

    mockedApi.setWorldview.mockRejectedValue(new Error("Server error"));

    fireEvent.click(screen.getByText("保存世界观"));

    await waitFor(() => {
      const liveRegion = screen.getByRole("status");
      expect(liveRegion.textContent).toContain("服务器和本机草稿均保存失败");
      expect(liveRegion.textContent).toContain("请立即复制");
    });

    Object.defineProperty(localStorage, "setItem", { configurable: true, value: origSetItem });
  });

  it("shows draft-saved message when server fails but setItem succeeds", async () => {
    render(<WorldviewEditor {...PROPS} />);

    await waitFor(() => {
      expect(screen.getByText("手动创建")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("+ 添加角色"));

    mockedApi.setWorldview.mockRejectedValue(new Error("Server error"));

    fireEvent.click(screen.getByText("保存世界观"));

    await waitFor(() => {
      const liveRegion = screen.getByRole("status");
      expect(liveRegion.textContent).toContain("服务器保存失败");
      expect(liveRegion.textContent).toContain("已保存为本机草稿");
    });
  });

  it("keeps recovery dialog open when clearDraft fails on valid draft discard", async () => {
    saveDraft("test-user-1", "proj-1", {
      data: { ...EMPTY_DATA, characters: [{ name: "X", personality: "", background: "", motivation: "", ability: "", relations: [] }] },
      importText: "",
      mode: "manual",
      source: "manual",
      savedAt: Date.now(),
      schemaVersion: 1,
    });

    const origRemoveItem = localStorage.removeItem;
    Object.defineProperty(localStorage, "removeItem", {
      configurable: true,
      value: vi.fn(() => { throw new Error("SecurityError"); }),
    });

    render(<WorldviewEditor {...PROPS} />);
    await screen.findByText("发现未保存的草稿");

    fireEvent.click(screen.getByText("丢弃草稿"));

    // Dialog should still be open
    await waitFor(() => {
      expect(screen.getByText("发现未保存的草稿")).toBeInTheDocument();
    });

    // Error message should be visible
    expect(screen.getByRole("status").textContent).toContain("清除失败");

    // Draft should still be in localStorage
    expect(loadDraft("test-user-1", "proj-1").status).toBe("ok");

    Object.defineProperty(localStorage, "removeItem", { configurable: true, value: origRemoveItem });
  });

  it("keeps recovery dialog open when clearDraft fails on corrupt draft discard", async () => {
    localStorage.setItem("wv-draft:test-user-1:proj-1", "{corrupt");

    const origRemoveItem = localStorage.removeItem;
    Object.defineProperty(localStorage, "removeItem", {
      configurable: true,
      value: vi.fn(() => { throw new Error("SecurityError"); }),
    });

    render(<WorldviewEditor {...PROPS} />);
    await screen.findByText("草稿已损坏");

    // First "确认丢弃" — secondary confirmation
    fireEvent.click(screen.getByText("确认丢弃"));
    await screen.findByText("确认永久丢弃");

    // Second "确认丢弃" — actual discard attempt
    const confirmBtns = screen.getAllByText("确认丢弃");
    fireEvent.click(confirmBtns[confirmBtns.length - 1]);

    // Should go back to first dialog (not close)
    await waitFor(() => {
      expect(screen.getByText("草稿已损坏")).toBeInTheDocument();
    });

    // Error message should be visible
    expect(screen.getByRole("status").textContent).toContain("清除失败");

    // Corrupt data should still be in localStorage
    expect(localStorage.getItem("wv-draft:test-user-1:proj-1")).toBe("{corrupt");

    Object.defineProperty(localStorage, "removeItem", { configurable: true, value: origRemoveItem });
  });

  it("shows specific message when server save succeeds but clearDraft fails", async () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    mockedApi.setWorldview.mockResolvedValue({} as any);

    // Pre-save a draft so clearDraft has something to clear
    saveDraft("test-user-1", "proj-1", {
      data: EMPTY_DATA,
      importText: "old draft",
      mode: "manual",
      source: "manual",
      savedAt: Date.now(),
      schemaVersion: 1,
    });

    const origRemoveItem = localStorage.removeItem;
    Object.defineProperty(localStorage, "removeItem", {
      configurable: true,
      value: vi.fn(() => { throw new Error("SecurityError"); }),
    });

    render(<WorldviewEditor {...PROPS} />);

    await waitFor(() => {
      expect(screen.getByText("手动创建")).toBeInTheDocument();
    });

    // Dismiss any recovery dialog
    const recoveryText = screen.queryByText("发现未保存的草稿");
    if (recoveryText) {
      // Can't discard because removeItem is mocked to fail, so just ignore
      // The test setup pre-saved a draft, so recovery dialog will show.
      // We need to handle this. Let me not pre-save and instead use autosave.
    }

    Object.defineProperty(localStorage, "removeItem", { configurable: true, value: origRemoveItem });
    // Clear the draft so no recovery dialog shows
    clearDraft("test-user-1", "proj-1");

    // Re-render
    cleanup();
    vi.clearAllMocks();
    mockedApi.getWorldview.mockRejectedValue(new Error("404 Not Found"));
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    mockedApi.setWorldview.mockResolvedValue({} as any);

    Object.defineProperty(localStorage, "removeItem", {
      configurable: true,
      value: vi.fn(() => { throw new Error("SecurityError"); }),
    });

    render(<WorldviewEditor {...PROPS} />);

    await waitFor(() => {
      expect(screen.getByText("手动创建")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("+ 添加角色"));
    fireEvent.click(screen.getByText("保存世界观"));

    await waitFor(() => {
      const liveRegion = screen.getByRole("status");
      expect(liveRegion.textContent).toContain("服务器已保存");
      expect(liveRegion.textContent).toContain("本机旧草稿未清除");
    });

    Object.defineProperty(localStorage, "removeItem", { configurable: true, value: origRemoveItem });
  });

  it("shows manual copy textarea when both clipboard and execCommand fail", async () => {
    // Mock navigator.clipboard to reject
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: vi.fn().mockRejectedValue(new Error("NotAllowedError")) },
    });

    // Mock execCommand to return false
    const origExecCommand = document.execCommand;
    document.execCommand = vi.fn(() => false);

    render(<WorldviewEditor {...PROPS} />);

    await waitFor(() => {
      expect(screen.getByText("手动创建")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("+ 添加角色"));

    // Trigger save failure to get the copy button
    mockedApi.setWorldview.mockRejectedValue(new Error("Server error"));
    fireEvent.click(screen.getByText("保存世界观"));

    await waitFor(() => {
      expect(screen.getByText("复制当前内容")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("复制当前内容"));

    await waitFor(() => {
      const liveRegion = screen.getByRole("status");
      expect(liveRegion.textContent).toContain("复制失败");
      expect(liveRegion.textContent).toContain("手动复制");
    });

    // Should show a readonly textarea with content
    const manualTextarea = document.querySelector('textarea[readonly]') as HTMLTextAreaElement;
    expect(manualTextarea).toBeTruthy();
    expect(manualTextarea.readOnly).toBe(true);
    expect(manualTextarea.value).toContain("characters");

    document.execCommand = origExecCommand;
  });
});

// ═══════════════════════════════════════════════════
// P0-3: pagehide and recovery lock
// ═══════════════════════════════════════════════════
describe("WorldviewEditor — P0-3 pagehide and recovery lock", () => {
  it("does not recreate draft on pagehide after save cleared it (dirty=false)", async () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    mockedApi.setWorldview.mockResolvedValue({} as any);

    render(<WorldviewEditor {...PROPS} />);

    await waitFor(() => {
      expect(screen.getByText("手动创建")).toBeInTheDocument();
    });

    // Add character and save
    fireEvent.click(screen.getByText("+ 添加角色"));
    fireEvent.click(screen.getByText("保存世界观"));

    await waitFor(() => {
      expect(screen.getByRole("status").textContent).toContain("保存成功");
    });

    // Draft should be cleared
    expect(loadDraft("test-user-1", "proj-1").status).toBe("none");

    // Simulate pagehide — should NOT recreate draft (dirty=false)
    window.dispatchEvent(new Event("pagehide"));

    // Draft should still be none
    expect(loadDraft("test-user-1", "proj-1").status).toBe("none");
  });

  it("does not write to localStorage on pagehide during recovery (ok)", async () => {
    saveDraft("test-user-1", "proj-1", {
      data: { ...EMPTY_DATA, characters: [{ name: "Draft", personality: "", background: "", motivation: "", ability: "", relations: [] }] },
      importText: "draft text",
      mode: "manual",
      source: "manual",
      savedAt: Date.now(),
      schemaVersion: 1,
    });

    render(<WorldviewEditor {...PROPS} />);
    await screen.findByText("发现未保存的草稿");

    // Simulate pagehide during recovery — should NOT write
    window.dispatchEvent(new Event("pagehide"));

    // The original draft should still be intact (not overwritten)
    const result = loadDraft("test-user-1", "proj-1");
    expect(result.status).toBe("ok");
    if (result.status === "ok") {
      expect(result.draft.data.characters[0].name).toBe("Draft");
      expect(result.draft.importText).toBe("draft text");
    }
  });

  it("does not write to localStorage on pagehide during recovery (corrupt)", async () => {
    localStorage.setItem("wv-draft:test-user-1:proj-1", "{corrupt json");

    render(<WorldviewEditor {...PROPS} />);
    await screen.findByText("草稿已损坏");

    // Simulate pagehide during corrupt recovery — should NOT write
    window.dispatchEvent(new Event("pagehide"));

    // The corrupt data should still be there (not overwritten)
    expect(localStorage.getItem("wv-draft:test-user-1:proj-1")).toBe("{corrupt json");
  });

  it("keeps recovery dialog locked when clicking overlay", async () => {
    saveDraft("test-user-1", "proj-1", {
      data: { ...EMPTY_DATA, characters: [{ name: "Draft", personality: "", background: "", motivation: "", ability: "", relations: [] }] },
      importText: "",
      mode: "manual",
      source: "manual",
      savedAt: Date.now(),
      schemaVersion: 1,
    });

    render(<WorldviewEditor {...PROPS} />);
    await screen.findByText("发现未保存的草稿");

    // Click overlay
    const overlay = document.querySelector(".draft-overlay") as HTMLElement;
    fireEvent.click(overlay);

    // Dialog should still be open
    expect(screen.getByText("发现未保存的草稿")).toBeInTheDocument();

    // Status should show prompt
    expect(screen.getByRole("status").textContent).toContain("请选择恢复或丢弃");

    // Draft should still be in localStorage
    expect(loadDraft("test-user-1", "proj-1").status).toBe("ok");
  });

  it("keeps recovery dialog locked when pressing Escape", async () => {
    saveDraft("test-user-1", "proj-1", {
      data: { ...EMPTY_DATA, characters: [{ name: "Draft", personality: "", background: "", motivation: "", ability: "", relations: [] }] },
      importText: "",
      mode: "manual",
      source: "manual",
      savedAt: Date.now(),
      schemaVersion: 1,
    });

    render(<WorldviewEditor {...PROPS} />);
    await screen.findByText("发现未保存的草稿");

    // Press Escape
    const dialog = screen.getByRole("dialog");
    fireEvent.keyDown(dialog, { key: "Escape" });

    // Dialog should still be open
    expect(screen.getByText("发现未保存的草稿")).toBeInTheDocument();

    // Status should show prompt
    expect(screen.getByRole("status").textContent).toContain("请选择恢复或丢弃");

    // Draft should still be in localStorage
    expect(loadDraft("test-user-1", "proj-1").status).toBe("ok");
  });
});

// ═══════════════════════════════════════════════════
// P0-5: Import text empty string must be latest
// ═══════════════════════════════════════════════════
describe("WorldviewEditor — P0-5 empty raw_text in save payload", () => {
  it("sends empty string as raw_text when importText is cleared", async () => {
    render(<WorldviewEditor {...PROPS} />);

    await waitFor(() => {
      expect(screen.getByText("手动创建")).toBeInTheDocument();
    });

    // Switch to import mode and type text
    fireEvent.click(screen.getByText("导入文档"));
    const textarea = screen.getByPlaceholderText(/在此粘贴世界观文档内容/);
    fireEvent.change(textarea, { target: { value: "Original worldview text content here" } });

    // Switch to manual (sets raw_text from importText)
    fireEvent.click(screen.getByText("手动创建"));

    // Now clear the importText by switching back to import and clearing
    fireEvent.click(screen.getByText("导入文档"));
    const textarea2 = screen.getByPlaceholderText(/在此粘贴世界观文档内容/);
    fireEvent.change(textarea2, { target: { value: "" } });

    // Switch back to manual (sets raw_text to empty)
    fireEvent.click(screen.getByText("手动创建"));

    // Add a character and save
    fireEvent.click(screen.getByText("+ 添加角色"));
    fireEvent.click(screen.getByText("保存世界观"));

    await waitFor(() => {
      expect(mockedApi.setWorldview).toHaveBeenCalled();
    });

    const [, body] = mockedApi.setWorldview.mock.calls[0];
    expect((body as { raw_text?: string }).raw_text).toBe("");
  });
});

// ═══════════════════════════════════════════════════
// P1: Focus safety
// ═══════════════════════════════════════════════════
describe("WorldviewEditor — P1 focus safety", () => {
  it("focuses '继续编辑' by default in unsaved confirm dialog", async () => {
    render(<WorldviewEditor {...PROPS} />);

    await waitFor(() => {
      expect(screen.getByText("手动创建")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("+ 添加角色"));
    fireEvent.click(screen.getByText("← 返回项目详情"));

    await screen.findByText("未保存的修改");

    // The focused element should be "继续编辑", not "确认离开"
    const continueBtn = screen.getByText("继续编辑");
    const confirmBtn = screen.getByText("确认离开");
    expect(document.activeElement).toBe(continueBtn);
    expect(document.activeElement).not.toBe(confirmBtn);
  });

  it("focuses '继续编辑' in failed state (not '承担风险离开')", async () => {
    const origSetItem = localStorage.setItem;
    Object.defineProperty(localStorage, "setItem", {
      configurable: true,
      value: vi.fn(() => {
        throw new DOMException("Quota exceeded", "QuotaExceededError");
      }),
    });

    render(<WorldviewEditor {...PROPS} />);

    await waitFor(() => {
      expect(screen.getByText("手动创建")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("+ 添加角色"));
    fireEvent.click(screen.getByText("← 返回项目详情"));

    await screen.findByText("未保存的修改");

    // Click "确认离开" — draft save will fail
    fireEvent.click(screen.getByText("确认离开"));

    await waitFor(() => {
      expect(screen.getByText("承担风险离开")).toBeInTheDocument();
    });

    // Focus should be on "继续编辑", NOT "承担风险离开"
    const continueBtn = screen.getByText("继续编辑");
    const forceLeaveBtn = screen.getByText("承担风险离开");
    expect(document.activeElement).toBe(continueBtn);
    expect(document.activeElement).not.toBe(forceLeaveBtn);

    Object.defineProperty(localStorage, "setItem", { configurable: true, value: origSetItem });
  });

  it("does not overwrite lastFocusedRef when leaveSaveFailed state changes", async () => {
    const origSetItem = localStorage.setItem;
    Object.defineProperty(localStorage, "setItem", {
      configurable: true,
      value: vi.fn(() => {
        throw new DOMException("Quota exceeded", "QuotaExceededError");
      }),
    });

    render(<WorldviewEditor {...PROPS} />);

    await waitFor(() => {
      expect(screen.getByText("手动创建")).toBeInTheDocument();
    });

    // Focus the back button before triggering dialog
    const backBtn = screen.getByText("← 返回项目详情");
    backBtn.focus();

    fireEvent.click(screen.getByText("+ 添加角色"));
    fireEvent.click(backBtn);

    await screen.findByText("未保存的修改");

    // Click "确认离开" — draft save will fail → leaveSaveFailed becomes true
    fireEvent.click(screen.getByText("确认离开"));

    await waitFor(() => {
      expect(screen.getByText("承担风险离开")).toBeInTheDocument();
    });

    // Click "继续编辑" — should restore focus to the back button (original trigger)
    fireEvent.click(screen.getByText("继续编辑"));

    await waitFor(() => {
      expect(screen.queryByText("未保存的修改")).not.toBeInTheDocument();
    });

    // Focus should be restored to the back button (the original external trigger)
    // not to "确认离开" or any other dialog button
    expect(document.activeElement).toBe(backBtn);

    Object.defineProperty(localStorage, "setItem", { configurable: true, value: origSetItem });
  });

  it("cancel on corrupt secondary confirmation returns to first dialog with focus on safe button", async () => {
    localStorage.setItem("wv-draft:test-user-1:proj-1", "{corrupt json");

    render(<WorldviewEditor {...PROPS} />);
    await screen.findByText("草稿已损坏");

    // Click "确认丢弃" → secondary confirmation
    fireEvent.click(screen.getByText("确认丢弃"));
    await screen.findByText("确认永久丢弃");

    // Click "取消"
    fireEvent.click(screen.getByText("取消"));

    // Should be back to first dialog
    await waitFor(() => {
      expect(screen.queryByText("确认永久丢弃")).not.toBeInTheDocument();
      expect(screen.getByText("草稿已损坏")).toBeInTheDocument();
    });

    // Focus should be in the first dialog (on a safe button, not on "确认丢弃")
    const dialog = screen.getByRole("dialog");
    expect(dialog.contains(document.activeElement)).toBe(true);
  });
});

// ═══════════════════════════════════════════════════
// P0-1: Server raw_text preserved on save without edits
// ═══════════════════════════════════════════════════
describe("WorldviewEditor — P0-1 server raw_text preserved", () => {
  it("sends server raw_text in save payload when user does not edit", async () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    mockedApi.getWorldview.mockResolvedValue({
      characters: [],
      geography: [],
      factions: [],
      power_system: [],
      history: [],
      conflicts: [],
      special_settings: [],
      raw_text: "原文",
      source: "manual",
    } as any);

    render(<WorldviewEditor {...PROPS} hasWorldview={true} />);

    // Wait for GET to resolve and data to load
    await waitFor(() => {
      expect(screen.getByText("角色 (0)")).toBeInTheDocument();
    });

    // Save without editing
    fireEvent.click(screen.getByText("保存世界观"));

    await waitFor(() => {
      expect(mockedApi.setWorldview).toHaveBeenCalled();
    });

    const [, body] = mockedApi.setWorldview.mock.calls[0];
    expect((body as { raw_text?: string }).raw_text).toBe("原文");
  });
});

// ═══════════════════════════════════════════════════
// P0-2: Restore draft invalidates late initial GET
// ═══════════════════════════════════════════════════
describe("WorldviewEditor — P0-2 restore draft invalidates late GET", () => {
  it("preserves restored draft when late initial GET returns", async () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    let resolveGetWorldview!: (v: any) => void;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    mockedApi.getWorldview.mockImplementation(
      () => new Promise<any>((resolve) => { resolveGetWorldview = resolve; })
    );

    // Save a draft
    saveDraft("test-user-1", "proj-1", {
      data: { ...EMPTY_DATA, characters: [{ name: "DraftChar", personality: "", background: "", motivation: "", ability: "", relations: [] }] },
      importText: "draft import text",
      mode: "manual",
      source: "manual",
      savedAt: Date.now(),
      schemaVersion: 1,
    });

    render(<WorldviewEditor {...PROPS} />);

    // Recovery dialog should show
    await screen.findByText("发现未保存的草稿");

    // Restore draft
    fireEvent.click(screen.getByText("恢复草稿"));

    // Draft data should be visible
    await waitFor(() => {
      expect(screen.getByDisplayValue("DraftChar")).toBeInTheDocument();
    });

    // Now resolve the late GET with different data
    await act(async () => {
      resolveGetWorldview({
        characters: [{ name: "ServerChar", personality: "", background: "", motivation: "", ability: "", relations: [] }],
        geography: [], factions: [], power_system: [], history: [], conflicts: [], special_settings: [],
        raw_text: "server text",
      });
    });

    // Draft data should still be there — server data must NOT overwrite
    await waitFor(() => {
      expect(screen.getByDisplayValue("DraftChar")).toBeInTheDocument();
      expect(screen.queryByDisplayValue("ServerChar")).not.toBeInTheDocument();
    });

    // dirty should still be true — back button triggers unsaved confirm
    fireEvent.click(screen.getByText("← 返回项目详情"));
    await waitFor(() => {
      expect(screen.getByText("未保存的修改")).toBeInTheDocument();
    });

    // Draft key should not be cleared
    expect(loadDraft("test-user-1", "proj-1").status).toBe("ok");
  });
});

// ═══════════════════════════════════════════════════
// P1-1: Scope reset clears async button locks
// ═══════════════════════════════════════════════════
describe("WorldviewEditor — P1-1 scope reset button locks", () => {
  it("resets loading state when projectId changes during pending save", async () => {
    let resolveSave!: (v: unknown) => void;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    mockedApi.setWorldview.mockImplementation(
      () => new Promise<any>((resolve) => { resolveSave = resolve; })
    );

    const { rerender } = render(<WorldviewEditor {...PROPS} />);

    await waitFor(() => {
      expect(screen.getByText("手动创建")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("+ 添加角色"));
    fireEvent.click(screen.getByText("保存世界观"));

    // Button should show "保存中..."
    expect(screen.getByText("保存中...")).toBeInTheDocument();

    // Switch to proj-2
    mockedApi.getWorldview.mockRejectedValue(new Error("404 Not Found"));
    rerender(<WorldviewEditor {...PROPS} projectId="proj-2" />);

    // Button should show "保存世界观" (not "保存中...")
    await waitFor(() => {
      expect(screen.getByText("保存世界观")).toBeInTheDocument();
      expect(screen.queryByText("保存中...")).not.toBeInTheDocument();
    });
  });

  it("resets importing state when projectId changes during pending upload", async () => {
    let resolveUpload!: (v: unknown) => void;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    mockedApi.uploadWorldviewFile.mockImplementation(
      () => new Promise<any>((resolve) => { resolveUpload = resolve; })
    );

    const { container, rerender } = render(<WorldviewEditor {...PROPS} />);

    await waitFor(() => {
      expect(screen.getByText("手动创建")).toBeInTheDocument();
    });

    // Switch to import mode
    fireEvent.click(screen.getByText("导入文档"));

    // Upload a file
    const fileInput = container.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File(["content"], "test.txt", { type: "text/plain" });
    fireEvent.change(fileInput, { target: { files: [file] } });

    // Button should show "解析文件中..."
    await waitFor(() => {
      expect(screen.getByText("解析文件中...")).toBeInTheDocument();
    });

    // Switch to proj-2
    mockedApi.getWorldview.mockRejectedValue(new Error("404 Not Found"));
    rerender(<WorldviewEditor {...PROPS} projectId="proj-2" />);

    // Mode is reset to manual, switch to import mode in new scope
    await waitFor(() => {
      expect(screen.getByText("导入文档")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByText("导入文档"));

    // Upload button should show "上传文件" (not "解析文件中...")
    await waitFor(() => {
      expect(screen.getByText("上传文件")).toBeInTheDocument();
      expect(screen.queryByText("解析文件中...")).not.toBeInTheDocument();
    });
  });
});

// ═══════════════════════════════════════════════════
// P1-2: Save/upload/mode state completeness
// ═══════════════════════════════════════════════════
describe("WorldviewEditor — P1-2 save state completeness", () => {
  it("shows '进入下一步' button after first save", async () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    mockedApi.setWorldview.mockResolvedValue({} as any);

    render(<WorldviewEditor {...PROPS} hasWorldview={false} />);

    await waitFor(() => {
      expect(screen.getByText("手动创建")).toBeInTheDocument();
    });

    // "进入下一步" should not be visible before save
    expect(screen.queryByText("进入下一步 →")).not.toBeInTheDocument();

    fireEvent.click(screen.getByText("+ 添加角色"));
    fireEvent.click(screen.getByText("保存世界观"));

    // "进入下一步" should be visible after save
    await waitFor(() => {
      expect(screen.getByText("进入下一步 →")).toBeInTheDocument();
    });
  });

  it("marks dirty after file upload", async () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    mockedApi.uploadWorldviewFile.mockResolvedValue({ text: "uploaded file content" } as any);

    const { container } = render(<WorldviewEditor {...PROPS} />);

    await waitFor(() => {
      expect(screen.getByText("手动创建")).toBeInTheDocument();
    });

    // Switch to import mode
    fireEvent.click(screen.getByText("导入文档"));

    // Upload a file
    const fileInput = container.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File(["content"], "test.txt", { type: "text/plain" });
    fireEvent.change(fileInput, { target: { files: [file] } });

    await waitFor(() => {
      expect(screen.getByDisplayValue("uploaded file content")).toBeInTheDocument();
    });

    // Dirty should be true — back button triggers unsaved confirm
    fireEvent.click(screen.getByText("← 返回项目详情"));
    await waitFor(() => {
      expect(screen.getByText("未保存的修改")).toBeInTheDocument();
    });
  });

  it("marks dirty when mode actually changes", async () => {
    render(<WorldviewEditor {...PROPS} />);

    await waitFor(() => {
      expect(screen.getByText("手动创建")).toBeInTheDocument();
    });

    // Switch to import mode (actual change)
    fireEvent.click(screen.getByText("导入文档"));

    // Dirty should be true — back button triggers unsaved confirm
    fireEvent.click(screen.getByText("← 返回项目详情"));
    await waitFor(() => {
      expect(screen.getByText("未保存的修改")).toBeInTheDocument();
    });
  });

  it("does not create false dirty when clicking current mode", async () => {
    render(<WorldviewEditor {...PROPS} />);

    await waitFor(() => {
      expect(screen.getByText("手动创建")).toBeInTheDocument();
    });

    // Click current mode (manual)
    fireEvent.click(screen.getByText("手动创建"));

    // Dirty should be false — back button navigates immediately
    fireEvent.click(screen.getByText("← 返回项目详情"));
    expect(PROPS.onBack).toHaveBeenCalled();
  });
});

// ═══════════════════════════════════════════════════
// P1-3: Storage error recovery exit
// ═══════════════════════════════════════════════════
describe("WorldviewEditor — P1-3 storage error exit", () => {
  it("allows editing after storage error via continue button", async () => {
    const origGetItem = localStorage.getItem;
    Object.defineProperty(localStorage, "getItem", {
      configurable: true,
      value: vi.fn(() => { throw new Error("SecurityError"); }),
    });

    render(<WorldviewEditor {...PROPS} />);

    // Should show storage error dialog
    await screen.findByText("存储不可用");

    // Should have "继续编辑" button with accessible text
    const continueBtn = screen.getByText("继续编辑（无法使用本机草稿）");
    expect(continueBtn).toBeInTheDocument();

    // Click it
    fireEvent.click(continueBtn);

    // Dialog should close
    await waitFor(() => {
      expect(screen.queryByText("存储不可用")).not.toBeInTheDocument();
    });

    // Should be able to edit — add a character
    fireEvent.click(screen.getByText("+ 添加角色"));
    expect(screen.getByText("角色 (1)")).toBeInTheDocument();

    Object.defineProperty(localStorage, "getItem", { configurable: true, value: origGetItem });
  });

  it("prompts '请选择继续编辑' when pressing Escape in error dialog", async () => {
    const origGetItem = localStorage.getItem;
    Object.defineProperty(localStorage, "getItem", {
      configurable: true,
      value: vi.fn(() => { throw new Error("SecurityError"); }),
    });

    render(<WorldviewEditor {...PROPS} />);

    await screen.findByText("存储不可用");

    const dialog = screen.getByRole("dialog");
    fireEvent.keyDown(dialog, { key: "Escape" });

    // Should show prompt
    await waitFor(() => {
      expect(screen.getByRole("status").textContent).toContain("请选择继续编辑");
    });

    // Dialog should still be open
    expect(screen.getByText("存储不可用")).toBeInTheDocument();

    Object.defineProperty(localStorage, "getItem", { configurable: true, value: origGetItem });
  });
});

// ═══════════════════════════════════════════════════
// P1-4: Manual copy fallback inside active dialogs
// ═══════════════════════════════════════════════════
describe("WorldviewEditor — P1-4 copy fallback in dialogs", () => {
  it("renders manual copy textarea inside corrupt draft dialog with accessible name and focus", async () => {
    localStorage.setItem("wv-draft:test-user-1:proj-1", "{corrupt json");

    // Mock clipboard to reject
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: vi.fn().mockRejectedValue(new Error("NotAllowedError")) },
    });

    // Mock execCommand to return false
    const origExecCommand = document.execCommand;
    document.execCommand = vi.fn(() => false);

    render(<WorldviewEditor {...PROPS} />);
    await screen.findByText("草稿已损坏");

    // Click "复制内容"
    fireEvent.click(screen.getByText("复制内容"));

    // Wait for copy failure
    await waitFor(() => {
      expect(screen.getByRole("status").textContent).toContain("复制失败");
    });

    // Textarea should be inside the dialog with accessible name
    const dialog = screen.getByRole("dialog");
    const textarea = within(dialog).getByLabelText("手动复制内容") as HTMLTextAreaElement;
    expect(textarea).toBeTruthy();
    expect(textarea.readOnly).toBe(true);

    // Textarea should have focus
    await waitFor(() => {
      expect(document.activeElement).toBe(textarea);
    });

    // Content should be present
    expect(textarea.value).toContain("corrupt");

    document.execCommand = origExecCommand;
  });

  it("renders manual copy textarea in global area for non-dialog save error with accessible name", async () => {
    // Mock clipboard to reject
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: vi.fn().mockRejectedValue(new Error("NotAllowedError")) },
    });

    // Mock execCommand to return false
    const origExecCommand = document.execCommand;
    document.execCommand = vi.fn(() => false);

    render(<WorldviewEditor {...PROPS} />);

    await waitFor(() => {
      expect(screen.getByText("手动创建")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("+ 添加角色"));

    mockedApi.setWorldview.mockRejectedValue(new Error("Server error"));
    fireEvent.click(screen.getByText("保存世界观"));

    await waitFor(() => {
      expect(screen.getByText("复制当前内容")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("复制当前内容"));

    await waitFor(() => {
      expect(screen.getByRole("status").textContent).toContain("复制失败");
    });

    // Textarea should have accessible name
    const textarea = screen.getByLabelText("手动复制内容") as HTMLTextAreaElement;
    expect(textarea).toBeTruthy();
    expect(textarea.readOnly).toBe(true);

    document.execCommand = origExecCommand;
  });
});

// ═══════════════════════════════════════════════════
// P1-5: Clear failure visibility and state consistency
// ═══════════════════════════════════════════════════
describe("WorldviewEditor — P1-5 clear failure visibility", () => {
  it("clears stale localSaveError after successful save and clear", async () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    mockedApi.setWorldview.mockResolvedValue({} as any);

    // First: mock removeItem to fail so localSaveError becomes true
    const origRemoveItem = localStorage.removeItem;
    Object.defineProperty(localStorage, "removeItem", {
      configurable: true,
      value: vi.fn(() => { throw new Error("SecurityError"); }),
    });

    render(<WorldviewEditor {...PROPS} />);

    await waitFor(() => {
      expect(screen.getByText("手动创建")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("+ 添加角色"));
    fireEvent.click(screen.getByText("保存世界观"));

    // Save succeeds but clearDraft fails → localSaveError=true
    await waitFor(() => {
      expect(screen.getByRole("status").textContent).toContain("本机旧草稿未清除");
    });

    // localSaveError banner should be visible (has copy button)
    expect(screen.getByText("复制当前内容")).toBeInTheDocument();

    // Restore removeItem
    Object.defineProperty(localStorage, "removeItem", { configurable: true, value: origRemoveItem });

    // Save again — this time clearDraft succeeds
    fireEvent.click(screen.getByText("保存世界观"));

    await waitFor(() => {
      expect(screen.getByRole("status").textContent).toContain("保存成功");
    });

    // localSaveError should be cleared — no warning banner with copy button
    expect(screen.queryByText("复制当前内容")).not.toBeInTheDocument();
  });

  it("shows persistent visible error inside dialog when clearDraft fails on valid draft", async () => {
    saveDraft("test-user-1", "proj-1", {
      data: { ...EMPTY_DATA, characters: [{ name: "X", personality: "", background: "", motivation: "", ability: "", relations: [] }] },
      importText: "",
      mode: "manual",
      source: "manual",
      savedAt: Date.now(),
      schemaVersion: 1,
    });

    const origRemoveItem = localStorage.removeItem;
    Object.defineProperty(localStorage, "removeItem", {
      configurable: true,
      value: vi.fn(() => { throw new Error("SecurityError"); }),
    });

    render(<WorldviewEditor {...PROPS} />);
    await screen.findByText("发现未保存的草稿");

    fireEvent.click(screen.getByText("丢弃草稿"));

    // Dialog should still be open
    await waitFor(() => {
      expect(screen.getByText("发现未保存的草稿")).toBeInTheDocument();
    });

    // Visible error should be inside the dialog (not just sr-only live region)
    const dialog = screen.getByRole("dialog");
    expect(dialog.textContent).toContain("清除失败");

    Object.defineProperty(localStorage, "removeItem", { configurable: true, value: origRemoveItem });
  });
});

// ═══════════════════════════════════════════════════
// P1-6: Auth error includes local safety result
// ═══════════════════════════════════════════════════
describe("WorldviewEditor — P1-6 auth error local safety", () => {
  it("shows auth error with draft-saved message when local draft succeeds", async () => {
    render(<WorldviewEditor {...PROPS} />);

    await waitFor(() => {
      expect(screen.getByText("手动创建")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("+ 添加角色"));

    mockedApi.setWorldview.mockRejectedValue(new Error("登录已过期，请重新登录"));

    fireEvent.click(screen.getByText("保存世界观"));

    await waitFor(() => {
      const liveRegion = screen.getByRole("status");
      expect(liveRegion.textContent).toContain("登录已过期");
      expect(liveRegion.textContent).toContain("已保存为本机草稿");
    });
  });

  it("shows auth error with dual-failure message when local draft also fails", async () => {
    const origSetItem = localStorage.setItem;
    Object.defineProperty(localStorage, "setItem", {
      configurable: true,
      value: vi.fn(() => {
        throw new DOMException("Quota exceeded", "QuotaExceededError");
      }),
    });

    render(<WorldviewEditor {...PROPS} />);

    await waitFor(() => {
      expect(screen.getByText("手动创建")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("+ 添加角色"));

    mockedApi.setWorldview.mockRejectedValue(new Error("登录已过期，请重新登录"));

    fireEvent.click(screen.getByText("保存世界观"));

    await waitFor(() => {
      const liveRegion = screen.getByRole("status");
      expect(liveRegion.textContent).toContain("登录已过期");
      expect(liveRegion.textContent).toContain("均保存失败");
      expect(liveRegion.textContent).toContain("请立即复制");
    });

    Object.defineProperty(localStorage, "setItem", { configurable: true, value: origSetItem });
  });
});

// ═══════════════════════════════════════════════════
// BUG-004 Round 5: Copy fallback includes latest importText
// ═══════════════════════════════════════════════════
describe("WorldviewEditor — BUG-004 round 5 copy fallback importText", () => {
  it("clipboard success includes latest importText token after server+localStorage failure", async () => {
    const origSetItem = localStorage.setItem;
    Object.defineProperty(localStorage, "setItem", {
      configurable: true,
      value: vi.fn(() => {
        throw new DOMException("Quota exceeded", "QuotaExceededError");
      }),
    });

    // Mock clipboard to succeed
    const writeTextSpy = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: writeTextSpy },
    });

    // Mock import to succeed so the editor + save button are visible
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    mockedApi.importWorldview.mockResolvedValue({
      characters: [{ name: "Parsed", personality: "", background: "", motivation: "", ability: "", relations: [] }],
      geography: [], factions: [], power_system: [], history: [], conflicts: [], special_settings: [],
      element_count: 1,
    } as any);

    render(<WorldviewEditor {...PROPS} />);

    await waitFor(() => {
      expect(screen.getByText("手动创建")).toBeInTheDocument();
    });

    // Switch to hybrid mode and import text
    fireEvent.click(screen.getByText("混合模式"));
    const textarea = screen.getByPlaceholderText(/在此粘贴世界观文档内容/);
    fireEvent.change(textarea, { target: { value: "ORIGINAL_IMPORT_TEXT_ROUND5_A" } });
    fireEvent.click(screen.getByText("开始导入解析"));

    await waitFor(() => {
      expect(screen.getByText("已提取 1 个要素")).toBeInTheDocument();
    });

    // Modify import text to a unique new token — data.raw_text still holds the old value
    const UNIQUE_TOKEN = "UPDATED_IMPORT_TOKEN_ROUND5_UNIQUE_999";
    fireEvent.change(textarea, { target: { value: UNIQUE_TOKEN } });

    // Trigger dual save failure (server + localStorage)
    mockedApi.setWorldview.mockRejectedValue(new Error("Server error"));
    fireEvent.click(screen.getByText("保存世界观"));

    await waitFor(() => {
      expect(screen.getByText("复制当前内容")).toBeInTheDocument();
    });

    // Click copy — clipboard succeeds
    fireEvent.click(screen.getByText("复制当前内容"));

    await waitFor(() => {
      expect(writeTextSpy).toHaveBeenCalled();
    });

    const copiedContent = writeTextSpy.mock.calls[0][0] as string;
    // Must contain the unique latest importText token
    expect(copiedContent).toContain(UNIQUE_TOKEN);
    // Must include importText field — not just old data.raw_text
    expect(copiedContent).toContain("importText");

    Object.defineProperty(localStorage, "setItem", { configurable: true, value: origSetItem });
  });

  it("manual copy textarea value includes latest importText token when clipboard+execCommand fail", async () => {
    const origSetItem = localStorage.setItem;
    Object.defineProperty(localStorage, "setItem", {
      configurable: true,
      value: vi.fn(() => {
        throw new DOMException("Quota exceeded", "QuotaExceededError");
      }),
    });

    // Mock clipboard to reject
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: vi.fn().mockRejectedValue(new Error("NotAllowedError")) },
    });

    // Mock execCommand to return false
    const origExecCommand = document.execCommand;
    document.execCommand = vi.fn(() => false);

    // Mock import to succeed so the editor + save button are visible
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    mockedApi.importWorldview.mockResolvedValue({
      characters: [{ name: "Parsed", personality: "", background: "", motivation: "", ability: "", relations: [] }],
      geography: [], factions: [], power_system: [], history: [], conflicts: [], special_settings: [],
      element_count: 1,
    } as any);

    render(<WorldviewEditor {...PROPS} />);

    await waitFor(() => {
      expect(screen.getByText("手动创建")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("混合模式"));
    const textarea = screen.getByPlaceholderText(/在此粘贴世界观文档内容/);
    fireEvent.change(textarea, { target: { value: "ORIGINAL_IMPORT_TEXT_ROUND5_B" } });
    fireEvent.click(screen.getByText("开始导入解析"));

    await waitFor(() => {
      expect(screen.getByText("已提取 1 个要素")).toBeInTheDocument();
    });

    const UNIQUE_TOKEN = "UPDATED_IMPORT_TOKEN_ROUND5_UNIQUE_888";
    fireEvent.change(textarea, { target: { value: UNIQUE_TOKEN } });

    mockedApi.setWorldview.mockRejectedValue(new Error("Server error"));
    fireEvent.click(screen.getByText("保存世界观"));

    await waitFor(() => {
      expect(screen.getByText("复制当前内容")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("复制当前内容"));

    await waitFor(() => {
      expect(screen.getByRole("status").textContent).toContain("复制失败");
    });

    // Manual copy textarea should contain the unique latest importText token
    const manualTextarea = document.querySelector('textarea[readonly]') as HTMLTextAreaElement;
    expect(manualTextarea).toBeTruthy();
    expect(manualTextarea.readOnly).toBe(true);
    expect(manualTextarea.value).toContain(UNIQUE_TOKEN);

    Object.defineProperty(localStorage, "setItem", { configurable: true, value: origSetItem });
    document.execCommand = origExecCommand;
  });

  it("copy from valid recovered draft includes importText when data.raw_text is stale", async () => {
    const STALE_RAW_TEXT = "STALE_RAW_TEXT_TOKEN_ROUND5";
    const UNIQUE_IMPORT_TOKEN = "UNIQUE_DRAFT_IMPORT_TOKEN_ROUND5_777";

    saveDraft("test-user-1", "proj-1", {
      data: { ...EMPTY_DATA, raw_text: STALE_RAW_TEXT },
      importText: UNIQUE_IMPORT_TOKEN,
      mode: "import",
      source: "imported",
      savedAt: Date.now(),
      schemaVersion: 1,
    });

    // Mock clipboard to succeed
    const writeTextSpy = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: writeTextSpy },
    });

    render(<WorldviewEditor {...PROPS} />);
    await screen.findByText("发现未保存的草稿");

    fireEvent.click(screen.getByText("复制内容"));

    await waitFor(() => {
      expect(writeTextSpy).toHaveBeenCalled();
    });

    const copiedContent = writeTextSpy.mock.calls[0][0] as string;
    // Must contain the unique importText token (not just stale data.raw_text)
    expect(copiedContent).toContain(UNIQUE_IMPORT_TOKEN);
    // Should also contain the stale raw_text inside data
    expect(copiedContent).toContain(STALE_RAW_TEXT);
    // Should include the importText field name
    expect(copiedContent).toContain("importText");
  });
});
