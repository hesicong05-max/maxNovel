import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useNavigate } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as apiModule from "@/services/api";
import { ApiError } from "@/services/api";
import { savePendingPlanningOperation } from "@/services/planningOperations";
import { savePendingForeshadowOperation } from "@/services/foreshadowOperations";
import type { ForeshadowLifecycle, ForeshadowMutationReceipt } from "@/types/foreshadow";
import type { NovelPlan } from "@/types/planning";
import ForeshadowPlanningPage from "./ForeshadowPlanningPage";

const authState = vi.hoisted(() => ({ userId: "user-1" }));
vi.mock("@/components/AuthContext", () => ({ useAuth: () => ({ user: { id: authState.userId } }) }));

const id = (value: string) => value.padEnd(32, value).slice(0, 32);
const projectId = id("project");
const planId = id("plan");
const partId = id("part");
const chapterOne = id("chapter-one");
const chapterTwo = id("chapter-two");
const lifecycleId = id("lifecycle");
const now = "2026-08-11T08:00:00Z";
const otherProjectId = id("other-project");

function ProjectSwitcher() {
  const navigate = useNavigate();
  return <button onClick={() => navigate(`/project/${otherProjectId}/plan/foreshadows`)}>切换测试项目</button>;
}

const plan: NovelPlan = {
  id: planId, project_id: projectId, status: "active", structure_version: 3, assignment_version: 2, created_at: now, updated_at: now,
  parts: [{ id: partId, project_id: projectId, plan_id: planId, title: "第一篇", description: "", position: 1, status: "active", lock_version: 2, created_at: now, updated_at: now, chapters: [
    { id: chapterOne, project_id: projectId, plan_id: planId, part_id: partId, title: "第一章", summary: "", target_word_count: null, position: 1, status: "active", lock_version: 4, created_at: now, updated_at: now },
    { id: chapterTwo, project_id: projectId, plan_id: planId, part_id: partId, title: "第二章", summary: "", target_word_count: null, position: 2, status: "active", lock_version: 5, created_at: now, updated_at: now },
  ] }],
};

function lifecycle(overrides: Partial<ForeshadowLifecycle> = {}): ForeshadowLifecycle {
  return {
    id: lifecycleId, project_id: projectId, plan_id: planId, status: "active", state: "unplanted", lock_version: 1,
    element: { id: id("element"), name: "黑羽", summary: "雨夜留下的羽毛", confirmation_status: "confirmed", lifecycle_status: "active", enabled: true, content_version: 1, lock_version: 2 },
    plans: [], facts: [], created_at: now, updated_at: now, ...overrides,
  };
}

function list(items = [lifecycle()], next: string | null = null) {
  return { items, counts: { unplanted: 1, planted: 0, pending_resolution: 0, resolved: 0 }, next_cursor: next };
}

function receipt(operationKey: string, operationType: ForeshadowMutationReceipt["operation_type"], value = lifecycle({ lock_version: 2 })): ForeshadowMutationReceipt {
  return { receipt_id: id("receipt"), operation_key: operationKey, operation_type: operationType, replayed: false, project_id: projectId, lifecycle_id: value.id, previous_lifecycle_version: value.lock_version - 1, new_lifecycle_version: value.lock_version, event_id: id("event"), lifecycle: value, created_at: now };
}

function renderPage(overrides: Record<string, unknown> = {}) {
  const mocked = {
    ...apiModule.api,
    getPlanning: vi.fn().mockResolvedValue(plan),
    listForeshadows: vi.fn().mockResolvedValue(list()),
    getForeshadow: vi.fn().mockResolvedValue(lifecycle()),
    getForeshadowHistory: vi.fn().mockResolvedValue({ lifecycle_id: lifecycleId, items: [] }),
    getForeshadowOperationByKey: vi.fn().mockRejectedValue(new ApiError(404, { detail: "not found", code: "FORESHADOW_OPERATION_NOT_FOUND", retryable: true, recommended_action: "retry_original_operation" })),
    listLoreElements: vi.fn().mockResolvedValue({ items: [], next_cursor: null, has_more: false, total: 0, facets: {}, migration_status: "relational" }),
    ...overrides,
  };
  vi.spyOn(apiModule, "api", "get").mockReturnValue(mocked as typeof apiModule.api);
  render(<MemoryRouter initialEntries={[`/project/${projectId}/plan/foreshadows`]}><Routes><Route path="/project/:id/plan/foreshadows" element={<><ProjectSwitcher /><ForeshadowPlanningPage /></>} /></Routes></MemoryRouter>);
  return mocked;
}

