import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as apiModule from "@/services/api";
import { ApiError } from "@/services/api";
import { savePendingPlanningOperation } from "@/services/planningOperations";
import type { GenerationRunPrepareInput, GenerationRunResponse } from "@/types/generation";
import type { NovelPlan } from "@/types/planning";
import ChapterPlanningPage from "./ChapterPlanningPage";

vi.mock("@/components/AuthContext", () => ({ useAuth: () => ({ user: { id: "user-1" } }) }));

const id = (value: string) => value.padEnd(32, value).slice(0, 32);
const projectId = id("project");
const planId = id("plan");
const partId = id("part");
const chapterId = id("chapter");
const elementId = id("element");
const typeId = id("type");
const now = "2026-08-11T05:00:00Z";

const plan: NovelPlan = {
  id: planId, project_id: projectId, status: "active", structure_version: 3, assignment_version: 2, created_at: now, updated_at: now,
  parts: [{ id: partId, project_id: projectId, plan_id: planId, title: "第一篇", description: "", position: 1, status: "active", lock_version: 1, created_at: now, updated_at: now, chapters: [{ id: chapterId, project_id: projectId, plan_id: planId, part_id: partId, title: "第一章", summary: "雨夜相遇", target_word_count: 2000, position: 1, status: "active", lock_version: 4, created_at: now, updated_at: now }] }],
};

const assignments = {
  scope: { scope_type: "chapter" as const, scope_target_id: chapterId, title: "第一章", status: "active" as const, part_id: partId },
  assignment_version: 2,
  direct_assignments: [],
  effective_elements: [{
    element_id: elementId, current_content_version: 1, content_changed_since_any_assignment: false,
    element: { id: elementId, name: "沈星", summary: "主角", type: { id: typeId, key: "character", display_name: "角色", status: "active" as const }, confirmation_status: "confirmed" as const, lifecycle_status: "active" as const, enabled: true, merged_into_element_id: null },
    direct_assignments: [], inherited_from: [{ assignment_id: id("assignment"), scope: { scope_type: "novel" as const, scope_target_id: projectId, title: "整部小说", status: "active" as const, part_id: null }, lock_version: 1, assigned_at_content_version: 1 }],
    all_sources: [{ assignment_id: id("assignment"), scope: { scope_type: "novel" as const, scope_target_id: projectId, title: "整部小说", status: "active" as const, part_id: null }, lock_version: 1, assigned_at_content_version: 1 }],
    generation_eligible: true, ineligible_reasons: [],
  }],
  counts: { direct: 0, direct_active: 0, direct_removed: 0, effective: 1, generation_eligible: 1, ineligible: 0 },
};

function response(input: GenerationRunPrepareInput): GenerationRunResponse {
  return {
    id: id("run"), project_id: projectId, plan_id: planId, planning_chapter_id: chapterId,
    operation_key: input.operation_key, replayed: false, status: "prepared", execution_mode: "preflight_only", ai_invoked: false, billing_effect: "none",
    structure_version: input.expected_structure_version, assignment_version: input.expected_assignment_version, chapter_lock_version: input.expected_chapter_lock_version, context_schema_version: 1,
    context_checksum: "c".repeat(64), context_size_bytes: 1024, created_at: now, updated_at: now,
    context_manifest: {
      schema_version: 1, project_id: projectId, plan_id: planId,
      versions: { structure: 3, assignment: 2, chapter_lock: 4 },
      part: { id: partId, title: "第一篇", description: "", position: 1, lock_version: 1 },
      chapter: { id: chapterId, title: "第一章", summary: "雨夜相遇", target_word_count: 2000, position: 1, lock_version: 4 },
      elements: [{ element_id: elementId, type: { id: typeId, key: "character", display_name: "角色", schema_revision: 1 }, version: { id: id("version"), element_id: elementId, type_id: typeId, version_no: 1, name: "沈星", summary: "主角", payload: { identity: "调查员" }, field_states: { identity: "confirmed" }, source_id: id("source") }, assignment_sources: [{ assignment_id: id("assignment"), scope_type: "novel", scope_target_id: projectId, scope_title: "整部小说", assignment_lock_version: 1, assigned_at_content_version: 1 }] }],
      relations: [], warnings: [], foreshadow_actions: { supported: false, items: [] }, counts: { elements: 1, relations: 0, warnings: 0 },
    },
  };
}

