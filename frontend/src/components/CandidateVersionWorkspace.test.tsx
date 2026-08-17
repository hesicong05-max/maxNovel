import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, useSearchParams } from "react-router-dom";
import CandidateVersionWorkspace from "./CandidateVersionWorkspace";
import { ApiError, api } from "@/services/api";
import { pendingProjectOperationKey } from "@/services/pendingProjectOperations";
import type { GenerationRunResponse } from "@/types/generation";
import type { GenerationCandidateAuditResponse, GenerationCandidateVersionDetail } from "@/types/generation";

const id = (seed: string) => seed.padEnd(32, seed).slice(0, 32);
const userId = id("user");
const projectId = id("project");
const chapterId = id("chapter");
const runId = id("run");
const rootId = id("root");
const manualId = id("manual");
const now = "2026-08-13T11:30:00Z";
const rootContent = "沈星站在星门前。";
const editedContent = "沈星站在星门前，记下了新航标。";

function renderWorkspace(onLockChange?: (locked: boolean) => void) {
  function WorkspaceFromPointer() {
    const [params] = useSearchParams();
    return <CandidateVersionWorkspace userId={userId} projectId={projectId} chapterId={chapterId} chapterTitle="第一章" run={run} initialCandidateId={params.get("candidate_version") ?? rootId} onLockChange={onLockChange} />;
  }
  return render(
    <MemoryRouter initialEntries={[`/project/${projectId}/plan/chapters?scope=chapter&target=${chapterId}&generation_run=${runId}`]}>
      <WorkspaceFromPointer />
    </MemoryRouter>
  );
}

function workspaceWithDisabledReason(disabledReason: string) {
  return (
    <MemoryRouter initialEntries={[`/project/${projectId}/plan/chapters?scope=chapter&target=${chapterId}&generation_run=${runId}`]}>
      <CandidateVersionWorkspace userId={userId} projectId={projectId} chapterId={chapterId} chapterTitle="第一章" run={run} initialCandidateId={rootId} disabledReason={disabledReason} />
    </MemoryRouter>
  );
}

function renderPointerWorkspace(externalCandidateId: string) {
  function WorkspaceFromPointer() {
    const [params, setParams] = useSearchParams();
    const changePointer = () => {
      const next = new URLSearchParams(params);
      next.set("candidate_version", externalCandidateId);
      setParams(next);
    };
    return <>
      <button onClick={changePointer}>模拟浏览器候选地址变化</button>
      <output data-testid="candidate-query">{params.toString()}</output>
      <CandidateVersionWorkspace userId={userId} projectId={projectId} chapterId={chapterId} chapterTitle="第一章" run={run} initialCandidateId={params.get("candidate_version") ?? rootId} />
    </>;
  }
  return render(
    <MemoryRouter initialEntries={[`/project/${projectId}/plan/chapters?scope=chapter&target=${chapterId}&generation_run=${runId}&candidate_version=${rootId}&keep=yes`]}>
      <WorkspaceFromPointer />
    </MemoryRouter>
  );
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((next) => { resolve = next; });
  return { promise, resolve };
}

const run = {
  id: runId,
  project_id: projectId,
  planning_chapter_id: chapterId,
  context_checksum: "b".repeat(64),
  context_manifest: {
    chapter: { target_word_count: 1000 },
    elements: [],
    counts: { relations: 0 },
    warnings: [],
  },
} as unknown as GenerationRunResponse;

async function digest(content: string) {
  const bytes = new TextEncoder().encode(content);
  const result = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(result), (byte) =>
    byte.toString(16).padStart(2, "0")
  ).join("");
}

async function candidate(
  content: string,
  origin: "generated" | "technical_demo" | "manual_edit",
  version: number
) {
  const bytes = new TextEncoder().encode(content);
  const candidateId = origin === "manual_edit" ? manualId : origin === "technical_demo" ? id("technical") : rootId;
  return {
    id: candidateId,
    version_no: version,
    origin_kind: origin,
    parent_candidate_id: origin === "manual_edit" ? rootId : null,
    parent_version_no: origin === "manual_edit" ? 1 : null,
    root_candidate_id: origin === "technical_demo" ? candidateId : rootId,
    root_origin_kind: origin === "technical_demo" ? "technical_demo" as const : "generated" as const,
    ai_invoked_for_this_version: origin === "generated",
    billing_effect_for_this_version: origin === "generated" ? "possible" as const : "none" as const,
    usage_status_for_this_version: origin === "generated" ? "unavailable" as const : "not_applicable" as const,
    title: "第一章",
    content_checksum: await digest(content),
    content_size_bytes: bytes.byteLength,
    word_count: content.match(/[A-Za-z0-9]+|[\u3400-\u4dbf\u4e00-\u9fff]/g)?.length ?? 0,
    created_by: userId,
    created_at: now,
    project_id: projectId,
    run_id: runId,
    planning_chapter_id: chapterId,
    content,
    content_format: "plain_text" as const,
  };
}

async function manualVersion(version: number, seed: string) {
  const value = await candidate(`候选版本${version}正文。`, "manual_edit", version);
  return { ...value, id: id(seed) };
}

function list(
  items: Array<Record<string, unknown>>,
  options: { hasMore?: boolean; nextCursor?: string | null } = {}
) {
  return {
    schema_version: 1,
    project_id: projectId,
    run_id: runId,
    planning_chapter_id: chapterId,
    items: items.map(({ project_id, run_id, planning_chapter_id, content, content_format, ...item }) => item),
    next_cursor: options.nextCursor ?? null,
    has_more: options.hasMore ?? false,
  };
}

function auditFor(value: GenerationCandidateVersionDetail): GenerationCandidateAuditResponse {
  const minimum = 700;
  const maximum = 1300;
  const lengthStatus = value.word_count >= minimum && value.word_count <= maximum ? "pass" : "review";
  return {
    schema_version: 1,
    ruleset_version: 1,
    project_id: projectId,
    run_id: runId,
    planning_chapter_id: chapterId,
    candidate_id: value.id,
    candidate_version: value.version_no,
    candidate_checksum: value.content_checksum,
    context_checksum: run.context_checksum,
    status: lengthStatus,
    integrity: { status: "pass", content_size_bytes: value.content_size_bytes, word_count: value.word_count, storage_limit_bytes: 262144, storage_limit_reached: false },
    target_length: { status: lengthStatus, actual_word_count: value.word_count, target_word_count: 1000, minimum_word_count: minimum, maximum_word_count: maximum },
    preparation: { status: "pass", warnings: [] },
    unrecognized_explicit_terms: { status: "pass", items: [], truncated: false },
    context_summary: { element_count: 0, relation_count: 0, warning_count: 0, elements: [], foreshadow_actions_supported: false, foreshadow_action_count: 0 },
  };
}

