import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { ApiError } from "@/services/api";
import { pendingProjectOperationKey } from "@/services/pendingProjectOperations";
import type { GenerationCandidateAuditResponse, GenerationRunResponse } from "@/types/generation";
import type { TechnicalDemoCandidateResponse, TechnicalDemoCapabilityResponse, TechnicalDemoExecutionResponse } from "@/types/demo";

const mocks = vi.hoisted(() => ({
  capability: vi.fn(), execute: vi.fn(), byKey: vi.fn(), candidate: vi.fn(), audit: vi.fn(), save: vi.fn(), clear: vi.fn(),
}));
vi.mock("@/services/technicalDemoExecution", async (importOriginal) => {
  const original = await importOriginal<typeof import("@/services/technicalDemoExecution")>();
  return { ...original,
    readTechnicalDemoCapability: mocks.capability,
    requestTechnicalDemoExecution: mocks.execute,
    readTechnicalDemoExecutionByKey: mocks.byKey,
    readTechnicalDemoCandidate: mocks.candidate,
    savePendingTechnicalDemoExecution: (operation: Parameters<typeof original.savePendingTechnicalDemoExecution>[0]) => mocks.save(operation, original.savePendingTechnicalDemoExecution),
  };
});
vi.mock("@/services/generationExecution", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/services/generationExecution")>()),
  readGenerationCandidateAudit: mocks.audit,
}));
vi.mock("@/services/pendingProjectOperations", async (importOriginal) => {
  const original = await importOriginal<typeof import("@/services/pendingProjectOperations")>();
  return { ...original, clearPendingProjectOperationRecord: (user: string, project: string) => mocks.clear(user, project, original.clearPendingProjectOperationRecord) };
});

import TechnicalDemoExecution from "./TechnicalDemoExecution";

const id = (value: string) => value.padEnd(32, value).slice(0, 32);
const userId = id("user"), projectId = id("project"), planId = id("plan"), partId = id("part"), chapterId = id("chapter"), runId = id("run");
const now = "2026-08-13T08:00:00Z";
const run: GenerationRunResponse = {
  id: runId, project_id: projectId, plan_id: planId, planning_chapter_id: chapterId, operation_key: "planning:generation_prepare:12345678", replayed: false, status: "prepared", execution_mode: "preflight_only", ai_invoked: false, billing_effect: "none", structure_version: 1, assignment_version: 1, chapter_lock_version: 1, context_schema_version: 1, context_checksum: "a".repeat(64), context_size_bytes: 100, created_at: now, updated_at: now,
  context_manifest: { schema_version: 1, project_id: projectId, plan_id: planId, versions: { structure: 1, assignment: 1, chapter_lock: 1 }, part: { id: partId, title: "第一篇", description: "", position: 1, lock_version: 1 }, chapter: { id: chapterId, title: "第一章", summary: "", target_word_count: null, position: 1, lock_version: 1 }, elements: [], relations: [], warnings: [], foreshadow_actions: { supported: false, items: [] }, counts: { elements: 0, relations: 0, warnings: 0 } },
};
const capability: TechnicalDemoCapabilityResponse = { schema_version: 1, execution_mode: "technical_demo", fixture_version: 1, adapter_schema_version: 1, content_spec_version: 1, project_id: projectId, planning_chapter_id: chapterId, run_id: runId, context_checksum: run.context_checksum, fixed_response: true, ai_invoked: false, billing_effect: "none", usage_status: "not_applicable", capability_checksum: "b".repeat(64) };
const execution: TechnicalDemoExecutionResponse = { schema_version: 1, execution_mode: "technical_demo", fixture_version: 1, adapter_schema_version: 1, content_spec_version: 1, project_id: projectId, planning_chapter_id: chapterId, run_id: runId, operation_key: "technical-demo:execute:placeholder", context_checksum: run.context_checksum, capability_checksum: capability.capability_checksum, execution_id: id("execution"), candidate_id: id("candidate"), status: "succeeded", replayed: false, ai_invoked: false, billing_effect: "none", usage_status: "not_applicable", created_at: now, completed_at: now };
const candidate: TechnicalDemoCandidateResponse = { schema_version: 1, id: execution.candidate_id, project_id: projectId, run_id: runId, planning_chapter_id: chapterId, source_technical_demo_execution_id: execution.execution_id, parent_candidate_id: null, version_no: 1, origin_kind: "technical_demo", title: "第一章", content: "固定技术模拟候选正文", content_format: "plain_text", content_checksum: "c".repeat(64), content_size_bytes: 30, word_count: 10, created_by: userId, ai_invoked: false, billing_effect: "none", usage_status: "not_applicable", created_at: now };
const audit: GenerationCandidateAuditResponse = { schema_version: 1, ruleset_version: 1, project_id: projectId, run_id: runId, planning_chapter_id: chapterId, candidate_id: candidate.id, candidate_version: 1, candidate_checksum: candidate.content_checksum, context_checksum: run.context_checksum, status: "pass", integrity: { status: "pass", content_size_bytes: 30, word_count: 10, storage_limit_bytes: 262144, storage_limit_reached: false }, target_length: { status: "not_applicable", actual_word_count: 10, target_word_count: null, minimum_word_count: null, maximum_word_count: null }, preparation: { status: "pass", warnings: [] }, unrecognized_explicit_terms: { status: "pass", items: [], truncated: false }, context_summary: { element_count: 0, relation_count: 0, warning_count: 0, elements: [], foreshadow_actions_supported: false, foreshadow_action_count: 0 } };

