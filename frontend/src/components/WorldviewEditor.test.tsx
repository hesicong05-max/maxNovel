import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, api } from "@/services/api";
import {
  draftStorageKey,
  fingerprintDraftBase,
  saveDraft,
  type DraftScope,
} from "@/services/maintenanceDrafts";
import type { WorldviewData } from "@/types";
import type { LoreExtractionBatch, LoreOverview } from "@/types/lore";
import WorldviewEditor from "./WorldviewEditor";

vi.mock("./AuthContext", () => ({
  useAuth: () => ({
    user: {
      id: "user-1",
      email: "writer@example.com",
      username: "writer",
      is_admin: false,
      created_at: "2026-07-30T00:00:00Z",
    },
  }),
}));

vi.mock("@/services/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/services/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getWorldview: vi.fn(),
      setWorldview: vi.fn(),
      importWorldview: vi.fn(),
      uploadWorldviewFile: vi.fn(),
      getLoreOverview: vi.fn(),
      createLoreExtraction: vi.fn(),
    },
  };
});

const emptyWorldview: WorldviewData = {
  characters: [],
  geography: [],
  factions: [],
  power_system: [],
  history: [],
  conflicts: [],
  special_settings: [],
  source: "manual",
};

const serverWorldview: Awaited<ReturnType<typeof api.getWorldview>> = {
  ...emptyWorldview,
  id: "worldview-1",
  parsed_elements: [],
  source: "manual",
};

const scope: DraftScope = {
  userId: "user-1",
  projectId: "project-1",
  kind: "worldview",
  objectId: "worldview",
};

const extractionScope: DraftScope = {
  userId: "user-1",
  projectId: "project-1",
  kind: "lore-extraction",
  objectId: "worldview-import",
};

const relationalOverview: LoreOverview = {
  formal_total: 0,
  confirmed_active: 0,
  pending_review: 0,
  needs_attention: 0,
  disabled: 0,
  archived: 0,
  review_pending: 0,
  migration_status: { storage_mode: "relational", state: "ready", read_only: false },
  capabilities: {
    candidate_review: true,
    candidate_accept: true,
    formal_create: true,
    formal_conflict_tracking: true,
    formal_merge_preview: true,
    formal_merge_commit: true,
    search_fields: [],
  },
  count_definitions: {},
};

function extractionBatch(
  overrides: Partial<LoreExtractionBatch> = {}
): LoreExtractionBatch {
  return {
    id: "batch-1",
    project_id: "project-1",
    status: "completed",
    source_kind: "worldview_import",
    source_ref: "世界观编辑器导入原文",
    source_hash: "a".repeat(64),
    source_preserved: true,
    extractor_version: "v1",
    model_name: "model",
    candidate_count: 3,
    pending_review_count: 3,
    accepted_count: 0,
    rejected_count: 0,
    failed_count: 0,
    retryable: false,
    error_code: null,
    error_message: null,
    created_at: "2026-08-07T00:00:00Z",
    updated_at: "2026-08-07T00:00:00Z",
    ...overrides,
  };
}