describe("CandidateVersionWorkspace", () => {
  beforeEach(() => {
    sessionStorage.clear();
    vi.restoreAllMocks();
    vi.spyOn(api, "getGenerationCandidateAudit").mockRejectedValue(new Error("审计暂时不可用"));
  });

  it("persists v5 before POST, refreshes list, clears pending and focuses the new version", async () => {
    const root = await candidate(rootContent, "generated", 1);
    const manual = await candidate(editedContent, "manual_edit", 2);
    const listSpy = vi.spyOn(api, "listGenerationCandidateVersions")
      .mockResolvedValueOnce(list([root]))
      .mockResolvedValueOnce(list([manual, root]));
    const detailSpy = vi.spyOn(api, "getGenerationCandidateVersion").mockResolvedValue(root);
    const create = vi.spyOn(api, "createGenerationCandidateManualEdit")
      .mockImplementation(async (_project, _run, payload) => {
        expect(JSON.parse(sessionStorage.getItem(
          pendingProjectOperationKey(userId, projectId)
        )!)).toMatchObject({ schema_version: 5, workspace: "candidate_manual_edit" });
        expect(payload.content).toBe(editedContent);
        return {
          schema_version: 1,
          replayed: false,
          ai_invoked: false,
          billing_effect: "none",
          usage_status: "not_applicable",
          candidate: manual,
        };
      });
    const onLockChange = vi.fn();
    renderWorkspace(onLockChange);

    await screen.findByText("第一章 · 候选版本 1");
    expect(screen.queryByRole("button", { name: /设为当前|覆盖原稿|采用/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "基于此候选编辑" }));
    fireEvent.change(screen.getByLabelText("编辑副本"), { target: { value: editedContent } });
    fireEvent.click(screen.getByRole("button", { name: "保存为新候选版本" }));

    await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.getByText("第一章 · 候选版本 2")).toHaveFocus());
    expect(listSpy).toHaveBeenCalledTimes(2);
    expect(detailSpy).toHaveBeenCalledTimes(2);
    expect(sessionStorage.getItem(pendingProjectOperationKey(userId, projectId))).toBeNull();
    expect(api.getGenerationCandidateAudit).toHaveBeenCalledTimes(2);
    expect(onLockChange).toHaveBeenCalledWith(true);
    expect(onLockChange).toHaveBeenCalledWith(false);
  });

  it("labels generated, technical and manual sources precisely without selected-current claims", async () => {
    const generated = await candidate(rootContent, "generated", 1);
    const technical = await candidate("固定技术模拟正文。", "technical_demo", 2);
    const manual = await candidate(editedContent, "manual_edit", 3);
    vi.spyOn(api, "listGenerationCandidateVersions").mockResolvedValue(list([manual, technical, generated]));
    vi.spyOn(api, "getGenerationCandidateVersion").mockImplementation(async (_project, _run, candidateId) =>
      candidateId === manual.id ? manual : candidateId === technical.id ? technical : generated
    );
    renderWorkspace();
    await screen.findByText("第一章 · 候选版本 1");
    expect(screen.getByText("均为独立候选，不会覆盖原稿；选用功能尚未开放。")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "候选版本" })).toHaveClass("candidate-version-workspace");
    expect(screen.getByRole("heading", { name: "版本列表" }).closest(".candidate-version-list-panel")).not.toBeNull();
    expect(screen.getAllByText("模型生成候选").length).toBeGreaterThan(0);
    expect(screen.getAllByText("模型已调用；费用与用量以生成执行收据为准").length).toBeGreaterThan(0);
    expect(screen.getByText("固定技术模拟候选")).toBeInTheDocument();
    expect(screen.getByText("未调用模型、无模型费用")).toBeInTheDocument();
    expect(screen.getByText("作者手工另存｜基于版本 1")).toBeInTheDocument();
    expect(screen.getByText("本次未调用模型、无新增模型费用")).toBeInTheDocument();
    expect(screen.getByText("根来源：固定技术模拟候选")).toBeInTheDocument();
    expect(screen.queryByText(/章节当前版本|本次手工另存：零 AI/)).not.toBeInTheDocument();
    const generatedRow = screen.getByRole("button", { name: /^版本 1 模型生成候选/ });
    expect(generatedRow).toHaveAttribute("aria-pressed", "true");
    expect(generatedRow).toHaveTextContent("正在查看");
    const manualRow = screen.getByRole("button", { name: /^版本 3 作者手工另存/ });
    fireEvent.click(manualRow);
    await waitFor(() => expect(screen.getByText("第一章 · 候选版本 3")).toHaveFocus());
    expect(manualRow).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByLabelText("正在查看的候选版本正文")).toHaveTextContent(editedContent);
  });

  it("focuses the editor on entry and returns focus to the edit trigger after cancel", async () => {
    const root = await candidate(rootContent, "generated", 1);
    vi.spyOn(api, "listGenerationCandidateVersions").mockResolvedValue(list([root]));
    vi.spyOn(api, "getGenerationCandidateVersion").mockResolvedValue(root);
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderWorkspace();
    const edit = await screen.findByRole("button", { name: "基于此候选编辑" });
    fireEvent.click(edit);
    await waitFor(() => expect(screen.getByLabelText("编辑副本")).toHaveFocus());
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "基于此候选编辑" })).toHaveFocus());
  });

  it.each(["clear", "cancel"] as const)("falls back to the candidate heading after %s when the edit action is disabled", async (action) => {
    const root = await candidate(rootContent, "generated", 1);
    vi.spyOn(api, "listGenerationCandidateVersions").mockResolvedValue(list([root]));
    vi.spyOn(api, "getGenerationCandidateVersion").mockResolvedValue(root);
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const disabledReason = "当前上下文已禁用候选编辑。";
    if (action === "clear") {
      sessionStorage.setItem(`novel_candidate_manual_edit_draft_v1:${userId}:${projectId}`, "{broken");
      render(workspaceWithDisabledReason(disabledReason));
      const heading = await screen.findByRole("heading", { name: "第一章 · 候选版本 1" });
      fireEvent.click(screen.getByRole("button", { name: "确认仅清除浏览器损坏草稿" }));
      await waitFor(() => expect(heading).toHaveFocus());
    } else {
      const rendered = render(workspaceWithDisabledReason(""));
      const heading = await screen.findByRole("heading", { name: "第一章 · 候选版本 1" });
      fireEvent.click(screen.getByRole("button", { name: "基于此候选编辑" }));
      await screen.findByLabelText("编辑副本");
      rendered.rerender(workspaceWithDisabledReason(disabledReason));
      fireEvent.click(screen.getByRole("button", { name: "取消" }));
      await waitFor(() => expect(heading).toHaveFocus());
    }
    expect(screen.getByRole("button", { name: "基于此候选编辑" })).toBeDisabled();
  });

  it("keeps version-list loading, empty and retry states independent", async () => {
    const root = await candidate(rootContent, "generated", 1);
    const firstList = deferred<ReturnType<typeof list>>();
    const listSpy = vi.spyOn(api, "listGenerationCandidateVersions")
      .mockReturnValueOnce(firstList.promise)
      .mockRejectedValueOnce(new Error("版本列表读取失败"))
      .mockResolvedValueOnce(list([root]));
    vi.spyOn(api, "getGenerationCandidateVersion").mockResolvedValue(root);
    const firstRender = renderWorkspace();
    expect(screen.getByText("正在读取版本列表…")).toBeInTheDocument();
    firstList.resolve(list([]));
    expect(await screen.findByText("暂无可查看的候选版本。")).toHaveAttribute("role", "status");
    // Exercise the independent retry state by remounting with a first-read error.
    firstRender.unmount();
    renderWorkspace();
    expect(await screen.findByText("版本列表读取失败")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重新读取版本列表" }));
    const retriedFirstRow = await screen.findByRole("button", { name: /版本 1/ });
    await waitFor(() => expect(retriedFirstRow).toHaveFocus());
    expect(screen.getByText("版本列表已重新读取，共 1 个版本。")).toHaveAttribute("role", "status");
    expect(listSpy).toHaveBeenCalledTimes(3);
  });

  it("lets an accept refresh abort a deferred list retry so the old first page cannot replace the new version", async () => {
    const root = await candidate(rootContent, "generated", 1);
    const manual = await candidate(editedContent, "manual_edit", 2);
    const oldRetry = deferred<ReturnType<typeof list>>();
    const listSpy = vi.spyOn(api, "listGenerationCandidateVersions")
      .mockRejectedValueOnce(new Error("首次列表失败"))
      .mockReturnValueOnce(oldRetry.promise)
      .mockResolvedValueOnce(list([manual, root]));
    vi.spyOn(api, "getGenerationCandidateVersion").mockResolvedValue(root);
    const create = vi.spyOn(api, "createGenerationCandidateManualEdit").mockResolvedValue({
      schema_version: 1, replayed: false, ai_invoked: false,
      billing_effect: "none", usage_status: "not_applicable", candidate: manual,
    });
    renderWorkspace();
    fireEvent.click(await screen.findByRole("button", { name: "重新读取版本列表" }));
    await waitFor(() => expect(listSpy).toHaveBeenCalledTimes(2));
    const retrySignal = listSpy.mock.calls[1][2]?.signal;
    fireEvent.click(screen.getByRole("button", { name: "基于此候选编辑" }));
    fireEvent.change(screen.getByLabelText("编辑副本"), { target: { value: editedContent } });
    fireEvent.click(screen.getByRole("button", { name: "保存为新候选版本" }));
    await waitFor(() => expect(screen.getByText("第一章 · 候选版本 2")).toHaveFocus());
    expect(retrySignal?.aborted).toBe(true);
    await act(async () => {
      oldRetry.resolve(list([root]));
      await Promise.resolve();
    });
    expect(screen.getByRole("button", { name: /^版本 2 / })).toBeInTheDocument();
    expect(sessionStorage.getItem(pendingProjectOperationKey(userId, projectId))).toBeNull();
    expect(create).toHaveBeenCalledTimes(1);
  });

  it("does zero POST when v5 storage fails", async () => {
    const root = await candidate(rootContent, "generated", 1);
    vi.spyOn(api, "listGenerationCandidateVersions").mockResolvedValue(list([root]));
    vi.spyOn(api, "getGenerationCandidateVersion").mockResolvedValue(root);
    const create = vi.spyOn(api, "createGenerationCandidateManualEdit");
    renderWorkspace();
    await screen.findByText("第一章 · 候选版本 1");
    fireEvent.click(screen.getByRole("button", { name: "基于此候选编辑" }));
    const editor = screen.getByLabelText("编辑副本");
    await waitFor(() => expect(editor).toHaveFocus());
    fireEvent.change(editor, { target: { value: editedContent } });
    sessionStorage.setItem(pendingProjectOperationKey(userId, projectId), JSON.stringify({
      schema_version: 1,
      operation_key: "planning:blocked-by-existing-owner",
    }));
    fireEvent.click(screen.getByRole("button", { name: "保存为新候选版本" }));
    const storageError = await screen.findByText(/已停止提交/);
    await waitFor(() => expect(storageError).toHaveFocus());
    expect(create).not.toHaveBeenCalled();
  });

  it.each([
    ["GENERATION_CANDIDATE_CONTENT_UNCHANGED", "edit_candidate_content", "零写入并清除旧恢复线索", false],
    ["GENERATION_CANDIDATE_PARENT_CHANGED", "reload_generation_candidate_versions", "草稿未丢失", true],
    ["GENERATION_CONTEXT_CHECKSUM_CONFLICT", "reload_generation_candidate_versions", "草稿未丢失", true],
    ["GENERATION_CANDIDATE_OPERATION_CONFLICT", "start_new_candidate_manual_edit", "草稿未丢失", true],
  ] as const)("routes confirmed zero-write conflict %s without an automatic retry", async (code, action, expectedText, stale) => {
    const root = await candidate(rootContent, "generated", 1);
    vi.spyOn(api, "listGenerationCandidateVersions").mockResolvedValue(list([root]));
    vi.spyOn(api, "getGenerationCandidateVersion").mockResolvedValue(root);
    const create = vi.spyOn(api, "createGenerationCandidateManualEdit").mockRejectedValue(new ApiError(409, {
      detail: "服务端确认冲突",
      code,
      retryable: false,
      recommended_action: action,
    }));
    renderWorkspace();
    await screen.findByText("第一章 · 候选版本 1");
    fireEvent.click(screen.getByRole("button", { name: "基于此候选编辑" }));
    fireEvent.change(screen.getByLabelText("编辑副本"), { target: { value: editedContent } });
    fireEvent.click(screen.getByRole("button", { name: "保存为新候选版本" }));
    expect(await screen.findByText(new RegExp(expectedText))).toBeInTheDocument();
    expect(create).toHaveBeenCalledTimes(1);
    expect(sessionStorage.getItem(pendingProjectOperationKey(userId, projectId))).toBeNull();
    expect(sessionStorage.getItem(`novel_candidate_manual_edit_draft_v1:${userId}:${projectId}`)).toContain(editedContent);
    if (stale) {
      expect(screen.getByLabelText("待处理的旧草稿正文")).toHaveValue(editedContent);
      expect(screen.getByRole("button", { name: "基于页面已严格读取的权威父版本重新开始" })).toBeInTheDocument();
    }
  });

  it("keeps a version conflict on the original key until an explicit retry", async () => {
    const root = await candidate(rootContent, "generated", 1);
    vi.spyOn(api, "listGenerationCandidateVersions").mockResolvedValue(list([root]));
    vi.spyOn(api, "getGenerationCandidateVersion").mockResolvedValue(root);
    const create = vi.spyOn(api, "createGenerationCandidateManualEdit").mockRejectedValue(new ApiError(409, {
      detail: "候选版本并发号冲突",
      code: "GENERATION_CANDIDATE_VERSION_CONFLICT",
      retryable: true,
      recommended_action: "retry_candidate_manual_edit",
    }));
    renderWorkspace();
    await screen.findByText("第一章 · 候选版本 1");
    fireEvent.click(screen.getByRole("button", { name: "基于此候选编辑" }));
    fireEvent.change(screen.getByLabelText("编辑副本"), { target: { value: editedContent } });
    fireEvent.click(screen.getByRole("button", { name: "保存为新候选版本" }));
    expect(await screen.findByRole("button", { name: "明确确认原请求重试" })).toBeInTheDocument();
    expect(screen.getByText(/服务端确认版本并发冲突/)).toBeInTheDocument();
    expect(screen.queryByText(/服务端精确确认未找到原记录/)).not.toBeInTheDocument();
    expect(create).toHaveBeenCalledTimes(1);
    expect(sessionStorage.getItem(pendingProjectOperationKey(userId, projectId))).toContain("candidate_manual_edit");
    expect(sessionStorage.getItem(`novel_candidate_manual_edit_draft_v1:${userId}:${projectId}`)).toContain(editedContent);
  });

  it("fails closed for an unknown 409 and preserves the complete recovery record", async () => {
    const root = await candidate(rootContent, "generated", 1);
    vi.spyOn(api, "listGenerationCandidateVersions").mockResolvedValue(list([root]));
    vi.spyOn(api, "getGenerationCandidateVersion").mockResolvedValue(root);
    const create = vi.spyOn(api, "createGenerationCandidateManualEdit").mockRejectedValue(new ApiError(409, {
      detail: "未识别冲突",
      code: "GENERATION_CANDIDATE_FUTURE_CONFLICT",
      retryable: true,
      recommended_action: "future_action",
    }));
    renderWorkspace();
    await screen.findByText("第一章 · 候选版本 1");
    fireEvent.click(screen.getByRole("button", { name: "基于此候选编辑" }));
    fireEvent.change(screen.getByLabelText("编辑副本"), { target: { value: editedContent } });
    fireEvent.click(screen.getByRole("button", { name: "保存为新候选版本" }));
    expect(await screen.findByText(/冲突语义无法安全确认/)).toBeInTheDocument();
    expect(create).toHaveBeenCalledTimes(1);
    expect(sessionStorage.getItem(pendingProjectOperationKey(userId, projectId))).toContain("candidate_manual_edit");
    expect(screen.queryByRole("button", { name: "明确确认原请求重试" })).not.toBeInTheDocument();
  });

  it.each([
    ["GENERATION_CANDIDATE_CONTENT_UNCHANGED", true, "edit_candidate_content"],
    ["GENERATION_CANDIDATE_PARENT_CHANGED", true, "reload_generation_candidate_versions"],
    ["GENERATION_CONTEXT_CHECKSUM_CONFLICT", true, "reload_generation_candidate_versions"],
    ["GENERATION_CANDIDATE_OPERATION_CONFLICT", true, "start_new_candidate_manual_edit"],
    ["GENERATION_CANDIDATE_VERSION_CONFLICT", false, "retry_candidate_manual_edit"],
  ] as const)("fails closed when 409 retryability contradicts %s", async (code, retryable, action) => {
    const root = await candidate(rootContent, "generated", 1);
    vi.spyOn(api, "listGenerationCandidateVersions").mockResolvedValue(list([root]));
    vi.spyOn(api, "getGenerationCandidateVersion").mockResolvedValue(root);
    const create = vi.spyOn(api, "createGenerationCandidateManualEdit").mockRejectedValue(new ApiError(409, {
      detail: "冲突标志不一致",
      code,
      retryable,
      recommended_action: action,
    }));
    renderWorkspace();
    await screen.findByText("第一章 · 候选版本 1");
    fireEvent.click(screen.getByRole("button", { name: "基于此候选编辑" }));
    fireEvent.change(screen.getByLabelText("编辑副本"), { target: { value: editedContent } });
    fireEvent.click(screen.getByRole("button", { name: "保存为新候选版本" }));
    expect(await screen.findByText(/冲突语义无法安全确认/)).toBeInTheDocument();
    expect(create).toHaveBeenCalledTimes(1);
    expect(sessionStorage.getItem(pendingProjectOperationKey(userId, projectId))).toContain("candidate_manual_edit");
    expect(screen.queryByRole("button", { name: "明确确认原请求重试" })).not.toBeInTheDocument();
  });

  it("turns unknown POST into by-key GET and requires explicit exact-404 retry", async () => {
    const root = await candidate(rootContent, "generated", 1);
    const manual = await candidate(editedContent, "manual_edit", 2);
    vi.spyOn(api, "listGenerationCandidateVersions")
      .mockResolvedValueOnce(list([root]))
      .mockResolvedValueOnce(list([manual, root]));
    vi.spyOn(api, "getGenerationCandidateVersion").mockResolvedValue(root);
    const create = vi.spyOn(api, "createGenerationCandidateManualEdit")
      .mockRejectedValueOnce(new TypeError("network"))
      .mockResolvedValueOnce({
        schema_version: 1,
        replayed: true,
        ai_invoked: false,
        billing_effect: "none",
        usage_status: "not_applicable",
        candidate: manual,
      });
    const byKey = vi.spyOn(api, "getGenerationCandidateManualEditByKey")
      .mockRejectedValue(new ApiError(404, {
        detail: "未找到",
        code: "GENERATION_CANDIDATE_MANUAL_EDIT_NOT_FOUND",
        retryable: true,
        recommended_action: "retry_original_candidate_manual_edit",
      }));
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderWorkspace();
    await screen.findByText("第一章 · 候选版本 1");
    fireEvent.click(screen.getByRole("button", { name: "基于此候选编辑" }));
    fireEvent.change(screen.getByLabelText("编辑副本"), { target: { value: editedContent } });
    fireEvent.click(screen.getByRole("button", { name: "保存为新候选版本" }));
    const retry = await screen.findByRole("button", { name: "明确确认原请求重试" });
    expect(screen.getByText(/服务端精确确认未找到原记录/)).toBeInTheDocument();
    expect(screen.queryByText(/服务端确认版本并发冲突/)).not.toBeInTheDocument();
    expect(create).toHaveBeenCalledTimes(1);
    expect(byKey).toHaveBeenCalledTimes(1);
    fireEvent.click(retry);
    await waitFor(() => expect(create).toHaveBeenCalledTimes(2));
    expect(create.mock.calls[1][2]).toEqual(create.mock.calls[0][2]);
  });

  it("restores an identity-bound current-tab draft with zero POST", async () => {
    const root = await candidate(rootContent, "generated", 1);
    vi.spyOn(api, "listGenerationCandidateVersions").mockResolvedValue(list([root]));
    vi.spyOn(api, "getGenerationCandidateVersion").mockResolvedValue(root);
    const create = vi.spyOn(api, "createGenerationCandidateManualEdit");
    sessionStorage.setItem(`novel_candidate_manual_edit_draft_v1:${userId}:${projectId}`, JSON.stringify({
      schema_version: 1,
      workspace: "candidate_manual_edit_draft",
      user_id: userId,
      project_id: projectId,
      chapter_id: chapterId,
      run_id: runId,
      parent_candidate_id: rootId,
      parent_version_no: 1,
      parent_checksum: root.content_checksum,
      context_checksum: run.context_checksum,
      content: editedContent,
      updated_at: now,
    }));

    renderWorkspace();

    const restoredTitle = await screen.findByText("发现本标签页未另存草稿");
    await waitFor(() => expect(restoredTitle).toHaveFocus());
    expect(screen.getByText("草稿恢复核对完成，等待你的选择。")).toHaveAttribute("role", "status");
    expect(screen.queryByLabelText("编辑副本")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "继续编辑草稿" }));
    expect(await screen.findByLabelText("编辑副本")).toHaveValue(editedContent);
    await waitFor(() => expect(screen.getByLabelText("编辑副本")).toHaveFocus());
    expect(create).not.toHaveBeenCalled();
  });

  it("fails closed when a restored draft parent or context has drifted", async () => {
    const root = await candidate(rootContent, "generated", 1);
    vi.spyOn(api, "listGenerationCandidateVersions").mockResolvedValue(list([root]));
    vi.spyOn(api, "getGenerationCandidateVersion").mockResolvedValue(root);
    const create = vi.spyOn(api, "createGenerationCandidateManualEdit");
    sessionStorage.setItem(`novel_candidate_manual_edit_draft_v1:${userId}:${projectId}`, JSON.stringify({
      schema_version: 1,
      workspace: "candidate_manual_edit_draft",
      user_id: userId,
      project_id: projectId,
      chapter_id: chapterId,
      run_id: runId,
      parent_candidate_id: rootId,
      parent_version_no: 1,
      parent_checksum: "f".repeat(64),
      context_checksum: run.context_checksum,
      content: editedContent,
      updated_at: now,
    }));

    renderWorkspace();

    expect(await screen.findByText(/草稿与当前父版本或冻结上下文不一致/)).toBeInTheDocument();
    expect(screen.getByLabelText("待处理的旧草稿正文")).toHaveValue(editedContent);
    expect(screen.queryByLabelText("编辑副本")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /版本 1/ })).toBeDisabled();
    expect(create).not.toHaveBeenCalled();
  });

  it("rebases a stale draft only after explicit confirmation and still sends zero POST", async () => {
    const root = await candidate(rootContent, "generated", 1);
    vi.spyOn(api, "listGenerationCandidateVersions").mockResolvedValue(list([root]));
    vi.spyOn(api, "getGenerationCandidateVersion").mockResolvedValue(root);
    const create = vi.spyOn(api, "createGenerationCandidateManualEdit");
    vi.spyOn(window, "confirm").mockReturnValue(true);
    sessionStorage.setItem(`novel_candidate_manual_edit_draft_v1:${userId}:${projectId}`, JSON.stringify({
      schema_version: 1, workspace: "candidate_manual_edit_draft", user_id: userId,
      project_id: projectId, chapter_id: chapterId, run_id: runId,
      parent_candidate_id: rootId, parent_version_no: 1, parent_checksum: "f".repeat(64),
      context_checksum: run.context_checksum, content: editedContent, updated_at: now,
    }));
    renderWorkspace();
    const rebase = await screen.findByRole("button", { name: "基于页面已严格读取的权威父版本重新开始" });
    fireEvent.click(rebase);
    expect(await screen.findByLabelText("编辑副本")).toHaveValue(editedContent);
    expect(create).not.toHaveBeenCalled();
    const stored = JSON.parse(sessionStorage.getItem(`novel_candidate_manual_edit_draft_v1:${userId}:${projectId}`)!);
    expect(stored).toMatchObject({ parent_candidate_id: root.id, parent_version_no: root.version_no, parent_checksum: root.content_checksum, content: editedContent });
  });

  it("announces a copied stale draft politely without moving focus", async () => {
    const root = await candidate(rootContent, "generated", 1);
    vi.spyOn(api, "listGenerationCandidateVersions").mockResolvedValue(list([root]));
    vi.spyOn(api, "getGenerationCandidateVersion").mockResolvedValue(root);
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    sessionStorage.setItem(`novel_candidate_manual_edit_draft_v1:${userId}:${projectId}`, JSON.stringify({
      schema_version: 1, workspace: "candidate_manual_edit_draft", user_id: userId,
      project_id: projectId, chapter_id: chapterId, run_id: runId,
      parent_candidate_id: rootId, parent_version_no: 1, parent_checksum: "f".repeat(64),
      context_checksum: run.context_checksum, content: editedContent, updated_at: now,
    }));
    renderWorkspace();
    const copy = await screen.findByRole("button", { name: "复制旧草稿" });
    copy.focus();
    fireEvent.click(copy);
    expect(await screen.findByText("旧草稿正文已复制；没有修改任何记录。")).toHaveAttribute("role", "status");
    expect(copy).toHaveFocus();
    expect(writeText).toHaveBeenCalledWith(editedContent);
  });

  it("offers a precise GET-only return link for a foreign tab draft", async () => {
    const root = await candidate(rootContent, "generated", 1);
    const foreignChapter = id("foreignchapter");
    vi.spyOn(api, "listGenerationCandidateVersions").mockResolvedValue(list([root]));
    vi.spyOn(api, "getGenerationCandidateVersion").mockResolvedValue(root);
    const create = vi.spyOn(api, "createGenerationCandidateManualEdit");
    sessionStorage.setItem(`novel_candidate_manual_edit_draft_v1:${userId}:${projectId}`, JSON.stringify({
      schema_version: 1, workspace: "candidate_manual_edit_draft", user_id: userId,
      project_id: projectId, chapter_id: foreignChapter, run_id: runId,
      parent_candidate_id: rootId, parent_version_no: 1, parent_checksum: root.content_checksum,
      context_checksum: run.context_checksum, content: editedContent, updated_at: now,
    }));
    renderWorkspace();
    const link = await screen.findByRole("link", { name: "返回原章处理草稿" });
    expect(link).toHaveAttribute("href", `/project/${projectId}/plan/chapters?scope=chapter&target=${foreignChapter}&generation_run=${runId}&candidate_version=${rootId}`);
    expect(create).not.toHaveBeenCalled();
  });

  it("clears a corrupt browser draft only after confirmation and never touches shared/server state", async () => {
    const root = await candidate(rootContent, "generated", 1);
    vi.spyOn(api, "listGenerationCandidateVersions").mockResolvedValue(list([root]));
    vi.spyOn(api, "getGenerationCandidateVersion").mockResolvedValue(root);
    const create = vi.spyOn(api, "createGenerationCandidateManualEdit");
    const sharedKey = pendingProjectOperationKey(userId, projectId);
    const draftKey = `novel_candidate_manual_edit_draft_v1:${userId}:${projectId}`;
    sessionStorage.setItem(draftKey, "{broken");
    vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
    renderWorkspace();
    const clear = await screen.findByRole("button", { name: "确认仅清除浏览器损坏草稿" });
    fireEvent.click(clear);
    expect(sessionStorage.getItem(draftKey)).toBe("{broken");
    fireEvent.click(clear);
    await waitFor(() => expect(sessionStorage.getItem(draftKey)).toBeNull());
    await waitFor(() => expect(screen.getByRole("button", { name: "基于此候选编辑" })).toHaveFocus());
    expect(sessionStorage.getItem(sharedKey)).toBeNull();
    expect(create).not.toHaveBeenCalled();
  });

  it("returns focus to the edit action after explicitly abandoning a stale draft", async () => {
    const root = await candidate(rootContent, "generated", 1);
    vi.spyOn(api, "listGenerationCandidateVersions").mockResolvedValue(list([root]));
    vi.spyOn(api, "getGenerationCandidateVersion").mockResolvedValue(root);
    const create = vi.spyOn(api, "createGenerationCandidateManualEdit");
    vi.spyOn(window, "confirm").mockReturnValue(true);
    sessionStorage.setItem(`novel_candidate_manual_edit_draft_v1:${userId}:${projectId}`, JSON.stringify({
      schema_version: 1, workspace: "candidate_manual_edit_draft", user_id: userId,
      project_id: projectId, chapter_id: chapterId, run_id: runId,
      parent_candidate_id: rootId, parent_version_no: 1, parent_checksum: "f".repeat(64),
      context_checksum: run.context_checksum, content: editedContent, updated_at: now,
    }));
    renderWorkspace();
    fireEvent.click(await screen.findByRole("button", { name: "放弃旧草稿" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "基于此候选编辑" })).toHaveFocus());
    expect(screen.getByText("旧草稿已放弃；没有修改服务端候选。")).toHaveAttribute("role", "status");
    expect(create).not.toHaveBeenCalled();
  });

  it("keeps focus on the alert when clearing a corrupt draft fails", async () => {
    const root = await candidate(rootContent, "generated", 1);
    vi.spyOn(api, "listGenerationCandidateVersions").mockResolvedValue(list([root]));
    vi.spyOn(api, "getGenerationCandidateVersion").mockResolvedValue(root);
    const draftKey = `novel_candidate_manual_edit_draft_v1:${userId}:${projectId}`;
    sessionStorage.setItem(draftKey, "{broken");
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderWorkspace();
    const clear = await screen.findByRole("button", { name: "确认仅清除浏览器损坏草稿" });
    vi.spyOn(Object.getPrototypeOf(sessionStorage) as Storage, "removeItem").mockImplementation(() => {
      throw new DOMException("blocked", "SecurityError");
    });
    fireEvent.click(clear);
    expect(await screen.findByText("损坏草稿未能安全清除，仍保持禁写。")).toHaveFocus();
    expect(sessionStorage.getItem(draftKey)).toBe("{broken");
  });

  it("keeps editing disabled when only draft storage is unavailable", async () => {
    const root = await candidate(rootContent, "generated", 1);
    vi.spyOn(api, "listGenerationCandidateVersions").mockResolvedValue(list([root]));
    vi.spyOn(api, "getGenerationCandidateVersion").mockResolvedValue(root);
    const create = vi.spyOn(api, "createGenerationCandidateManualEdit");
    vi.spyOn(Object.getPrototypeOf(sessionStorage) as Storage, "getItem")
      .mockImplementation((key: string) => {
        if (key.startsWith("novel_candidate_manual_edit_draft_v1:")) {
          throw new DOMException("blocked", "SecurityError");
        }
        return null;
      });
    renderWorkspace();
    expect(await screen.findByText(/草稿存储不可用.*修复浏览器会话存储设置后重新加载页面/)).toBeInTheDocument();
    await screen.findByText("第一章 · 候选版本 1");
    expect(screen.getByRole("button", { name: "基于此候选编辑" })).toBeDisabled();
    expect(create).not.toHaveBeenCalled();
  });

  it.each(["list", "detail"] as const)("keeps an identity-bound draft locked when the initial %s GET fails", async (failure) => {
    const root = await candidate(rootContent, "generated", 1);
    const draftKey = `novel_candidate_manual_edit_draft_v1:${userId}:${projectId}`;
    const serialized = JSON.stringify({
      schema_version: 1, workspace: "candidate_manual_edit_draft", user_id: userId,
      project_id: projectId, chapter_id: chapterId, run_id: runId,
      parent_candidate_id: rootId, parent_version_no: 1, parent_checksum: root.content_checksum,
      context_checksum: run.context_checksum, content: editedContent, updated_at: now,
    });
    sessionStorage.setItem(draftKey, serialized);
    const listSpy = vi.spyOn(api, "listGenerationCandidateVersions");
    if (failure === "list") listSpy.mockRejectedValue(new Error("版本列表暂不可用"));
    else listSpy.mockResolvedValue(list([root]));
    vi.spyOn(api, "getGenerationCandidateVersion").mockRejectedValue(new Error("版本详情暂不可用"));
    const create = vi.spyOn(api, "createGenerationCandidateManualEdit");
    const onLockChange = vi.fn();
    renderWorkspace(onLockChange);
    expect(await screen.findByText(failure === "list" ? "版本列表暂不可用" : "版本详情暂不可用")).toBeInTheDocument();
    expect(onLockChange).toHaveBeenLastCalledWith(true);
    expect(sessionStorage.getItem(draftKey)).toBe(serialized);
    expect(create).not.toHaveBeenCalled();
  });

  it.each(["foreign", "corrupt"] as const)("keeps a %s draft locked when the initial list GET fails", async (kind) => {
    const root = await candidate(rootContent, "generated", 1);
    const draftKey = `novel_candidate_manual_edit_draft_v1:${userId}:${projectId}`;
    const serialized = kind === "corrupt" ? "{broken" : JSON.stringify({
      schema_version: 1, workspace: "candidate_manual_edit_draft", user_id: userId,
      project_id: projectId, chapter_id: id("foreignchapter"), run_id: runId,
      parent_candidate_id: rootId, parent_version_no: 1, parent_checksum: root.content_checksum,
      context_checksum: run.context_checksum, content: editedContent, updated_at: now,
    });
    sessionStorage.setItem(draftKey, serialized);
    vi.spyOn(api, "listGenerationCandidateVersions").mockRejectedValue(new Error("版本列表暂不可用"));
    const create = vi.spyOn(api, "createGenerationCandidateManualEdit");
    const onLockChange = vi.fn();
    renderWorkspace(onLockChange);
    expect(await screen.findByText("版本列表暂不可用")).toBeInTheDocument();
    expect(onLockChange).toHaveBeenLastCalledWith(true);
    expect(sessionStorage.getItem(draftKey)).toBe(serialized);
    expect(create).not.toHaveBeenCalled();
  });

  it("keeps draft-storage unavailable locked even when the initial list GET also fails", async () => {
    vi.spyOn(Object.getPrototypeOf(sessionStorage) as Storage, "getItem")
      .mockImplementation((key: string) => {
        if (key.startsWith("novel_candidate_manual_edit_draft_v1:")) throw new DOMException("blocked", "SecurityError");
        return null;
      });
    vi.spyOn(api, "listGenerationCandidateVersions").mockRejectedValue(new Error("版本列表暂不可用"));
    const create = vi.spyOn(api, "createGenerationCandidateManualEdit");
    const onLockChange = vi.fn();
    renderWorkspace(onLockChange);
    expect(await screen.findByText("版本列表暂不可用")).toBeInTheDocument();
    expect(onLockChange).toHaveBeenLastCalledWith(true);
    expect(create).not.toHaveBeenCalled();
  });

  it("does not reconcile or clear v5 when frozen parent metadata mismatches", async () => {
    const root = await candidate(rootContent, "generated", 1);
    vi.spyOn(api, "listGenerationCandidateVersions").mockResolvedValue(list([root]));
    vi.spyOn(api, "getGenerationCandidateVersion").mockResolvedValue(root);
    const byKey = vi.spyOn(api, "getGenerationCandidateManualEditByKey");
    const record = {
      schema_version: 5,
      workspace: "candidate_manual_edit",
      user_id: userId,
      project_id: projectId,
      chapter_id: chapterId,
      run_id: runId,
      operation_key: "candidate:manual-edit:mismatch1",
      payload: {
        operation_key: "candidate:manual-edit:mismatch1",
        parent_candidate_id: rootId,
        expected_parent_version_no: 1,
        expected_parent_checksum: "f".repeat(64),
        expected_context_checksum: run.context_checksum,
        content: editedContent,
      },
      created_at: now,
    };
    sessionStorage.setItem(pendingProjectOperationKey(userId, projectId), JSON.stringify(record));

    renderWorkspace();

    expect(await screen.findByText(/恢复记录与当前父版本或冻结上下文不一致/)).toBeInTheDocument();
    expect(byKey).not.toHaveBeenCalled();
    expect(sessionStorage.getItem(pendingProjectOperationKey(userId, projectId))).toBe(JSON.stringify(record));
  });

  it("keeps a cross-chapter v5 locked and never reconciles it in the current chapter", async () => {
    const root = await candidate(rootContent, "generated", 1);
    vi.spyOn(api, "listGenerationCandidateVersions").mockResolvedValue(list([root]));
    vi.spyOn(api, "getGenerationCandidateVersion").mockResolvedValue(root);
    const byKey = vi.spyOn(api, "getGenerationCandidateManualEditByKey");
    const create = vi.spyOn(api, "createGenerationCandidateManualEdit");
    sessionStorage.setItem(pendingProjectOperationKey(userId, projectId), JSON.stringify({
      schema_version: 5,
      workspace: "candidate_manual_edit",
      user_id: userId,
      project_id: projectId,
      chapter_id: id("otherchapter"),
      run_id: runId,
      operation_key: "candidate:manual-edit:otherchapter",
      payload: {
        operation_key: "candidate:manual-edit:otherchapter",
        parent_candidate_id: rootId,
        expected_parent_version_no: 1,
        expected_parent_checksum: root.content_checksum,
        expected_context_checksum: run.context_checksum,
        content: editedContent,
      },
      created_at: now,
    }));

    renderWorkspace();

    expect(await screen.findByText(/另一章还有未核对的候选另存/)).toBeInTheDocument();
    expect(byKey).not.toHaveBeenCalled();
    expect(create).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: /版本 1/ })).toBeDisabled();
  });

  it("reconciles a matching v5 by key, compare-clears it, and unlocks without POST", async () => {
    const root = await candidate(rootContent, "generated", 1);
    const manual = await candidate(editedContent, "manual_edit", 2);
    vi.spyOn(api, "listGenerationCandidateVersions")
      .mockResolvedValueOnce(list([root]))
      .mockResolvedValueOnce(list([manual, root]));
    vi.spyOn(api, "getGenerationCandidateVersion").mockResolvedValue(root);
    vi.spyOn(api, "getGenerationCandidateManualEditByKey").mockResolvedValue({
      schema_version: 1,
      replayed: true,
      ai_invoked: false,
      billing_effect: "none",
      usage_status: "not_applicable",
      candidate: manual,
    });
    const create = vi.spyOn(api, "createGenerationCandidateManualEdit");
    const onLockChange = vi.fn();
    sessionStorage.setItem(pendingProjectOperationKey(userId, projectId), JSON.stringify({
      schema_version: 5,
      workspace: "candidate_manual_edit",
      user_id: userId,
      project_id: projectId,
      chapter_id: chapterId,
      run_id: runId,
      operation_key: "candidate:manual-edit:recover-ok",
      payload: {
        operation_key: "candidate:manual-edit:recover-ok",
        parent_candidate_id: rootId,
        expected_parent_version_no: 1,
        expected_parent_checksum: root.content_checksum,
        expected_context_checksum: run.context_checksum,
        content: editedContent,
      },
      created_at: now,
    }));

    renderWorkspace(onLockChange);

    await waitFor(() => expect(screen.getByText("第一章 · 候选版本 2")).toHaveFocus());
    expect(sessionStorage.getItem(pendingProjectOperationKey(userId, projectId))).toBeNull();
    expect(create).not.toHaveBeenCalled();
    expect(onLockChange).toHaveBeenCalledWith(true);
    await waitFor(() => expect(onLockChange).toHaveBeenLastCalledWith(false));
  });

  it("invalidates a deferred parent read after unmount before pending reconciliation", async () => {
    const root = await candidate(rootContent, "generated", 1);
    const parentRead = deferred<typeof root>();
    vi.spyOn(api, "listGenerationCandidateVersions").mockResolvedValue(list([root]));
    vi.spyOn(api, "getGenerationCandidateVersion").mockReturnValue(parentRead.promise);
    const byKey = vi.spyOn(api, "getGenerationCandidateManualEditByKey");
    const record = {
      schema_version: 5,
      workspace: "candidate_manual_edit",
      user_id: userId,
      project_id: projectId,
      chapter_id: chapterId,
      run_id: runId,
      operation_key: "candidate:manual-edit:deferred1",
      payload: {
        operation_key: "candidate:manual-edit:deferred1",
        parent_candidate_id: rootId,
        expected_parent_version_no: 1,
        expected_parent_checksum: root.content_checksum,
        expected_context_checksum: run.context_checksum,
        content: editedContent,
      },
      created_at: now,
    };
    const serialized = JSON.stringify(record);
    sessionStorage.setItem(pendingProjectOperationKey(userId, projectId), serialized);

    const rendered = renderWorkspace();
    await waitFor(() => expect(api.listGenerationCandidateVersions).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(api.getGenerationCandidateVersion).toHaveBeenCalledTimes(1));
    rendered.unmount();
    parentRead.resolve(root);
    await Promise.resolve();
    await Promise.resolve();

    expect(byKey).not.toHaveBeenCalled();
    expect(sessionStorage.getItem(pendingProjectOperationKey(userId, projectId))).toBe(serialized);
  });

  it("isolates audit requests from candidate selection and ignores the late parent audit", async () => {
    const root = await candidate("星".repeat(700), "generated", 1);
    const manual = await candidate(editedContent, "manual_edit", 2);
    const lateParentAudit = deferred<GenerationCandidateAuditResponse>();
    vi.spyOn(api, "listGenerationCandidateVersions").mockResolvedValue(list([manual, root]));
    vi.spyOn(api, "getGenerationCandidateVersion").mockImplementation(async (_project, _run, candidateId) =>
      candidateId === manual.id ? manual : root
    );
    const auditSpy = vi.spyOn(api, "getGenerationCandidateAudit")
      .mockReturnValueOnce(lateParentAudit.promise)
      .mockResolvedValueOnce(auditFor(manual));
    const create = vi.spyOn(api, "createGenerationCandidateManualEdit");

    renderWorkspace();
    await screen.findByText("第一章 · 候选版本 1");
    const firstSignal = auditSpy.mock.calls[0][2];
    fireEvent.click(screen.getByRole("button", { name: /版本 2/ }));
    expect(await screen.findByText("需要作者人工核对。", { exact: false })).toBeInTheDocument();
    expect(firstSignal?.aborted).toBe(true);
    lateParentAudit.resolve(auditFor(root));
    await Promise.resolve();
    expect(screen.getByText("需要作者人工核对。", { exact: false })).toBeInTheDocument();
    expect(screen.queryByText("未发现确定性完整问题。", { exact: false })).not.toBeInTheDocument();
    expect(create).not.toHaveBeenCalled();
  });

  it("clears the parent audit immediately while the newly saved child audit is pending", async () => {
    const root = await candidate(rootContent, "generated", 1);
    const manual = await candidate(editedContent, "manual_edit", 2);
    const childAudit = deferred<GenerationCandidateAuditResponse>();
    vi.spyOn(api, "listGenerationCandidateVersions")
      .mockResolvedValueOnce(list([root]))
      .mockResolvedValueOnce(list([manual, root]));
    vi.spyOn(api, "getGenerationCandidateVersion").mockResolvedValue(root);
    const auditSpy = vi.spyOn(api, "getGenerationCandidateAudit")
      .mockResolvedValueOnce(auditFor(root))
      .mockReturnValueOnce(childAudit.promise);
    const create = vi.spyOn(api, "createGenerationCandidateManualEdit").mockResolvedValue({
      schema_version: 1, replayed: false, ai_invoked: false,
      billing_effect: "none", usage_status: "not_applicable", candidate: manual,
    });
    renderWorkspace();
    expect(await screen.findByText("需要作者人工核对。", { exact: false })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "基于此候选编辑" }));
    fireEvent.change(screen.getByLabelText("编辑副本"), { target: { value: editedContent } });
    fireEvent.click(screen.getByRole("button", { name: "保存为新候选版本" }));
    await screen.findByText("第一章 · 候选版本 2");
    expect(screen.queryByText("需要作者人工核对。", { exact: false })).not.toBeInTheDocument();
    expect(screen.getByText("正在读取检查…")).toBeInTheDocument();
    childAudit.resolve(auditFor(manual));
    expect(await screen.findByText("需要作者人工核对。", { exact: false })).toBeInTheDocument();
    expect(auditSpy).toHaveBeenCalledTimes(2);
    expect(create).toHaveBeenCalledTimes(1);
  });

  it("retries only the selected candidate audit with zero manual-edit POST", async () => {
    const root = await candidate(rootContent, "generated", 1);
    vi.spyOn(api, "listGenerationCandidateVersions").mockResolvedValue(list([root]));
    vi.spyOn(api, "getGenerationCandidateVersion").mockResolvedValue(root);
    const auditSpy = vi.spyOn(api, "getGenerationCandidateAudit")
      .mockRejectedValueOnce(new Error("检查网络中断"))
      .mockResolvedValueOnce(auditFor(root));
    const create = vi.spyOn(api, "createGenerationCandidateManualEdit");
    renderWorkspace();
    const retry = await screen.findByRole("button", { name: "重新读取检查" });
    fireEvent.click(retry);
    expect(await screen.findByText("需要作者人工核对。", { exact: false })).toBeInTheDocument();
    expect(auditSpy).toHaveBeenCalledTimes(2);
    expect(create).not.toHaveBeenCalled();
  });

  it("announces audit completion politely without stealing the author's focus", async () => {
    const root = await candidate(rootContent, "generated", 1);
    const audit = deferred<GenerationCandidateAuditResponse>();
    vi.spyOn(api, "listGenerationCandidateVersions").mockResolvedValue(list([root]));
    vi.spyOn(api, "getGenerationCandidateVersion").mockResolvedValue(root);
    vi.spyOn(api, "getGenerationCandidateAudit").mockReturnValue(audit.promise);
    renderWorkspace();
    const edit = await screen.findByRole("button", { name: "基于此候选编辑" });
    edit.focus();
    audit.resolve(auditFor(root));
    const announcement = await screen.findByText("需要作者人工核对。", { exact: false });
    expect(announcement).toHaveAttribute("role", "status");
    expect(edit).toHaveFocus();
  });

  it("loads a strictly older page only after the author explicitly requests it", async () => {
    const root = await candidate(rootContent, "generated", 1);
    const v5 = await manualVersion(5, "five");
    const v4 = await manualVersion(4, "four");
    const v3 = await manualVersion(3, "three");
    const v2 = await manualVersion(2, "two");
    const listSpy = vi.spyOn(api, "listGenerationCandidateVersions")
      .mockResolvedValueOnce(list([v5, v4], { hasMore: true, nextCursor: "4" }))
      .mockResolvedValueOnce(list([v3, v2]));
    vi.spyOn(api, "getGenerationCandidateVersion").mockResolvedValue(root);

    renderWorkspace();
    const more = await screen.findByRole("button", { name: "加载更多版本" });
    expect(listSpy).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("button", { name: /^版本 3 / })).not.toBeInTheDocument();
    fireEvent.click(more);

    const firstNewRow = await screen.findByRole("button", { name: /^版本 3 / });
    await waitFor(() => expect(firstNewRow).toHaveFocus());
    expect(screen.getByText("已加载 2 个较早版本。 已显示全部候选版本。")).toHaveAttribute("role", "status");
    expect(listSpy).toHaveBeenCalledTimes(2);
    expect(listSpy.mock.calls[1]).toEqual([
      projectId,
      runId,
      { limit: 50, beforeVersionNo: 4, signal: expect.any(AbortSignal) },
    ]);
    expect(screen.getByRole("button", { name: /^版本 2 / })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "加载更多版本" })).not.toBeInTheDocument();
  });

  it("focuses the list title and announces completion when an explicit page contains no new rows", async () => {
    const root = await candidate(rootContent, "generated", 1);
    const v2 = await manualVersion(2, "two");
    vi.spyOn(api, "listGenerationCandidateVersions")
      .mockResolvedValueOnce(list([v2], { hasMore: true, nextCursor: "2" }))
      .mockResolvedValueOnce(list([]));
    vi.spyOn(api, "getGenerationCandidateVersion").mockResolvedValue(root);
    renderWorkspace();
    fireEvent.click(await screen.findByRole("button", { name: "加载更多版本" }));
    await waitFor(() => expect(screen.getByRole("heading", { name: "版本列表" })).toHaveFocus());
    expect(screen.getByText("没有更多候选版本，已显示全部候选版本。")).toHaveAttribute("role", "status");
  });

  it.each([
    ["重复", async () => {
      const v4 = await manualVersion(4, "four");
      return list([{ ...(await manualVersion(3, "five")), id: v4.id }]);
    }],
    ["乱序", async () => list([await manualVersion(2, "two"), await manualVersion(3, "three")])],
  ])("keeps the first page when a %s next page violates the strict merge contract", async (_label, nextPage) => {
    const root = await candidate(rootContent, "generated", 1);
    const v5 = await manualVersion(5, "five");
    const v4 = await manualVersion(4, "four");
    vi.spyOn(api, "listGenerationCandidateVersions")
      .mockResolvedValueOnce(list([v5, v4], { hasMore: true, nextCursor: "4" }))
      .mockResolvedValueOnce(await nextPage());
    vi.spyOn(api, "getGenerationCandidateVersion").mockResolvedValue(root);
    const create = vi.spyOn(api, "createGenerationCandidateManualEdit");

    renderWorkspace();
    fireEvent.click(await screen.findByRole("button", { name: "加载更多版本" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/重复|降序|顺序无效|页尾|契约/);
    expect(screen.getByRole("button", { name: /^版本 5 / })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^版本 4 / })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^版本 [23] / })).not.toBeInTheDocument();
    expect(create).not.toHaveBeenCalled();
  });

  it("keeps the shown page and retries the same explicit next-page read after a network error", async () => {
    const root = await candidate(rootContent, "generated", 1);
    const v5 = await manualVersion(5, "five");
    const v4 = await manualVersion(4, "four");
    const v3 = await manualVersion(3, "three");
    const listSpy = vi.spyOn(api, "listGenerationCandidateVersions")
      .mockResolvedValueOnce(list([v5, v4], { hasMore: true, nextCursor: "4" }))
      .mockRejectedValueOnce(new Error("分页网络中断"))
      .mockResolvedValueOnce(list([v3]));
    vi.spyOn(api, "getGenerationCandidateVersion").mockResolvedValue(root);

    renderWorkspace();
    fireEvent.click(await screen.findByRole("button", { name: "加载更多版本" }));
    const retry = await screen.findByRole("button", { name: "重试加载更多版本" });
    await waitFor(() => expect(retry.closest("[role='alert']")).toHaveFocus());
    expect(screen.getByRole("button", { name: /^版本 4 / })).toBeInTheDocument();
    fireEvent.click(retry);
    expect(await screen.findByRole("button", { name: /^版本 3 / })).toBeInTheDocument();
    expect(listSpy).toHaveBeenCalledTimes(3);
  });

  it("falls back to the list title when list retry and pagination rows remain storage-blocked", async () => {
    const root = await candidate(rootContent, "generated", 1);
    const v4 = await manualVersion(4, "four");
    const v3 = await manualVersion(3, "three");
    const v2 = await manualVersion(2, "two");
    vi.spyOn(api, "listGenerationCandidateVersions")
      .mockRejectedValueOnce(new Error("首次列表失败"))
      .mockResolvedValueOnce(list([v4, v3], { hasMore: true, nextCursor: "3" }))
      .mockResolvedValueOnce(list([v2]));
    vi.spyOn(api, "getGenerationCandidateVersion").mockResolvedValue(root);
    sessionStorage.setItem(`novel_candidate_manual_edit_draft_v1:${userId}:${projectId}`, "{broken");
    renderWorkspace();
    fireEvent.click(await screen.findByRole("button", { name: "重新读取版本列表" }));
    const listTitle = screen.getByRole("heading", { name: "版本列表" });
    await waitFor(() => expect(listTitle).toHaveFocus());
    expect(screen.getByRole("button", { name: /^版本 4 / })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "加载更多版本" }));
    await screen.findByText("已加载 1 个较早版本。 已显示全部候选版本。");
    await waitFor(() => expect(listTitle).toHaveFocus());
    expect(screen.getByRole("button", { name: /^版本 2 / })).toBeDisabled();
  });

  it("aborts a deferred page on unmount and never submits or lands its rows", async () => {
    const root = await candidate(rootContent, "generated", 1);
    const v5 = await manualVersion(5, "five");
    const v4 = await manualVersion(4, "four");
    const v3 = await manualVersion(3, "three");
    const latePage = deferred<ReturnType<typeof list>>();
    const listSpy = vi.spyOn(api, "listGenerationCandidateVersions")
      .mockResolvedValueOnce(list([v5, v4], { hasMore: true, nextCursor: "4" }))
      .mockReturnValueOnce(latePage.promise);
    vi.spyOn(api, "getGenerationCandidateVersion").mockResolvedValue(root);
    const create = vi.spyOn(api, "createGenerationCandidateManualEdit");
    const rendered = renderWorkspace();
    fireEvent.click(await screen.findByRole("button", { name: "加载更多版本" }));
    await waitFor(() => expect(listSpy).toHaveBeenCalledTimes(2));
    const signal = listSpy.mock.calls[1][2]?.signal;
    rendered.unmount();
    expect(signal?.aborted).toBe(true);
    await act(async () => {
      latePage.resolve(list([v3]));
      await Promise.resolve();
    });
    expect(create).not.toHaveBeenCalled();
  });

  it.each(["user", "chapter"] as const)("ignores a deferred page after the %s scope changes", async (kind) => {
    const root = await candidate(rootContent, "generated", 1);
    const v5 = await manualVersion(5, "five");
    const v4 = await manualVersion(4, "four");
    const v3 = await manualVersion(3, "three");
    const latePage = deferred<ReturnType<typeof list>>();
    const listSpy = vi.spyOn(api, "listGenerationCandidateVersions").mockImplementation(async (_project, _run, options) =>
      options?.beforeVersionNo ? latePage.promise : list([v5, v4], { hasMore: true, nextCursor: "4" })
    );
    vi.spyOn(api, "getGenerationCandidateVersion").mockResolvedValue(root);
    const create = vi.spyOn(api, "createGenerationCandidateManualEdit");
    const otherUser = id("other-user");
    const otherChapter = id("other-chapter");
    const otherRun = { ...run, planning_chapter_id: otherChapter } as GenerationRunResponse;
    const node = (changed: boolean) => (
      <MemoryRouter initialEntries={[`/project/${projectId}/plan/chapters?candidate_version=${rootId}`]}>
        <CandidateVersionWorkspace
          userId={changed && kind === "user" ? otherUser : userId}
          projectId={projectId}
          chapterId={changed && kind === "chapter" ? otherChapter : chapterId}
          chapterTitle="第一章"
          run={changed && kind === "chapter" ? otherRun : run}
          initialCandidateId={rootId}
        />
      </MemoryRouter>
    );
    const rendered = render(node(false));
    fireEvent.click(await screen.findByRole("button", { name: "加载更多版本" }));
    await waitFor(() => expect(listSpy).toHaveBeenCalledTimes(2));
    const signal = listSpy.mock.calls[1][2]?.signal;
    rendered.rerender(node(true));
    expect(signal?.aborted).toBe(true);
    await act(async () => {
      latePage.resolve(list([v3]));
      await Promise.resolve();
    });
    expect(screen.queryByRole("button", { name: /^版本 3 / })).not.toBeInTheDocument();
    expect(create).not.toHaveBeenCalled();
  });

  it("does not reread list/detail/audit after an internal version pointer write", async () => {
    const root = await candidate(rootContent, "generated", 1);
    const manual = await candidate(editedContent, "manual_edit", 2);
    const listSpy = vi.spyOn(api, "listGenerationCandidateVersions").mockResolvedValue(list([manual, root]));
    const detailSpy = vi.spyOn(api, "getGenerationCandidateVersion").mockImplementation(async (_project, _run, candidateId) => candidateId === manual.id ? manual : root);
    const auditSpy = vi.spyOn(api, "getGenerationCandidateAudit").mockImplementation(async (_project, candidateId) => auditFor(candidateId === manual.id ? manual : root));

    renderWorkspace();
    await screen.findByText("第一章 · 候选版本 1");
    fireEvent.click(screen.getByRole("button", { name: /^版本 2 / }));
    await waitFor(() => expect(screen.getByText("第一章 · 候选版本 2")).toHaveFocus());
    await Promise.resolve();
    expect(listSpy).toHaveBeenCalledTimes(1);
    expect(detailSpy).toHaveBeenCalledTimes(2);
    expect(auditSpy).toHaveBeenCalledTimes(2);
  });

  it("performs exactly one strict detail and audit read for an external pointer change while preserving query", async () => {
    const root = await candidate(rootContent, "generated", 1);
    const manual = await candidate(editedContent, "manual_edit", 2);
    const listSpy = vi.spyOn(api, "listGenerationCandidateVersions").mockResolvedValue(list([manual, root]));
    const detailSpy = vi.spyOn(api, "getGenerationCandidateVersion").mockImplementation(async (_project, _run, candidateId) => candidateId === manual.id ? manual : root);
    const auditSpy = vi.spyOn(api, "getGenerationCandidateAudit").mockImplementation(async (_project, candidateId) => auditFor(candidateId === manual.id ? manual : root));
    const create = vi.spyOn(api, "createGenerationCandidateManualEdit");

    renderPointerWorkspace(manual.id);
    await screen.findByText("第一章 · 候选版本 1");
    fireEvent.click(screen.getByRole("button", { name: "模拟浏览器候选地址变化" }));
    await waitFor(() => expect(screen.getByText("第一章 · 候选版本 2")).toHaveFocus());
    expect(listSpy).toHaveBeenCalledTimes(1);
    expect(detailSpy).toHaveBeenCalledTimes(2);
    expect(auditSpy).toHaveBeenCalledTimes(2);
    expect(create).not.toHaveBeenCalled();
    expect(screen.getByTestId("candidate-query")).toHaveTextContent("keep=yes");
  });

  it("replaces an external pointer with the accepted candidate while editing without rereading or losing the draft", async () => {
    const root = await candidate(rootContent, "generated", 1);
    const manual = await candidate(editedContent, "manual_edit", 2);
    vi.spyOn(api, "listGenerationCandidateVersions").mockResolvedValue(list([manual, root]));
    const detailSpy = vi.spyOn(api, "getGenerationCandidateVersion").mockResolvedValue(root);
    const auditSpy = vi.spyOn(api, "getGenerationCandidateAudit").mockResolvedValue(auditFor(root));
    const create = vi.spyOn(api, "createGenerationCandidateManualEdit");
    renderPointerWorkspace(manual.id);
    fireEvent.click(await screen.findByRole("button", { name: "基于此候选编辑" }));
    fireEvent.change(screen.getByLabelText("编辑副本"), { target: { value: editedContent } });
    fireEvent.click(screen.getByRole("button", { name: "模拟浏览器候选地址变化" }));
    await waitFor(() => expect(screen.getByTestId("candidate-query")).toHaveTextContent(`candidate_version=${rootId}`));
    expect(screen.getByTestId("candidate-query")).toHaveTextContent("keep=yes");
    expect(screen.getByLabelText("编辑副本")).toHaveValue(editedContent);
    expect(detailSpy).toHaveBeenCalledTimes(1);
    expect(auditSpy).toHaveBeenCalledTimes(1);
    expect(create).not.toHaveBeenCalled();
  });

  it("replaces an external pointer with the accepted candidate while a v5 receipt is pending", async () => {
    const root = await candidate(rootContent, "generated", 1);
    const manual = await candidate(editedContent, "manual_edit", 2);
    vi.spyOn(api, "listGenerationCandidateVersions").mockResolvedValue(list([root]));
    const detailSpy = vi.spyOn(api, "getGenerationCandidateVersion").mockResolvedValue(root);
    const auditSpy = vi.spyOn(api, "getGenerationCandidateAudit");
    vi.spyOn(api, "getGenerationCandidateManualEditByKey").mockReturnValue(new Promise(() => undefined));
    const create = vi.spyOn(api, "createGenerationCandidateManualEdit");
    sessionStorage.setItem(pendingProjectOperationKey(userId, projectId), JSON.stringify({
      schema_version: 5, workspace: "candidate_manual_edit", user_id: userId,
      project_id: projectId, chapter_id: chapterId, run_id: runId,
      operation_key: "candidate:manual-edit:pointer-pending",
      payload: {
        operation_key: "candidate:manual-edit:pointer-pending", parent_candidate_id: root.id,
        expected_parent_version_no: root.version_no, expected_parent_checksum: root.content_checksum,
        expected_context_checksum: run.context_checksum, content: editedContent,
      },
      created_at: now,
    }));
    renderPointerWorkspace(manual.id);
    await screen.findByText("已保留本地恢复线索。只按原编号核对，不会自动重复另存。");
    fireEvent.click(screen.getByRole("button", { name: "模拟浏览器候选地址变化" }));
    await waitFor(() => expect(screen.getByTestId("candidate-query")).toHaveTextContent(`candidate_version=${rootId}`));
    expect(screen.getByTestId("candidate-query")).toHaveTextContent("keep=yes");
    expect(screen.getByText("第一章 · 候选版本 1")).toBeInTheDocument();
    expect(detailSpy).toHaveBeenCalledTimes(1);
    expect(auditSpy).not.toHaveBeenCalled();
    expect(create).not.toHaveBeenCalled();
  });

  it("aborts a deferred external pointer read on unmount without audit or POST", async () => {
    const root = await candidate(rootContent, "generated", 1);
    const manual = await candidate(editedContent, "manual_edit", 2);
    const lateDetail = deferred<typeof manual>();
    vi.spyOn(api, "listGenerationCandidateVersions").mockResolvedValue(list([manual, root]));
    const detailSpy = vi.spyOn(api, "getGenerationCandidateVersion")
      .mockResolvedValueOnce(root)
      .mockReturnValueOnce(lateDetail.promise);
    const auditSpy = vi.spyOn(api, "getGenerationCandidateAudit").mockResolvedValue(auditFor(root));
    const create = vi.spyOn(api, "createGenerationCandidateManualEdit");
    const rendered = renderPointerWorkspace(manual.id);
    await screen.findByText("第一章 · 候选版本 1");
    fireEvent.click(screen.getByRole("button", { name: "模拟浏览器候选地址变化" }));
    await waitFor(() => expect(detailSpy).toHaveBeenCalledTimes(2));
    const signal = detailSpy.mock.calls[1][3];
    rendered.unmount();
    expect(signal?.aborted).toBe(true);
    await act(async () => {
      lateDetail.resolve(manual);
      await Promise.resolve();
    });
    expect(auditSpy).toHaveBeenCalledTimes(1);
    expect(create).not.toHaveBeenCalled();
  });
});