function LocationProbe() { return <output data-testid="location">{useLocation().search}</output>; }

function renderPage(
  overrides: Record<string, unknown> = {},
  initialEntry = `/project/${projectId}/plan/chapters?scope=chapter&target=${chapterId}`
) {
  const api = {
    ...apiModule.api,
    getPlanning: vi.fn().mockResolvedValue(plan),
    getPlanningLoreAssignments: vi.fn().mockResolvedValue(assignments),
    getPlanningOperation: vi.fn().mockRejectedValue(new ApiError(404, { detail: "not found" })),
    getGenerationRunByKey: vi.fn().mockRejectedValue(new ApiError(404, { detail: "not found" })),
    getGenerationRun: vi.fn().mockRejectedValue(new ApiError(404, { detail: "not found" })),
    prepareGenerationRun: vi.fn().mockImplementation((_project: string, _chapter: string, input: GenerationRunPrepareInput) => Promise.resolve(response(input))),
    ...overrides,
  };
  vi.spyOn(apiModule, "api", "get").mockReturnValue(api as typeof apiModule.api);
  render(<MemoryRouter initialEntries={[initialEntry]}><LocationProbe /><Routes><Route path="/project/:id/plan/chapters" element={<ChapterPlanningPage />} /></Routes></MemoryRouter>);
  return api;
}

