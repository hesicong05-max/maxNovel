import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as apiModule from "@/services/api";
import { ApiError } from "@/services/api";
import { savePendingPlanningOperation } from "@/services/planningOperations";
import { loadPendingGenerationExecution, savePendingGenerationExecution, type PendingGenerationExecution } from "@/services/generationExecution";
import type { GenerationAttemptResponse, GenerationCandidateResponse, GenerationCapabilityResponse, GenerationRunPrepareInput, GenerationRunResponse } from "@/types/generation";
import type { NovelPlan } from "@/types/planning";
import ChapterPlanningPage from "./ChapterPlanningPage";

vi.mock("@/components/AuthContext", () => ({ useAuth: () => ({ user: { id: "useruseruseruseruseruseruseruser" } }) }));

const id = (value: string) => value.padEnd(32, value).slice(0, 32);
const projectId = id("project");
const planId = id("plan");
const partId = id("part");
const chapterId = id("chapter");
const elementId = id("element");
const typeId = id("type");
const now = "2026-08-11T05:00:00Z";
const userId = "useruseruseruseruseruseruseruser";

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

const capability: GenerationCapabilityResponse = {
  schema_version: 1,
  provider_name: "deepseek",
  model_name: "deepseek-chat",
  max_output_tokens: 4096,
  input_limit_availability: "unavailable",
  max_input_tokens: null,
  price_availability: "unavailable",
  capability_checksum: "a".repeat(64),
};

function executionOperation(operationKey = "generation:execute:12345678"): PendingGenerationExecution {
  return {
    schema_version: 3,
    workspace: "generation_execution",
    user_id: userId,
    project_id: projectId,
    chapter_id: chapterId,
    run_id: id("run"),
    operation_key: operationKey,
    payload: {
      operation_key: operationKey,
      expected_context_checksum: "c".repeat(64),
      expected_capability_checksum: capability.capability_checksum,
      confirm_model_call: true,
    },
    created_at: now,
  };
}

function executionAttempt(operation: PendingGenerationExecution, status: GenerationAttemptResponse["status"] = "reserved"): GenerationAttemptResponse {
  const invoked = status !== "reserved" && !(status === "failed");
  const terminal = status === "failed" || status === "outcome_unknown" || status === "succeeded";
  return {
    id: id("attempt"), project_id: projectId, run_id: operation.run_id, planning_chapter_id: chapterId,
    operation_key: operation.operation_key, replayed: false, status,
    execution_mode: "single_call", billing_confirmed: true, ai_invoked: invoked, billing_effect: invoked ? "possible" : "none",
    capability, model_name: capability.model_name, prompt_schema_version: 1,
    prompt_checksum: "b".repeat(64), context_checksum: operation.payload.expected_context_checksum,
    lock_version: 1, usage: status === "calling" || status === "outcome_unknown"
      ? { status: "unknown", input_tokens: null, output_tokens: null, total_tokens: null }
      : { status: "unavailable", input_tokens: null, output_tokens: null, total_tokens: null },
    candidate_id: status === "succeeded" ? id("candidate") : null,
    error: status === "failed" || status === "outcome_unknown" ? { code: "PROVIDER_ERROR", message: "服务商返回安全失败说明", retryable: false, recommended_action: status === "failed" ? "inspect_failure" : "keep_unknown_result" } : null,
    claimed_at: invoked ? now : null,
    completed_at: terminal ? now : null,
    created_at: now, updated_at: now,
  };
}