describe("ForeshadowPlanningPage", () => {
  beforeEach(() => { vi.restoreAllMocks(); sessionStorage.clear(); authState.userId = "user-1"; });

  it("shows the four authoritative states and keeps plans separate from author facts", async () => {
    renderPage();
    expect(await screen.findByRole("heading", { name: "伏笔管理" })).toBeInTheDocument();
    expect(screen.getByText(/计划表示作者未来安排，不代表正文已经发生/)).toBeInTheDocument();
    expect(screen.getByText(/系统尚未核对、生成或修改正文/)).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "未来计划" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "作者确认事实" })).toBeInTheDocument();
    for (const label of ["未埋入", "已埋入", "待回收", "已回收"]) expect(screen.getAllByText(label).length).toBeGreaterThan(0);
  });

  it("uses server state filters and an explicit cursor load-more", async () => {
    const second = lifecycle({ id: id("lifecycle-two"), element: { ...lifecycle().element, id: id("element-two"), name: "旧钥匙" } });
    const listForeshadows = vi.fn()
      .mockResolvedValueOnce(list([lifecycle()], lifecycleId))
      .mockResolvedValueOnce({ items: [second], counts: list().counts, next_cursor: null });
    renderPage({ listForeshadows });
    await screen.findByText("黑羽");
    await userEvent.click(screen.getByRole("button", { name: "加载更多" }));
    expect(await screen.findByText("旧钥匙")).toBeInTheDocument();
    expect(listForeshadows).toHaveBeenLastCalledWith(projectId, expect.objectContaining({ after: lifecycleId, limit: 25 }));
    await userEvent.click(screen.getAllByRole("button", { name: /待回收/ })[0]);
    await waitFor(() => expect(listForeshadows).toHaveBeenLastCalledWith(projectId, expect.objectContaining({ status: "active", state: "pending_resolution" })));
  });

  it("creates a resolve plan only with an explicit target and condition", async () => {
    const createForeshadowPlan = vi.fn((_project: string, _lifecycle: string, input: { operation_key: string }) => Promise.resolve(receipt(input.operation_key, "foreshadow_plan_create")));
    renderPage({ createForeshadowPlan });
    await screen.findByRole("heading", { name: "未来计划" });
    await userEvent.selectOptions(screen.getByLabelText("计划类型"), "resolve");
    const save = screen.getByRole("button", { name: "保存未来计划" });
    expect(save).toBeDisabled();
    await userEvent.selectOptions(screen.getByLabelText("目标位置"), chapterTwo);
    expect(save).toBeDisabled();
    await userEvent.type(screen.getByLabelText("回收条件"), "主角发现真相");
    await userEvent.click(save);
    await waitFor(() => expect(createForeshadowPlan).toHaveBeenCalledWith(projectId, lifecycleId, expect.objectContaining({ expected_lifecycle_version: 1, expected_structure_version: 3, action_kind: "resolve", target_type: "chapter", target_id: chapterTwo, expected_target_lock_version: 5, condition_text: "主角发现真相" })));
  });

  it("records an author-confirmed fact only after the explicit alert dialog", async () => {
    const recordForeshadowFact = vi.fn((_project: string, _lifecycle: string, input: { operation_key: string }) => Promise.resolve(receipt(input.operation_key, "foreshadow_fact_record", lifecycle({ state: "planted", lock_version: 2 }))));
    renderPage({ recordForeshadowFact });
    await screen.findByRole("heading", { name: "作者确认事实" });
    await userEvent.selectOptions(screen.getByLabelText("实际发生章节"), chapterOne);
    await userEvent.click(screen.getByRole("button", { name: "继续确认" }));
    const dialog = screen.getByRole("alertdialog");
    expect(dialog).toHaveTextContent(/系统尚未核对正文，也不会修改正文/);
    expect(recordForeshadowFact).not.toHaveBeenCalled();
    const cancel = within(dialog).getByRole("button", { name: "取消" });
    const confirm = within(dialog).getByRole("button", { name: "确认并记录" });
    await waitFor(() => expect(within(dialog).getByRole("heading", { name: "确认已经埋入" })).toHaveFocus());
    await userEvent.tab({ shift: true });
    expect(confirm).toHaveFocus();
    await userEvent.tab();
    expect(cancel).toHaveFocus();
    await userEvent.tab({ shift: true });
    expect(confirm).toHaveFocus();
    await userEvent.click(confirm);
    await waitFor(() => expect(recordForeshadowFact).toHaveBeenCalledWith(projectId, lifecycleId, expect.objectContaining({ fact_kind: "planted", chapter_id: chapterOne, expected_chapter_lock_version: 4 })));
  });

  it("closes the confirmation dialog with Escape and returns focus to its trigger", async () => {
    renderPage();
    await screen.findByRole("heading", { name: "作者确认事实" });
    await userEvent.selectOptions(screen.getByLabelText("实际发生章节"), chapterOne);
    const trigger = screen.getByRole("button", { name: "继续确认" });
    await userEvent.click(trigger);
    expect(await screen.findByRole("alertdialog")).toBeInTheDocument();
    await userEvent.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    expect(trigger).toHaveFocus();
  });

  it("recovers an unknown bind by key and only retries the exact request after the typed 404", async () => {
    const lore = { id: id("new-element"), type: { key: "foreshadow", display_name: "伏笔" }, name: "铜铃", summary: "会在夜里响", confirmation_status: "confirmed", lifecycle_status: "active", enabled: true, generation_eligible: true, source_summary: "手动", current_version: 1, revision: 1, lock_version: 3, updated_at: now, relation_count: 0 };
    let attempts = 0;
    const boundLifecycle = lifecycle({ lock_version: 2, element: { ...lifecycle().element, id: lore.id, name: lore.name, summary: lore.summary, lock_version: lore.lock_version } });
    const bindForeshadow = vi.fn((_project: string, input: { operation_key: string }) => {
      attempts += 1;
      return attempts === 1 ? Promise.reject(new Error("network unknown")) : Promise.resolve(receipt(input.operation_key, "foreshadow_bind", boundLifecycle));
    });
    const getForeshadowOperationByKey = vi.fn().mockRejectedValue(new ApiError(404, { detail: "not found", code: "FORESHADOW_OPERATION_NOT_FOUND", retryable: true, recommended_action: "retry_original_operation" }));
    renderPage({ listLoreElements: vi.fn().mockResolvedValue({ items: [lore], next_cursor: null }), bindForeshadow, getForeshadowOperationByKey });
    await screen.findByText("黑羽");
    await userEvent.click(screen.getByRole("button", { name: "加入已有伏笔" }));
    await userEvent.click(await screen.findByRole("button", { name: "加入管理" }));
    expect(await screen.findByRole("button", { name: "使用原编号和内容重试" })).toBeInTheDocument();
    const original = bindForeshadow.mock.calls[0][1];
    await userEvent.click(screen.getByRole("button", { name: "使用原编号和内容重试" }));
    await waitFor(() => expect(bindForeshadow).toHaveBeenCalledTimes(2));
    expect(bindForeshadow.mock.calls[1][1]).toEqual(original);
  });

  it("freezes foreshadow writes when the planning workspace owns the shared pending slot", async () => {
    const payload = { operation_key: "planning:part_create:12345678", expected_structure_version: 3, title: "第二篇", description: "" };
    savePendingPlanningOperation({ schema_version: 1, user_id: "user-1", project_id: projectId, operation_key: payload.operation_key, action: "part_create", target_id: null, payload, created_at: now });
    renderPage();
    expect(await screen.findByText(/章节规划中还有结果未确认的写入/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "加入已有伏笔" })).toBeDisabled();
    expect(screen.getByRole("link", { name: "返回章节规划核对" })).toHaveAttribute("href", `/project/${projectId}/plan/chapters`);
    await userEvent.click(screen.getAllByRole("button", { name: /待回收/ })[0]);
    expect(screen.getByText(/章节规划中还有结果未确认的写入/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "加入已有伏笔" })).toBeDisabled();
  });

  it("distinguishes generation execution pending state and links back without implying a planning write", async () => {
    sessionStorage.setItem(`novel_pending_planning_operation_v1:user-1:${projectId}`, JSON.stringify({
      schema_version: 3,
      workspace: "generation_execution",
      user_id: "user-1",
      project_id: projectId,
      chapter_id: "c".repeat(32),
      run_id: "r".repeat(32),
      operation_key: "generation:execute:pending123",
      payload: {
        operation_key: "generation:execute:pending123",
        expected_context_checksum: "a".repeat(64),
        expected_capability_checksum: "b".repeat(64),
        confirm_model_call: true,
      },
      created_at: now,
    }));
    renderPage();
    expect(await screen.findByText(/生成候选中还有结果未确认的模型调用/)).toBeInTheDocument();
    expect(screen.queryByText(/章节规划中还有结果未确认的写入/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "加入已有伏笔" })).toBeDisabled();
    expect(screen.getByRole("link", { name: "返回发起章节核对生成" })).toHaveAttribute("href", `/project/${projectId}/plan/chapters`);
  });

  it("deep-links a v5 candidate edit recovery to its exact chapter, run, and parent version", async () => {
    authState.userId = id("user");
    const recoveryChapterId = id("recoverychapter");
    const runId = id("run");
    const candidateId = id("candidate");
    sessionStorage.setItem(`novel_pending_planning_operation_v1:${authState.userId}:${projectId}`, JSON.stringify({
      schema_version: 5,
      workspace: "candidate_manual_edit",
      user_id: authState.userId,
      project_id: projectId,
      chapter_id: recoveryChapterId,
      run_id: runId,
      operation_key: "candidate:manual-edit:pending123",
      payload: {
        operation_key: "candidate:manual-edit:pending123",
        parent_candidate_id: candidateId,
        expected_parent_version_no: 2,
        expected_parent_checksum: "a".repeat(64),
        expected_context_checksum: "b".repeat(64),
        content: "沈星修订后的候选正文。",
      },
      created_at: now,
    }));

    renderPage();

    const link = await screen.findByRole("link", { name: "返回原章节核对候选版本" });
    expect(link).toHaveAttribute("href", `/project/${projectId}/plan/chapters?scope=chapter&target=${recoveryChapterId}&generation_run=${runId}&candidate_version=${candidateId}`);
    expect(screen.getByRole("button", { name: "加入已有伏笔" })).toBeDisabled();
  });

  it("protects dirty forms from filter, route, and browser exits", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    const listForeshadows = vi.fn().mockResolvedValue(list());
    renderPage({ listForeshadows });
    await screen.findByRole("heading", { name: "未来计划" });
    await userEvent.type(screen.getByLabelText("计划备注"), "未提交草稿");
    const unload = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(unload);
    expect(unload.defaultPrevented).toBe(true);
    await userEvent.click(screen.getAllByRole("button", { name: /待回收/ })[0]);
    expect(confirm).toHaveBeenCalled();
    expect(screen.getByLabelText("计划备注")).toHaveValue("未提交草稿");
    await userEvent.click(screen.getByRole("link", { name: "打开设定仓库" }));
    expect(confirm).toHaveBeenCalledTimes(2);
    expect(screen.getByRole("heading", { name: "伏笔管理" })).toBeInTheDocument();
  });

  it("loads history lazily and keeps a history error local", async () => {
    const getForeshadowHistory = vi.fn().mockRejectedValue(new Error("history offline"));
    renderPage({ getForeshadowHistory });
    const summary = await screen.findByText("操作历史");
    expect(getForeshadowHistory).not.toHaveBeenCalled();
    await userEvent.click(summary);
    expect(await screen.findByText("history offline")).toBeInTheDocument();
    expect(screen.getAllByText("黑羽").length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "重新加载历史" })).toBeInTheDocument();
  });

  it("discards a Lore search response that arrives after the project changes", async () => {
    let resolveLore!: (value: { items: Array<{ id: string; name: string }>; next_cursor: null }) => void;
    const oldLore = new Promise<{ items: Array<{ id: string; name: string }>; next_cursor: null }>((resolve) => { resolveLore = resolve; });
    const listForeshadows = vi.fn((currentProject: string) => Promise.resolve(currentProject === projectId ? list() : { items: [], counts: { unplanted: 0, planted: 0, pending_resolution: 0, resolved: 0 }, next_cursor: null }));
    renderPage({ listForeshadows, listLoreElements: vi.fn().mockReturnValue(oldLore) });
    await screen.findByText("黑羽");
    await userEvent.click(screen.getByRole("button", { name: "加入已有伏笔" }));
    await userEvent.click(screen.getByRole("button", { name: "切换测试项目" }));
    resolveLore({ items: [{ id: id("old-lore"), name: "旧项目伏笔" }], next_cursor: null });
    await waitFor(() => expect(listForeshadows).toHaveBeenCalledWith(otherProjectId, expect.anything()));
    expect(screen.queryByText("旧项目伏笔")).not.toBeInTheDocument();
  });

  it("discards history from a previously selected lifecycle", async () => {
    const second = lifecycle({ id: id("lifecycle-two"), element: { ...lifecycle().element, id: id("element-two"), name: "旧钥匙" } });
    let resolveHistory!: (value: { lifecycle_id: string; items: Array<Record<string, unknown>> }) => void;
    const oldHistory = new Promise<{ lifecycle_id: string; items: Array<Record<string, unknown>> }>((resolve) => { resolveHistory = resolve; });
    renderPage({
      listForeshadows: vi.fn().mockResolvedValue(list([lifecycle(), second])),
      getForeshadow: vi.fn((_project: string, currentLifecycle: string) => Promise.resolve(currentLifecycle === second.id ? second : lifecycle())),
      getForeshadowHistory: vi.fn().mockReturnValue(oldHistory),
    });
    await screen.findByRole("heading", { name: "未来计划" });
    await userEvent.click(screen.getByText("操作历史"));
    await userEvent.click(screen.getByRole("button", { name: /旧钥匙/ }));
    resolveHistory({ lifecycle_id: lifecycleId, items: [{ id: id("old-event"), event_kind: "create", plan_item_id: null, fact_id: null, previous_lifecycle_version: 0, new_lifecycle_version: 9, metadata: {}, created_at: now }] });
    expect(await screen.findByRole("heading", { name: "旧钥匙" })).toBeInTheDocument();
    expect(screen.queryByText(/生命周期版本 0 → 9/)).not.toBeInTheDocument();
  });

  it("fails closed for a cross-project lifecycle response", async () => {
    renderPage({ listForeshadows: vi.fn().mockResolvedValue(list([lifecycle({ project_id: id("other") })])) });
    expect(await screen.findByRole("alert")).toHaveTextContent(/身份不一致/);
    expect(screen.getByRole("button", { name: "加入已有伏笔" })).toBeDisabled();
  });

  it("keeps correction actions available while an archived lifecycle hides ordinary writes", async () => {
    const archived = lifecycle({
      status: "archived",
      plans: [{ id: id("plan-item"), action_kind: "plant", target: { target_type: "chapter", target_id: chapterOne, title: "第一章", status: "active", part_id: partId, position: 1 }, condition_text: "", note: "", status: "active", lock_version: 1, created_at: now, updated_at: now }],
      facts: [{ id: id("fact"), fact_kind: "planted", chapter: { target_type: "chapter", target_id: chapterOne, title: "第一章", status: "active", part_id: partId, position: 1 }, note: "", status: "active", lock_version: 1, created_at: now, retracted_at: null }],
    });
    renderPage({ listForeshadows: vi.fn().mockResolvedValue(list([archived])), getForeshadow: vi.fn().mockResolvedValue(archived) });
    expect(await screen.findByRole("button", { name: "取消未来计划" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "撤回并保留历史" })).toBeDisabled();
    await userEvent.type(screen.getByLabelText("撤回原因"), "章节记录错误");
    expect(screen.getByRole("button", { name: "撤回并保留历史" })).toBeEnabled();
    expect(screen.queryByLabelText("计划类型")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("确认类型")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "恢复伏笔" })).toBeInTheDocument();
  });

  it("fails closed when a write receipt operation type does not match the original request", async () => {
    const createForeshadowPlan = vi.fn((_project: string, _lifecycle: string, input: { operation_key: string }) => Promise.resolve({ ...receipt(input.operation_key, "foreshadow_plan_create"), operation_type: "foreshadow_fact_record" }));
    renderPage({ createForeshadowPlan });
    await screen.findByRole("heading", { name: "未来计划" });
    await userEvent.selectOptions(screen.getByLabelText("目标位置"), chapterOne);
    await userEvent.click(screen.getByRole("button", { name: "保存未来计划" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/收据损坏|原请求不一致/);
    expect(screen.getByRole("button", { name: "加入已有伏笔" })).toBeDisabled();
  });

  it("keeps a confirmed receipt and freezes writes when the authoritative refresh fails", async () => {
    const getPlanning = vi.fn().mockResolvedValueOnce(plan).mockRejectedValueOnce(new Error("refresh failed"));
    const recordForeshadowFact = vi.fn((_project: string, _lifecycle: string, input: { operation_key: string }) => Promise.resolve(receipt(input.operation_key, "foreshadow_fact_record", lifecycle({ state: "planted", lock_version: 2 }))));
    renderPage({ getPlanning, recordForeshadowFact });
    await screen.findByRole("heading", { name: "作者确认事实" });
    await userEvent.selectOptions(screen.getByLabelText("实际发生章节"), chapterOne);
    await userEvent.click(screen.getByRole("button", { name: "继续确认" }));
    await userEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "确认并记录" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/操作结果已确认/);
    expect(screen.getByRole("button", { name: "加入已有伏笔" })).toBeDisabled();
    expect(recordForeshadowFact).toHaveBeenCalledTimes(1);
  });

  it("recovers a saved foreshadow operation by key without posting again", async () => {
    const operationKey = "foreshadow_archive:recover123";
    const saved = { schema_version: 2 as const, workspace: "foreshadow" as const, user_id: "user-1", project_id: projectId, operation_key: operationKey, operation_type: "foreshadow_archive" as const, lifecycle_id: lifecycleId, resource_id: null, payload: { operation_key: operationKey, expected_lifecycle_version: 1 }, created_at: now };
    expect(savePendingForeshadowOperation(saved)).toBe(true);
    const recovered = { ...receipt(operationKey, "foreshadow_archive"), replayed: true };
    const changeForeshadowState = vi.fn();
    renderPage({ getForeshadowOperationByKey: vi.fn().mockResolvedValue(recovered), changeForeshadowState });
    expect(await screen.findByText(/已找回上次操作结果/)).toBeInTheDocument();
    expect(changeForeshadowState).not.toHaveBeenCalled();
    expect(sessionStorage.length).toBe(0);
  });

  it("keeps the original pending key when the server reports a corrupt or reused operation", async () => {
    const createForeshadowPlan = vi.fn().mockRejectedValue(new ApiError(409, {
      detail: "operation key reused",
      code: "FORESHADOW_OPERATION_KEY_REUSED",
      recommended_action: "contact_support",
    }));
    renderPage({ createForeshadowPlan });
    await screen.findByRole("heading", { name: "未来计划" });
    await userEvent.selectOptions(screen.getByLabelText("目标位置"), chapterOne);
    await userEvent.click(screen.getByRole("button", { name: "保存未来计划" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("operation key reused");
    expect(sessionStorage.length).toBe(1);
    expect(screen.getByRole("button", { name: "加入已有伏笔" })).toBeDisabled();
    expect(createForeshadowPlan).toHaveBeenCalledTimes(1);
  });

  it("classifies a determined conflict from an original-key retry and exits pending recovery", async () => {
    const createForeshadowPlan = vi.fn()
      .mockRejectedValueOnce(new Error("network unknown"))
      .mockRejectedValueOnce(new ApiError(409, { detail: "version changed", code: "FORESHADOW_VERSION_CONFLICT", recommended_action: "refresh_foreshadow" }));
    renderPage({
      createForeshadowPlan,
      getForeshadowOperationByKey: vi.fn().mockRejectedValue(new ApiError(404, { detail: "not found", code: "FORESHADOW_OPERATION_NOT_FOUND", retryable: true, recommended_action: "retry_original_operation" })),
    });
    await screen.findByRole("heading", { name: "未来计划" });
    await userEvent.selectOptions(screen.getByLabelText("目标位置"), chapterOne);
    await userEvent.click(screen.getByRole("button", { name: "保存未来计划" }));
    await userEvent.click(await screen.findByRole("button", { name: "使用原编号和内容重试" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("version changed");
    expect(sessionStorage.length).toBe(0);
    expect(screen.queryByRole("button", { name: "使用原编号和内容重试" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "保存未来计划" })).toBeDisabled();
    expect(createForeshadowPlan).toHaveBeenCalledTimes(2);
  });

  it("accepts a matching bind receipt, refreshes the new lifecycle, and clears the submitted form", async () => {
    const lore = { id: id("new-element"), type: { key: "foreshadow", display_name: "伏笔" }, name: "铜铃", summary: "会在夜里响", confirmation_status: "confirmed", lifecycle_status: "active", enabled: true, generation_eligible: true, source_summary: "手动", current_version: 1, revision: 1, lock_version: 3, updated_at: now, relation_count: 0 };
    const bound = lifecycle({ lock_version: 2, element: { ...lifecycle().element, id: lore.id, name: lore.name, summary: lore.summary, lock_version: lore.lock_version } });
    const bindForeshadow = vi.fn((_project: string, input: { operation_key: string }) => Promise.resolve(receipt(input.operation_key, "foreshadow_bind", bound)));
    renderPage({
      listLoreElements: vi.fn().mockResolvedValue({ items: [lore], next_cursor: null }),
      bindForeshadow,
      getForeshadow: vi.fn().mockResolvedValue(bound),
      listForeshadows: vi.fn().mockResolvedValue(list([bound])),
    });
    await screen.findByText("铜铃");
    await userEvent.click(screen.getByRole("button", { name: "加入已有伏笔" }));
    await userEvent.click(await screen.findByRole("button", { name: "加入管理" }));
    expect(await screen.findByText("伏笔已加入管理。")).toBeInTheDocument();
    expect(screen.queryByText(/操作结果已确认，但最新/)).not.toBeInTheDocument();
    expect(sessionStorage.length).toBe(0);
  });

  it("clears only the successfully submitted plan fields to prevent duplicate submission", async () => {
    const createForeshadowPlan = vi.fn((_project: string, _lifecycle: string, input: { operation_key: string }) => Promise.resolve(receipt(input.operation_key, "foreshadow_plan_create", lifecycle({ lock_version: 2 }))));
    renderPage({ createForeshadowPlan });
    await screen.findByRole("heading", { name: "未来计划" });
    await userEvent.selectOptions(screen.getByLabelText("目标位置"), chapterOne);
    await userEvent.type(screen.getByLabelText("计划备注"), "只提交一次");
    await userEvent.click(screen.getByRole("button", { name: "保存未来计划" }));
    await screen.findByText(/未来计划已保存/);
    expect(screen.getByLabelText("目标位置")).toHaveValue("");
    expect(screen.getByLabelText("计划备注")).toHaveValue("");
    expect(screen.getByRole("button", { name: "保存未来计划" })).toBeDisabled();
    expect(createForeshadowPlan).toHaveBeenCalledTimes(1);
  });
});