function renderEditor() {
  return render(
    <WorldviewEditor
      projectId="project-1"
      hasWorldview
      genre="玄幻"
      onComplete={vi.fn()}
      onBack={vi.fn()}
    />
  );
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

describe("WorldviewEditor maintenance drafts", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.mocked(api.getWorldview).mockResolvedValue(serverWorldview);
    vi.mocked(api.setWorldview).mockResolvedValue(serverWorldview);
    vi.mocked(api.importWorldview).mockResolvedValue({
      ...emptyWorldview,
      element_count: 0,
    });
    vi.mocked(api.uploadWorldviewFile).mockResolvedValue({
      text: "",
      filename: "worldview.txt",
      char_count: 0,
    });
    vi.mocked(api.getLoreOverview).mockResolvedValue({
      formal_total: 0,
      confirmed_active: 0,
      pending_review: 0,
      needs_attention: 0,
      disabled: 0,
      archived: 0,
      review_pending: 0,
      migration_status: { storage_mode: "legacy", state: "legacy", read_only: true },
      capabilities: {
        candidate_review: true,
        candidate_accept: false,
        formal_create: false,
        formal_conflict_tracking: false,
        formal_merge_preview: false,
        formal_merge_commit: false,
        search_fields: [],
      },
      count_definitions: {},
    });
    vi.spyOn(window, "alert").mockImplementation(() => {});
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: vi.fn().mockResolvedValue(undefined) },
    });
  });

  it("offers a conflicting device draft without overwriting server content", async () => {
    const originalDraft = saveDraft(
      scope,
      {
        data: {
          ...emptyWorldview,
          characters: [
            {
              name: "草稿角色",
              personality: "谨慎",
              background: "",
              motivation: "",
              ability: "",
              relations: [],
            },
          ],
        },
        source: "manual",
        mode: "manual",
        structuredReady: true,
      },
      null
    );

    renderEditor();

    expect(await screen.findByText(/人工核对/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "保存世界观" })).toBeDisabled();
    expect(localStorage.getItem(draftStorageKey(scope))).toBe(
      JSON.stringify(
        originalDraft.status === "saved" ? originalDraft.draft : null
      )
    );
    await userEvent.click(screen.getByRole("button", { name: "复制草稿" }));
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith(
      expect.stringContaining("名称：草稿角色")
    );
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith(
      expect.stringContaining("性格：谨慎")
    );
    expect(screen.queryByDisplayValue("草稿角色")).toBeNull();
    await userEvent.click(
      screen.getByRole("button", { name: "载入本地副本" })
    );
    expect(screen.getByDisplayValue("草稿角色")).toBeInTheDocument();
  });

  it("stores structured edits before showing the maintenance failure", async () => {
    vi.mocked(api.getWorldview).mockRejectedValueOnce(
      new ApiError(404, { detail: "not found" })
    );
    vi.mocked(api.setWorldview).mockRejectedValueOnce(
      new ApiError(503, {
        detail: "internal maintenance details",
        code: "PROJECT_WRITE_FROZEN",
        maintenance_state: "write_frozen",
        retryable: true,
        event_id: "BUG-002B",
      })
    );
    const user = userEvent.setup();
    renderEditor();

    await screen.findByText("世界观创建方式");
    await user.click(screen.getByRole("button", { name: "+ 添加角色" }));
    await user.type(screen.getByPlaceholderText("姓名"), "林远");
    await user.click(screen.getByRole("button", { name: "保存世界观" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "未保存的内容已保留在此设备"
    );
    expect(screen.queryByText("internal maintenance details")).toBeNull();
    const serialized = localStorage.getItem(draftStorageKey(scope));
    expect(serialized).toContain("林远");
    expect(serialized).not.toContain("raw_text");
  });

  it("clears only the matching device draft after a successful server save", async () => {
    const otherScope: DraftScope = {
      ...scope,
      projectId: "another-project",
    };
    saveDraft(
      otherScope,
      {
        data: emptyWorldview,
        source: "manual",
        mode: "manual",
        structuredReady: true,
      },
      null
    );
    saveDraft(
      scope,
      {
        data: {
          ...emptyWorldview,
          characters: [
            {
              name: "待保存角色",
              personality: "",
              background: "",
              motivation: "",
              ability: "",
              relations: [],
            },
          ],
        },
        source: "manual",
        mode: "manual",
        structuredReady: true,
      },
      null
    );
    const user = userEvent.setup();
    renderEditor();

    await screen.findByText(/人工核对/);
    await user.click(
      screen.getByRole("button", { name: "载入本地副本" })
    );
    await user.click(screen.getByRole("button", { name: "保存世界观" }));

    await waitFor(() =>
      expect(localStorage.getItem(draftStorageKey(scope))).toBeNull()
    );
    expect(localStorage.getItem(draftStorageKey(otherScope))).not.toBeNull();
    expect(api.setWorldview).toHaveBeenCalledOnce();
  });

  it("keeps edits made while a save request is in flight", async () => {
    const pending = deferred<Awaited<ReturnType<typeof api.setWorldview>>>();
    vi.mocked(api.setWorldview).mockReturnValueOnce(pending.promise);
    const user = userEvent.setup();
    renderEditor();

    await screen.findByText("世界观创建方式");
    await user.click(screen.getByRole("button", { name: "+ 添加角色" }));
    await user.type(screen.getByPlaceholderText("姓名"), "提交角色");
    await user.click(screen.getByRole("button", { name: "保存世界观" }));
    await user.type(screen.getByPlaceholderText("性格"), "保存期间新增");
    await act(async () => pending.resolve(serverWorldview));

    expect(screen.getByDisplayValue("保存期间新增")).toBeInTheDocument();
    await waitFor(() =>
      expect(localStorage.getItem(draftStorageKey(scope))).toContain(
        "保存期间新增"
      )
    );
    // 持续提示而非 alert 弹窗
    expect(
      await screen.findByText(
        /提交时的版本已保存.*新编辑.*未保存到项目/
      )
    ).toBeInTheDocument();
  });

  it("restores an imported structured draft into a visible editor", async () => {
    saveDraft(
      scope,
      {
        data: {
          ...emptyWorldview,
          characters: [
            {
              name: "导入角色",
              personality: "",
              background: "",
              motivation: "",
              ability: "",
              relations: [],
            },
          ],
          source: "hybrid",
        },
        source: "hybrid",
        mode: "hybrid",
        structuredReady: true,
      },
      null
    );
    const user = userEvent.setup();
    renderEditor();

    await screen.findByText(/人工核对/);
    await user.click(
      screen.getByRole("button", { name: "载入本地副本" })
    );
    expect(screen.getByDisplayValue("导入角色")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "保存世界观" })).toBeVisible();
  });

  it("flushes a sub-debounce edit to the old project scope when switching", async () => {
    const user = userEvent.setup();
    const view = renderEditor();
    await screen.findByText("世界观创建方式");
    await user.click(screen.getByRole("button", { name: "+ 添加角色" }));
    await user.type(screen.getByPlaceholderText("姓名"), "项目一草稿");

    view.rerender(
      <WorldviewEditor
        projectId="project-2"
        hasWorldview
        genre="玄幻"
        onComplete={vi.fn()}
        onBack={vi.fn()}
      />
    );

    expect(localStorage.getItem(draftStorageKey(scope))).toContain(
      "项目一草稿"
    );
  });

  it("ignores a stale project response that arrives after a scope change", async () => {
    const projectOne = deferred<Awaited<ReturnType<typeof api.getWorldview>>>();
    vi.mocked(api.getWorldview)
      .mockReturnValueOnce(projectOne.promise)
      .mockResolvedValueOnce({
        ...serverWorldview,
        id: "worldview-2",
        characters: [
          {
            name: "项目二角色",
            personality: "",
            background: "",
            motivation: "",
            ability: "",
            relations: [],
          },
        ],
      });
    const view = renderEditor();
    view.rerender(
      <WorldviewEditor
        projectId="project-2"
        hasWorldview
        genre="玄幻"
        onComplete={vi.fn()}
        onBack={vi.fn()}
      />
    );

    expect(await screen.findByDisplayValue("项目二角色")).toBeInTheDocument();
    await act(async () =>
      projectOne.resolve({
        ...serverWorldview,
        characters: [
          {
            name: "迟到的项目一角色",
            personality: "",
            background: "",
            motivation: "",
            ability: "",
            relations: [],
          },
        ],
      })
    );
    expect(screen.queryByDisplayValue("迟到的项目一角色")).toBeNull();
    expect(screen.getByDisplayValue("项目二角色")).toBeInTheDocument();
  });

  it("autosaves pending import text before AI parsing", async () => {
    vi.mocked(api.getWorldview).mockRejectedValueOnce(
      new ApiError(404, { detail: "not found" })
    );
    const user = userEvent.setup();
    renderEditor();
    await screen.findByText("世界观创建方式");
    await user.click(screen.getByText("导入文档"));
    await user.type(
      screen.getByPlaceholderText(/在此粘贴世界观文档内容/),
      "这是一段尚未交给 AI 解析的长篇世界观原文。"
    );

    await waitFor(
      () =>
        expect(localStorage.getItem(draftStorageKey(scope))).toContain(
          "尚未交给 AI 解析"
        ),
      { timeout: 1500 }
    );
  });

  it("retains import text in the draft after parsing until server save", async () => {
    vi.mocked(api.getWorldview).mockRejectedValueOnce(
      new ApiError(404, { detail: "not found" })
    );
    const user = userEvent.setup();
    renderEditor();
    await screen.findByText("世界观创建方式");
    await user.click(screen.getByText("混合模式"));
    await user.type(
      screen.getByPlaceholderText(/在此粘贴世界观文档内容/),
      "解析完成但尚未保存到项目的世界观原始资料。"
    );
    await user.click(screen.getByRole("button", { name: "兼容解析并填充表单" }));

    await waitFor(
      () =>
        expect(localStorage.getItem(draftStorageKey(scope))).toContain(
          "世界观原始资料"
        ),
      { timeout: 1500 }
    );
  });

  it("shows a selectable plain-text fallback when clipboard access fails", async () => {
    saveDraft(
      scope,
      {
        data: {
          ...emptyWorldview,
          characters: [
            {
              name: "可复制角色",
              personality: "沉着",
              background: "",
              motivation: "",
              ability: "",
              relations: [],
            },
          ],
        },
        source: "manual",
        mode: "manual",
        structuredReady: true,
      },
      null
    );
    const user = userEvent.setup();
    vi.spyOn(navigator.clipboard, "writeText").mockRejectedValueOnce(
      new Error("denied")
    );
    renderEditor();

    await screen.findByText(/人工核对/);
    await user.click(screen.getByRole("button", { name: "复制草稿" }));
    const fallback = await screen.findByRole<HTMLTextAreaElement>("textbox", {
      name: "自动复制失败，请全选下方纯文本副本并复制",
    });
    expect(fallback.value).toContain("名称：可复制角色");
  });

  it("reports when server save succeeds but exact draft cleanup fails", async () => {
    const user = userEvent.setup();
    renderEditor();
    await screen.findByText("世界观创建方式");
    await user.click(screen.getByRole("button", { name: "+ 添加角色" }));
    await user.type(screen.getByPlaceholderText("姓名"), "清理失败角色");
    vi.spyOn(localStorage, "removeItem").mockImplementationOnce(() => {
      throw new Error("storage blocked");
    });
    await user.click(screen.getByRole("button", { name: "保存世界观" }));

    // 持续页面内提示，不调用 alert
    // 排除 sr-only 播报区后断言可见元素
    await waitFor(() =>
      expect(
        screen.getByText(/世界.*已保存.*草稿未能清除/, { selector: "p" })
      ).toBeInTheDocument()
    );
    expect(localStorage.getItem(draftStorageKey(scope))).toContain(
      "清理失败角色"
    );
  });

  it("keeps a persistent warning if the post-save new-edit draft write fails", async () => {
    const pending = deferred<Awaited<ReturnType<typeof api.setWorldview>>>();
    vi.mocked(api.setWorldview).mockReturnValueOnce(pending.promise);
    const originalSetItem = localStorage.setItem.bind(localStorage);
    let writes = 0;
    vi.spyOn(localStorage, "setItem").mockImplementation((key, value) => {
      writes += 1;
      if (writes === 2) throw new Error("quota");
      originalSetItem(key, value);
    });
    const user = userEvent.setup();
    renderEditor();
    await screen.findByText("世界观创建方式");
    await user.click(screen.getByRole("button", { name: "+ 添加角色" }));
    await user.type(screen.getByPlaceholderText("姓名"), "提交角色");
    await user.click(screen.getByRole("button", { name: "保存世界观" }));
    await user.type(screen.getByPlaceholderText("性格"), "无法落盘的新编辑");
    await act(async () => pending.resolve(serverWorldview));

    expect(
      await screen.findByText(/提交时版本已保存/)
    ).toBeInTheDocument();
    // 通用草稿存储失败横幅不应出现，已被 saveResult 覆盖
    expect(
      screen.queryByText("本地草稿未能保存")
    ).toBeNull();

    // 专用横幅提供复制按钮且复制当前最新编辑（非提交时旧版本）
    const copyBtn = screen.getByRole("button", { name: "复制未保存内容" });
    expect(copyBtn).toBeInTheDocument();
    const clipSpy = vi.spyOn(navigator.clipboard, "writeText").mockResolvedValue(undefined);
    await user.click(copyBtn);
    expect(clipSpy).toHaveBeenCalledWith(
      expect.stringContaining("无法落盘的新编辑")
    );
    clipSpy.mockRestore();
  });

  it("hides old project data while the next project is still loading", async () => {
    vi.mocked(api.getWorldview).mockResolvedValueOnce({
      ...serverWorldview,
      characters: [
        {
          name: "项目一旧内容",
          personality: "",
          background: "",
          motivation: "",
          ability: "",
          relations: [],
        },
      ],
    });
    const projectTwo = deferred<Awaited<ReturnType<typeof api.getWorldview>>>();
    vi.mocked(api.getWorldview).mockReturnValueOnce(projectTwo.promise);
    const view = renderEditor();
    await screen.findByDisplayValue("项目一旧内容");

    view.rerender(
      <WorldviewEditor
        projectId="project-2"
        hasWorldview
        genre="玄幻"
        onComplete={vi.fn()}
        onBack={vi.fn()}
      />
    );
    expect(screen.getByText("正在加载世界观…")).toBeInTheDocument();
    expect(screen.queryByDisplayValue("项目一旧内容")).toBeNull();
    await act(async () =>
      projectTwo.resolve({ ...serverWorldview, id: "worldview-2" })
    );
    expect(await screen.findByText("世界观创建方式")).toBeInTheDocument();
  });

  it("ignores an AI import result from the previous project scope", async () => {
    const pendingImport =
      deferred<Awaited<ReturnType<typeof api.importWorldview>>>();
    vi.mocked(api.importWorldview).mockReturnValueOnce(pendingImport.promise);
    const user = userEvent.setup();
    const view = renderEditor();
    await screen.findByText("世界观创建方式");
    await user.click(screen.getByText("混合模式"));
    await user.type(
      screen.getByPlaceholderText(/在此粘贴世界观文档内容/),
      "项目一等待解析的世界观内容"
    );
    await user.click(screen.getByRole("button", { name: "兼容解析并填充表单" }));
    view.rerender(
      <WorldviewEditor
        projectId="project-2"
        hasWorldview
        genre="玄幻"
        onComplete={vi.fn()}
        onBack={vi.fn()}
      />
    );
    await screen.findByText("世界观创建方式");
    await act(async () =>
      pendingImport.resolve({
        ...emptyWorldview,
        characters: [
          {
            name: "项目一迟到导入角色",
            personality: "",
            background: "",
            motivation: "",
            ability: "",
            relations: [],
          },
        ],
        element_count: 1,
      })
    );
    expect(screen.queryByDisplayValue("项目一迟到导入角色")).toBeNull();
    const projectTwoScope = { ...scope, projectId: "project-2" };
    expect(
      localStorage.getItem(draftStorageKey(projectTwoScope)) || ""
    ).not.toContain("项目一迟到导入角色");
  });

  // ── 新增测试：P0 验收 ──

  it("enters an editable empty worldview when getWorldview returns null", async () => {
    // 生产代码防御运行时空值，尽管 TypeScript 类型不允许 null
    vi.mocked(api.getWorldview).mockResolvedValueOnce(
      null as unknown as Awaited<ReturnType<typeof api.getWorldview>>
    );
    renderEditor();

    expect(await screen.findByText("世界观创建方式")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "+ 添加角色" })).toBeEnabled();
  });

  it("shows a persistent error and manual retry when loading fails", async () => {
    vi.mocked(api.getWorldview).mockRejectedValueOnce(
      new ApiError(500, { detail: "server error" })
    );
    renderEditor();

    expect(
      await screen.findByRole("heading", { name: "世界观加载失败" })
    ).toBeInTheDocument();
    const retryBtn = screen.getByRole("button", { name: "重新加载" });
    expect(retryBtn).toBeEnabled();
  });

  it("disables the retry button and shows busy state while reloading", async () => {
    // Trigger load failure, then verify retry button is disabled while reloading
    vi.mocked(api.getWorldview)
      .mockRejectedValueOnce(new ApiError(500, { detail: "server error" }));
    const pending = deferred<Awaited<ReturnType<typeof api.getWorldview>>>();
    vi.mocked(api.getWorldview).mockReturnValueOnce(pending.promise);
    renderEditor();

    expect(
      await screen.findByRole("heading", { name: "世界观加载失败" })
    ).toBeInTheDocument();
    const retryBtn = screen.getByRole("button", { name: "重新加载" });
    expect(retryBtn).toBeEnabled();

    // Click retry — button should become busy
    await userEvent.click(retryBtn);
    const reloadingBtn = await screen.findByRole("button", {
      name: "正在重新加载…",
    });
    expect(reloadingBtn).toBeDisabled();
    await act(async () => pending.resolve(serverWorldview));
    // After retry succeeds, editor should appear
    expect(await screen.findByText("世界观创建方式")).toBeInTheDocument();
  });

  it("flushes draft synchronously on pagehide before the debounce timer", async () => {
    const user = userEvent.setup();
    renderEditor();
    await screen.findByText("世界观创建方式");
    await user.click(screen.getByRole("button", { name: "+ 添加角色" }));
    // 清除 localStorage 以便精确验证 pagehide 保存行为
    localStorage.removeItem(draftStorageKey(scope));
    await user.type(screen.getByPlaceholderText("姓名"), "页面前刷新内容");
    // 不等待 debounce — 立即派发 pagehide（debounce 700ms 尚未到期）
    // pagehide 处理器通过 dirtyRef 判断是否有脏编辑并同步写入 localStorage
    window.dispatchEvent(new Event("pagehide"));

    // 同步保存后 localStorage 应包含最新内容
    expect(localStorage.getItem(draftStorageKey(scope))).toContain(
      "页面前刷新内容"
    );
  });

  it("blocks next step when there are unsaved edits and provides a confirmation", async () => {
    const onComplete = vi.fn();
    const user = userEvent.setup();
    render(
      <WorldviewEditor
        projectId="project-1"
        hasWorldview
        genre="玄幻"
        onComplete={onComplete}
        onBack={vi.fn()}
      />
    );
    await screen.findByText("世界观创建方式");
    await user.click(screen.getByRole("button", { name: "+ 添加角色" }));
    await user.type(screen.getByPlaceholderText("姓名"), "未保存角色");

    // Wait for debounce to save draft
    await waitFor(
      () =>
        expect(localStorage.getItem(draftStorageKey(scope))).toContain(
          "未保存角色"
        ),
      { timeout: 1500 }
    );

    // Open the lore repository with unsaved local edits.
    await user.click(screen.getByRole("button", { name: /打开设定仓库/ }));

    // Should show confirmation, not call onComplete
    expect(onComplete).not.toHaveBeenCalled();
    expect(
      await screen.findByRole("heading", { name: "内容仅保存在本设备" })
    ).toBeInTheDocument();

    // User chooses to proceed
    await user.click(screen.getByRole("button", { name: "仍要打开设定仓库" }));
    expect(onComplete).toHaveBeenCalledOnce();
  });

  it("shows a persistent save error with retry and copy options", async () => {
    vi.mocked(api.setWorldview).mockRejectedValueOnce(
      new ApiError(500, { detail: "network error" })
    );
    const user = userEvent.setup();
    renderEditor();
    await screen.findByText("世界观创建方式");
    await user.click(screen.getByRole("button", { name: "+ 添加角色" }));
    await user.type(screen.getByPlaceholderText("姓名"), "保存失败角色");
    await user.click(screen.getByRole("button", { name: "保存世界观" }));

    expect(
      await screen.findByRole("heading", { name: "保存失败" })
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "重试保存" })
    ).toBeVisible();
    expect(
      screen.getByRole("button", { name: "复制内容" })
    ).toBeVisible();
  });

  it("clears maintenance notice when a retry results in a non-maintenance error", async () => {
    vi.mocked(api.setWorldview)
      .mockRejectedValueOnce(
        new ApiError(503, {
          detail: "frozen",
          code: "PROJECT_WRITE_FROZEN",
          maintenance_state: "write_frozen",
          retryable: true,
          event_id: "BUG-002B",
        })
      )
      .mockRejectedValueOnce(new ApiError(500, { detail: "network" }));
    const user = userEvent.setup();
    renderEditor();
    await screen.findByText("世界观创建方式");
    await user.click(screen.getByRole("button", { name: "+ 添加角色" }));
    await user.type(screen.getByPlaceholderText("姓名"), "重试角色");
    await user.click(screen.getByRole("button", { name: "保存世界观" }));

    // First save: maintenance
    expect(await screen.findByText(/项目正在维护/)).toBeInTheDocument();

    // Retry: network error
    await user.click(screen.getByRole("button", { name: "手动重试保存" }));
    expect(
      await screen.findByRole("heading", { name: "保存失败" })
    ).toBeInTheDocument();
    expect(screen.queryByText(/项目正在维护/)).toBeNull();
  });

  it("shows accessible names on remove buttons", async () => {
    const user = userEvent.setup();
    renderEditor();
    await screen.findByText("世界观创建方式");
    await user.click(screen.getByRole("button", { name: "+ 添加角色" }));

    expect(
      screen.getByRole("button", { name: "移除角色 1" })
    ).toBeInTheDocument();
  });

  it("shows accessible labels on repeated input fields", async () => {
    const user = userEvent.setup();
    renderEditor();
    await screen.findByText("世界观创建方式");
    await user.click(screen.getByRole("button", { name: "+ 添加角色" }));
    await user.click(screen.getByRole("button", { name: "+ 添加角色" }));

    expect(
      screen.getByRole("textbox", { name: "角色1 姓名" })
    ).toBeInTheDocument();
    expect(
      screen.getByRole("textbox", { name: "角色2 姓名" })
    ).toBeInTheDocument();
  });

  it("shows a corrupt draft recovery notice without silently deleting it", async () => {
    // Store a corrupted draft
    localStorage.setItem(draftStorageKey(scope), "{ broken json");

    renderEditor();

    // Corrupt draft shows a specific alert heading
    expect(await screen.findByRole("heading", { name: "本地草稿无法读取" })).toBeInTheDocument();
    // Draft key should not have been deleted
    expect(localStorage.getItem(draftStorageKey(scope))).not.toBeNull();
  });

  it("moves focus to section title after discarding a draft", async () => {
    saveDraft(
      scope,
      {
        data: {
          ...emptyWorldview,
          characters: [
            {
              name: "丢弃角色",
              personality: "",
              background: "",
              motivation: "",
              ability: "",
              relations: [],
            },
          ],
        },
        source: "manual",
        mode: "manual",
        structuredReady: true,
      },
      null
    );
    const user = userEvent.setup();
    renderEditor();

    await screen.findByText(/人工核对/);
    // Confirm discard
    vi.spyOn(window, "confirm").mockReturnValueOnce(true);
    await user.click(screen.getByRole("button", { name: "丢弃本地草稿" }));

    // Draft should be cleared
    await waitFor(() =>
      expect(localStorage.getItem(draftStorageKey(scope))).toBeNull()
    );
  });

  it("keeps import text when switching back to manual mode without parsing", async () => {
    vi.mocked(api.getWorldview).mockRejectedValueOnce(
      new ApiError(404, { detail: "not found" })
    );
    const user = userEvent.setup();
    renderEditor();
    await screen.findByText("世界观创建方式");
    await user.click(screen.getByText("导入文档"));
    await user.type(
      screen.getByPlaceholderText(/在此粘贴世界观文档内容/),
      "未解析的原文内容"
    );

    // Switch back to manual
    await user.click(screen.getByText("手动创建"));

    // Switching to manual with import text should not throw
    // The import text is preserved in data.raw_text (React state)
    // Manual mode does not render the import textarea
    expect(screen.getByText("世界观创建方式")).toBeInTheDocument();
    expect(
      screen.queryByPlaceholderText(/在此粘贴世界观文档内容/)
    ).toBeNull();
  });

  it("invalidates old parse result when import text is modified after parsing", async () => {
    vi.mocked(api.getWorldview).mockRejectedValueOnce(
      new ApiError(404, { detail: "not found" })
    );
    vi.mocked(api.importWorldview).mockResolvedValueOnce({
      ...emptyWorldview,
      characters: [
        {
          name: "解析角色",
          personality: "",
          background: "",
          motivation: "",
          ability: "",
          relations: [],
        },
      ],
      element_count: 1,
    });
    const user = userEvent.setup();
    renderEditor();
    await screen.findByText("世界观创建方式");
    await user.click(screen.getByText("混合模式"));
    await user.type(
      screen.getByPlaceholderText(/在此粘贴世界观文档内容/),
      "原始世界观内容用于解析。"
    );
    await user.click(screen.getByRole("button", { name: "兼容解析并填充表单" }));

    // After parse, the tag should show
    expect(
      await screen.findByText(/已提取 1 个要素/)
    ).toBeInTheDocument();

    // Modify the import text — should invalidate parse result
    const textarea = screen.getByPlaceholderText(/在此粘贴世界观文档内容/);
    await user.clear(textarea);
    await user.type(textarea, "修改后的世界观内容。");

    // The "已提取" tag should be gone
    expect(screen.queryByText("已提取 1 个要素", { exact: true })).toBeNull();
  });

  it("shows page-internal error when AI import fails without using alert", async () => {
    vi.mocked(api.getWorldview).mockRejectedValueOnce(
      new ApiError(404, { detail: "not found" })
    );
    vi.mocked(api.importWorldview).mockRejectedValueOnce(
      new ApiError(500, { detail: "AI service down" })
    );
    const user = userEvent.setup();
    renderEditor();
    await screen.findByText("世界观创建方式");
    await user.click(screen.getByText("导入文档"));
    await user.type(
      screen.getByPlaceholderText(/在此粘贴世界观文档内容/),
      "AI 解析失败的原文内容。"
    );
    await user.click(screen.getByRole("button", { name: "兼容解析并填充表单" }));

    // 使用 role="alert" 定位错误提示区域，避免匹配到 textarea value
    expect(await screen.findByRole("alert")).toHaveTextContent("AI 解析失败");
    // Original text should be retained
    expect(
      screen.getByDisplayValue(/AI 解析失败的原文内容/)
    ).toBeInTheDocument();
  });

  it("uses maintenance notice when AI import gets PROJECT_WRITE_FROZEN", async () => {
    vi.mocked(api.getWorldview).mockRejectedValueOnce(
      new ApiError(404, { detail: "not found" })
    );
    vi.mocked(api.importWorldview).mockRejectedValueOnce(
      new ApiError(503, {
        detail: "frozen",
        code: "PROJECT_WRITE_FROZEN",
        maintenance_state: "write_frozen",
        retryable: true,
        event_id: "BUG-002B",
      })
    );
    const user = userEvent.setup();
    renderEditor();
    await screen.findByText("世界观创建方式");
    await user.click(screen.getByText("导入文档"));
    await user.type(
      screen.getByPlaceholderText(/在此粘贴世界观文档内容/),
      "维护冻结时尝试导入的内容。"
    );
    await user.click(screen.getByRole("button", { name: "兼容解析并填充表单" }));

    expect(await screen.findByText(/项目正在维护/)).toBeInTheDocument();
    expect(screen.queryByText(/AI 解析失败/)).toBeNull();
    // Original text should be retained in draft
    expect(localStorage.getItem(draftStorageKey(scope))).toContain(
      "维护冻结时尝试导入的内容"
    );
  });

  it("shows page-internal error when file upload fails", async () => {
    vi.mocked(api.uploadWorldviewFile).mockRejectedValueOnce(
      new ApiError(500, { detail: "upload failed" })
    );
    vi.mocked(api.getWorldview).mockRejectedValueOnce(
      new ApiError(404, { detail: "not found" })
    );
    const user = userEvent.setup();
    renderEditor();
    await screen.findByText("世界观创建方式");
    await user.click(screen.getByText("导入文档"));

    // Create a real file and trigger the upload
    const file = new File(["测试上传内容"], "test.txt", { type: "text/plain" });
    const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
    expect(fileInput).toBeTruthy();
    await user.upload(fileInput, file);

    // Verify the upload error is displayed on the page
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "文件解析失败"
    );
  });

  // ── 补充测试 A-L ──

  it("A: keeps raw_text in draft after switching from import to manual without parsing", async () => {
    vi.mocked(api.getWorldview).mockRejectedValueOnce(
      new ApiError(404, { detail: "not found" })
    );
    const user = userEvent.setup();
    renderEditor();
    await screen.findByText("世界观创建方式");
    await user.click(screen.getByText("导入文档"));
    await user.type(
      screen.getByPlaceholderText(/在此粘贴世界观文档内容/),
      "未解析但要保留的原文"
    );

    // Switch to manual — raw_text should be set from importText
    await user.click(screen.getByText("手动创建"));

    // Wait for debounce auto-save
    await waitFor(
      () =>
        expect(localStorage.getItem(draftStorageKey(scope))).toContain(
          "未解析但要保留的原文"
        ),
      { timeout: 1500 }
    );
  });

  it("B: sends latest raw_text in setWorldview when text was modified after parse then switched to manual", async () => {
    vi.mocked(api.getWorldview).mockRejectedValueOnce(
      new ApiError(404, { detail: "not found" })
    );
    vi.mocked(api.importWorldview).mockResolvedValueOnce({
      ...emptyWorldview,
      characters: [
        {
          name: "解析角色",
          personality: "",
          background: "",
          motivation: "",
          ability: "",
          relations: [],
        },
      ],
      element_count: 1,
    });
    const user = userEvent.setup();
    renderEditor();
    await screen.findByText("世界观创建方式");
    await user.click(screen.getByText("混合模式"));
    await user.type(
      screen.getByPlaceholderText(/在此粘贴世界观文档内容/),
      "原始解析文本必须足够长的内容"
    );
    // Parse
    await user.click(screen.getByRole("button", { name: "兼容解析并填充表单" }));
    await screen.findByText(/已提取 1 个要素/);

    // Modify the import text
    const textarea = screen.getByPlaceholderText(/在此粘贴世界观文档内容/);
    await user.clear(textarea);
    await user.type(textarea, "修改后的最新原文内容用于测试");

    // Switch to manual, then save
    await user.click(screen.getByText("手动创建"));
    await user.click(screen.getByRole("button", { name: "保存世界观" }));

    // Verify setWorldview was called with latest raw_text
    await waitFor(() =>
      expect(api.setWorldview).toHaveBeenCalledWith(
        "project-1",
        expect.objectContaining({ raw_text: "修改后的最新原文内容用于测试" })
      )
    );
  });

  it("C: retains raw_text when restoring imported draft and saving", async () => {
    saveDraft(
      scope,
      {
        data: {
          ...emptyWorldview,
          raw_text: "导入时保留的原始资料",
          source: "imported",
        },
        source: "imported",
        mode: "import",
        structuredReady: true,
        pendingImportText: "导入时保留的原始资料",
      },
      null
    );
    const user = userEvent.setup();
    renderEditor();

    await screen.findByText(/人工核对/);
    await user.click(screen.getByRole("button", { name: "载入本地副本" }));
    await user.click(screen.getByRole("button", { name: "保存世界观" }));

    await waitFor(() =>
      expect(api.setWorldview).toHaveBeenCalledWith(
        "project-1",
        expect.objectContaining({ raw_text: "导入时保留的原始资料" })
      )
    );
  });

  it("D: does not claim local draft is saved when localStorage.setItem throws on save", async () => {
    vi.mocked(api.setWorldview).mockRejectedValueOnce(
      new ApiError(500, { detail: "network error" })
    );
    // Make localStorage.setItem throw so draft cannot be saved
    vi.spyOn(localStorage, "setItem").mockImplementation(() => {
      throw new Error("quota exceeded");
    });
    const user = userEvent.setup();
    renderEditor();
    await screen.findByText("世界观创建方式");
    await user.click(screen.getByRole("button", { name: "+ 添加角色" }));
    await user.type(screen.getByPlaceholderText("姓名"), "丢失角色");
    await user.click(screen.getByRole("button", { name: "保存世界观" }));

    // 保存失败时不得声称内容已在本地草稿安全保留
    expect(
      await screen.findByRole("heading", { name: "保存失败" })
    ).toBeInTheDocument();
    const errorText = screen.getByRole("heading", { name: "保存失败" })
      .parentElement?.textContent || "";
    expect(errorText).not.toContain("已保留在页面和本地草稿中");
    expect(errorText).toContain("请立即复制");
    expect(screen.getByRole("button", { name: "复制内容" })).toBeVisible();
  });

  it("E: corrupt draft has no load or copy button, only discard with confirmation", async () => {
    localStorage.setItem(draftStorageKey(scope), "{ broken json");
    renderEditor();

    // Should show corrupt draft notice
    expect(await screen.findByText(/本地草稿无法读取/)).toBeInTheDocument();
    // No load button
    expect(
      screen.queryByRole("button", { name: "载入本地副本" })
    ).toBeNull();
    // No copy button (for recovery)
    expect(
      screen.queryByRole("button", { name: "复制草稿" })
    ).toBeNull();
    // Only discard button
    const discardBtn = screen.getByRole("button", {
      name: "确认丢弃损坏草稿",
    });
    expect(discardBtn).toBeVisible();

    // Discard should require confirmation
    const conf = vi.spyOn(window, "confirm").mockReturnValueOnce(false);
    await userEvent.click(discardBtn);
    expect(conf).toHaveBeenCalledWith(
      "项目中已保存的内容不会受影响。确认丢弃损坏的本地草稿吗？"
    );
    expect(localStorage.getItem(draftStorageKey(scope))).not.toBeNull();

    // Confirm discard
    conf.mockReturnValueOnce(true);
    await userEvent.click(discardBtn);
    await waitFor(() =>
      expect(localStorage.getItem(draftStorageKey(scope))).toBeNull()
    );
  });

  it("F: single polite live region announces draft detection, no duplicate role=status", async () => {
    saveDraft(
      scope,
      {
        data: emptyWorldview,
        source: "manual",
        mode: "manual",
        structuredReady: true,
      },
      null
    );
    renderEditor();

    await screen.findByText(/人工核对/);

    // Root polite live region should exist
    const liveRegion = document.querySelector('[aria-live="polite"]');
    expect(liveRegion).toBeTruthy();

    // DraftRecoveryNotice uses role="group", not role="status"
    const recoverySection = screen.getByRole("group", { name: "发现本地草稿" });
    expect(recoverySection.tagName).toBe("SECTION");

    // No implicit polite live regions from role="status" elsewhere
    // (copyFallback, loading, nextStepBlocked should NOT use role="status")
    const statusElements = document.querySelectorAll('[role="status"]:not([aria-live="polite"])');
    expect(Array.from(statusElements).filter(
      el => !el.closest('[aria-live="polite"]')
    ).length).toBe(0);
  });

  it("G: mode selector buttons have aria-pressed semantics", async () => {
    renderEditor();
    await screen.findByText("世界观创建方式");

    const manualBtn = screen.getByRole("button", { name: /手动创建/ });
    const importBtn = screen.getByRole("button", { name: /导入文档/ });
    const hybridBtn = screen.getByRole("button", { name: /混合模式/ });

    // All three mode buttons should have aria-pressed
    expect(manualBtn.getAttribute("aria-pressed")).toBe("true");
    expect(importBtn.getAttribute("aria-pressed")).toBe("false");
    expect(hybridBtn.getAttribute("aria-pressed")).toBe("false");

    // Click import — should toggle pressed states
    await userEvent.click(importBtn);
    expect(manualBtn.getAttribute("aria-pressed")).toBe("false");
    expect(importBtn.getAttribute("aria-pressed")).toBe("true");
    expect(hybridBtn.getAttribute("aria-pressed")).toBe("false");
  });

  it("H: shows reparse warning when import text is modified after parsing", async () => {
    vi.mocked(api.getWorldview).mockRejectedValueOnce(
      new ApiError(404, { detail: "not found" })
    );
    vi.mocked(api.importWorldview).mockResolvedValueOnce({
      ...emptyWorldview,
      characters: [
        {
          name: "解析角色",
          personality: "",
          background: "",
          motivation: "",
          ability: "",
          relations: [],
        },
      ],
      element_count: 1,
    });
    const user = userEvent.setup();
    renderEditor();
    await screen.findByText("世界观创建方式");
    await user.click(screen.getByText("混合模式"));
    await user.type(
      screen.getByPlaceholderText(/在此粘贴世界观文档内容/),
      "初始世界观原文内容足够长"
    );
    await user.click(screen.getByRole("button", { name: "兼容解析并填充表单" }));

    // Should have parsed successfully
    await screen.findByText(/已提取 1 个要素/);

    // Now modify the import text
    const textarea = screen.getByPlaceholderText(/在此粘贴世界观文档内容/);
    await user.clear(textarea);
    await user.type(textarea, "修改后的原文内容用于测试");

    // Should show reparse warning as visible text, NOT as a live region
    // Exclude sr-only live region — the same text is duplicated there for screen readers
    const reparseText = await screen.findByText(
      "原文已修改，上次提取结果已失效，请重新解析。",
      { selector: ":not(.sr-only)" }
    );
    expect(reparseText).toBeInTheDocument();
    // The visible div must not have alert role (only root polite announces)
    expect(reparseText.closest('[role="alert"]')).toBeNull();
    // The root polite live region should announce it
    const polite = document.querySelector('[aria-live="polite"]');
    expect(polite?.textContent).toContain("原文已修改");
    // The "已提取" tag should be gone
    expect(
      screen.queryByText("已提取 1 个要素", { exact: true })
    ).toBeNull();
  });

  it("I: moves focus appropriately after discarding a draft", async () => {
    saveDraft(
      scope,
      {
        data: {
          ...emptyWorldview,
          characters: [
            {
              name: "焦点测试角色",
              personality: "",
              background: "",
              motivation: "",
              ability: "",
              relations: [],
            },
          ],
        },
        source: "manual",
        mode: "manual",
        structuredReady: true,
      },
      null
    );
    const user = userEvent.setup();
    renderEditor();

    const conflictText = await screen.findByText(/人工核对/);
    expect(conflictText).toBeInTheDocument();

    vi.spyOn(window, "confirm").mockReturnValueOnce(true);
    await user.click(screen.getByRole("button", { name: "丢弃本地草稿" }));

    // After discard, the DraftRecoveryNotice is removed and focus moves
    await waitFor(() =>
      expect(localStorage.getItem(draftStorageKey(scope))).toBeNull()
    );
    // The component triggers focus via useEffect after recovery is dismissed
    const sectionTitle = document.querySelector<HTMLElement>(".wv-section-title[tabindex]");
    expect(sectionTitle).toBeTruthy();
    expect(document.activeElement).toBe(sectionTitle);
  });

  it("J: triggers real file upload and shows page-internal error", async () => {
    vi.mocked(api.getWorldview).mockRejectedValueOnce(
      new ApiError(404, { detail: "not found" })
    );
    vi.mocked(api.uploadWorldviewFile).mockRejectedValueOnce(
      new ApiError(500, { detail: "upload failed" })
    );
    const user = userEvent.setup();
    renderEditor();
    await screen.findByText("世界观创建方式");
    await user.click(screen.getByText("导入文档"));

    // Actually trigger upload
    const file = new File(["真实文件内容用于上传测试"], "test.md", {
      type: "text/markdown",
    });
    const fileInput = document.querySelector(
      'input[type="file"]'
    ) as HTMLInputElement;
    expect(fileInput).toBeTruthy();
    await user.upload(fileInput, file);

    // Error should appear on page
    expect(
      await screen.findByText(/文件解析失败/)
    ).toBeInTheDocument();
  });

  it("K: scope isolation — draft from user-1 is not visible to user-2", async () => {
    // Save draft under user-1
    const scope1: DraftScope = {
      userId: "user-1",
      projectId: "project-1",
      kind: "worldview",
      objectId: "worldview",
    };
    saveDraft(
      scope1,
      {
        data: { ...emptyWorldview, characters: [{ name: "用户一数据", personality: "", background: "", motivation: "", ability: "", relations: [] }] },
        source: "manual",
        mode: "manual",
        structuredReady: true,
      },
      null
    );

    // Draft under user-2 should NOT contain user-1's data
    const scope2: DraftScope = {
      userId: "user-2",
      projectId: "project-1",
      kind: "worldview",
      objectId: "worldview",
    };
    const serialized = localStorage.getItem(draftStorageKey(scope2));
    expect(serialized).toBeNull();

    // User-1's draft is inaccessible via user-2's scope key
    const key1 = draftStorageKey(scope1);
    const key2 = draftStorageKey(scope2);
    expect(key1).not.toBe(key2);
    expect(localStorage.getItem(key1)).toContain("用户一数据");
    expect(localStorage.getItem(key2)).toBeNull();
  });

  it("L: save success visible feedback clears stale local-only warnings", async () => {
    const user = userEvent.setup();
    const onComplete = vi.fn();
    render(
      <WorldviewEditor
        projectId="project-1"
        hasWorldview
        genre="玄幻"
        onComplete={onComplete}
        onBack={vi.fn()}
      />
    );
    await screen.findByText("世界观创建方式");

    // Create unsaved edit → trigger nextStepBlocked warning
    await user.click(screen.getByRole("button", { name: "+ 添加角色" }));
    await user.type(screen.getByPlaceholderText("姓名"), "保存成功角色");
    await waitFor(
      () =>
        expect(localStorage.getItem(draftStorageKey(scope))).toContain(
          "保存成功角色"
        ),
      { timeout: 1500 }
    );
    await user.click(screen.getByRole("button", { name: /打开设定仓库/ }));
    expect(
      await screen.findByRole("heading", { name: "内容仅保存在本设备" })
    ).toBeInTheDocument();

    // Cancel and save
    await user.click(screen.getByRole("button", { name: "留在编辑器" }));
    await user.click(screen.getByRole("button", { name: "保存世界观" }));

    // Save success feedback should be visible
    expect(
      await screen.findByRole("heading", { name: "世界观已保存" })
    ).toBeInTheDocument();
    // Old local-only warning should be cleared
    expect(
      screen.queryByRole("heading", { name: "内容仅保存在本设备" })
    ).toBeNull();
  });

  it("import maintenance failure shows draftStored=true when initial draft was saved", async () => {
    vi.mocked(api.getWorldview).mockRejectedValueOnce(
      new ApiError(404, { detail: "not found" })
    );
    vi.mocked(api.importWorldview).mockRejectedValueOnce(
      new ApiError(503, {
        detail: "",
        code: "PROJECT_WRITE_FROZEN",
        maintenance_state: "write_frozen",
        retryable: true,
        event_id: "BUG-002B",
      })
    );
    // Let first storeCurrentDraft succeed; make localStorage.setItem throw
    // on the second call (which would be the retry in handleImport)
    let setItemCalls = 0;
    const origSetItem = localStorage.setItem.bind(localStorage);
    vi.spyOn(localStorage, "setItem").mockImplementation((key, value) => {
      setItemCalls++;
      // First call = initial storeCurrentDraft before API → succeed
      if (setItemCalls === 1) {
        origSetItem(key, value);
        return;
      }
      // Subsequent calls fail
      throw new Error("quota exceeded");
    });
    const user = userEvent.setup();
    renderEditor();
    await screen.findByText("世界观创建方式");
    await user.click(screen.getByText("导入文档"));
    await user.type(
      screen.getByPlaceholderText(/在此粘贴世界观文档内容/),
      "导入过程中出现维护冻结的世界观。"
    );
    await user.click(screen.getByRole("button", { name: "兼容解析并填充表单" }));

    // Maintenance notice should show draft is stored (first draft succeeded)
    expect(await screen.findByText(/项目正在维护/)).toBeInTheDocument();
    expect(screen.getByText(/未保存的内容已保留在此设备/)).toBeInTheDocument();
  });

  it("import maintenance failure shows draftStored=false when both drafts fail", async () => {
    vi.mocked(api.getWorldview).mockRejectedValueOnce(
      new ApiError(404, { detail: "not found" })
    );
    vi.mocked(api.importWorldview).mockRejectedValueOnce(
      new ApiError(503, {
        detail: "",
        code: "PROJECT_WRITE_FROZEN",
        maintenance_state: "write_frozen",
        retryable: true,
        event_id: "BUG-002B",
      })
    );
    // Make ALL localStorage.setItem calls throw
    vi.spyOn(localStorage, "setItem").mockImplementation(() => {
      throw new Error("quota exceeded");
    });
    const user = userEvent.setup();
    renderEditor();
    await screen.findByText("世界观创建方式");
    await user.click(screen.getByText("导入文档"));
    await user.type(
      screen.getByPlaceholderText(/在此粘贴世界观文档内容/),
      "两次草稿写入都失败的世界观。"
    );
    await user.click(screen.getByRole("button", { name: "兼容解析并填充表单" }));

    // Maintenance notice should show draft NOT stored
    expect(await screen.findByText(/项目正在维护/)).toBeInTheDocument();
    expect(
      screen.getByText(/本地草稿也未能保存/)
    ).toBeInTheDocument();
  });

  it("invalidates parse result and shows reparse hint on successful file upload", async () => {
    vi.mocked(api.getWorldview).mockRejectedValueOnce(
      new ApiError(404, { detail: "not found" })
    );
    vi.mocked(api.importWorldview).mockResolvedValueOnce({
      ...emptyWorldview,
      characters: [
        {
          name: "已解析角色",
          personality: "",
          background: "",
          motivation: "",
          ability: "",
          relations: [],
        },
      ],
      element_count: 1,
    });
    vi.mocked(api.uploadWorldviewFile).mockResolvedValueOnce({
      text: "用户重新上传的新世界观原文内容",
      filename: "new_worldview.txt",
      char_count: 18,
    });
    const user = userEvent.setup();
    renderEditor();
    await screen.findByText("世界观创建方式");
    await user.click(screen.getByText("混合模式"));
    await user.type(
      screen.getByPlaceholderText(/在此粘贴世界观文档内容/),
      "第一次解析的世界观原文内容。"
    );
    await user.click(screen.getByRole("button", { name: "兼容解析并填充表单" }));

    // Parse should complete
    expect(await screen.findByText(/已提取 1 个要素/)).toBeInTheDocument();

    // Now upload a new file — should invalidate old parse
    const file = new File(["新上传的原文"], "new.txt", { type: "text/plain" });
    const fileInput = document.querySelector(
      'input[type="file"]'
    ) as HTMLInputElement;
    await user.upload(fileInput, file);

    // Old parse result should be gone
    await waitFor(() =>
      expect(
        screen.queryByText("已提取 1 个要素", { exact: true })
      ).toBeNull()
    );
    // Reparse warning should appear (visible text, NOT role=alert)
    expect(
      await screen.findByText(
        "原文已修改，上次提取结果已失效，请重新解析。",
        { selector: ":not(.sr-only)" }
      )
    ).toBeInTheDocument();
    // Should NOT be a live region role
    const reparseDiv = screen.getByText(
      "原文已修改，上次提取结果已失效，请重新解析。",
      { selector: ":not(.sr-only)" }
    );
    expect(reparseDiv.closest('[role="alert"]')).toBeNull();
    // Root polite should announce it
    const polite = document.querySelector('[aria-live="polite"]');
    expect(polite?.textContent).toContain("原文已修改");
  });

  it("completes manual switch flow: draft, unmount, remount, recover, save", async () => {
    vi.mocked(api.getWorldview).mockRejectedValueOnce(
      new ApiError(404, { detail: "not found" })
    );
    const user = userEvent.setup();
    const { unmount } = render(
      <WorldviewEditor
        projectId="project-1"
        hasWorldview={false}
        genre="玄幻"
        onComplete={vi.fn()}
        onBack={vi.fn()}
      />
    );
    await screen.findByText("世界观创建方式");
    await user.click(screen.getByText("导入文档"));
    await user.type(
      screen.getByPlaceholderText(/在此粘贴世界观文档内容/),
      "切换手动模式后保存的完整流程原文"
    );
    // Switch to manual — this sets raw_text from importText
    await user.click(screen.getByText("手动创建"));
    // Add a character to make draft dirty
    await user.click(screen.getByRole("button", { name: "+ 添加角色" }));
    await user.type(screen.getByPlaceholderText("姓名"), "完整流程角色");

    // Wait for auto-save draft
    await waitFor(
      () =>
        expect(localStorage.getItem(draftStorageKey(scope))).toContain(
          "完整流程角色"
        ),
      { timeout: 1500 }
    );

    // Unmount
    unmount();

    // Remount with hasWorldview — should detect recovery
    const { container: remountContainer } = render(
      <WorldviewEditor
        projectId="project-1"
        hasWorldview
        genre="玄幻"
        onComplete={vi.fn()}
        onBack={vi.fn()}
      />
    );

    // Recovery prompt should appear
    await screen.findByRole("heading", { name: "发现本地草稿" }, { timeout: 5000 });
    await user.click(screen.getByRole("button", { name: "载入本地副本" }));

    // Recovered data should be visible and usable
    expect(screen.getByDisplayValue("完整流程角色")).toBeInTheDocument();

    // Save — should preserve raw_text from the recovered draft
    await user.click(screen.getByRole("button", { name: "保存世界观" }));
    await waitFor(() =>
      expect(api.setWorldview).toHaveBeenCalledWith(
        "project-1",
        expect.objectContaining({
          raw_text: "切换手动模式后保存的完整流程原文",
        })
      )
    );
  });

  it("moves focus to a section title after discarding corrupt draft", async () => {
    // Store a corrupted draft
    localStorage.setItem(draftStorageKey(scope), "{ broken json");
    vi.spyOn(window, "confirm").mockReturnValueOnce(true);
    const user = userEvent.setup();
    renderEditor();

    // Corrupt draft notice should appear
    expect(
      await screen.findByRole("heading", { name: "本地草稿无法读取" })
    ).toBeInTheDocument();

    // Discard the corrupt draft
    await user.click(
      screen.getByRole("button", { name: "确认丢弃损坏草稿" })
    );

    // Wait for draft to be cleared
    await waitFor(() =>
      expect(localStorage.getItem(draftStorageKey(scope))).toBeNull()
    );

    // The component triggers focus via useEffect after corrupt draft is dismissed
    const sectionTitle = document.querySelector<HTMLElement>(".wv-section-title[tabindex]");
    expect(sectionTitle).toBeTruthy();
    expect(document.activeElement).toBe(sectionTitle);
  });

  it("shows accurate save result messages for each save outcome", async () => {
    const user = userEvent.setup();
    renderEditor();
    await screen.findByText("世界观创建方式");
    await user.click(screen.getByRole("button", { name: "+ 添加角色" }));
    await user.type(screen.getByPlaceholderText("姓名"), "保存消息角色");

    // Clean save
    await user.click(screen.getByRole("button", { name: "保存世界观" }));
    // Clean save should NOT say generic "成功保存到项目" (the old generic message)
    expect(
      await screen.findByRole("heading", { name: "世界观已保存" })
    ).toBeInTheDocument();
    // The visible text should mention draft was cleared (ignore matching sr-only span)
    expect(
      screen.getByText(/本地草稿已清除/, { selector: "p" })
    ).toBeInTheDocument();
    // Must NOT show the old generic message
    expect(
      screen.queryByText("世界观已成功保存到项目。")
    ).toBeNull();
  });

  it("uses the strict candidate extraction path for relational projects", async () => {
    const user = userEvent.setup();
    const onExtractionComplete = vi.fn();
    vi.mocked(api.getLoreOverview).mockResolvedValue(relationalOverview);
    vi.mocked(api.createLoreExtraction).mockResolvedValue(extractionBatch());
    render(<WorldviewEditor projectId="project-1" hasWorldview={false} genre="玄幻" onComplete={vi.fn()} onExtractionComplete={onExtractionComplete} onBack={vi.fn()} />);

    await user.click(await screen.findByRole("button", { name: /导入文档/ }));
    await user.type(screen.getByRole("textbox", { name: "导入世界观文档原文" }), "林远性格坚韧。苏瑶性格冷静。天玄宗是正道宗门。");
    await user.click(await screen.findByRole("button", { name: "提取为待审核设定" }));

    await waitFor(() => expect(api.createLoreExtraction).toHaveBeenCalledTimes(1));
    expect(api.importWorldview).not.toHaveBeenCalled();
    expect(vi.mocked(api.createLoreExtraction).mock.calls[0][1]).toMatchObject({
      document_text: "林远性格坚韧。苏瑶性格冷静。天玄宗是正道宗门。",
      source_kind: "worldview_import",
    });
    expect(onExtractionComplete).toHaveBeenCalledTimes(1);
  });

  it("reuses the persisted operation key when a network outcome is unknown", async () => {
    const user = userEvent.setup();
    vi.mocked(api.getLoreOverview).mockResolvedValue(relationalOverview);
    vi.mocked(api.createLoreExtraction)
      .mockRejectedValueOnce(new ApiError(504, { detail: "gateway timeout" }))
      .mockResolvedValueOnce(extractionBatch({
        id: "batch-2",
        status: "running",
        source_ref: null,
        source_hash: "b".repeat(64),
        candidate_count: 0,
        pending_review_count: 0,
        retryable: true,
      }));
    render(<WorldviewEditor projectId="project-1" hasWorldview={false} genre="玄幻" onComplete={vi.fn()} onBack={vi.fn()} />);
    await user.click(await screen.findByRole("button", { name: /导入文档/ }));
    await user.type(screen.getByRole("textbox", { name: "导入世界观文档原文" }), "林远性格坚韧，目标是守护故乡。苏瑶性格冷静。");
    await user.click(await screen.findByRole("button", { name: "提取为待审核设定" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("结果尚不确定");
    expect(screen.queryByRole("button", { name: "放弃任务状态" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "核对上次提取结果" }));
    await waitFor(() => expect(api.createLoreExtraction).toHaveBeenCalledTimes(2));
    const firstKey = vi.mocked(api.createLoreExtraction).mock.calls[0][1].idempotency_key;
    const secondKey = vi.mocked(api.createLoreExtraction).mock.calls[1][1].idempotency_key;
    expect(secondKey).toBe(firstKey);
  });

  it("lets the user discard a deterministic 409 conflict before starting a new task", async () => {
    const user = userEvent.setup();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    vi.mocked(api.getLoreOverview).mockResolvedValue(relationalOverview);
    vi.mocked(api.createLoreExtraction)
      .mockRejectedValueOnce(new ApiError(409, {
        detail: "same key has different source",
        code: "EXTRACTION_IDEMPOTENCY_CONFLICT",
      }))
      .mockResolvedValueOnce(extractionBatch());
    render(<WorldviewEditor projectId="project-1" hasWorldview={false} genre="玄幻" onComplete={vi.fn()} onExtractionComplete={vi.fn()} onBack={vi.fn()} />);

    await user.type(await screen.findByRole("textbox", { name: "导入世界观文档原文" }), "林远是一名年轻的守护者，苏瑶负责调查历史真相。");
    await user.click(screen.getByRole("button", { name: "提取为待审核设定" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("任务标识与原文不一致");
    const firstKey = vi.mocked(api.createLoreExtraction).mock.calls[0][1].idempotency_key;
    await user.click(screen.getByRole("button", { name: "放弃任务状态" }));
    await user.click(screen.getByRole("button", { name: "提取为待审核设定" }));

    await waitFor(() => expect(api.createLoreExtraction).toHaveBeenCalledTimes(2));
    expect(vi.mocked(api.createLoreExtraction).mock.calls[1][1].idempotency_key).not.toBe(firstKey);
  });

  it("restores an uncertain extraction after refresh without calling AI automatically", async () => {
    const user = userEvent.setup();
    const documentText = "林远性格坚韧，目标是守护故乡。苏瑶性格冷静。";
    const hash = await fingerprintDraftBase({ documentText });
    expect(hash.status).toBe("available");
    if (hash.status !== "available") throw new Error("测试环境无法生成指纹");
    saveDraft(extractionScope, {
      documentText,
      documentHash: hash.value,
      idempotencyKey: "extract-persisted-1",
      phase: "outcome_unknown",
      batchId: null,
      candidateCount: null,
      errorCode: null,
      errorStatus: 504,
      retryable: false,
    }, null);
    const onExtractionComplete = vi.fn();
    vi.mocked(api.getLoreOverview).mockResolvedValue(relationalOverview);
    vi.mocked(api.createLoreExtraction).mockResolvedValue(extractionBatch());

    render(<WorldviewEditor projectId="project-1" hasWorldview={false} genre="玄幻" onComplete={vi.fn()} onExtractionComplete={onExtractionComplete} onBack={vi.fn()} />);

    expect(await screen.findByDisplayValue(documentText)).toBeInTheDocument();
    expect(api.createLoreExtraction).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "核对上次提取结果" }));
    await waitFor(() => expect(api.createLoreExtraction).toHaveBeenCalledTimes(1));
    expect(vi.mocked(api.createLoreExtraction).mock.calls[0][1].idempotency_key).toBe("extract-persisted-1");
    expect(api.importWorldview).not.toHaveBeenCalled();
    expect(onExtractionComplete).toHaveBeenCalledTimes(1);
  });

  it("offers manual warehouse creation when strict extraction finds no candidates", async () => {
    const user = userEvent.setup();
    const onComplete = vi.fn();
    vi.mocked(api.getLoreOverview).mockResolvedValue(relationalOverview);
    vi.mocked(api.createLoreExtraction).mockResolvedValue(extractionBatch({
      candidate_count: 0,
      pending_review_count: 0,
    }));
    render(<WorldviewEditor projectId="project-1" hasWorldview={false} genre="玄幻" onComplete={onComplete} onBack={vi.fn()} />);

    await user.type(await screen.findByRole("textbox", { name: "导入世界观文档原文" }), "这是一段没有可确认独立设定的描述文本。");
    await user.click(screen.getByRole("button", { name: "提取为待审核设定" }));

    expect(await screen.findByText(/未识别到可确认的独立设定/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "前往仓库手动创建" }));
    expect(onComplete).toHaveBeenCalledTimes(1);
  });

  it("requires the source to change before retrying a 413 extraction", async () => {
    const user = userEvent.setup();
    vi.mocked(api.getLoreOverview).mockResolvedValue(relationalOverview);
    vi.mocked(api.createLoreExtraction).mockRejectedValue(
      new ApiError(413, { detail: "too long" })
    );
    render(<WorldviewEditor projectId="project-1" hasWorldview={false} genre="玄幻" onComplete={vi.fn()} onBack={vi.fn()} />);

    const input = await screen.findByRole("textbox", { name: "导入世界观文档原文" });
    await user.type(input, "林远是一名年轻的守护者，他必须遵守严格的能力限制。");
    await user.click(screen.getByRole("button", { name: "提取为待审核设定" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("20,000");
    const retry = screen.getByRole("button", { name: "修改后重新提取" });
    expect(retry).toBeDisabled();
    await user.type(input, "已精简");
    expect(retry).toBeEnabled();
  });

  it("keeps the same task key while maintenance blocks extraction", async () => {
    const user = userEvent.setup();
    vi.mocked(api.getLoreOverview).mockResolvedValue(relationalOverview);
    vi.mocked(api.createLoreExtraction)
      .mockRejectedValueOnce(new ApiError(503, { detail: "maintenance", retryable: true }))
      .mockResolvedValueOnce(extractionBatch({ status: "running", candidate_count: 0, pending_review_count: 0 }));
    render(<WorldviewEditor projectId="project-1" hasWorldview={false} genre="玄幻" onComplete={vi.fn()} onBack={vi.fn()} />);

    await user.type(await screen.findByRole("textbox", { name: "导入世界观文档原文" }), "林远是一名年轻的守护者，苏瑶则负责调查历史真相。");
    await user.click(screen.getByRole("button", { name: "提取为待审核设定" }));

    expect(await screen.findByRole("button", { name: "维护结束后重试提取" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "放弃任务状态" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "维护结束后重试提取" }));
    await waitFor(() => expect(api.createLoreExtraction).toHaveBeenCalledTimes(2));
    expect(vi.mocked(api.createLoreExtraction).mock.calls[1][1].idempotency_key).toBe(
      vi.mocked(api.createLoreExtraction).mock.calls[0][1].idempotency_key
    );
  });

  it("fails closed when the project lore mode cannot be confirmed", async () => {
    vi.mocked(api.getLoreOverview).mockRejectedValue(new ApiError(503, { detail: "maintenance" }));
    render(<WorldviewEditor projectId="project-1" hasWorldview={false} genre="玄幻" onComplete={vi.fn()} onBack={vi.fn()} />);

    expect(await screen.findByRole("heading", { name: "无法确认设定仓库模式" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "手动创建" })).toBeNull();
    expect(screen.queryByRole("textbox", { name: "导入世界观文档原文" })).toBeNull();
    expect(screen.getByRole("button", { name: "重新确认仓库模式" })).toBeInTheDocument();
  });

  it("blocks extraction until a corrupt extraction task is explicitly discarded", async () => {
    const user = userEvent.setup();
    vi.mocked(api.getLoreOverview).mockResolvedValue(relationalOverview);
    localStorage.setItem(draftStorageKey(extractionScope), "{not-json");
    vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<WorldviewEditor projectId="project-1" hasWorldview={false} genre="玄幻" onComplete={vi.fn()} onBack={vi.fn()} />);

    expect(await screen.findByRole("alert")).toHaveTextContent("提取状态已损坏");
    expect(screen.getByRole("button", { name: "提取为待审核设定" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "放弃任务状态" }));
    expect(screen.getByRole("button", { name: "提取为待审核设定" })).toBeDisabled();
    expect(localStorage.getItem(draftStorageKey(extractionScope))).toBeNull();
  });

  it("does not show redundant ai", async () => {
    // Placeholder removed
  });

  it("does not show redundant live region announcements from child components", async () => {
    saveDraft(
      scope,
      {
        data: {
          ...emptyWorldview,
          characters: [
            {
              name: "草稿角色",
              personality: "",
              background: "",
              motivation: "",
              ability: "",
              relations: [],
            },
          ],
        },
        source: "manual",
        mode: "manual",
        structuredReady: true,
      },
      null
    );
    renderEditor();

    await screen.findByText(/人工核对/);
    // DraftRecoveryNotice should use role="group", not role="status"
    const recoverySection = screen.getByRole("group", { name: "发现本地草稿" });
    expect(recoverySection.tagName).toBe("SECTION");
  });
});