async function candidateResponse(operation: PendingGenerationExecution, attemptId = id("attempt")): Promise<GenerationCandidateResponse> {
  const content = "雨夜里，沈星在旧桥下发现了线索。";
  const bytes = new TextEncoder().encode(content);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  const checksum = Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
  return {
    id: id("candidate"), project_id: projectId, run_id: operation.run_id,
    planning_chapter_id: chapterId, source_attempt_id: attemptId, parent_candidate_id: null,
    version_no: 1, origin_kind: "generated", title: "第一章", content,
    content_format: "plain_text", content_checksum: checksum, content_size_bytes: bytes.byteLength,
    word_count: content.match(/[A-Za-z0-9]+|[\u3400-\u4dbf\u4e00-\u9fff]/g)?.length ?? 0,
    created_by: userId, created_at: now,
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
    getGenerationCapability: vi.fn().mockResolvedValue(capability),
    executeGenerationAttempt: vi.fn().mockImplementation((_project: string, _run: string, input: { operation_key: string }) => {
      const operation = executionOperation(input.operation_key);
      return Promise.resolve(executionAttempt(operation));
    }),
    getGenerationAttemptByKey: vi.fn().mockRejectedValue(new ApiError(404, { detail: "not found" })),
    getGenerationCandidate: vi.fn().mockRejectedValue(new ApiError(404, { detail: "candidate not found" })),
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
    expect(screen.getByText("本次检查：AI 未调用")).toBeInTheDocument();
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
    savePendingPlanningOperation({ schema_version: 1, user_id: userId, project_id: projectId, operation_key: payload.operation_key, action: "generation_prepare", target_id: chapterId, payload, created_at: now });
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
    savePendingPlanningOperation({ schema_version: 1, user_id: userId, project_id: projectId, operation_key: payload.operation_key, action: "generation_prepare", target_id: chapterId, payload, created_at: now });
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

  it("loads an authoritative v3 pending execution by key and never posts on page startup", async () => {
    const operation = executionOperation("generation:execute:recover123");
    expect(savePendingGenerationExecution(operation)).toBe(true);
    const getGenerationRun = vi.fn().mockResolvedValue(response({
      operation_key: "planning:generation_prepare:saved123",
      expected_structure_version: 3,
      expected_assignment_version: 2,
      expected_chapter_lock_version: 4,
    }));
    const getGenerationAttemptByKey = vi.fn().mockResolvedValue(executionAttempt(operation));
    const api = renderPage({ getGenerationRun, getGenerationAttemptByKey });
    expect(await screen.findByText(/已预留，尚未确认结果/)).toBeInTheDocument();
    expect(getGenerationAttemptByKey).toHaveBeenCalledWith(projectId, operation.operation_key, undefined);
    expect(api.executeGenerationAttempt).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "再次检查当前上下文" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "关闭这条记录" })).toBeDisabled();
    expect(screen.getByTestId("location")).toHaveTextContent(`scope=chapter`);
    expect(screen.getByTestId("location")).toHaveTextContent(`target=${chapterId}`);
  });

  it.each(["reserved", "calling", "outcome_unknown"] as const)("keeps a %s v3 receipt in its originating chapter when another chapter is selected", async (status) => {
    const secondChapterId = id("secondchapter");
    const planWithSecond: NovelPlan = {
      ...plan,
      parts: [{ ...plan.parts[0], chapters: [
        ...plan.parts[0].chapters,
        { ...plan.parts[0].chapters[0], id: secondChapterId, title: "第二章", position: 2 },
      ] }],
    };
    const operation = executionOperation(`generation:execute:${status}`);
    expect(savePendingGenerationExecution(operation)).toBe(true);
    const api = renderPage({
      getPlanning: vi.fn().mockResolvedValue(planWithSecond),
      getGenerationRun: vi.fn().mockResolvedValue(response({ operation_key: `planning:generation_prepare:${status}`, expected_structure_version: 3, expected_assignment_version: 2, expected_chapter_lock_version: 4 })),
      getGenerationAttemptByKey: vi.fn().mockResolvedValue(executionAttempt(operation, status)),
    });
    await screen.findByText(status === "reserved" ? /已预留，尚未确认结果/ : status === "calling" ? "模型调用中" : "调用结果未知");
    await userEvent.click(screen.getByRole("button", { name: "第二章" }));
    expect(screen.getByTestId("location")).toHaveTextContent(`target=${chapterId}`);
    expect(screen.getByRole("button", { name: "第一章" })).toHaveAttribute("aria-current", "true");
    expect(screen.getByText(/生成执行仍等待核对/)).toBeInTheDocument();
    expect(api.executeGenerationAttempt).not.toHaveBeenCalled();
  });

  it("keeps v3 by-key recovery reachable after another session archives the chapter", async () => {
    const operation = executionOperation("generation:execute:archived-v3");
    expect(savePendingGenerationExecution(operation)).toBe(true);
    const archivedPlan: NovelPlan = {
      ...plan,
      parts: plan.parts.map((partItem) => ({
        ...partItem,
        status: "archived" as const,
        chapters: partItem.chapters.map((chapterItem) => ({ ...chapterItem, status: "archived" as const })),
      })),
    };
    const getGenerationAttemptByKey = vi.fn().mockResolvedValue(executionAttempt(operation, "outcome_unknown"));
    const api = renderPage({
      getPlanning: vi.fn().mockResolvedValue(archivedPlan),
      getGenerationRun: vi.fn().mockResolvedValue(response({ operation_key: "planning:generation_prepare:archived-v3", expected_structure_version: 3, expected_assignment_version: 2, expected_chapter_lock_version: 4 })),
      getGenerationAttemptByKey,
    });
    expect(await screen.findByText("服务商返回安全失败说明")).toBeInTheDocument();
    const check = screen.getByRole("button", { name: "按原编号核对状态" });
    await userEvent.click(check);
    expect(getGenerationAttemptByKey).toHaveBeenCalledTimes(2);
    expect(api.executeGenerationAttempt).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: "查看模型信息并确认生成" })).not.toBeInTheDocument();
  });

  it("keeps missing-chapter v3 recovery on a page-level same-key GET path", async () => {
    const missingChapterId = id("missingvthree");
    const operation = { ...executionOperation("generation:execute:missing-v3"), chapter_id: missingChapterId };
    expect(savePendingGenerationExecution(operation)).toBe(true);
    const safeMissing = new ApiError(404, { detail: "not found", code: "GENERATION_ATTEMPT_NOT_FOUND", retryable: true, recommended_action: "retry_original_execute" });
    const getGenerationAttemptByKey = vi.fn().mockRejectedValue(safeMissing);
    const api = renderPage({ getGenerationAttemptByKey }, `/project/${projectId}/plan/chapters`);
    const check = await screen.findByRole("button", { name: "按原编号核对生成状态" });
    expect(screen.getByText(/生成恢复记录对应的章节当前不存在/)).toBeInTheDocument();
    expect(api.executeGenerationAttempt).not.toHaveBeenCalled();
    await userEvent.click(check);
    expect(await screen.findByText(/原章节不可用，不能安全完成付费重试确认/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "使用原编号和原载荷重试" })).not.toBeInTheDocument();
    expect(getGenerationAttemptByKey).toHaveBeenCalledTimes(1);
    expect(api.executeGenerationAttempt).not.toHaveBeenCalled();
  });

  it("gets capability, saves v3 pending, and sends exactly one POST after explicit confirmation", async () => {
    const api = renderPage();
    await userEvent.click(await screen.findByRole("button", { name: "检查生成上下文" }));
    await screen.findByText("检查记录已保存");
    await userEvent.click(screen.getByRole("button", { name: "查看模型信息并确认生成" }));
    expect(await screen.findByRole("alertdialog", { name: "确认调用模型生成候选" })).toBeInTheDocument();
    expect(api.getGenerationCapability).toHaveBeenCalledTimes(1);
    expect(api.executeGenerationAttempt).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "确认并生成一次候选" }));
    await waitFor(() => expect(api.executeGenerationAttempt).toHaveBeenCalledTimes(1));
    expect(JSON.parse(sessionStorage.getItem(`novel_pending_planning_operation_v1:${userId}:${projectId}`)!)).toMatchObject({ schema_version: 3, workspace: "generation_execution", chapter_id: chapterId, run_id: id("run") });
    expect(await screen.findByText(/已预留，尚未确认结果/)).toBeInTheDocument();
  });

  it("sends zero execution POSTs when v3 pending storage fails", async () => {
    const api = renderPage();
    await userEvent.click(await screen.findByRole("button", { name: "检查生成上下文" }));
    await screen.findByText("检查记录已保存");
    await userEvent.click(screen.getByRole("button", { name: "查看模型信息并确认生成" }));
    await screen.findByRole("alertdialog");
    vi.spyOn(Object.getPrototypeOf(sessionStorage) as Storage, "setItem").mockImplementation(() => { throw new Error("blocked"); });
    await userEvent.click(screen.getByRole("button", { name: "确认并生成一次候选" }));
    expect(await screen.findByText(/本次模型请求未发送/)).toBeInTheDocument();
    expect(api.executeGenerationAttempt).not.toHaveBeenCalled();
  });

  it("closes confirmation and sends zero POSTs when chapter context changes while the dialog is open", async () => {
    const api = renderPage();
    await userEvent.click(await screen.findByRole("button", { name: "检查生成上下文" }));
    await screen.findByText("检查记录已保存");
    await userEvent.click(screen.getByRole("button", { name: "查看模型信息并确认生成" }));
    await screen.findByRole("alertdialog");
    await userEvent.type(screen.getByLabelText("章节摘要"), "未保存的变化");
    await userEvent.click(screen.getByRole("button", { name: "确认并生成一次候选" }));
    expect(await screen.findByText(/确认期间规划、设定分配或章节版本已变化/)).toBeInTheDocument();
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(api.executeGenerationAttempt).not.toHaveBeenCalled();
  });

  it("discards a late capability response after the chapter context changes", async () => {
    let resolveCapability!: (value: GenerationCapabilityResponse) => void;
    const capabilityRequest = new Promise<GenerationCapabilityResponse>((resolve) => { resolveCapability = resolve; });
    const api = renderPage({ getGenerationCapability: vi.fn().mockReturnValue(capabilityRequest) });
    await userEvent.click(await screen.findByRole("button", { name: "检查生成上下文" }));
    await screen.findByText("检查记录已保存");
    await userEvent.click(screen.getByRole("button", { name: "查看模型信息并确认生成" }));
    await userEvent.type(screen.getByLabelText("章节摘要"), "能力读取期间发生变化");
    resolveCapability(capability);
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    expect(api.executeGenerationAttempt).not.toHaveBeenCalled();
    expect(loadPendingGenerationExecution(userId, projectId).status).toBe("missing");
  });

  it("rechecks capability and requires a second billing confirmation before exact original-payload retry", async () => {
    const exactMissing = new ApiError(404, {
      detail: "not found",
      code: "GENERATION_ATTEMPT_NOT_FOUND",
      retryable: true,
      recommended_action: "retry_original_execute",
    });
    const executeGenerationAttempt = vi.fn()
      .mockRejectedValueOnce(new Error("network unknown"))
      .mockImplementation((_project: string, _run: string, input: { operation_key: string }) => Promise.resolve(executionAttempt(executionOperation(input.operation_key))));
    const getGenerationAttemptByKey = vi.fn().mockRejectedValue(exactMissing);
    const api = renderPage({ executeGenerationAttempt, getGenerationAttemptByKey });
    await userEvent.click(await screen.findByRole("button", { name: "检查生成上下文" }));
    await screen.findByText("检查记录已保存");
    await userEvent.click(screen.getByRole("button", { name: "查看模型信息并确认生成" }));
    await userEvent.click(await screen.findByRole("button", { name: "确认并生成一次候选" }));
    expect(await screen.findByRole("button", { name: "使用原编号和原载荷重试" })).toBeInTheDocument();
    expect(executeGenerationAttempt).toHaveBeenCalledTimes(1);
    expect(getGenerationAttemptByKey).toHaveBeenCalledTimes(1);
    const originalPayload = executeGenerationAttempt.mock.calls[0][2];
    await userEvent.click(screen.getByRole("button", { name: "使用原编号和原载荷重试" }));
    expect(await screen.findByRole("alertdialog", { name: "确认调用模型生成候选" })).toHaveTextContent(/原操作编号和原载荷/);
    expect(executeGenerationAttempt).toHaveBeenCalledTimes(1);
    expect(api.getGenerationCapability).toHaveBeenCalledTimes(2);
    await userEvent.click(screen.getByRole("button", { name: "确认并使用原编号重试" }));
    await waitFor(() => expect(executeGenerationAttempt).toHaveBeenCalledTimes(2));
    expect(executeGenerationAttempt.mock.calls[1][2]).toEqual(originalPayload);
  });

  it("sends zero retry POSTs when the capability checksum drifts and requires a new preflight", async () => {
    const exactMissing = new ApiError(404, {
      detail: "not found", code: "GENERATION_ATTEMPT_NOT_FOUND", retryable: true,
      recommended_action: "retry_original_execute",
    });
    const driftedCapability = { ...capability, capability_checksum: "d".repeat(64) };
    const executeGenerationAttempt = vi.fn().mockRejectedValue(new Error("network unknown"));
    const api = renderPage({
      executeGenerationAttempt,
      getGenerationAttemptByKey: vi.fn().mockRejectedValue(exactMissing),
      getGenerationCapability: vi.fn().mockResolvedValueOnce(capability).mockResolvedValueOnce(driftedCapability),
    });
    await userEvent.click(await screen.findByRole("button", { name: "检查生成上下文" }));
    await screen.findByText("检查记录已保存");
    await userEvent.click(screen.getByRole("button", { name: "查看模型信息并确认生成" }));
    await userEvent.click(await screen.findByRole("button", { name: "确认并生成一次候选" }));
    await userEvent.click(await screen.findByRole("button", { name: "使用原编号和原载荷重试" }));
    expect(await screen.findByText(/模型能力信息已变化/)).toBeInTheDocument();
    expect(executeGenerationAttempt).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(loadPendingGenerationExecution(userId, projectId).status).toBe("missing");
    expect(screen.getByRole("button", { name: "再次检查当前上下文" })).toBeEnabled();
    expect(api.getGenerationCapability).toHaveBeenCalledTimes(2);
  });

  it("keeps an unknown outcome GET-only and shows the server-safe error", async () => {
    const executeGenerationAttempt = vi.fn().mockRejectedValue(new Error("network unknown"));
    const getGenerationAttemptByKey = vi.fn().mockImplementation((_project: string, operationKey: string) => Promise.resolve(executionAttempt(executionOperation(operationKey), "outcome_unknown")));
    renderPage({ executeGenerationAttempt, getGenerationAttemptByKey });
    await userEvent.click(await screen.findByRole("button", { name: "检查生成上下文" }));
    await screen.findByText("检查记录已保存");
    await userEvent.click(screen.getByRole("button", { name: "查看模型信息并确认生成" }));
    await userEvent.click(await screen.findByRole("button", { name: "确认并生成一次候选" }));
    expect(await screen.findByText("服务商返回安全失败说明")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /原载荷重试/ })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "按原编号核对状态" }));
    expect(executeGenerationAttempt).toHaveBeenCalledTimes(1);
    expect(getGenerationAttemptByKey).toHaveBeenCalledTimes(2);
  });

  it("reads and validates a succeeded candidate, writes URL pointers, then compare-clears pending", async () => {
    const operation = executionOperation();
    const succeeded = { ...executionAttempt(operation, "succeeded"), ai_invoked: true, billing_effect: "possible" as const };
    const candidate = await candidateResponse(operation, succeeded.id);
    const api = renderPage({
      executeGenerationAttempt: vi.fn().mockImplementation((_project: string, _run: string, input: { operation_key: string }) => Promise.resolve({
        ...succeeded,
        operation_key: input.operation_key,
      })),
      getGenerationCandidate: vi.fn().mockResolvedValue(candidate),
    });
    await userEvent.click(await screen.findByRole("button", { name: "检查生成上下文" }));
    await screen.findByText("检查记录已保存");
    await userEvent.click(screen.getByRole("button", { name: "查看模型信息并确认生成" }));
    await userEvent.click(await screen.findByRole("button", { name: "确认并生成一次候选" }));
    expect(await screen.findByText(candidate.content)).toBeInTheDocument();
    expect(screen.getByTestId("location")).toHaveTextContent(`generation_attempt=${succeeded.id}`);
    expect(screen.getByTestId("location")).toHaveTextContent(`generation_candidate=${candidate.id}`);
    expect(sessionStorage.length).toBe(0);
    expect(api.executeGenerationAttempt).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "关闭这条记录" })).toBeEnabled();
  });

  it("keeps a failed pending receipt until a replacement is explicitly confirmed", async () => {
    const operation = executionOperation("generation:execute:failed-old");
    expect(savePendingGenerationExecution(operation)).toBe(true);
    const failed = executionAttempt(operation, "failed");
    const getGenerationCapability = vi.fn().mockRejectedValue(new Error("capability offline"));
    const api = renderPage({
      getGenerationRun: vi.fn().mockResolvedValue(response({ operation_key: "planning:generation_prepare:failed-old", expected_structure_version: 3, expected_assignment_version: 2, expected_chapter_lock_version: 4 })),
      getGenerationAttemptByKey: vi.fn().mockResolvedValue(failed),
      getGenerationCapability,
    });
    const startNew = await screen.findByRole("button", { name: "重新获取模型信息并确认新尝试" });
    await userEvent.click(startNew);
    expect(await screen.findByText("capability offline")).toBeInTheDocument();
    expect(loadPendingGenerationExecution(userId, projectId)).toEqual({ status: "available", operation });
    expect(api.executeGenerationAttempt).not.toHaveBeenCalled();
  });

  it("replaces a failed receipt only when the author confirms a fresh capability and new key", async () => {
    const oldOperation = executionOperation("generation:execute:failed-old-confirmed");
    expect(savePendingGenerationExecution(oldOperation)).toBe(true);
    const failed = executionAttempt(oldOperation, "failed");
    const api = renderPage({
      getGenerationRun: vi.fn().mockResolvedValue(response({ operation_key: "planning:generation_prepare:failed-old", expected_structure_version: 3, expected_assignment_version: 2, expected_chapter_lock_version: 4 })),
      getGenerationAttemptByKey: vi.fn().mockResolvedValue(failed),
    });
    await userEvent.click(await screen.findByRole("button", { name: "重新获取模型信息并确认新尝试" }));
    expect(await screen.findByRole("alertdialog", { name: "确认调用模型生成候选" })).toBeInTheDocument();
    expect(loadPendingGenerationExecution(userId, projectId)).toEqual({ status: "available", operation: oldOperation });
    await userEvent.click(screen.getByRole("button", { name: "确认并生成一次候选" }));
    await waitFor(() => expect(api.executeGenerationAttempt).toHaveBeenCalledTimes(1));
    const nextPayload = api.executeGenerationAttempt.mock.calls[0][2] as { operation_key: string };
    expect(nextPayload.operation_key).not.toBe(oldOperation.operation_key);
    expect(loadPendingGenerationExecution(userId, projectId)).toEqual(expect.objectContaining({
      status: "available",
      operation: expect.objectContaining({ operation_key: nextPayload.operation_key }),
    }));
  });

  it("restores URL candidate pointers with reads only and never treats them as POST authority", async () => {
    const operation = executionOperation();
    const attempt = executionAttempt(operation, "succeeded");
    const candidate = await candidateResponse(operation, attempt.id);
    const api = renderPage({
      getGenerationRun: vi.fn().mockResolvedValue(response({ operation_key: "planning:generation_prepare:url-read", expected_structure_version: 3, expected_assignment_version: 2, expected_chapter_lock_version: 4 })),
      getGenerationCandidate: vi.fn().mockResolvedValue(candidate),
    }, `/project/${projectId}/plan/chapters?scope=chapter&target=${chapterId}&generation_run=${operation.run_id}&generation_attempt=${attempt.id}&generation_candidate=${candidate.id}`);
    expect(await screen.findByText(candidate.content)).toBeInTheDocument();
    expect(api.getGenerationRun).toHaveBeenCalledTimes(1);
    expect(api.getGenerationCandidate).toHaveBeenCalledTimes(1);
    expect(api.executeGenerationAttempt).not.toHaveBeenCalled();
    expect(api.getGenerationAttemptByKey).not.toHaveBeenCalled();
  });

  it("retains pending after candidate read failure and retries only the candidate GET", async () => {
    const operation = executionOperation();
    const succeeded = executionAttempt(operation, "succeeded");
    const candidate = await candidateResponse(operation, succeeded.id);
    const getGenerationCandidate = vi.fn()
      .mockRejectedValueOnce(new Error("candidate offline"))
      .mockResolvedValue(candidate);
    const executeGenerationAttempt = vi.fn().mockImplementation((_project: string, _run: string, input: { operation_key: string }) => Promise.resolve({ ...succeeded, operation_key: input.operation_key }));
    renderPage({ executeGenerationAttempt, getGenerationCandidate });
    await userEvent.click(await screen.findByRole("button", { name: "检查生成上下文" }));
    await screen.findByText("检查记录已保存");
    await userEvent.click(screen.getByRole("button", { name: "查看模型信息并确认生成" }));
    await userEvent.click(await screen.findByRole("button", { name: "确认并生成一次候选" }));
    expect(await screen.findByText(/candidate offline/)).toBeInTheDocument();
    expect(loadPendingGenerationExecution(userId, projectId).status).toBe("available");
    await userEvent.click(screen.getByRole("button", { name: "重新读取生成候选" }));
    expect(await screen.findByText(candidate.content)).toBeInTheDocument();
    expect(getGenerationCandidate).toHaveBeenCalledTimes(2);
    expect(executeGenerationAttempt).toHaveBeenCalledTimes(1);
    expect(loadPendingGenerationExecution(userId, projectId).status).toBe("missing");
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
    savePendingPlanningOperation({ schema_version: 1, user_id: userId, project_id: projectId, operation_key: payload.operation_key, action: "generation_prepare", target_id: missingChapterId, payload, created_at: now });
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
    savePendingPlanningOperation({ schema_version: 1, user_id: userId, project_id: projectId, operation_key: payload.operation_key, action: "generation_prepare", target_id: chapterId, payload, created_at: now });
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
