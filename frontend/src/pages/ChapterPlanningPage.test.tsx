import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
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

const emptyAssignments = {
  scope: { scope_type: "novel" as const, scope_target_id: "project-1", title: "整部小说", status: "active" as const, part_id: null },
  assignment_version: 1,
  direct_assignments: [],
  effective_elements: [],
  counts: { direct: 0, direct_active: 0, direct_removed: 0, effective: 0, generation_eligible: 0, ineligible: 0 },
};

function ProjectSwitcher() {
  const navigate = useNavigate();
  return <button onClick={() => navigate("/project/project-2/plan/chapters")}>切换项目</button>;
}

function canonicalJson(value: unknown): string {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  const record = value as Record<string, unknown>;
  return `{${Object.keys(record).sort().map((key) => `${JSON.stringify(key)}:${canonicalJson(record[key])}`).join(",")}}`;
}

async function digest(value: unknown): Promise<string> {
  const result = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(canonicalJson(value)));
  return Array.from(new Uint8Array(result), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

async function reorderReceipt(body: { operation_key: string; expected_structure_version: number; parts: Array<{ part_id: string; chapter_ids: string[] }> }) {
  return {
    receipt_kind: "structure",
    receipt_id: "receipt-1",
    operation_key: body.operation_key,
    operation_type: "structure_reorder",
    replayed: false,
    changed: true,
    project_id: "project-1",
    plan_id: "plan-1",
    previous_structure_version: body.expected_structure_version,
    new_structure_version: body.expected_structure_version + 1,
    affected_node: null,
    placement: null,
    structure: {
      digest: await digest(body.parts),
      part_count: body.parts.length,
      chapter_count: body.parts.reduce((total, part) => total + part.chapter_ids.length, 0),
      changed_part_count: 0,
      changed_chapter_count: 2,
    },
    created_at: "2026-08-17T00:00:00Z",
  };
}

function renderPage(overrides: Record<string, unknown> = {}) {
  const mocked = {
    ...apiModule.api,
    getPlanning: vi.fn().mockResolvedValue(plan),
    getPlanningLoreAssignments: vi.fn().mockImplementation((_projectId: string, scopeType: "novel" | "part" | "chapter", scopeTargetId: string) => Promise.resolve({
      ...emptyAssignments,
      scope: { ...emptyAssignments.scope, scope_type: scopeType, scope_target_id: scopeTargetId, title: scopeType === "novel" ? "整部小说" : scopeTargetId },
    })),
    getPlanningOperation: vi.fn().mockRejectedValue(new ApiError(404, { detail: "not found" })),
    reorderPlanningStructure: vi.fn().mockImplementation((_projectId: string, body: Parameters<typeof reorderReceipt>[0]) => reorderReceipt(body)),
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

  it("keeps status semantics and supporting context around the visual workspace", async () => {
    renderPage();

    await screen.findByRole("navigation", { name: "篇章与章节结构" });
    const workspace = document.querySelector(".planning-workspace--studio");
    const support = document.querySelector(".planning-page__support");
    const statusStack = document.querySelector(".planning-status-stack");
    const detailRegion = screen.getByRole("region", { name: "章节规划详情" });

    expect(workspace).toBeInTheDocument();
    expect(detailRegion).toHaveClass("planning-workspace__detail");
    expect(detailRegion.querySelector("h2")).toHaveTextContent("整部小说");
    expect(document.querySelector(".planning-page main")).not.toBeInTheDocument();
    expect(statusStack).toContainElement(document.querySelector(".planning-live"));
    expect(workspace?.compareDocumentPosition(support as Node) ?? 0).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING
    );
  });

  it("keeps one named detail region while the plan is loading", async () => {
    let resolvePlan: ((value: NovelPlan) => void) | undefined;
    const pendingPlan = new Promise<NovelPlan>((resolve) => { resolvePlan = resolve; });
    renderPage({ getPlanning: vi.fn(() => pendingPlan) });

    expect(screen.getByRole("region", { name: "章节规划详情" })).toHaveTextContent("正在加载章节规划…");
    expect(document.querySelector(".planning-page main")).not.toBeInTheDocument();
    await act(async () => { resolvePlan?.(plan); });
    expect(await screen.findByRole("navigation", { name: "篇章与章节结构" })).toBeInTheDocument();
  });

  it("shows explicit initialization and never creates planning automatically", async () => {
    const getPlanning = vi.fn().mockRejectedValue(new ApiError(404, { detail: "章节规划尚未创建。", code: "PLANNING_NOT_INITIALIZED" }));
    const initializePlanning = vi.fn().mockResolvedValue({ ...plan, parts: [] });
    renderPage({ getPlanning, initializePlanning });
    expect(await screen.findByRole("heading", { name: "创建空白章节规划" })).toBeInTheDocument();
    expect(screen.getAllByRole("region", { name: "章节规划详情" })).toHaveLength(1);
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

  it("submits one existing structure reorder after a valid chapter drop", async () => {
    const mocked = renderPage();
    await screen.findByRole("heading", { name: "章节规划" });
    const source = screen.getByTestId("chapter-drag-handle-chapter-1");
    const target = screen.getByRole("button", { name: "第二章" }).closest("li")!;
    vi.spyOn(target, "getBoundingClientRect").mockReturnValue({
      top: 0, bottom: 100, left: 0, right: 200, width: 200, height: 100, x: 0, y: 0, toJSON: () => ({}),
    });
    const dataTransfer = { effectAllowed: "none", dropEffect: "none", setData: vi.fn() };
    fireEvent.dragStart(source, { dataTransfer });
    fireEvent.dragOver(target, { dataTransfer, clientY: 75 });
    fireEvent.drop(target, { dataTransfer, clientY: 75 });

    await waitFor(() => expect(mocked.reorderPlanningStructure).toHaveBeenCalledTimes(1));
    expect(mocked.reorderPlanningStructure).toHaveBeenCalledWith("project-1", expect.objectContaining({
      expected_structure_version: 3,
      parts: [
        { part_id: "part-1", chapter_ids: ["chapter-2", "chapter-1"] },
        { part_id: "part-2", chapter_ids: [] },
      ],
    }));
    await waitFor(() => expect(mocked.getPlanning).toHaveBeenCalledTimes(2));
  });

  it("keeps a mismatched direct reorder receipt and freezes further writes", async () => {
    const reorderPlanningStructure = vi.fn().mockImplementation(async (_projectId: string, body: Parameters<typeof reorderReceipt>[0]) => ({
      ...await reorderReceipt(body),
      structure: { ...((await reorderReceipt(body)).structure), digest: "0".repeat(64) },
    }));
    renderPage({ reorderPlanningStructure });
    await screen.findByRole("heading", { name: "章节规划" });
    await userEvent.click(screen.getByRole("button", { name: "下移章节 第一章" }));

    expect(await screen.findByText(/排序收据与本地全量结构不一致/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "新建篇章" })).toBeDisabled();
    expect(sessionStorage.getItem("novel_pending_planning_operation_v1:user-1:project-1")).toContain("structure_reorder");
    expect(reorderPlanningStructure).toHaveBeenCalledTimes(1);
  });

  it("checks an unknown reorder by key before allowing one explicit original retry", async () => {
    const payload = {
      operation_key: "planning:structure_reorder:pending123",
      expected_structure_version: 3,
      parts: [
        { part_id: "part-1", chapter_ids: ["chapter-2", "chapter-1"] },
        { part_id: "part-2", chapter_ids: [] },
      ],
    };
    savePendingPlanningOperation({
      schema_version: 1,
      user_id: "user-1",
      project_id: "project-1",
      operation_key: payload.operation_key,
      action: "structure_reorder",
      target_id: "chapter-1",
      payload,
      created_at: "2026-08-17T00:00:00Z",
    });
    const getPlanningOperation = vi.fn().mockRejectedValue(new ApiError(404, {
      detail: "未找到该操作结果。",
      code: "PLANNING_OPERATION_NOT_FOUND",
      recommended_action: "retry_original_request",
    }));
    const reorderPlanningStructure = vi.fn().mockImplementation((_projectId: string, body: Parameters<typeof reorderReceipt>[0]) => reorderReceipt(body));
    renderPage({ getPlanningOperation, reorderPlanningStructure });

    const retry = await screen.findByRole("button", { name: "使用原编号与全量结构重试" });
    expect(getPlanningOperation).toHaveBeenCalledTimes(1);
    expect(reorderPlanningStructure).not.toHaveBeenCalled();
    await userEvent.click(retry);
    await waitFor(() => expect(reorderPlanningStructure).toHaveBeenCalledTimes(1));
    expect(reorderPlanningStructure).toHaveBeenCalledWith("project-1", payload);
  });

  it("fails closed when a recovered reorder receipt has the wrong digest", async () => {
    const payload = {
      operation_key: "planning:structure_reorder:pending124",
      expected_structure_version: 3,
      parts: [
        { part_id: "part-1", chapter_ids: ["chapter-2", "chapter-1"] },
        { part_id: "part-2", chapter_ids: [] },
      ],
    };
    savePendingPlanningOperation({
      schema_version: 1,
      user_id: "user-1",
      project_id: "project-1",
      operation_key: payload.operation_key,
      action: "structure_reorder",
      target_id: "chapter-1",
      payload,
      created_at: "2026-08-17T00:00:00Z",
    });
    const receipt = await reorderReceipt(payload);
    const getPlanningOperation = vi.fn().mockResolvedValue({
      ...receipt,
      structure: { ...receipt.structure, chapter_count: 99 },
    });
    const reorderPlanningStructure = vi.fn();
    renderPage({ getPlanningOperation, reorderPlanningStructure });

    expect(await screen.findByText(/排序收据与本地全量结构不一致/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "使用原编号与全量结构重试" })).not.toBeInTheDocument();
    expect(reorderPlanningStructure).not.toHaveBeenCalled();
    expect(sessionStorage.getItem("novel_pending_planning_operation_v1:user-1:project-1")).toContain(payload.operation_key);
  });

  it("ignores a direct reorder receipt that arrives after switching projects", async () => {
    let resolveReorder: ((value: Awaited<ReturnType<typeof reorderReceipt>>) => void) | undefined;
    let submittedBody: Parameters<typeof reorderReceipt>[0] | undefined;
    const delayedReorder = new Promise<Awaited<ReturnType<typeof reorderReceipt>>>((resolve) => { resolveReorder = resolve; });
    const projectTwo = {
      ...plan,
      id: "plan-2",
      project_id: "project-2",
      parts: [{ ...plan.parts[0], project_id: "project-2", plan_id: "plan-2", title: "新项目篇章" }],
    };
    const mocked = {
      ...apiModule.api,
      getPlanning: vi.fn((projectId: string) => Promise.resolve(projectId === "project-1" ? plan : projectTwo)),
      getPlanningLoreAssignments: vi.fn().mockResolvedValue(emptyAssignments),
      getPlanningOperation: vi.fn().mockRejectedValue(new ApiError(404, { detail: "not found" })),
      reorderPlanningStructure: vi.fn((_projectId: string, body: Parameters<typeof reorderReceipt>[0]) => {
        submittedBody = body;
        return delayedReorder;
      }),
    };
    vi.spyOn(apiModule, "api", "get").mockReturnValue(mocked as unknown as typeof apiModule.api);
    render(
      <MemoryRouter initialEntries={["/project/project-1/plan/chapters"]}>
        <ProjectSwitcher />
        <Routes><Route path="/project/:id/plan/chapters" element={<ChapterPlanningPage />} /></Routes>
      </MemoryRouter>
    );

    await screen.findByText("第一篇");
    await userEvent.click(screen.getByRole("button", { name: "下移章节 第一章" }));
    await waitFor(() => expect(mocked.reorderPlanningStructure).toHaveBeenCalledTimes(1));
    await userEvent.click(screen.getByRole("button", { name: "切换项目" }));
    expect(await screen.findByText("新项目篇章")).toBeInTheDocument();
    resolveReorder?.(await reorderReceipt(submittedBody!));

    await waitFor(() => expect(screen.getByText("新项目篇章")).toBeInTheDocument());
    expect(screen.queryByText(/排序收据与本地全量结构不一致/)).not.toBeInTheDocument();
    expect(sessionStorage.getItem("novel_pending_planning_operation_v1:user-1:project-2")).toBeNull();
    expect(sessionStorage.getItem("novel_pending_planning_operation_v1:user-1:project-1")).toContain("structure_reorder");
  });

  it("ignores an original-key reorder receipt that arrives after switching projects", async () => {
    const payload = {
      operation_key: "planning:structure_reorder:late-retry",
      expected_structure_version: 3,
      parts: [
        { part_id: "part-1", chapter_ids: ["chapter-2", "chapter-1"] },
        { part_id: "part-2", chapter_ids: [] },
      ],
    };
    savePendingPlanningOperation({
      schema_version: 1,
      user_id: "user-1",
      project_id: "project-1",
      operation_key: payload.operation_key,
      action: "structure_reorder",
      target_id: "chapter-1",
      payload,
      created_at: "2026-08-17T00:00:00Z",
    });
    let resolveRetry: ((value: Awaited<ReturnType<typeof reorderReceipt>>) => void) | undefined;
    const delayedRetry = new Promise<Awaited<ReturnType<typeof reorderReceipt>>>((resolve) => { resolveRetry = resolve; });
    const projectTwo = {
      ...plan,
      id: "plan-2",
      project_id: "project-2",
      parts: [{ ...plan.parts[0], project_id: "project-2", plan_id: "plan-2", title: "新项目篇章" }],
    };
    const mocked = {
      ...apiModule.api,
      getPlanning: vi.fn((projectId: string) => Promise.resolve(projectId === "project-1" ? plan : projectTwo)),
      getPlanningLoreAssignments: vi.fn().mockResolvedValue(emptyAssignments),
      getPlanningOperation: vi.fn().mockRejectedValue(new ApiError(404, {
        detail: "未找到该操作结果。",
        code: "PLANNING_OPERATION_NOT_FOUND",
        recommended_action: "retry_original_request",
      })),
      reorderPlanningStructure: vi.fn(() => delayedRetry),
    };
    vi.spyOn(apiModule, "api", "get").mockReturnValue(mocked as unknown as typeof apiModule.api);
    render(
      <MemoryRouter initialEntries={["/project/project-1/plan/chapters"]}>
        <ProjectSwitcher />
        <Routes><Route path="/project/:id/plan/chapters" element={<ChapterPlanningPage />} /></Routes>
      </MemoryRouter>
    );

    await userEvent.click(await screen.findByRole("button", { name: "使用原编号与全量结构重试" }));
    await waitFor(() => expect(mocked.reorderPlanningStructure).toHaveBeenCalledTimes(1));
    await userEvent.click(screen.getByRole("button", { name: "切换项目" }));
    expect(await screen.findByText("新项目篇章")).toBeInTheDocument();
    resolveRetry?.(await reorderReceipt(payload));

    await waitFor(() => expect(screen.getByText("新项目篇章")).toBeInTheDocument());
    expect(screen.queryByText(/排序收据与本地全量结构不一致/)).not.toBeInTheDocument();
    expect(sessionStorage.getItem("novel_pending_planning_operation_v1:user-1:project-2")).toBeNull();
    expect(sessionStorage.getItem("novel_pending_planning_operation_v1:user-1:project-1")).toContain(payload.operation_key);
  });

  it("ignores a recovered reorder receipt when the project changes during digest verification", async () => {
    const payload = {
      operation_key: "planning:structure_reorder:digest-switch",
      expected_structure_version: 3,
      parts: [
        { part_id: "part-1", chapter_ids: ["chapter-2", "chapter-1"] },
        { part_id: "part-2", chapter_ids: [] },
      ],
    };
    savePendingPlanningOperation({
      schema_version: 1,
      user_id: "user-1",
      project_id: "project-1",
      operation_key: payload.operation_key,
      action: "structure_reorder",
      target_id: "chapter-1",
      payload,
      created_at: "2026-08-17T00:00:00Z",
    });
    const receipt = await reorderReceipt(payload);
    const originalDigest = crypto.subtle.digest.bind(crypto.subtle);
    let releaseDigest: (() => void) | undefined;
    const digestGate = new Promise<void>((resolve) => { releaseDigest = resolve; });
    const digestSpy = vi.spyOn(crypto.subtle, "digest").mockImplementation(async (algorithm, data) => {
      await digestGate;
      return originalDigest(algorithm, data);
    });
    const projectTwo = {
      ...plan,
      id: "plan-2",
      project_id: "project-2",
      parts: [{ ...plan.parts[0], project_id: "project-2", plan_id: "plan-2", title: "新项目篇章" }],
    };
    const mocked = {
      ...apiModule.api,
      getPlanning: vi.fn((projectId: string) => Promise.resolve(projectId === "project-1" ? plan : projectTwo)),
      getPlanningLoreAssignments: vi.fn().mockResolvedValue(emptyAssignments),
      getPlanningOperation: vi.fn().mockResolvedValue(receipt),
    };
    vi.spyOn(apiModule, "api", "get").mockReturnValue(mocked as unknown as typeof apiModule.api);
    render(
      <MemoryRouter initialEntries={["/project/project-1/plan/chapters"]}>
        <ProjectSwitcher />
        <Routes><Route path="/project/:id/plan/chapters" element={<ChapterPlanningPage />} /></Routes>
      </MemoryRouter>
    );

    await waitFor(() => expect(digestSpy).toHaveBeenCalledTimes(1));
    await userEvent.click(screen.getByRole("button", { name: "切换项目" }));
    expect(await screen.findByText("新项目篇章")).toBeInTheDocument();
    releaseDigest?.();

    await waitFor(() => expect(screen.getByText("新项目篇章")).toBeInTheDocument());
    expect(screen.queryByText(/排序收据与本地全量结构不一致/)).not.toBeInTheDocument();
    expect(sessionStorage.getItem("novel_pending_planning_operation_v1:user-1:project-2")).toBeNull();
    expect(sessionStorage.getItem("novel_pending_planning_operation_v1:user-1:project-1")).toContain(payload.operation_key);
  });

  it("routes relational migration requirements to the lore repository", async () => {
    renderPage({ getPlanning: vi.fn().mockRejectedValue(new ApiError(409, { detail: "请先升级设定仓库。", code: "PLANNING_LORE_MIGRATION_REQUIRED" })) });
    expect(await screen.findByRole("heading", { name: "请先升级设定仓库" })).toBeInTheDocument();
    expect(screen.getAllByRole("region", { name: "章节规划详情" })).toHaveLength(1);
    expect(screen.getAllByRole("link", { name: "打开设定仓库" })[1]).toHaveAttribute("href", "/project/project-1/lore?migration=preview");
  });

  it("keeps the named detail region for legacy planning", async () => {
    renderPage({ getPlanning: vi.fn().mockRejectedValue(new ApiError(409, { detail: "检测到历史章节资料。", code: "PLANNING_LEGACY_IMPORT_REQUIRED" })) });
    expect(await screen.findByRole("heading", { name: "检测到历史章节资料" })).toBeInTheDocument();
    expect(screen.getAllByRole("region", { name: "章节规划详情" })).toHaveLength(1);
  });

  it("keeps the named detail region when planning fails to load", async () => {
    renderPage({ getPlanning: vi.fn().mockRejectedValue(new ApiError(500, { detail: "网络错误" })) });
    expect(await screen.findByRole("heading", { name: "规划暂时无法加载" })).toBeInTheDocument();
    expect(screen.getAllByRole("region", { name: "章节规划详情" })).toHaveLength(1);
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
    expect(await screen.findByRole("button", { name: "添加设定" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "新建篇章" })).toBeDisabled();
    await userEvent.click(retry);
    await waitFor(() => expect(createPlanningPart).toHaveBeenCalledWith("project-1", payload));
  });

  it("freezes chapter planning when the foreshadow workspace owns the shared pending slot", async () => {
    sessionStorage.setItem("novel_pending_planning_operation_v1:user-1:project-1", JSON.stringify({
      schema_version: 2,
      workspace: "foreshadow",
      user_id: "user-1",
      project_id: "project-1",
      operation_key: "foreshadow_archive:pending123",
      operation_type: "foreshadow_archive",
      lifecycle_id: "l".repeat(32),
      resource_id: null,
      payload: { operation_key: "foreshadow_archive:pending123", expected_lifecycle_version: 1 },
      created_at: "2026-08-11T08:00:00Z",
    }));
    renderPage();
    expect(await screen.findByText(/伏笔管理中还有结果未确认的写入/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "新建篇章" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "添加设定" })).toBeDisabled();
    expect(screen.getByRole("link", { name: "前往伏笔管理核对" })).toHaveAttribute("href", "/project/project-1/plan/foreshadows");
  });

  it("keeps planning writes frozen when candidate selection v6 owns the shared slot", async () => {
    sessionStorage.setItem(
      "novel_pending_planning_operation_v1:user-1:project-1",
      JSON.stringify({ schema_version: 6, workspace: "candidate_selection" })
    );
    const createPlanningPart = vi.fn();
    renderPage({ createPlanningPart });
    await screen.findByText("第一篇");
    expect(screen.getByRole("button", { name: "新建篇章" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "添加设定" })).toBeDisabled();
    expect(createPlanningPart).not.toHaveBeenCalled();
  });

  it("fails closed instead of treating a malformed generation execution as foreshadow work", async () => {
    sessionStorage.setItem("novel_pending_planning_operation_v1:user-1:project-1", JSON.stringify({
      schema_version: 3,
      workspace: "generation_execution",
      user_id: "user-1",
      project_id: "project-1",
      chapter_id: "c".repeat(32),
      run_id: "r".repeat(32),
      operation_key: "generation:execute:pending123",
      payload: {
        operation_key: "generation:execute:pending123",
        expected_context_checksum: "a".repeat(64),
        expected_capability_checksum: "b".repeat(64),
        confirm_model_call: true,
      },
      created_at: "2026-08-13T08:00:00Z",
    }));
    renderPage();
    expect(await screen.findByText(/损坏或身份不匹配的浏览器恢复记录/)).toBeInTheDocument();
    expect(screen.queryByText(/伏笔管理中还有结果未确认/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "新建篇章" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "确认清除损坏恢复记录" })).toBeInTheDocument();
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
      getPlanningLoreAssignments: vi.fn().mockResolvedValue(emptyAssignments),
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
      getPlanningLoreAssignments: vi.fn().mockResolvedValue(emptyAssignments),
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

  it("adds a lore element and unlocks only after the plan and scope refresh agree", async () => {
    const loreItem = {
      id: "element-1", type: { key: "character", display_name: "角色" }, name: "林岚", summary: "谨慎的调查者",
      confirmation_status: "confirmed", lifecycle_status: "active", enabled: true, generation_eligible: true,
      source_summary: "", current_version: 4, revision: 1, lock_version: 1, updated_at: "", relation_count: 0,
    };
    const assigned = {
      id: "assignment-1", element_id: "element-1", scope: emptyAssignments.scope, status: "active" as const, lock_version: 1,
      assigned_at_content_version: 4, current_content_version: 4, content_changed_since_assignment: false,
      element: { id: "element-1", name: "林岚", summary: "谨慎的调查者", type: { id: "type-1", key: "character", display_name: "角色", status: "active" as const }, confirmation_status: "confirmed" as const, lifecycle_status: "active" as const, enabled: true, merged_into_element_id: null },
      generation_eligible: true, ineligible_reasons: [], created_at: "", updated_at: "",
    };
    const afterAssignments = {
      ...emptyAssignments,
      assignment_version: 2,
      direct_assignments: [assigned],
      effective_elements: [{ element_id: "element-1", current_content_version: 4, content_changed_since_any_assignment: false, element: assigned.element, direct_assignments: [{ assignment_id: "assignment-1", scope: emptyAssignments.scope, lock_version: 1, assigned_at_content_version: 4 }], inherited_from: [], all_sources: [{ assignment_id: "assignment-1", scope: emptyAssignments.scope, lock_version: 1, assigned_at_content_version: 4 }], generation_eligible: true, ineligible_reasons: [] }],
      counts: { direct: 1, direct_active: 1, direct_removed: 0, effective: 1, generation_eligible: 1, ineligible: 0 },
    };
    const getPlanning = vi.fn().mockResolvedValueOnce(plan).mockResolvedValueOnce({ ...plan, assignment_version: 2 });
    const getPlanningLoreAssignments = vi.fn().mockResolvedValueOnce(emptyAssignments).mockResolvedValueOnce(afterAssignments);
    const createPlanningLoreAssignment = vi.fn().mockResolvedValue({ receipt_kind: "assignment" });
    renderPage({
      getPlanning,
      getPlanningLoreAssignments,
      createPlanningLoreAssignment,
      listLoreElements: vi.fn().mockResolvedValue({ items: [loreItem], total: 1, next_cursor: null, has_more: false, facets: {}, migration_status: {} }),
    });
    await userEvent.click(await screen.findByRole("button", { name: "添加设定" }));
    await userEvent.click(await screen.findByRole("button", { name: "加入当前范围" }));
    await waitFor(() => expect(createPlanningLoreAssignment).toHaveBeenCalledWith("project-1", expect.objectContaining({
      expected_assignment_version: 1,
      expected_element_content_version: 4,
      scope_type: "novel",
      scope_target_id: "project-1",
    })));
    expect(await screen.findByText("《林岚》已加入整部小说。")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "林岚" })).toHaveFocus();
    expect(getPlanning).toHaveBeenCalledTimes(2);
    expect(getPlanningLoreAssignments).toHaveBeenCalledTimes(2);
  });

  it("keeps all planning writes frozen when a confirmed assignment cannot complete both authoritative reads", async () => {
    const loreItem = {
      id: "element-1", type: { key: "character", display_name: "角色" }, name: "林岚", summary: "",
      confirmation_status: "confirmed", lifecycle_status: "active", enabled: true, generation_eligible: true,
      source_summary: "", current_version: 1, revision: 1, lock_version: 1, updated_at: "", relation_count: 0,
    };
    const getPlanning = vi.fn().mockResolvedValue(plan);
    const getPlanningLoreAssignments = vi.fn()
      .mockResolvedValueOnce(emptyAssignments)
      .mockRejectedValue(new ApiError(500, { detail: "分配刷新失败" }));
    const createPlanningLoreAssignment = vi.fn().mockResolvedValue({ receipt_kind: "assignment" });
    renderPage({
      getPlanning,
      getPlanningLoreAssignments,
      createPlanningLoreAssignment,
      listLoreElements: vi.fn().mockResolvedValue({ items: [loreItem], total: 1, next_cursor: null, has_more: false, facets: {}, migration_status: {} }),
    });
    await userEvent.click(await screen.findByRole("button", { name: "添加设定" }));
    await userEvent.click(await screen.findByRole("button", { name: "加入当前范围" }));
    expect(await screen.findByText(/操作已确认，但最新规划与分配尚未完整载入/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "新建篇章" })).toBeDisabled();
    expect(createPlanningLoreAssignment).toHaveBeenCalledTimes(1);
  });

  it("requires a dual authoritative reload before unfreezing a confirmed assignment", async () => {
    const loreItem = {
      id: "element-1", type: { key: "character", display_name: "角色" }, name: "林岚", summary: "",
      confirmation_status: "confirmed", lifecycle_status: "active", enabled: true, generation_eligible: true,
      source_summary: "", current_version: 1, revision: 1, lock_version: 1, updated_at: "", relation_count: 0,
    };
    const getPlanning = vi.fn().mockResolvedValue(plan);
    const getPlanningLoreAssignments = vi.fn()
      .mockResolvedValueOnce(emptyAssignments)
      .mockRejectedValueOnce(new ApiError(500, { detail: "分配刷新失败" }))
      .mockResolvedValueOnce(emptyAssignments);
    renderPage({
      getPlanning,
      getPlanningLoreAssignments,
      createPlanningLoreAssignment: vi.fn().mockResolvedValue({ receipt_kind: "assignment" }),
      listLoreElements: vi.fn().mockResolvedValue({ items: [loreItem], total: 1, next_cursor: null, has_more: false, facets: {}, migration_status: {} }),
    });
    await userEvent.click(await screen.findByRole("button", { name: "添加设定" }));
    await userEvent.click(await screen.findByRole("button", { name: "加入当前范围" }));
    expect(await screen.findByText(/操作已确认，但最新规划与分配尚未完整载入/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "重新加载设定" }));
    expect(await screen.findByText("已重新载入最新规划与设定分配。")).toBeInTheDocument();
    expect(getPlanning).toHaveBeenCalledTimes(3);
    expect(getPlanningLoreAssignments).toHaveBeenCalledTimes(3);
    expect(screen.getByRole("button", { name: "新建篇章" })).toBeEnabled();
  });

  it("freezes all planning writes when assignment reads report maintenance", async () => {
    renderPage({
      getPlanningLoreAssignments: vi.fn().mockRejectedValue(new ApiError(503, {
        detail: "项目资料正在维护。",
        code: "PROJECT_WRITE_FROZEN",
      })),
    });
    expect(await screen.findByRole("status")).toHaveTextContent(/项目资料正在维护/);
    expect(screen.getByRole("button", { name: "新建篇章" })).toBeDisabled();
  });

  it("recovers from assignment maintenance only after both authorities are readable", async () => {
    const getPlanningLoreAssignments = vi.fn()
      .mockRejectedValueOnce(new ApiError(503, {
        detail: "项目资料正在维护。",
        code: "PROJECT_WRITE_FROZEN",
      }))
      .mockResolvedValueOnce(emptyAssignments);
    const getPlanning = vi.fn().mockResolvedValue(plan);
    renderPage({ getPlanning, getPlanningLoreAssignments });
    expect(await screen.findByRole("status")).toHaveTextContent(/项目资料正在维护/);
    expect(screen.getByRole("button", { name: "新建篇章" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "重新加载设定" }));
    await waitFor(() => expect(screen.queryByRole("status")).not.toBeInTheDocument());
    expect(getPlanning).toHaveBeenCalledTimes(2);
    expect(getPlanningLoreAssignments).toHaveBeenCalledTimes(2);
    expect(screen.getByRole("button", { name: "新建篇章" })).toBeEnabled();
  });

  it("keeps structure writes available when an ordinary assignment-only reload fails", async () => {
    const getPlanningLoreAssignments = vi.fn().mockRejectedValue(new ApiError(500, {
      detail: "设定列表暂时无法读取。",
    }));
    renderPage({ getPlanningLoreAssignments });
    const reload = await screen.findByRole("button", { name: "重新加载设定" });
    expect(screen.getByRole("button", { name: "新建篇章" })).toBeEnabled();
    await userEvent.click(reload);
    await waitFor(() => expect(getPlanningLoreAssignments).toHaveBeenCalledTimes(2));
    expect(screen.getByRole("button", { name: "新建篇章" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "重新加载设定" })).toBeEnabled();
  });

  it("refreshes stale lore search results before allowing an element-version conflict to retry", async () => {
    const item = (version: number) => ({
      id: "element-1", type: { key: "character", display_name: "角色" }, name: "林岚", summary: "",
      confirmation_status: "confirmed", lifecycle_status: "active", enabled: true, generation_eligible: true,
      source_summary: "", current_version: version, revision: version, lock_version: version, updated_at: "", relation_count: 0,
    });
    const listLoreElements = vi.fn()
      .mockResolvedValueOnce({ items: [item(1)], total: 1, next_cursor: null, has_more: false, facets: {}, migration_status: {} })
      .mockResolvedValueOnce({ items: [item(2)], total: 1, next_cursor: null, has_more: false, facets: {}, migration_status: {} });
    const createPlanningLoreAssignment = vi.fn()
      .mockRejectedValueOnce(new ApiError(409, {
        detail: "设定内容已更新，请核对后重试。",
        code: "PLANNING_ELEMENT_VERSION_CONFLICT",
        recommended_action: "review_lore_element",
      }))
      .mockResolvedValueOnce({ receipt_kind: "assignment" });
    renderPage({ listLoreElements, createPlanningLoreAssignment });
    await userEvent.click(await screen.findByRole("button", { name: "添加设定" }));
    await userEvent.click(await screen.findByRole("button", { name: "加入当前范围" }));
    await userEvent.click(await screen.findByRole("button", { name: "已核对最新分配" }));
    await waitFor(() => expect(listLoreElements).toHaveBeenCalledTimes(2));
    await userEvent.click(screen.getByRole("button", { name: "加入当前范围" }));
    await waitFor(() => expect(createPlanningLoreAssignment).toHaveBeenCalledTimes(2));
    expect(createPlanningLoreAssignment.mock.calls[0][1]).toEqual(expect.objectContaining({ expected_element_content_version: 1 }));
    expect(createPlanningLoreAssignment.mock.calls[1][1]).toEqual(expect.objectContaining({ expected_element_content_version: 2 }));
  });

  it("retries an unknown assignment with the exact stored key, scope, and payload", async () => {
    const payload = {
      operation_key: "planning:assignment_create:12345678",
      expected_assignment_version: 1,
      element_id: "element-1",
      expected_element_content_version: 2,
      scope_type: "novel" as const,
      scope_target_id: "project-1",
    };
    savePendingPlanningOperation({
      schema_version: 1, user_id: "user-1", project_id: "project-1", operation_key: payload.operation_key,
      action: "assignment_create", target_id: "element-1", payload, created_at: "2026-08-11T00:00:00Z",
    });
    const createPlanningLoreAssignment = vi.fn().mockResolvedValue({ receipt_kind: "assignment" });
    const getPlanning = vi.fn().mockResolvedValueOnce(plan).mockResolvedValueOnce({ ...plan, assignment_version: 1 });
    const getPlanningLoreAssignments = vi.fn().mockResolvedValue(emptyAssignments);
    renderPage({ createPlanningLoreAssignment, getPlanning, getPlanningLoreAssignments });
    const retry = await screen.findByRole("button", { name: "使用原操作编号安全重试" });
    expect(await screen.findByRole("button", { name: "添加设定" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "新建篇章" })).toBeDisabled();
    await userEvent.click(retry);
    await waitFor(() => expect(createPlanningLoreAssignment).toHaveBeenCalledWith("project-1", payload));
  });

  it("recovers a confirmed assignment by key and performs both authoritative reads without replaying the write", async () => {
    const payload = {
      operation_key: "planning:assignment_create:confirmed-12345678",
      expected_assignment_version: 1,
      element_id: "element-1",
      expected_element_content_version: 2,
      scope_type: "novel" as const,
      scope_target_id: "project-1",
    };
    savePendingPlanningOperation({
      schema_version: 1, user_id: "user-1", project_id: "project-1", operation_key: payload.operation_key,
      action: "assignment_create", target_id: "element-1", payload, created_at: "2026-08-11T00:00:00Z",
    });
    const getPlanning = vi.fn().mockResolvedValue(plan);
    const getPlanningLoreAssignments = vi.fn().mockResolvedValue(emptyAssignments);
    const createPlanningLoreAssignment = vi.fn();
    renderPage({
      getPlanning,
      getPlanningLoreAssignments,
      getPlanningOperation: vi.fn().mockResolvedValue({
        receipt_kind: "assignment",
        operation_key: payload.operation_key,
        operation_type: "assignment_create",
        project_id: "project-1",
      }),
      createPlanningLoreAssignment,
    });
    expect(await screen.findByText(/已找回上次操作结果/)).toBeInTheDocument();
    expect(createPlanningLoreAssignment).not.toHaveBeenCalled();
    expect(getPlanning).toHaveBeenCalledTimes(2);
    expect(getPlanningLoreAssignments.mock.calls.length).toBeGreaterThanOrEqual(2);
    expect(sessionStorage.getItem("novel_pending_planning_operation_v1:user-1:project-1")).toBeNull();
  });

  it.each([
    ["receipt kind", { receipt_kind: "structure" }],
    ["operation type", { operation_type: "part_create" }],
    ["operation key", { operation_key: "planning:assignment_create:different-12345678" }],
    ["project", { project_id: "project-2" }],
  ])("fails closed when a by-key receipt has a mismatched %s", async (_label, override) => {
    const payload = {
      operation_key: "planning:assignment_create:mismatch-12345678",
      expected_assignment_version: 1,
      element_id: "element-1",
      expected_element_content_version: 2,
      scope_type: "novel" as const,
      scope_target_id: "project-1",
    };
    savePendingPlanningOperation({
      schema_version: 1, user_id: "user-1", project_id: "project-1", operation_key: payload.operation_key,
      action: "assignment_create", target_id: "element-1", payload, created_at: "2026-08-11T00:00:00Z",
    });
    renderPage({
      getPlanningOperation: vi.fn().mockResolvedValue({
        receipt_kind: "assignment",
        operation_key: payload.operation_key,
        operation_type: "assignment_create",
        project_id: "project-1",
        ...override,
      }),
    });
    expect(await screen.findByText(/操作收据与本地恢复记录不一致/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "确认清除损坏恢复记录" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "新建篇章" })).toBeDisabled();
    expect(sessionStorage.getItem("novel_pending_planning_operation_v1:user-1:project-1")).not.toBeNull();
  });

  it("fails closed for a corrupt pending record until the user explicitly clears only that browser record", async () => {
    sessionStorage.setItem("novel_pending_planning_operation_v1:user-1:project-1", "{broken");
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderPage();
    const clear = await screen.findByRole("button", { name: "确认清除损坏恢复记录" });
    expect(screen.getByRole("button", { name: "新建篇章" })).toBeDisabled();
    await userEvent.click(clear);
    expect(await screen.findByText(/损坏的本地恢复记录已清除/)).toBeInTheDocument();
    expect(sessionStorage.getItem("novel_pending_planning_operation_v1:user-1:project-1")).toBeNull();
    expect(screen.getByRole("button", { name: "新建篇章" })).toBeEnabled();
  });

  it("keeps a structure draft intact when an assignment conflict is refreshed", async () => {
    let conflictTriggered = false;
    const loreItem = {
      id: "element-1", type: { key: "character", display_name: "角色" }, name: "林岚", summary: "",
      confirmation_status: "confirmed", lifecycle_status: "active", enabled: true, generation_eligible: true,
      source_summary: "", current_version: 1, revision: 1, lock_version: 1, updated_at: "", relation_count: 0,
    };
    const getPlanning = vi.fn(() => Promise.resolve({ ...plan, assignment_version: conflictTriggered ? 2 : 1 }));
    const getPlanningLoreAssignments = vi.fn((_projectId: string, scopeType: "novel" | "part" | "chapter", scopeTargetId: string) => Promise.resolve({
      ...emptyAssignments,
      assignment_version: conflictTriggered ? 2 : 1,
      scope: { ...emptyAssignments.scope, scope_type: scopeType, scope_target_id: scopeTargetId, title: scopeType === "part" ? "第一篇" : "整部小说", part_id: scopeType === "part" ? scopeTargetId : null },
    }));
    const createPlanningLoreAssignment = vi.fn(() => {
      conflictTriggered = true;
      return Promise.reject(new ApiError(409, {
        detail: "设定分配版本已更新。",
        code: "PLANNING_ASSIGNMENT_VERSION_CONFLICT",
        recommended_action: "refresh_assignments",
      }));
    });
    renderPage({
      getPlanning,
      getPlanningLoreAssignments,
      createPlanningLoreAssignment,
      listLoreElements: vi.fn().mockResolvedValue({ items: [loreItem], total: 1, next_cursor: null, has_more: false, facets: {}, migration_status: {} }),
    });
    await userEvent.click(await screen.findByRole("button", { name: "第一篇" }));
    const title = screen.getByLabelText("篇章名称");
    await userEvent.clear(title);
    await userEvent.type(title, "我尚未保存的篇章草稿");
    await userEvent.click(await screen.findByRole("button", { name: "添加设定" }));
    await userEvent.click(await screen.findByRole("button", { name: "加入当前范围" }));
    const acknowledge = await screen.findByRole("button", { name: "已核对最新分配" });
    expect(acknowledge.closest('[role="alert"]')).toHaveFocus();
    expect(screen.getByLabelText("篇章名称")).toHaveValue("我尚未保存的篇章草稿");
  });

  it("does not move focus back to an old scope when its assignment finishes later", async () => {
    const loreItem = {
      id: "element-1", type: { key: "character", display_name: "角色" }, name: "林岚", summary: "",
      confirmation_status: "confirmed", lifecycle_status: "active", enabled: true, generation_eligible: true,
      source_summary: "", current_version: 1, revision: 1, lock_version: 1, updated_at: "", relation_count: 0,
    };
    let resolveWrite: (() => void) | undefined;
    const write = new Promise<void>((resolve) => { resolveWrite = resolve; });
    const getPlanningLoreAssignments = vi.fn((_projectId: string, scopeType: "novel" | "part" | "chapter", scopeTargetId: string) => Promise.resolve({
      ...emptyAssignments,
      scope: { ...emptyAssignments.scope, scope_type: scopeType, scope_target_id: scopeTargetId, title: scopeType === "novel" ? "整部小说" : "第一篇", part_id: scopeType === "chapter" ? "part-1" : null },
    }));
    renderPage({
      createPlanningLoreAssignment: vi.fn(() => write),
      getPlanningLoreAssignments,
      listLoreElements: vi.fn().mockResolvedValue({ items: [loreItem], total: 1, next_cursor: null, has_more: false, facets: {}, migration_status: {} }),
    });
    await userEvent.click(await screen.findByRole("button", { name: "添加设定" }));
    await userEvent.click(await screen.findByRole("button", { name: "加入当前范围" }));
    await userEvent.click(screen.getByRole("button", { name: "第一篇" }));
    const currentHeading = await screen.findByRole("heading", { name: "第一篇" });
    await waitFor(() => expect(currentHeading).toHaveFocus());
    resolveWrite?.();
    expect(await screen.findByText(/《林岚》已加入整部小说/)).toBeInTheDocument();
    await waitFor(() => expect(currentHeading).toHaveFocus());
    const partReads = getPlanningLoreAssignments.mock.calls
      .filter((call) => call[1] === "part" && call[2] === "part-1");
    expect(partReads.length).toBeGreaterThanOrEqual(2);
  });

  it("ignores a previous scope's delayed assignment response", async () => {
    let resolveNovel: ((value: typeof emptyAssignments) => void) | undefined;
    const delayedNovel = new Promise<typeof emptyAssignments>((resolve) => { resolveNovel = resolve; });
    const getPlanningLoreAssignments = vi.fn((_projectId: string, scopeType: "novel" | "part" | "chapter", scopeTargetId: string) => {
      if (scopeType === "novel") return delayedNovel;
      return Promise.resolve({
        ...emptyAssignments,
        scope: { ...emptyAssignments.scope, scope_type: scopeType, scope_target_id: scopeTargetId, title: "第一篇", part_id: scopeTargetId },
      });
    });
    renderPage({ getPlanningLoreAssignments });
    await userEvent.click(await screen.findByRole("button", { name: "第一篇" }));
    expect(await screen.findByText((_, node) => node?.textContent === "当前正在编辑：篇章《第一篇》。移除只影响本范围的直接来源。")).toBeInTheDocument();
    resolveNovel?.(emptyAssignments);
    await waitFor(() => expect(screen.getByText((_, node) => node?.textContent === "当前正在编辑：篇章《第一篇》。移除只影响本范围的直接来源。")).toBeInTheDocument());
    expect(screen.queryByText((_, node) => node?.textContent?.includes("当前正在编辑：整部小说") === true)).not.toBeInTheDocument();
  });

  it("keeps an unsaved chapter draft when inherited-source navigation is cancelled", async () => {
    const inheritedElement = {
      id: "element-inherited", name: "整书法则", summary: "不可违背的世界规则",
      type: { id: "type-rule", key: "rule", display_name: "世界规则", status: "active" as const },
      confirmation_status: "confirmed" as const, lifecycle_status: "active" as const,
      enabled: true, merged_into_element_id: null,
    };
    const chapterAssignments = {
      ...emptyAssignments,
      scope: { scope_type: "chapter" as const, scope_target_id: "chapter-1", title: "第一章", status: "active" as const, part_id: "part-1" },
      effective_elements: [{
        element_id: inheritedElement.id,
        current_content_version: 1,
        content_changed_since_any_assignment: false,
        element: inheritedElement,
        direct_assignments: [],
        inherited_from: [{ assignment_id: "assignment-novel", scope: emptyAssignments.scope, lock_version: 1, assigned_at_content_version: 1 }],
        all_sources: [{ assignment_id: "assignment-novel", scope: emptyAssignments.scope, lock_version: 1, assigned_at_content_version: 1 }],
        generation_eligible: true,
        ineligible_reasons: [],
      }],
      counts: { direct: 0, direct_active: 0, direct_removed: 0, effective: 1, generation_eligible: 1, ineligible: 0 },
    };
    const getPlanningLoreAssignments = vi.fn().mockImplementation(
      (_projectId: string, scopeType: string) => Promise.resolve(
        scopeType === "chapter" ? chapterAssignments : emptyAssignments
      )
    );
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    renderPage({ getPlanningLoreAssignments });

    await userEvent.click(await screen.findByRole("button", { name: "第一章" }));
    const summary = await screen.findByLabelText("章节摘要");
    await userEvent.type(summary, "尚未保存的章节安排");
    await userEvent.click(await screen.findByRole("button", { name: "前往整部小说调整" }));

    expect(confirm).toHaveBeenCalledWith(expect.stringMatching(/尚未保存/));
    expect(screen.getByRole("heading", { name: "第一章" })).toBeInTheDocument();
    expect(screen.getByLabelText("章节摘要")).toHaveValue("尚未保存的章节安排");

    await userEvent.click(screen.getByRole("link", { name: "在设定仓库中查找" }));
    expect(confirm).toHaveBeenCalledTimes(2);
    expect(screen.getByRole("heading", { name: "第一章" })).toBeInTheDocument();
    expect(screen.getByLabelText("章节摘要")).toHaveValue("尚未保存的章节安排");
  });

  it("keeps a new-chapter draft in its source part when scope switching is cancelled", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    renderPage();

    await userEvent.click(await screen.findByRole("button", { name: "第一篇" }));
    await userEvent.type(screen.getByLabelText("新章节名称"), "只属于第一篇的草稿");
    await userEvent.click(screen.getByRole("button", { name: "第二篇" }));

    expect(confirm).toHaveBeenCalledWith(expect.stringMatching(/尚未保存/));
    expect(screen.getByRole("heading", { name: "第一篇" })).toBeInTheDocument();
    expect(screen.getByLabelText("新章节名称")).toHaveValue("只属于第一篇的草稿");
  });

  it("clears a new-chapter draft after the user confirms switching to another part", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderPage();

    await userEvent.click(await screen.findByRole("button", { name: "第一篇" }));
    await userEvent.type(screen.getByLabelText("新章节名称"), "不应带到第二篇");
    await userEvent.click(screen.getByRole("button", { name: "第二篇" }));

    expect(await screen.findByRole("heading", { name: "第二篇" })).toBeInTheDocument();
    expect(screen.getByLabelText("新章节名称")).toHaveValue("");
    expect(screen.getByRole("button", { name: "添加章节" })).toBeDisabled();
  });

  it("treats an unsubmitted target-part selection as a protected chapter draft", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    renderPage();

    await userEvent.click(await screen.findByRole("button", { name: "第一章" }));
    await userEvent.selectOptions(screen.getByLabelText("移动至篇章"), "part-2");
    expect(screen.getByTestId("chapter-drag-handle-chapter-1")).toHaveAttribute("draggable", "false");
    expect(screen.getByRole("button", { name: "下移章节 第一章" })).toBeDisabled();
    expect(screen.getByText("请先保存或放弃当前修改，再调整顺序。")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "移动到目标篇章末尾" })).toBeEnabled();
    await userEvent.click(screen.getByRole("button", { name: "整部小说" }));

    expect(confirm).toHaveBeenCalledWith(expect.stringMatching(/尚未保存/));
    expect(screen.getByRole("heading", { name: "第一章" })).toBeInTheDocument();
    expect(screen.getByLabelText("移动至篇章")).toHaveValue("part-2");
  });

  it("does not call the archive API or discard fields when dirty-archive confirmation is cancelled", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    const changePlanningChapterState = vi.fn();
    renderPage({ changePlanningChapterState });

    await userEvent.click(await screen.findByRole("button", { name: "第一章" }));
    await userEvent.type(screen.getByLabelText("章节摘要"), "不应被归档操作丢弃");
    await userEvent.click(screen.getByRole("button", { name: "归档章节" }));

    expect(confirm).toHaveBeenCalledWith(expect.stringMatching(/将放弃这些修改/));
    expect(changePlanningChapterState).not.toHaveBeenCalled();
    expect(screen.getByLabelText("章节摘要")).toHaveValue("不应被归档操作丢弃");
  });

  it("guards page-level project and lore exits while a structure draft exists", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    renderPage();

    await userEvent.click(await screen.findByRole("button", { name: "第一篇" }));
    await userEvent.type(screen.getByLabelText("篇章说明"), "离页前保留");
    await userEvent.click(screen.getByRole("link", { name: "打开设定仓库" }));
    await userEvent.click(screen.getByRole("button", { name: "← 返回项目" }));

    expect(confirm).toHaveBeenCalledTimes(2);
    expect(screen.getByRole("heading", { name: "第一篇" })).toBeInTheDocument();
    expect(screen.getByLabelText("篇章说明")).toHaveValue("离页前保留");
  });

  it("focuses an actionable archive-blocked error after the server rejects the write", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const changePlanningChapterState = vi.fn().mockRejectedValue(new ApiError(409, {
      detail: "该章节仍有活动设定分配。",
      code: "PLANNING_SCOPE_HAS_ACTIVE_ASSIGNMENTS",
      recommended_action: "remove_assignments_first",
    }));
    renderPage({ changePlanningChapterState });

    await userEvent.click(await screen.findByRole("button", { name: "第一章" }));
    await userEvent.click(screen.getByRole("button", { name: "归档章节" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("该章节仍有活动设定分配");
    expect(alert).toHaveTextContent("移除本级分配后重试");
    expect(alert).toHaveFocus();
  });
});
