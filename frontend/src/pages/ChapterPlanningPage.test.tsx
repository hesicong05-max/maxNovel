import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useNavigate } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as apiModule from "@/services/api";
import { ApiError } from "@/services/api";
import type { NovelPlan } from "@/types/planning";
import ChapterPlanningPage from "./ChapterPlanningPage";
import { savePendingPlanningOperation } from "@/services/planningOperations";

vi.mock("@/components/AuthContext", () => ({
  useAuth: () => ({ user: { id: "user-1" } }),
}));

const plan: NovelPlan = {
  id: "plan-1", project_id: "project-1", status: "active", structure_version: 3,
  assignment_version: 1, created_at: "2026-08-10T00:00:00Z", updated_at: "2026-08-10T00:00:00Z",
  parts: [
    {
      id: "part-1", project_id: "project-1", plan_id: "plan-1", title: "第一篇", description: "",
      position: 0, status: "active", lock_version: 1, created_at: "", updated_at: "",
      chapters: [
        { id: "chapter-1", project_id: "project-1", plan_id: "plan-1", part_id: "part-1", title: "第一章", summary: "", target_word_count: null, position: 0, status: "active", lock_version: 1, created_at: "", updated_at: "" },
        { id: "chapter-2", project_id: "project-1", plan_id: "plan-1", part_id: "part-1", title: "第二章", summary: "", target_word_count: null, position: 1, status: "active", lock_version: 1, created_at: "", updated_at: "" },
      ],
    },
    {
      id: "part-2", project_id: "project-1", plan_id: "plan-1", title: "第二篇", description: "",
      position: 1, status: "active", lock_version: 1, created_at: "", updated_at: "", chapters: [],
    },
  ],
};

function ProjectSwitcher() {
  const navigate = useNavigate();
  return <button onClick={() => navigate("/project/project-2/plan/chapters")}>切换项目</button>;
}

function renderPage(overrides: Record<string, unknown> = {}) {
  const mocked = {
    ...apiModule.api,
    getPlanning: vi.fn().mockResolvedValue(plan),
    getPlanningOperation: vi.fn().mockRejectedValue(new ApiError(404, { detail: "not found" })),
    reorderPlanningStructure: vi.fn().mockResolvedValue({ receipt_kind: "structure" }),
    ...overrides,
  };
  vi.spyOn(apiModule, "api", "get").mockReturnValue(mocked as typeof apiModule.api);
  render(
    <MemoryRouter initialEntries={["/project/project-1/plan/chapters"]}>
      <Routes>
        <Route path="/project/:id/plan/chapters" element={<ChapterPlanningPage />} />
        <Route path="/project/:id/lore" element={<div>设定仓库</div>} />
      </Routes>
    </MemoryRouter>
  );
  return mocked;
}