describe("ChapterPlanningPage generation preflight", () => {
  beforeEach(() => { vi.restoreAllMocks(); sessionStorage.clear(); });

  it("prepares with authoritative versions, persists a URL pointer, and shows no generation claim", async () => {
    const api = renderPage();
    await userEvent.click(await screen.findByRole("button", { name: "检查生成上下文" }));
    await waitFor(() => expect(api.prepareGenerationRun).toHaveBeenCalledWith(projectId, chapterId, expect.objectContaining({ expected_structure_version: 3, expected_assignment_version: 2, expected_chapter_lock_version: 4 })));
    expect(await screen.findByText("检查记录已保存")).toBeInTheDocument();
    expect(screen.getByText("AI 未调用")).toBeInTheDocument();
    expect(screen.queryByText(/生成完成|开始生成/)).not.toBeInTheDocument();
    expect(screen.getByTestId("location")).toHaveTextContent(`generation_run=${id("run")}`);
    expect(sessionStorage.length).toBe(0);
  });

  it("checks by key after an unknown result and retries the exact request only after 404", async () => {
    let attempts = 0;
    const prepareGenerationRun = vi.fn((_project: string, _chapter: string, input: GenerationRunPrepareInput) => {
      attempts += 1;
      return attempts === 1 ? Promise.reject(new Error("network unknown")) : Promise.resolve(response(input));
    });
    const getGenerationRunByKey = vi.fn().mockRejectedValue(new ApiError(404, {
      detail: "not found",
      code: "GENERATION_RUN_NOT_FOUND",
      retryable: true,
      recommended_action: "retry_original_prepare",
    }));
    const api = renderPage({ prepareGenerationRun, getGenerationRunByKey });
    await userEvent.click(await screen.findByRole("button", { name: "检查生成上下文" }));
    expect(await screen.findByRole("button", { name: "使用原请求安全重试" })).toBeInTheDocument();
    expect(getGenerationRunByKey).toHaveBeenCalledTimes(1);
    const first = prepareGenerationRun.mock.calls[0][2];
    await userEvent.click(screen.getByRole("button", { name: "使用原请求安全重试" }));
    await waitFor(() => expect(prepareGenerationRun).toHaveBeenCalledTimes(2));
    expect(prepareGenerationRun.mock.calls[1][2]).toEqual(first);
    expect(await screen.findByText("检查记录已保存")).toBeInTheDocument();
    expect(api.getPlanning).toHaveBeenCalled();
  });

  it("recovers a confirmed pending request by key without posting again", async () => {
    const payload: GenerationRunPrepareInput = { operation_key: "planning:generation_prepare:recover123", expected_structure_version: 3, expected_assignment_version: 2, expected_chapter_lock_version: 4 };
    savePendingPlanningOperation({ schema_version: 1, user_id: "user-1", project_id: projectId, operation_key: payload.operation_key, action: "generation_prepare", target_id: chapterId, payload, created_at: now });
    const getGenerationRunByKey = vi.fn().mockResolvedValue({ ...response(payload), replayed: true });
    const api = renderPage({ getGenerationRunByKey });
    expect(await screen.findByText("检查记录已保存")).toBeInTheDocument();
    expect(screen.getByText("已找回服务端保存的检查记录。")).toBeInTheDocument();
    expect(api.prepareGenerationRun).not.toHaveBeenCalled();
    expect(sessionStorage.length).toBe(0);
  });

  it("restores a saved URL record by id and rejects a non-zero-AI contract without rendering it", async () => {
    const payload: GenerationRunPrepareInput = { operation_key: "planning:generation_prepare:saved123", expected_structure_version: 3, expected_assignment_version: 2, expected_chapter_lock_version: 4 };
    const getGenerationRun = vi.fn().mockResolvedValue({ ...response(payload), ai_invoked: true });
    const api = renderPage(
      { getGenerationRun },
      `/project/${projectId}/plan/chapters?scope=chapter&target=${chapterId}&generation_run=${id("run")}`
    );
    expect(await screen.findByText(/未返回零 AI、零费用/)).toBeInTheDocument();
    expect(screen.queryByText("检查记录已保存")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "关闭无效记录指针" })).toBeInTheDocument();
    expect(api.prepareGenerationRun).not.toHaveBeenCalled();
  });

  it("blocks preparation while chapter fields are unsaved", async () => {
    const api = renderPage();
    const summary = await screen.findByLabelText("章节摘要");
    await userEvent.type(summary, "本地草稿");
    const prepare = screen.getByRole("button", { name: "检查生成上下文" });
    expect(prepare).toBeDisabled();
    expect(screen.getByText(/未保存修改/)).toBeInTheDocument();
    expect(api.prepareGenerationRun).not.toHaveBeenCalled();
  });

  it("keeps an arbitrary 404 fail-closed instead of authorizing a new request", async () => {
    const prepareGenerationRun = vi.fn().mockRejectedValue(new Error("network unknown"));
    const getGenerationRunByKey = vi.fn().mockRejectedValue(new ApiError(404, { detail: "generic missing" }));
    renderPage({ prepareGenerationRun, getGenerationRunByKey });
    await userEvent.click(await screen.findByRole("button", { name: "检查生成上下文" }));
    expect(await screen.findByText(/未给出可安全重试原请求/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "使用原请求安全重试" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "核对上次检查" })).toBeInTheDocument();
    expect(prepareGenerationRun).toHaveBeenCalledTimes(1);
  });

  it("blocks a mixed eligible and ineligible assignment scope", async () => {
    const mixedAssignments = {
      ...assignments,
      counts: { ...assignments.counts, effective: 2, ineligible: 1 },
    };
    const api = renderPage({ getPlanningLoreAssignments: vi.fn().mockResolvedValue(mixedAssignments) });
    const prepare = await screen.findByRole("button", { name: "检查生成上下文" });
    expect(prepare).toBeDisabled();
    expect(screen.getByText(/处理全部失效设定/)).toBeInTheDocument();
    expect(api.prepareGenerationRun).not.toHaveBeenCalled();
  });

  it("rejects a by-id response whose record id differs from the URL pointer", async () => {
    const payload: GenerationRunPrepareInput = { operation_key: "planning:generation_prepare:saved456", expected_structure_version: 3, expected_assignment_version: 2, expected_chapter_lock_version: 4 };
    const getGenerationRun = vi.fn().mockResolvedValue({ ...response(payload), id: id("different-run") });
    renderPage(
      { getGenerationRun },
      `/project/${projectId}/plan/chapters?scope=chapter&target=${chapterId}&generation_run=${id("run")}`
    );
    expect(await screen.findByText(/项目、章节或操作编号不一致/)).toBeInTheDocument();
    expect(screen.queryByText("检查记录已保存")).not.toBeInTheDocument();
  });

  it("keeps a cross-scope pending request visible and returns to its originating chapter", async () => {
    const payload: GenerationRunPrepareInput = { operation_key: "planning:generation_prepare:cross123", expected_structure_version: 3, expected_assignment_version: 2, expected_chapter_lock_version: 4 };
    savePendingPlanningOperation({ schema_version: 1, user_id: "user-1", project_id: projectId, operation_key: payload.operation_key, action: "generation_prepare", target_id: chapterId, payload, created_at: now });
    renderPage({}, `/project/${projectId}/plan/chapters`);
    const returnButton = await screen.findByRole("button", { name: "返回发起章节核对" });
    expect(screen.getByText(/冻结新的规划写入和新检查/)).toBeInTheDocument();
    await userEvent.click(returnButton);
    expect(await screen.findByRole("button", { name: "核对上次检查" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "检查生成上下文" })).not.toBeInTheDocument();
  });

  it("keeps the in-memory pending lock when session cleanup fails", async () => {
    vi.spyOn(Object.getPrototypeOf(sessionStorage) as Storage, "removeItem").mockImplementation(() => { throw new Error("blocked"); });
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: "检查生成上下文" }));
    expect(await screen.findByText(/无法清除原恢复线索/)).toBeInTheDocument();
    expect(screen.getByText(/冻结新的规划写入和新检查/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /再次检查当前上下文/ })).toBeDisabled();
  });

  it("focuses the visible feedback region when session recovery data cannot be written", async () => {
    vi.spyOn(Object.getPrototypeOf(sessionStorage) as Storage, "setItem").mockImplementation(() => { throw new Error("blocked"); });
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: "检查生成上下文" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/无法安全保存检查恢复信息/);
    await waitFor(() => expect(alert.closest(".planning-generation__feedback")).toHaveFocus());
  });

  it("clears maintenance only after a recovered run and authoritative planning plus assignment refresh", async () => {
    let savedPayload: GenerationRunPrepareInput | null = null;
    const prepareGenerationRun = vi.fn((_project: string, _chapter: string, input: GenerationRunPrepareInput) => {
      savedPayload = input;
      return Promise.reject(new ApiError(503, { detail: "维护中", retryable: true }));
    });
    const getGenerationRunByKey = vi.fn().mockImplementation(() => Promise.resolve(response(savedPayload!)));
    const api = renderPage({ prepareGenerationRun, getGenerationRunByKey });
    await userEvent.click(await screen.findByRole("button", { name: "检查生成上下文" }));
    expect(await screen.findByText(/项目资料正在维护/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "核对上次检查" }));
    expect(await screen.findByText("检查记录已保存")).toBeInTheDocument();
    expect(screen.queryByText(/项目资料正在维护/)).not.toBeInTheDocument();
    expect(api.getPlanning.mock.calls.length).toBeGreaterThanOrEqual(2);
    expect(api.getPlanningLoreAssignments.mock.calls.length).toBeGreaterThanOrEqual(2);
  });

  it("does not overwrite the shared pending slot while a chapter write is in flight", async () => {
    const updatePlanningChapter = vi.fn().mockImplementation(() => new Promise(() => undefined));
    const api = renderPage({ updatePlanningChapter });
    await userEvent.click(await screen.findByRole("button", { name: "保存章节" }));
    await waitFor(() => expect(updatePlanningChapter).toHaveBeenCalledTimes(1));
    expect(screen.getByRole("button", { name: "检查生成上下文" })).toBeDisabled();
    expect(screen.getByText(/当前操作尚未结束/)).toBeInTheDocument();
    expect(api.prepareGenerationRun).not.toHaveBeenCalled();
  });

  it("requires explicit confirmation before abandoning a corrupt server receipt clue", async () => {
    const prepareGenerationRun = vi.fn((_project: string, _chapter: string, input: GenerationRunPrepareInput) => Promise.resolve({ ...response(input), ai_invoked: true }));
    const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
    renderPage({ prepareGenerationRun });
    await userEvent.click(await screen.findByRole("button", { name: "检查生成上下文" }));
    const abandon = await screen.findByRole("button", { name: "明确放弃原检查恢复线索" });
    expect(sessionStorage.length).toBe(1);
    await userEvent.click(abandon);
    expect(sessionStorage.length).toBe(1);
    await userEvent.click(screen.getByRole("button", { name: "明确放弃原检查恢复线索" }));
    expect(confirm).toHaveBeenCalledTimes(2);
    expect(sessionStorage.length).toBe(0);
    expect(await screen.findByText(/服务器上可能存在的检查记录未被删除/)).toBeInTheDocument();
  });

  it("keeps by-key checking and original-payload retry reachable when the target chapter is missing", async () => {
    const missingChapterId = id("missing-chapter");
    const payload: GenerationRunPrepareInput = { operation_key: "planning:generation_prepare:missing1", expected_structure_version: 3, expected_assignment_version: 2, expected_chapter_lock_version: 4 };
    savePendingPlanningOperation({ schema_version: 1, user_id: "user-1", project_id: projectId, operation_key: payload.operation_key, action: "generation_prepare", target_id: missingChapterId, payload, created_at: now });
    const safeMissing = new ApiError(404, { detail: "not found", code: "GENERATION_RUN_NOT_FOUND", retryable: true, recommended_action: "retry_original_prepare" });
    const prepareGenerationRun = vi.fn().mockRejectedValue(new ApiError(404, { detail: "chapter missing" }));
    renderPage({ getGenerationRunByKey: vi.fn().mockRejectedValue(safeMissing), prepareGenerationRun }, `/project/${projectId}/plan/chapters`);
    const retry = await screen.findByRole("button", { name: "使用原编号与载荷重试" });
    expect(screen.getByRole("button", { name: "核对原检查结果" })).toBeInTheDocument();
    await userEvent.click(retry);
    await waitFor(() => expect(prepareGenerationRun).toHaveBeenCalledWith(projectId, missingChapterId, payload));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("chapter missing");
    expect(alert).toHaveFocus();
  });

  it("keeps recovery actions available when the originating chapter is archived", async () => {
    const archivedPlan: NovelPlan = {
      ...plan,
      parts: plan.parts.map((partItem) => ({
        ...partItem,
        chapters: partItem.chapters.map((chapterItem) => ({ ...chapterItem, status: "archived" as const })),
      })),
    };
    const payload: GenerationRunPrepareInput = { operation_key: "planning:generation_prepare:archived", expected_structure_version: 3, expected_assignment_version: 2, expected_chapter_lock_version: 4 };
    savePendingPlanningOperation({ schema_version: 1, user_id: "user-1", project_id: projectId, operation_key: payload.operation_key, action: "generation_prepare", target_id: chapterId, payload, created_at: now });
    const safeMissing = new ApiError(404, { detail: "not found", code: "GENERATION_RUN_NOT_FOUND", retryable: true, recommended_action: "retry_original_prepare" });
    const prepareGenerationRun = vi.fn().mockRejectedValue(new ApiError(409, { detail: "chapter archived" }));
    renderPage({ getPlanning: vi.fn().mockResolvedValue(archivedPlan), getGenerationRunByKey: vi.fn().mockRejectedValue(safeMissing), prepareGenerationRun });
    expect(await screen.findByRole("button", { name: "核对原检查结果" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "使用原编号与载荷重试" }));
    await waitFor(() => expect(prepareGenerationRun).toHaveBeenCalledWith(projectId, chapterId, payload));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("chapter archived");
    expect(alert).toHaveFocus();
  });

  it("does not carry chapter A generation feedback into chapter B", async () => {
    const secondChapterId = id("chapter-two");
    const twoChapterPlan: NovelPlan = {
      ...plan,
      parts: plan.parts.map((partItem) => ({
        ...partItem,
        chapters: [
          ...partItem.chapters,
          { ...partItem.chapters[0], id: secondChapterId, title: "第二章", position: 2 },
        ],
      })),
    };
    const prepareGenerationRun = vi.fn().mockRejectedValue(new ApiError(400, { detail: "第一章检查失败" }));
    renderPage({ getPlanning: vi.fn().mockResolvedValue(twoChapterPlan), prepareGenerationRun });
    await userEvent.click(await screen.findByRole("button", { name: "检查生成上下文" }));
    expect(await screen.findByText("第一章检查失败")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "第二章" }));
    await waitFor(() => expect(screen.queryByText("第一章检查失败")).not.toBeInTheDocument());
    expect(screen.getByRole("heading", { name: "第二章", level: 2 })).toBeInTheDocument();
  });
});