function renderComponent(entry = "/") {
  const onLockChange = vi.fn();
  const rendered = render(<MemoryRouter initialEntries={[entry]}><TechnicalDemoExecution userId={userId} projectId={projectId} chapterId={chapterId} chapterTitle="第一章" run={run} onLockChange={onLockChange} /></MemoryRouter>);
  return { onLockChange, ...rendered };
}

describe("TechnicalDemoExecution", () => {
  beforeEach(() => {
    sessionStorage.clear(); vi.restoreAllMocks(); Object.values(mocks).forEach((mock) => mock.mockReset());
    mocks.capability.mockResolvedValue(capability);
    mocks.save.mockImplementation((operation, original) => original(operation));
    mocks.clear.mockImplementation((user, project, original) => original(user, project));
    mocks.execute.mockImplementation(async (_identity, payload) => ({ ...execution, operation_key: payload.operation_key }));
    mocks.candidate.mockResolvedValue(candidate); mocks.audit.mockResolvedValue(audit);
  });

  it("persists v4 before a single POST, then reads candidate/audit and clears pending", async () => {
    let persistedAtPost = false;
    mocks.execute.mockImplementation(async (_identity, payload) => {
      persistedAtPost = Array.from({ length: sessionStorage.length }, (_, index) => sessionStorage.key(index)).some((key) => key?.includes(projectId));
      return { ...execution, operation_key: payload.operation_key };
    });
    const { onLockChange } = renderComponent();
    await userEvent.click(screen.getByRole("button", { name: "查看边界并确认技术模拟" }));
    const dialog = screen.getByRole("alertdialog", { name: "确认运行固定技术模拟" });
    expect(dialog).toHaveTextContent("不调用 AI");
    await waitFor(() => expect(screen.getByRole("button", { name: "取消，不执行" })).toHaveFocus());
    await userEvent.click(screen.getByRole("button", { name: "确认运行技术模拟" }));
    expect(await screen.findByRole("heading", { name: /固定技术模拟候选已就绪/ })).toHaveFocus();
    expect(persistedAtPost).toBe(true);
    expect(mocks.execute).toHaveBeenCalledTimes(1);
    expect(mocks.candidate).toHaveBeenCalledTimes(1);
    expect(mocks.audit).toHaveBeenCalledTimes(1);
    expect(screen.getByLabelText("固定技术模拟候选正文")).toHaveAttribute("tabindex", "0");
    expect(screen.getByText(/伏笔动作为 0/)).toBeInTheDocument();
    expect(onLockChange).toHaveBeenCalledWith(true);
    expect(onLockChange).toHaveBeenCalledWith(false);
  });

  it("makes a storage failure a zero-POST terminal error", async () => {
    mocks.save.mockReturnValue(false);
    renderComponent();
    await userEvent.click(screen.getByRole("button", { name: "查看边界并确认技术模拟" }));
    await userEvent.click(await screen.findByRole("button", { name: "确认运行技术模拟" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("无法在浏览器保存恢复线索");
    expect(mocks.execute).not.toHaveBeenCalled();
  });

  it("requires capability GET and a second alertdialog before exact-not-found original retry", async () => {
    mocks.execute.mockRejectedValueOnce(new Error("network lost")).mockImplementationOnce(async (_identity, payload) => ({ ...execution, operation_key: payload.operation_key }));
    mocks.byKey.mockRejectedValue(new ApiError(404, { detail: "not found", code: "TECHNICAL_DEMO_EXECUTION_NOT_FOUND", retryable: true, recommended_action: "retry_original_technical_demo" }));
    renderComponent();
    await userEvent.click(screen.getByRole("button", { name: "查看边界并确认技术模拟" }));
    await userEvent.click(await screen.findByRole("button", { name: "确认运行技术模拟" }));
    const originalTrigger = await screen.findByRole("button", { name: "重新核对并确认原请求" });
    await userEvent.click(originalTrigger);
    expect(mocks.byKey).toHaveBeenCalledTimes(1);
    expect(await screen.findByRole("alertdialog")).toHaveTextContent("原编号和原载荷");
    expect(mocks.execute).toHaveBeenCalledTimes(1);
    expect(mocks.capability).toHaveBeenCalledTimes(2);
    await userEvent.click(screen.getByRole("button", { name: "取消，不执行" }));
    await waitFor(() => expect(originalTrigger).toHaveFocus());
    await userEvent.click(originalTrigger);
    await userEvent.click(screen.getByRole("button", { name: "确认原请求重试" }));
    await screen.findByRole("heading", { name: /固定技术模拟候选已就绪/ });
    expect(mocks.execute).toHaveBeenCalledTimes(2);
  });

  it("treats URL pointers as read-only and never POSTs", async () => {
    renderComponent(`/?technical_demo_run=${runId}&technical_demo_execution=${execution.execution_id}&technical_demo_candidate=${candidate.id}`);
    await screen.findByRole("heading", { name: /固定技术模拟候选已就绪/ });
    expect(mocks.candidate).toHaveBeenCalledTimes(1);
    expect(mocks.execute).not.toHaveBeenCalled();
    expect(mocks.capability).not.toHaveBeenCalled();
  });

  it("closes the confirmation with Escape without a POST", async () => {
    renderComponent();
    const trigger = screen.getByRole("button", { name: "查看边界并确认技术模拟" });
    await userEvent.click(trigger);
    await screen.findByRole("alertdialog");
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    await waitFor(() => expect(trigger).toHaveFocus());
    expect(mocks.execute).not.toHaveBeenCalled();
  });

  it("clears a corrupt shared slot only after explicit confirmation and stays locked when clear fails", async () => {
    sessionStorage.setItem(pendingProjectOperationKey(userId, projectId), "{broken");
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true).mockReturnValueOnce(false);
    const { onLockChange } = renderComponent();
    const clearButton = await screen.findByRole("button", { name: "确认清除损坏恢复记录" });
    await userEvent.click(clearButton);
    expect(sessionStorage.getItem(pendingProjectOperationKey(userId, projectId))).toBe("{broken");
    expect(mocks.clear).not.toHaveBeenCalled();
    mocks.clear.mockReturnValueOnce(false);
    await userEvent.click(clearButton);
    expect(confirmSpy).toHaveBeenCalledTimes(2);
    expect(await screen.findByRole("alert")).toHaveTextContent("继续锁定新执行");
    expect(onLockChange).not.toHaveBeenCalledWith(false);
    mocks.clear.mockImplementation((user, project, original) => original(user, project));
    await userEvent.click(clearButton);
    expect(sessionStorage.getItem(pendingProjectOperationKey(userId, projectId))).toBeNull();
    expect(onLockChange).toHaveBeenCalledWith(false);
  });

  it("ignores a deferred by-key response after unmount without URL writes, clear, or unlock", async () => {
    const operationKey = "technical-demo:execute:deferred";
    sessionStorage.setItem(pendingProjectOperationKey(userId, projectId), JSON.stringify({ schema_version: 4, workspace: "technical_demo_execution", user_id: userId, project_id: projectId, chapter_id: chapterId, run_id: runId, operation_key: operationKey, payload: { operation_key: operationKey, expected_context_checksum: run.context_checksum, expected_capability_checksum: capability.capability_checksum, fixture_version: 1, confirm_technical_demo: true }, created_at: now }));
    let resolveByKey!: (value: TechnicalDemoExecutionResponse) => void;
    mocks.byKey.mockReturnValue(new Promise((resolve) => { resolveByKey = resolve; }));
    const { unmount, onLockChange } = renderComponent();
    await waitFor(() => expect(mocks.byKey).toHaveBeenCalledOnce());
    unmount();
    resolveByKey({ ...execution, operation_key: operationKey });
    await Promise.resolve(); await Promise.resolve();
    expect(mocks.candidate).not.toHaveBeenCalled();
    expect(sessionStorage.getItem(pendingProjectOperationKey(userId, projectId))).not.toBeNull();
    expect(onLockChange).not.toHaveBeenCalledWith(false);
  });

  it("uses a new key and new confirmation after an exact adapter 503", async () => {
    mocks.execute.mockRejectedValueOnce(new ApiError(503, { detail: "adapter unavailable", code: "TECHNICAL_DEMO_ADAPTER_UNAVAILABLE", retryable: true, recommended_action: "start_new_confirmed_technical_demo" }))
      .mockImplementationOnce(async (_identity, payload) => ({ ...execution, operation_key: payload.operation_key }));
    renderComponent();
    await userEvent.click(screen.getByRole("button", { name: "查看边界并确认技术模拟" }));
    await userEvent.click(await screen.findByRole("button", { name: "确认运行技术模拟" }));
    const newTrigger = await screen.findByRole("button", { name: "使用新编号重新确认" });
    await userEvent.click(newTrigger);
    expect(await screen.findByRole("alertdialog")).toBeInTheDocument();
    expect(mocks.execute).toHaveBeenCalledTimes(1);
    await userEvent.click(screen.getByRole("button", { name: "取消，不执行" }));
    await waitFor(() => expect(newTrigger).toHaveFocus());
    await userEvent.click(newTrigger);
    await userEvent.click(screen.getByRole("button", { name: "确认运行技术模拟" }));
    await screen.findByRole("heading", { name: /固定技术模拟候选已就绪/ });
    const firstKey = mocks.execute.mock.calls[0][1].operation_key;
    const secondKey = mocks.execute.mock.calls[1][1].operation_key;
    expect(secondKey).not.toBe(firstKey);
  });

  it("retries only candidate/audit GETs and exposes deterministic review evidence", async () => {
    const reviewAudit = { ...audit, status: "review" as const, preparation: { status: "review" as const, warnings: [{ code: "CHAPTER_SUMMARY_EMPTY" as const, element_id: null }] }, unrecognized_explicit_terms: { status: "review" as const, items: [{ term: "无名星门", excerpt: "提到《无名星门》。", start_offset: 2, end_offset: 8 }], truncated: false }, context_summary: { ...audit.context_summary, element_count: 1, elements: [{ element_id: id("element"), type_key: "location", type_display_name: "地点", name: "星门", version_no: 1 }], warning_count: 1 } };
    mocks.candidate.mockRejectedValueOnce(new Error("candidate read failed")).mockResolvedValueOnce(candidate);
    mocks.audit.mockRejectedValueOnce(new Error("audit read failed")).mockResolvedValueOnce(reviewAudit);
    renderComponent();
    await userEvent.click(screen.getByRole("button", { name: "查看边界并确认技术模拟" }));
    await userEvent.click(await screen.findByRole("button", { name: "确认运行技术模拟" }));
    await userEvent.click(await screen.findByRole("button", { name: "重新读取固定候选" }));
    await screen.findByRole("heading", { name: /固定技术模拟候选已就绪/ });
    await userEvent.click(await screen.findByRole("button", { name: "重新读取审计" }));
    expect(await screen.findByText("《无名星门》")).toBeInTheDocument();
    expect(screen.getByText(/本章摘要为空/)).toBeInTheDocument();
    expect(screen.getByText(/不判断情节、人物或世界规则的语义一致性/)).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/一致性通过|AI.*验证|违规设定|事实冲突/);
    expect(mocks.execute).toHaveBeenCalledTimes(1);
    expect(mocks.candidate).toHaveBeenCalledTimes(2);
    expect(mocks.audit).toHaveBeenCalledTimes(2);
  });
});