describe("ChapterPlanningPage", () => {
  beforeEach(() => { vi.restoreAllMocks(); sessionStorage.clear(); });

  it("shows explicit initialization and never creates planning automatically", async () => {
    const getPlanning = vi.fn().mockRejectedValue(new ApiError(404, { detail: "章节规划尚未创建。", code: "PLANNING_NOT_INITIALIZED" }));
    const initializePlanning = vi.fn().mockResolvedValue({ ...plan, parts: [] });
    renderPage({ getPlanning, initializePlanning });
    expect(await screen.findByRole("heading", { name: "创建空白章节规划" })).toBeInTheDocument();
    expect(initializePlanning).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "创建章节规划" }));
    await waitFor(() => expect(initializePlanning).toHaveBeenCalledWith("project-1"));
    expect(await screen.findByText("还没有篇章，请先新建第一个篇章。")).toBeInTheDocument();
  });

  it("submits a complete active structure when moving a chapter", async () => {
    const mocked = renderPage();
    expect(await screen.findByRole("heading", { name: "章节规划" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "下移章节 第一章" }));
    await waitFor(() => expect(mocked.reorderPlanningStructure).toHaveBeenCalled());
    expect(mocked.reorderPlanningStructure).toHaveBeenCalledWith("project-1", expect.objectContaining({
      expected_structure_version: 3,
      parts: [
        { part_id: "part-1", chapter_ids: ["chapter-2", "chapter-1"] },
        { part_id: "part-2", chapter_ids: [] },
      ],
    }));
    expect(mocked.getPlanning).toHaveBeenCalledTimes(2);
  });

  it("routes relational migration requirements to the lore repository", async () => {
    renderPage({ getPlanning: vi.fn().mockRejectedValue(new ApiError(409, { detail: "请先升级设定仓库。", code: "PLANNING_LORE_MIGRATION_REQUIRED" })) });
    expect(await screen.findByRole("heading", { name: "请先升级设定仓库" })).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: "打开设定仓库" })[1]).toHaveAttribute("href", "/project/project-1/lore?migration=preview");
  });

  it("blocks new writes when a confirmed mutation cannot refresh the authoritative plan", async () => {
    const getPlanning = vi.fn()
      .mockResolvedValueOnce(plan)
      .mockRejectedValueOnce(new ApiError(500, { detail: "刷新失败" }));
    const createPlanningPart = vi.fn().mockResolvedValue({
      receipt_kind: "structure",
      affected_node: { id: "part-new" },
    });
    renderPage({ getPlanning, createPlanningPart });
    await screen.findByText("第一篇");
    await userEvent.click(screen.getByRole("button", { name: "新建篇章" }));
    await userEvent.type(screen.getByLabelText("篇章名称"), "第三篇");
    await userEvent.click(screen.getByRole("button", { name: "创建" }));
    expect(await screen.findByText(/操作已成功，但最新规划暂时无法读取/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "新建篇章" })).toBeDisabled();
    expect(createPlanningPart).toHaveBeenCalledTimes(1);
  });

  it("enters maintenance state on an initial 503 instead of treating it as missing", async () => {
    renderPage({ getPlanning: vi.fn().mockRejectedValue(new ApiError(503, {
      detail: "项目资料正在维护。",
      code: "PROJECT_WRITE_FROZEN",
      recommended_action: "retry_later",
    })) });
    expect(await screen.findByText("项目资料正在维护。", { selector: "span" })).toBeInTheDocument();
    expect(screen.getByText(/已保留当前只读内容并暂停写入/)).toBeInTheDocument();
    expect(screen.queryByText("项目不存在")).not.toBeInTheDocument();
  });

  it("retries an unknown result with the exact stored key and payload", async () => {
    const payload = {
      operation_key: "planning:part_create:12345678",
      expected_structure_version: 3,
      title: "第三篇",
      description: "",
    };
    savePendingPlanningOperation({
      schema_version: 1,
      user_id: "user-1",
      project_id: "project-1",
      operation_key: payload.operation_key,
      action: "part_create",
      target_id: null,
      payload,
      created_at: "2026-08-10T00:00:00Z",
    });
    const createPlanningPart = vi.fn().mockResolvedValue({ receipt_kind: "structure" });
    renderPage({ createPlanningPart });
    const retry = await screen.findByRole("button", { name: "使用原操作编号安全重试" });
    await userEvent.click(retry);
    await waitFor(() => expect(createPlanningPart).toHaveBeenCalledWith("project-1", payload));
  });

  it("keeps historical planning projects on the safe compatibility exit", async () => {
    renderPage({ getPlanning: vi.fn().mockRejectedValue(new ApiError(409, {
      detail: "检测到历史章节资料。",
      code: "PLANNING_LEGACY_IMPORT_REQUIRED",
    })) });
    expect(await screen.findByRole("heading", { name: "检测到历史章节资料" })).toBeInTheDocument();
    expect(screen.getByText(/不会自动迁移或覆盖旧大纲/)).toBeInTheDocument();
  });

  it("clears the old pending operation when switching projects", async () => {
    const payload = {
      operation_key: "planning:part_create:project-one",
      expected_structure_version: 3,
      title: "旧项目篇章",
      description: "",
    };
    savePendingPlanningOperation({
      schema_version: 1,
      user_id: "user-1",
      project_id: "project-1",
      operation_key: payload.operation_key,
      action: "part_create",
      target_id: null,
      payload,
      created_at: "2026-08-10T00:00:00Z",
    });
    let resolveReceipt: (() => void) | undefined;
    const receipt = new Promise<void>((resolve) => { resolveReceipt = resolve; });
    const projectTwo = {
      ...plan,
      project_id: "project-2",
      parts: [{ ...plan.parts[0], project_id: "project-2", title: "新项目篇章" }],
    };
    const mocked = {
      ...apiModule.api,
      getPlanning: vi.fn((projectId: string) => Promise.resolve(projectId === "project-1" ? plan : projectTwo)),
      getPlanningOperation: vi.fn(() => receipt),
    };
    vi.spyOn(apiModule, "api", "get").mockReturnValue(mocked as unknown as typeof apiModule.api);
    render(
      <MemoryRouter initialEntries={["/project/project-1/plan/chapters"]}>
        <ProjectSwitcher />
        <Routes><Route path="/project/:id/plan/chapters" element={<ChapterPlanningPage />} /></Routes>
      </MemoryRouter>
    );
    expect(await screen.findByRole("button", { name: "使用原操作编号安全重试" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "切换项目" }));
    expect(await screen.findByText("新项目篇章")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "使用原操作编号安全重试" })).not.toBeInTheDocument();
    resolveReceipt?.();
    await waitFor(() => expect(screen.getByText("新项目篇章")).toBeInTheDocument());
  });

  it("keeps version-conflict choices visible until the user explicitly resolves them", async () => {
    const latestPlan = {
      ...plan,
      structure_version: 4,
      parts: [{ ...plan.parts[0], lock_version: 2, title: "服务器新标题" }, plan.parts[1]],
    };
    const getPlanning = vi.fn().mockResolvedValueOnce(plan).mockResolvedValueOnce(latestPlan);
    const updatePlanningPart = vi.fn().mockRejectedValue(new ApiError(409, {
      detail: "篇章已被其他操作更新。",
      code: "PLANNING_NODE_VERSION_CONFLICT",
      recommended_action: "review_current_node",
    }));
    renderPage({ getPlanning, updatePlanningPart });
    await userEvent.click(await screen.findByRole("button", { name: "第一篇" }));
    const title = screen.getByLabelText("篇章名称");
    await userEvent.clear(title);
    await userEvent.type(title, "我的草稿标题");
    await userEvent.click(screen.getByRole("button", { name: "保存篇章" }));
    expect(await screen.findByRole("button", { name: "载入服务器最新值" })).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveFocus();
    expect(screen.getByRole("button", { name: "保留草稿并继续核对" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "保存篇章" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "保留草稿并继续核对" }));
    expect(screen.getByLabelText("篇章名称")).toHaveValue("我的草稿标题");
    expect(screen.getByRole("button", { name: "保存篇章" })).toBeEnabled();
  });

  it("never unlocks a version conflict when the authoritative refresh fails", async () => {
    const getPlanning = vi.fn()
      .mockResolvedValueOnce(plan)
      .mockRejectedValueOnce(new ApiError(500, { detail: "刷新失败" }));
    const updatePlanningPart = vi.fn().mockRejectedValue(new ApiError(409, {
      detail: "篇章已被其他操作更新。",
      code: "PLANNING_NODE_VERSION_CONFLICT",
      recommended_action: "review_current_node",
    }));
    renderPage({ getPlanning, updatePlanningPart });
    await userEvent.click(await screen.findByRole("button", { name: "第一篇" }));
    await userEvent.click(screen.getByRole("button", { name: "保存篇章" }));
    expect(await screen.findByText(/最新规划读取失败；已保持禁写/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "载入服务器最新值" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "保存篇章" })).toBeDisabled();
  });

  it("moves focus into the detail and keeps it there after a detail write", async () => {
    const updatePlanningPart = vi.fn().mockResolvedValue({
      receipt_kind: "structure",
      affected_node: { id: "part-1" },
    });
    renderPage({ updatePlanningPart });
    await userEvent.click(await screen.findByRole("button", { name: "整部小说" }));
    await waitFor(() => expect(screen.getByRole("heading", { name: "整部小说" })).toHaveFocus());
    await userEvent.click(screen.getByRole("button", { name: "第一篇" }));
    await userEvent.click(screen.getByRole("button", { name: "保存篇章" }));
    await waitFor(() => expect(screen.getByRole("heading", { name: "第一篇" })).toHaveFocus());
  });

  it("ignores an old project's delayed authoritative refresh after switching projects", async () => {
    let resolveOldRefresh: ((value: NovelPlan) => void) | undefined;
    const oldRefresh = new Promise<NovelPlan>((resolve) => { resolveOldRefresh = resolve; });
    let projectOneReads = 0;
    const projectTwo = {
      ...plan,
      project_id: "project-2",
      parts: [{ ...plan.parts[0], project_id: "project-2", title: "新项目篇章" }],
    };
    const getPlanning = vi.fn((projectId: string) => {
      if (projectId === "project-2") return Promise.resolve(projectTwo);
      projectOneReads += 1;
      return projectOneReads === 1 ? Promise.resolve(plan) : oldRefresh;
    });
    const mocked = {
      ...apiModule.api,
      getPlanning,
      getPlanningOperation: vi.fn().mockRejectedValue(new ApiError(404, { detail: "not found" })),
      createPlanningPart: vi.fn().mockResolvedValue({ receipt_kind: "structure", affected_node: { id: "part-new" } }),
    };
    vi.spyOn(apiModule, "api", "get").mockReturnValue(mocked as unknown as typeof apiModule.api);
    render(
      <MemoryRouter initialEntries={["/project/project-1/plan/chapters"]}>
        <ProjectSwitcher />
        <Routes><Route path="/project/:id/plan/chapters" element={<ChapterPlanningPage />} /></Routes>
      </MemoryRouter>
    );
    await screen.findByText("第一篇");
    await userEvent.click(screen.getByRole("button", { name: "新建篇章" }));
    await userEvent.type(screen.getByLabelText("篇章名称"), "旧项目新篇");
    await userEvent.click(screen.getByRole("button", { name: "创建" }));
    await waitFor(() => expect(projectOneReads).toBe(2));
    await userEvent.click(screen.getByRole("button", { name: "切换项目" }));
    expect(await screen.findByText("新项目篇章")).toBeInTheDocument();
    resolveOldRefresh?.(plan);
    await waitFor(() => expect(screen.getByText("新项目篇章")).toBeInTheDocument());
    expect(screen.queryByText(/已暂停新的写入/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "新建篇章" })).toBeEnabled();
  });
});
