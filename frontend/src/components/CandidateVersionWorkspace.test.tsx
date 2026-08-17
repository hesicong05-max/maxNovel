import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, useSearchParams } from "react-router-dom";
import { useCallback, useState } from "react";
import CandidateVersionWorkspace from "./CandidateVersionWorkspace";
import { ApiError, api } from "@/services/api";
import { pendingProjectOperationKey } from "@/services/pendingProjectOperations";
import type { GenerationCandidateSelectionCurrentResponse, GenerationRunResponse } from "@/types/generation";
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
    chapter: { title: "第一章", target_word_count: 1000 },
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

function selectionItem(value: GenerationCandidateVersionDetail) {
  const { project_id, run_id, planning_chapter_id, content, content_format, ...item } = value;
  return item;
}

const noSelection: GenerationCandidateSelectionCurrentResponse = {
  schema_version: 1,
  project_id: projectId,
  planning_chapter_id: chapterId,
  state: "none",
  selection_version: 0,
  run_id: null,
  context_checksum: null,
  candidate: null,
  selected_at: null,
  changed_by: null,
};

function selectedCurrent(
  value: GenerationCandidateVersionDetail,
  selectionVersion = 1
): Extract<GenerationCandidateSelectionCurrentResponse, { state: "selected" }> {
  return {
    schema_version: 1,
    project_id: projectId,
    planning_chapter_id: chapterId,
    state: "selected",
    selection_version: selectionVersion,
    run_id: value.run_id,
    context_checksum: run.context_checksum,
    candidate: selectionItem(value),
    selected_at: now,
    changed_by: userId,
  };
}

function selectionReceipt(
  value: GenerationCandidateVersionDetail,
  operationKey: string,
  previous: GenerationCandidateSelectionCurrentResponse = noSelection,
  selectedAt = now
) {
  const previousSnapshot = {
    state: previous.state,
    selection_version: previous.selection_version,
    run_id: previous.run_id,
    context_checksum: previous.context_checksum,
    candidate: previous.candidate,
  };
  return {
    schema_version: 1,
    project_id: projectId,
    planning_chapter_id: chapterId,
    operation_key: operationKey,
    replayed: false,
    changed: true,
    ai_invoked: false,
    billing_effect: "none",
    usage_status: "not_applicable",
    previous: previousSnapshot,
    result: {
      state: "selected",
      selection_version: previous.selection_version + 1,
      run_id: value.run_id,
      context_checksum: run.context_checksum,
      candidate: selectionItem(value),
    },
    selected_at: selectedAt,
    changed_by: userId,
  };
}

function pendingSelection(value: GenerationCandidateVersionDetail, operationKey: string) {
  return {
    schema_version: 6 as const,
    workspace: "candidate_selection" as const,
    user_id: userId,
    project_id: projectId,
    chapter_id: chapterId,
    run_id: runId,
    operation_key: operationKey,
    payload: {
      operation_key: operationKey,
      expected_selection_version: 0,
      target_run_id: runId,
      target_candidate_id: value.id,
      expected_candidate_version_no: value.version_no,
      expected_candidate_checksum: value.content_checksum,
      expected_context_checksum: run.context_checksum,
    },
    expected_previous: { state: "none" as const, selection_version: 0, run_id: null, context_checksum: null, candidate: null },
    expected_target: selectionItem(value),
    created_at: now,
  };
}

function renderSelectionWorkspace(
  refresh: () => Promise<GenerationCandidateSelectionCurrentResponse>,
  initial: GenerationCandidateSelectionCurrentResponse = noSelection,
  onLockChange?: (locked: boolean) => void
) {
  function SelectionWorkspace() {
    const [current, setCurrent] = useState(initial);
    const refreshCurrent = useCallback(async () => {
      const value = await refresh();
      setCurrent(value);
      return value;
    }, []);
    return <CandidateVersionWorkspace userId={userId} projectId={projectId} chapterId={chapterId} chapterTitle="第一章" run={run} initialCandidateId={rootId} selectionCurrent={current} selectionLoading={false} selectionError="" onRefreshSelection={refreshCurrent} onLockChange={onLockChange} />;
  }
  return render(<MemoryRouter initialEntries={[`/project/${projectId}/plan/chapters?scope=chapter&target=${chapterId}&generation_run=${runId}&candidate_version=${rootId}`]}><SelectionWorkspace /></MemoryRouter>);
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

  it.each(["identity", "unmount"] as const)("cancels the accepted-candidate focus frame after %s cleanup", async (cleanupKind) => {
    const root = await candidate(rootContent, "generated", 1);
    const manual = await candidate(editedContent, "manual_edit", 2);
    const never = new Promise<ReturnType<typeof list>>(() => undefined);
    vi.spyOn(api, "listGenerationCandidateVersions")
      .mockResolvedValueOnce(list([root]))
      .mockResolvedValueOnce(list([manual, root]))
      .mockReturnValue(never);
    vi.spyOn(api, "getGenerationCandidateVersion").mockResolvedValue(root);
    vi.spyOn(api, "createGenerationCandidateManualEdit").mockResolvedValue({
      schema_version: 1,
      replayed: false,
      ai_invoked: false,
      billing_effect: "none",
      usage_status: "not_applicable",
      candidate: manual,
    });
    const frames = new Map<number, FrameRequestCallback>();
    let frameId = 0;
    vi.spyOn(window, "requestAnimationFrame").mockImplementation((callback) => {
      frameId += 1;
      frames.set(frameId, callback);
      return frameId;
    });
    const cancelFrame = vi.spyOn(window, "cancelAnimationFrame").mockImplementation((idToCancel) => {
      frames.delete(idToCancel);
    });
    const otherChapterId = id("other-chapter");
    const otherRun = { ...run, id: id("other-run"), planning_chapter_id: otherChapterId } as GenerationRunResponse;
    const node = (changed: boolean) => (
      <MemoryRouter initialEntries={[`/project/${projectId}/plan/chapters?scope=chapter&target=${chapterId}&generation_run=${runId}`]}>
        <CandidateVersionWorkspace
          userId={userId}
          projectId={projectId}
          chapterId={changed ? otherChapterId : chapterId}
          chapterTitle={changed ? "第二章" : "第一章"}
          run={changed ? otherRun : run}
          initialCandidateId={rootId}
        />
      </MemoryRouter>
    );
    const rendered = render(node(false));
    await screen.findByText("第一章 · 候选版本 1");
    fireEvent.click(screen.getByRole("button", { name: "基于此候选编辑" }));
    fireEvent.change(screen.getByLabelText("编辑副本"), { target: { value: editedContent } });
    fireEvent.click(screen.getByRole("button", { name: "保存为新候选版本" }));
    await screen.findByText("第一章 · 候选版本 2");
    const acceptedFrameId = frameId;
    const acceptedFrame = frames.get(acceptedFrameId);
    expect(acceptedFrame).toBeDefined();

    if (cleanupKind === "identity") rendered.rerender(node(true));
    else rendered.unmount();
    expect(cancelFrame).toHaveBeenCalledWith(acceptedFrameId);

    const sentinel = document.createElement("button");
    document.body.append(sentinel);
    sentinel.focus();
    act(() => acceptedFrame?.(performance.now()));
    expect(sentinel).toHaveFocus();
    sentinel.remove();
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
    expect(screen.getByText(/当前章节：第一章。均为独立候选，不会覆盖原稿；采用只更新章节的候选指针。/)).toBeInTheDocument();
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

  it("saves v6 before one selection POST and clears only after strict current confirmation", async () => {
    const root = await candidate(rootContent, "generated", 1);
    vi.spyOn(api, "listGenerationCandidateVersions").mockResolvedValue(list([root]));
    vi.spyOn(api, "getGenerationCandidateVersion").mockResolvedValue(root);
    const refresh = vi.fn().mockResolvedValue(selectedCurrent(root));
    const select = vi.spyOn(api, "selectGenerationCandidate")
      .mockImplementation(async (_project, _chapter, payload) => {
        const stored = JSON.parse(sessionStorage.getItem(
          pendingProjectOperationKey(userId, projectId)
        )!);
        expect(stored).toMatchObject({
          schema_version: 6,
          workspace: "candidate_selection",
          operation_key: payload.operation_key,
          expected_target: selectionItem(root),
        });
        return {
          schema_version: 1,
          project_id: projectId,
          planning_chapter_id: chapterId,
          operation_key: payload.operation_key,
          replayed: false,
          changed: true,
          ai_invoked: false,
          billing_effect: "none",
          usage_status: "not_applicable",
          previous: { state: "none", selection_version: 0, run_id: null, context_checksum: null, candidate: null },
          result: { state: "selected", selection_version: 1, run_id: runId, context_checksum: run.context_checksum, candidate: selectionItem(root) },
          selected_at: now,
          changed_by: userId,
        };
      });
    renderSelectionWorkspace(refresh);

    const trigger = await screen.findByRole("button", { name: "采用此版本" });
    fireEvent.click(trigger);
    const dialog = screen.getByRole("alertdialog", { name: "确认采用此候选" });
    expect(dialog).toBeInTheDocument();
    const cancel = screen.getByRole("button", { name: "取消，不改变采用状态" });
    expect(cancel).toHaveFocus();
    expect(dialog).toHaveTextContent("未调用模型、无新增模型费用");
    expect(dialog).toHaveTextContent(/不修改候选正文/);
    expect(dialog).toHaveTextContent(/不会确认设定事实、检查结论或伏笔状态/);
    expect(dialog).toHaveTextContent(/首次采用暂不提供取消采用/);
    fireEvent.click(cancel);
    expect(trigger).toHaveFocus();
    fireEvent.click(trigger);
    fireEvent.click(screen.getByRole("button", { name: "确认更新章节采用版本" }));

    await waitFor(() => expect(select).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(refresh).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.getByRole("heading", { name: "章节采用状态" })).toHaveFocus());
    expect(sessionStorage.getItem(pendingProjectOperationKey(userId, projectId))).toBeNull();
    expect(screen.getAllByText("章节采用版本").length).toBeGreaterThan(0);
    expect(screen.getByText(/未覆盖原稿，本次未调用模型/)).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent(/candidate:select:|[a-f0-9]{64}/);
  });

  it("locates an adopted target beyond the first fifty versions before clearing v6", async () => {
    const target = await candidate(rootContent, "generated", 51);
    const listSpy = vi.spyOn(api, "listGenerationCandidateVersions").mockImplementation(
      async (_project, _run, options) => options?.beforeVersionNo === 52
        ? list([target]) : list([])
    );
    vi.spyOn(api, "getGenerationCandidateVersion").mockResolvedValue(target);
    vi.spyOn(api, "selectGenerationCandidate").mockImplementation(async (_project, _chapter, payload) =>
      selectionReceipt(target, payload.operation_key));
    const refresh = vi.fn().mockResolvedValue(selectedCurrent(target));
    renderSelectionWorkspace(refresh);
    fireEvent.click(await screen.findByRole("button", { name: "采用此版本" }));
    fireEvent.click(screen.getByRole("button", { name: "确认更新章节采用版本" }));
    await waitFor(() => expect(sessionStorage.getItem(
      pendingProjectOperationKey(userId, projectId)
    )).toBeNull());
    expect(listSpy).toHaveBeenCalledWith(projectId, runId, expect.objectContaining({
      beforeVersionNo: 52,
      limit: 1,
    }));
  });

  it("recovers a version-fifty-one selection by key and clears only after its exact page", async () => {
    const target = await candidate(rootContent, "generated", 51);
    const operationKey = "candidate:select:recovery-51";
    const operation = {
      schema_version: 6,
      workspace: "candidate_selection",
      user_id: userId,
      project_id: projectId,
      chapter_id: chapterId,
      run_id: runId,
      operation_key: operationKey,
      payload: {
        operation_key: operationKey,
        expected_selection_version: 0,
        target_run_id: runId,
        target_candidate_id: target.id,
        expected_candidate_version_no: 51,
        expected_candidate_checksum: target.content_checksum,
        expected_context_checksum: run.context_checksum,
      },
      expected_previous: { state: "none", selection_version: 0, run_id: null, context_checksum: null, candidate: null },
      expected_target: selectionItem(target),
      created_at: now,
    };
    sessionStorage.setItem(pendingProjectOperationKey(userId, projectId), JSON.stringify(operation));
    const listSpy = vi.spyOn(api, "listGenerationCandidateVersions").mockImplementation(
      async (_project, _run, options) => options?.beforeVersionNo === 52
        ? list([target]) : list([])
    );
    vi.spyOn(api, "getGenerationCandidateVersion").mockResolvedValue(target);
    vi.spyOn(api, "getGenerationCandidateSelectionByKey").mockResolvedValue(
      selectionReceipt(target, operationKey)
    );
    const post = vi.spyOn(api, "selectGenerationCandidate");
    renderSelectionWorkspace(vi.fn().mockResolvedValue(selectedCurrent(target)));
    await waitFor(() => expect(sessionStorage.getItem(
      pendingProjectOperationKey(userId, projectId)
    )).toBeNull());
    expect(listSpy).toHaveBeenCalledWith(projectId, runId, expect.objectContaining({
      beforeVersionNo: 52,
      limit: 1,
    }));
    expect(post).not.toHaveBeenCalled();
  });

  it("does zero selection POST when the v6 recovery record cannot be stored", async () => {
    const root = await candidate(rootContent, "generated", 1);
    vi.spyOn(api, "listGenerationCandidateVersions").mockResolvedValue(list([root]));
    vi.spyOn(api, "getGenerationCandidateVersion").mockResolvedValue(root);
    const select = vi.spyOn(api, "selectGenerationCandidate");
    renderSelectionWorkspace(vi.fn().mockResolvedValue(noSelection));
    await screen.findByRole("button", { name: "采用此版本" });
    sessionStorage.setItem(pendingProjectOperationKey(userId, projectId), JSON.stringify({
      schema_version: 1,
      operation_key: "planning:blocked-by-existing-owner",
    }));
    fireEvent.click(screen.getByRole("button", { name: "采用此版本" }));
    fireEvent.click(screen.getByRole("button", { name: "确认更新章节采用版本" }));
    expect(await screen.findByText(/无法在浏览器保存采用恢复线索/)).toHaveFocus();
    expect(select).not.toHaveBeenCalled();
  });

  it("keeps v6 pending and the page lock when refreshed current disagrees with the strict receipt", async () => {
    const root = await candidate(rootContent, "generated", 1);
    vi.spyOn(api, "listGenerationCandidateVersions").mockResolvedValue(list([root]));
    vi.spyOn(api, "getGenerationCandidateVersion").mockResolvedValue(root);
    vi.spyOn(api, "selectGenerationCandidate").mockImplementation(async (_project, _chapter, payload) => ({
      schema_version: 1,
      project_id: projectId,
      planning_chapter_id: chapterId,
      operation_key: payload.operation_key,
      replayed: false,
      changed: true,
      ai_invoked: false,
      billing_effect: "none",
      usage_status: "not_applicable",
      previous: { state: "none", selection_version: 0, run_id: null, context_checksum: null, candidate: null },
      result: { state: "selected", selection_version: 1, run_id: runId, context_checksum: run.context_checksum, candidate: selectionItem(root) },
      selected_at: now,
      changed_by: userId,
    }));
    const onLockChange = vi.fn();
    renderSelectionWorkspace(vi.fn().mockResolvedValue(noSelection), noSelection, onLockChange);
    fireEvent.click(await screen.findByRole("button", { name: "采用此版本" }));
    fireEvent.click(screen.getByRole("button", { name: "确认更新章节采用版本" }));
    expect(await screen.findByText(/章节采用状态与本次严格回执不一致/)).toHaveFocus();
    expect(sessionStorage.getItem(pendingProjectOperationKey(userId, projectId))).not.toBeNull();
    expect(screen.queryByRole("button", { name: "按原编号核对采用状态" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "明确放弃失效线索" })).toBeInTheDocument();
    expect(onLockChange).toHaveBeenCalledWith(true);
  });

  it("turns an unknown selection POST into by-key GET and reconfirms exact 404 before the original-key retry", async () => {
    const root = await candidate(rootContent, "generated", 1);
    vi.spyOn(api, "listGenerationCandidateVersions").mockResolvedValue(list([root]));
    vi.spyOn(api, "getGenerationCandidateVersion").mockResolvedValue(root);
    const refresh = vi.fn().mockResolvedValue(selectedCurrent(root));
    const select = vi.spyOn(api, "selectGenerationCandidate")
      .mockRejectedValueOnce(new Error("network"))
      .mockImplementationOnce(async (_project, _chapter, payload) => ({
        schema_version: 1,
        project_id: projectId,
        planning_chapter_id: chapterId,
        operation_key: payload.operation_key,
        replayed: false,
        changed: true,
        ai_invoked: false,
        billing_effect: "none",
        usage_status: "not_applicable",
        previous: { state: "none", selection_version: 0, run_id: null, context_checksum: null, candidate: null },
        result: { state: "selected", selection_version: 1, run_id: runId, context_checksum: run.context_checksum, candidate: selectionItem(root) },
        selected_at: now,
        changed_by: userId,
      }));
    const byKey = vi.spyOn(api, "getGenerationCandidateSelectionByKey").mockRejectedValue(new ApiError(404, {
      code: "GENERATION_CANDIDATE_SELECTION_OPERATION_NOT_FOUND",
      detail: "尚未找到该章节采用操作。",
      retryable: true,
      recommended_action: "retry_original_candidate_selection",
    }));
    renderSelectionWorkspace(refresh);
    fireEvent.click(await screen.findByRole("button", { name: "采用此版本" }));
    fireEvent.click(screen.getByRole("button", { name: "确认更新章节采用版本" }));
    const retry = await screen.findByRole("button", { name: "再次确认原请求" });
    expect(select).toHaveBeenCalledTimes(1);
    expect(byKey).toHaveBeenCalledTimes(1);
    fireEvent.click(retry);
    expect(screen.getByRole("alertdialog", { name: "再次确认原采用请求" })).toBeInTheDocument();
    const originalCancel = screen.getByRole("button", { name: "取消，不改变采用状态" });
    const originalConfirm = screen.getByRole("button", { name: "确认并使用原编号重试" });
    expect(originalCancel).toHaveFocus();
    fireEvent.keyDown(document, { key: "Tab", shiftKey: true });
    expect(originalConfirm).toHaveFocus();
    fireEvent.keyDown(document, { key: "Tab" });
    expect(originalCancel).toHaveFocus();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(retry).toHaveFocus();
    fireEvent.click(retry);
    expect(select).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "确认并使用原编号重试" }));
    await waitFor(() => expect(select).toHaveBeenCalledTimes(2));
    expect(select.mock.calls[1][2].operation_key).toBe(select.mock.calls[0][2].operation_key);
    await waitFor(() => expect(sessionStorage.getItem(pendingProjectOperationKey(userId, projectId))).toBeNull());
  });

  it("refreshes strict current on a selection version conflict and requires a new-key confirmation", async () => {
    const root = await candidate(rootContent, "generated", 1);
    const concurrent = await manualVersion(2, "concurrent");
    vi.spyOn(api, "listGenerationCandidateVersions").mockImplementation(async (_project, _run, options) =>
      options?.beforeVersionNo === root.version_no + 1 ? list([root]) : list([concurrent, root]));
    vi.spyOn(api, "getGenerationCandidateVersion").mockResolvedValue(root);
    const concurrentCurrent = selectedCurrent(concurrent, 1);
    const finalCurrent = selectedCurrent(root, 2);
    const refresh = vi.fn()
      .mockResolvedValueOnce(concurrentCurrent)
      .mockResolvedValueOnce(finalCurrent);
    const select = vi.spyOn(api, "selectGenerationCandidate")
      .mockRejectedValueOnce(new ApiError(409, {
        detail: "章节采用版本已变化",
        code: "GENERATION_CANDIDATE_SELECTION_VERSION_CONFLICT",
        retryable: false,
        recommended_action: "reload_candidate_selection",
      }))
      .mockImplementationOnce(async (_project, _chapter, payload) => ({
        schema_version: 1,
        project_id: projectId,
        planning_chapter_id: chapterId,
        operation_key: payload.operation_key,
        replayed: false,
        changed: true,
        ai_invoked: false,
        billing_effect: "none",
        usage_status: "not_applicable",
        previous: { state: "selected", selection_version: 1, run_id: runId, context_checksum: run.context_checksum, candidate: selectionItem(concurrent) },
        result: { state: "selected", selection_version: 2, run_id: runId, context_checksum: run.context_checksum, candidate: selectionItem(root) },
        selected_at: now,
        changed_by: userId,
      }));
    renderSelectionWorkspace(refresh);
    fireEvent.click(await screen.findByRole("button", { name: "采用此版本" }));
    fireEvent.click(screen.getByRole("button", { name: "确认更新章节采用版本" }));
    const restart = await screen.findByRole("button", { name: "基于最新采用状态重新确认" });
    expect(select).toHaveBeenCalledTimes(1);
    expect(refresh).toHaveBeenCalledTimes(1);
    const originalKey = select.mock.calls[0][2].operation_key;
    expect(sessionStorage.getItem(pendingProjectOperationKey(userId, projectId))).toBeNull();
    fireEvent.click(restart);
    expect(screen.getByRole("alertdialog", { name: "确认改用此候选" })).toBeInTheDocument();
    expect(select).toHaveBeenCalledTimes(1);
    const conflictCancel = screen.getByRole("button", { name: "取消，不改变采用状态" });
    fireEvent.click(conflictCancel);
    expect(restart).toHaveFocus();
    fireEvent.click(restart);
    fireEvent.click(screen.getByRole("button", { name: "确认更新章节采用版本" }));
    await waitFor(() => expect(select).toHaveBeenCalledTimes(2));
    expect(select.mock.calls[1][2].operation_key).not.toBe(originalKey);
    await waitFor(() => expect(refresh).toHaveBeenCalledTimes(2));
    expect(sessionStorage.getItem(pendingProjectOperationKey(userId, projectId))).toBeNull();
  });

  it("uses the frozen run title for old-run adoption and rejects a candidate forged with the live title", async () => {
    const root = await candidate(rootContent, "generated", 1);
    const listSpy = vi.spyOn(api, "listGenerationCandidateVersions").mockResolvedValue(list([root]));
    const detailSpy = vi.spyOn(api, "getGenerationCandidateVersion").mockResolvedValue(root);
    const rendered = render(
      <MemoryRouter>
        <CandidateVersionWorkspace
          userId={userId} projectId={projectId} chapterId={chapterId}
          chapterTitle="实时新标题" run={run} initialCandidateId={root.id}
          selectionCurrent={noSelection} onRefreshSelection={vi.fn().mockResolvedValue(noSelection)}
          disabledReason="当前检查记录已过期，请先重新检查上下文。"
          selectionWarning="这条检查记录基于旧冻结上下文；仍可明确采用候选。"
        />
      </MemoryRouter>
    );
    const adopt = await screen.findByRole("button", { name: "采用此版本" });
    expect(adopt).toBeEnabled();
    fireEvent.click(adopt);
    expect(screen.getByRole("alertdialog")).toHaveTextContent(/旧冻结上下文/);
    rendered.unmount();

    const forged = { ...root, title: "实时新标题" };
    listSpy.mockResolvedValue(list([forged]));
    detailSpy.mockResolvedValue(forged);
    render(
      <MemoryRouter>
        <CandidateVersionWorkspace userId={userId} projectId={projectId} chapterId={chapterId} chapterTitle="实时新标题" run={run} initialCandidateId={root.id} />
      </MemoryRouter>
    );
    expect(await screen.findByText(/候选版本列表项的身份或内容元数据无效/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "采用此版本" })).not.toBeInTheDocument();
  });

  it("keeps stale context as a warning but blocks adoption for an independent archived reason", async () => {
    const root = await candidate(rootContent, "generated", 1);
    vi.spyOn(api, "listGenerationCandidateVersions").mockResolvedValue(list([root]));
    vi.spyOn(api, "getGenerationCandidateVersion").mockResolvedValue(root);
    const post = vi.spyOn(api, "selectGenerationCandidate");
    render(
      <MemoryRouter>
        <CandidateVersionWorkspace
          userId={userId} projectId={projectId} chapterId={chapterId}
          chapterTitle="实时新标题" run={run} initialCandidateId={root.id}
          selectionCurrent={noSelection} onRefreshSelection={vi.fn().mockResolvedValue(noSelection)}
          selectionWarning="这条检查记录基于旧冻结上下文；仍可明确采用候选。"
          selectionDisabledReason="归档章节不能修改采用版本；请先恢复章节。"
        />
      </MemoryRouter>
    );
    const adopt = await screen.findByRole("button", { name: "采用此版本" });
    expect(adopt).toBeDisabled();
    fireEvent.click(adopt);
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(post).not.toHaveBeenCalled();
    expect(screen.getByText(/归档章节不能修改采用版本/)).toBeInTheDocument();
  });

  it.each([
    "归档章节不能修改采用版本；请先恢复章节。",
    "项目正在维护，候选采用暂不可提交。",
    "浏览器恢复存储尚未恢复，不能提交候选采用。",
    "当前规划或设定尚未完成权威同步，不能提交候选采用。",
  ])("rechecks a changed selection gate inside the open dialog: %s", async (reason) => {
    const root = await candidate(rootContent, "generated", 1);
    vi.spyOn(api, "listGenerationCandidateVersions").mockResolvedValue(list([root]));
    vi.spyOn(api, "getGenerationCandidateVersion").mockResolvedValue(root);
    const post = vi.spyOn(api, "selectGenerationCandidate");
    function GateHarness() {
      const [blocked, setBlocked] = useState("");
      const refresh = useCallback(async () => noSelection, []);
      return <MemoryRouter><button onClick={() => setBlocked(reason)}>模拟门禁变化</button><CandidateVersionWorkspace
        userId={userId} projectId={projectId} chapterId={chapterId} chapterTitle="第一章"
        run={run} initialCandidateId={root.id} selectionCurrent={noSelection}
        onRefreshSelection={refresh} selectionDisabledReason={blocked}
      /></MemoryRouter>;
    }
    render(<GateHarness />);
    fireEvent.click(await screen.findByRole("button", { name: "采用此版本" }));
    fireEvent.click(screen.getByRole("button", { name: "模拟门禁变化" }));
    const confirm = screen.getByRole("button", { name: "确认更新章节采用版本" });
    expect(confirm).toBeDisabled();
    fireEvent.click(confirm);
    expect(post).not.toHaveBeenCalled();
    expect(sessionStorage.getItem(pendingProjectOperationKey(userId, projectId))).toBeNull();
  });

  it("keeps corrupt v6 selection storage locked when manual and draft effects finish", async () => {
    const root = await candidate(rootContent, "generated", 1);
    sessionStorage.setItem(pendingProjectOperationKey(userId, projectId), JSON.stringify({
      schema_version: 6,
      workspace: "candidate_selection",
    }));
    vi.spyOn(api, "listGenerationCandidateVersions").mockResolvedValue(list([root]));
    vi.spyOn(api, "getGenerationCandidateVersion").mockResolvedValue(root);
    const selectionPost = vi.spyOn(api, "selectGenerationCandidate");
    const manualPost = vi.spyOn(api, "createGenerationCandidateManualEdit");
    renderSelectionWorkspace(vi.fn().mockResolvedValue(noSelection));
    const adopt = await screen.findByRole("button", { name: "采用此版本" });
    const edit = screen.getByRole("button", { name: "基于此候选编辑" });
    expect(adopt).toBeDisabled();
    expect(edit).toBeDisabled();
    fireEvent.click(adopt);
    fireEvent.click(edit);
    expect(selectionPost).not.toHaveBeenCalled();
    expect(manualPost).not.toHaveBeenCalled();
  });

  it("rereads the shared slot after a parent recovery revision and releases only the selection storage lock", async () => {
    const root = await candidate(rootContent, "generated", 1);
    const storageKey = pendingProjectOperationKey(userId, projectId);
    sessionStorage.setItem(storageKey, JSON.stringify({ schema_version: 6, workspace: "candidate_selection" }));
    vi.spyOn(api, "listGenerationCandidateVersions").mockResolvedValue(list([root]));
    vi.spyOn(api, "getGenerationCandidateVersion").mockResolvedValue(root);
    const post = vi.spyOn(api, "selectGenerationCandidate");
    function RecoveryHarness() {
      const [revision, setRevision] = useState(0);
      const refresh = useCallback(async () => noSelection, []);
      return <MemoryRouter><button onClick={() => {
        sessionStorage.removeItem(storageKey);
        setRevision((value) => value + 1);
      }}>父页安全清除</button><CandidateVersionWorkspace
        userId={userId} projectId={projectId} chapterId={chapterId} chapterTitle="第一章"
        run={run} initialCandidateId={root.id} selectionCurrent={noSelection}
        onRefreshSelection={refresh} selectionRecoveryRevision={revision}
      /></MemoryRouter>;
    }
    render(<RecoveryHarness />);
    const adopt = await screen.findByRole("button", { name: "采用此版本" });
    expect(adopt).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "父页安全清除" }));
    await waitFor(() => expect(adopt).toBeEnabled());
    expect(post).not.toHaveBeenCalled();
  });

  it("does not carry a corrupt selection storage lock from identity A into missing identity B", async () => {
    const root = await candidate(rootContent, "generated", 1);
    const userB = id("user-b");
    const projectB = id("project-b");
    const rootB = { ...root, project_id: projectB, created_by: userB };
    sessionStorage.setItem(pendingProjectOperationKey(userId, projectId), "{broken");
    vi.spyOn(api, "listGenerationCandidateVersions").mockImplementation(async (project) => ({
      ...list([project === projectB ? rootB : root]),
      project_id: project,
    }));
    vi.spyOn(api, "getGenerationCandidateVersion").mockImplementation(async (project) =>
      project === projectB ? rootB : root);
    const post = vi.spyOn(api, "selectGenerationCandidate");
    function IdentityHarness() {
      const [identity, setIdentity] = useState({ userId, projectId });
      const refresh = useCallback(async () => noSelection, []);
      return <MemoryRouter><button onClick={() => setIdentity({ userId: userB, projectId: projectB })}>切换身份</button><CandidateVersionWorkspace
        userId={identity.userId} projectId={identity.projectId} chapterId={chapterId}
        chapterTitle="第一章" run={run} initialCandidateId={root.id}
        selectionCurrent={noSelection} onRefreshSelection={refresh}
      /></MemoryRouter>;
    }
    render(<IdentityHarness />);
    expect(await screen.findByRole("button", { name: "采用此版本" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "切换身份" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "采用此版本" })).toBeEnabled());
    expect(post).not.toHaveBeenCalled();
  });

  it("persists maintenance provenance across reload, forbids original retry, and uses a new key only after maintenance clears", async () => {
    const root = await candidate(rootContent, "generated", 1);
    vi.spyOn(api, "listGenerationCandidateVersions").mockResolvedValue(list([root]));
    vi.spyOn(api, "getGenerationCandidateVersion").mockResolvedValue(root);
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const post = vi.spyOn(api, "selectGenerationCandidate")
      .mockRejectedValueOnce(new ApiError(503, {
        detail: "维护中", code: "PROJECT_WRITE_FROZEN", retryable: true,
        recommended_action: "retry_later",
      }))
      .mockRejectedValueOnce(new Error("second request stops after proving the new key"));
    const byKey = vi.spyOn(api, "getGenerationCandidateSelectionByKey").mockRejectedValue(new ApiError(404, {
      detail: "维护期间未找到", code: "GENERATION_CANDIDATE_SELECTION_OPERATION_NOT_FOUND",
      retryable: true, recommended_action: "retry_original_candidate_selection",
    }));
    const first = renderSelectionWorkspace(vi.fn().mockResolvedValue(noSelection));
    fireEvent.click(await screen.findByRole("button", { name: "采用此版本" }));
    fireEvent.click(screen.getByRole("button", { name: "确认更新章节采用版本" }));
    await screen.findByText(/项目正在维护/);
    const originalKey = post.mock.calls[0][2].operation_key;
    first.unmount();

    function MaintenanceHarness() {
      const [maintenance, setMaintenance] = useState(true);
      const refresh = useCallback(async () => noSelection, []);
      return <MemoryRouter><button onClick={() => setMaintenance(false)}>权威解除维护</button><CandidateVersionWorkspace
        userId={userId} projectId={projectId} chapterId={chapterId} chapterTitle="第一章"
        run={run} initialCandidateId={root.id} selectionCurrent={noSelection}
        onRefreshSelection={refresh}
        selectionDisabledReason={maintenance ? "项目正在维护，候选采用暂不可提交。" : ""}
      /></MemoryRouter>;
    }
    render(<MaintenanceHarness />);
    await waitFor(() => expect(byKey).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole("button", { name: "再次确认原请求" })).not.toBeInTheDocument();
    expect(post).toHaveBeenCalledTimes(1);
    fireEvent.click(await screen.findByRole("button", { name: "放弃维护期失败线索" }));
    await waitFor(() => expect(sessionStorage.getItem(pendingProjectOperationKey(userId, projectId))).toBeNull());
    const adopt = screen.getByRole("button", { name: "采用此版本" });
    expect(adopt).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "权威解除维护" }));
    await waitFor(() => expect(adopt).toBeEnabled());
    fireEvent.click(adopt);
    fireEvent.click(screen.getByRole("button", { name: "确认更新章节采用版本" }));
    await waitFor(() => expect(post).toHaveBeenCalledTimes(2));
    expect(post.mock.calls[1][2].operation_key).not.toBe(originalKey);
  });

  it("treats a maintenance marker with a different complete pending identity as corrupt without clearing either record", async () => {
    const root = await candidate(rootContent, "generated", 1);
    const operation = pendingSelection(root, "candidate:select:maintenance-original");
    const replacement = pendingSelection(root, "candidate:select:maintenance-replacement");
    const sharedKey = pendingProjectOperationKey(userId, projectId);
    const markerKey = `${sharedKey}:candidate-selection-maintenance`;
    sessionStorage.setItem(sharedKey, JSON.stringify(operation));
    sessionStorage.setItem(markerKey, JSON.stringify(replacement));
    vi.spyOn(api, "listGenerationCandidateVersions").mockResolvedValue(list([root]));
    vi.spyOn(api, "getGenerationCandidateVersion").mockResolvedValue(root);
    const byKey = vi.spyOn(api, "getGenerationCandidateSelectionByKey").mockRejectedValue(new ApiError(404, {
      detail: "未找到", code: "GENERATION_CANDIDATE_SELECTION_OPERATION_NOT_FOUND",
      retryable: true, recommended_action: "retry_original_candidate_selection",
    }));
    const post = vi.spyOn(api, "selectGenerationCandidate");
    renderSelectionWorkspace(vi.fn().mockResolvedValue(noSelection));
    expect(await screen.findByText(/维护期本地标记与当前恢复线索身份不一致/)).toBeInTheDocument();
    expect(byKey).not.toHaveBeenCalled();
    expect(post).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: "再次确认原请求" })).not.toBeInTheDocument();
    expect(sessionStorage.getItem(sharedKey)).toBe(JSON.stringify(operation));
    expect(sessionStorage.getItem(markerKey)).toBe(JSON.stringify(replacement));
  });

  it("rechecks a matching maintenance marker before a manual by-key read and stops when it was replaced", async () => {
    const root = await candidate(rootContent, "generated", 1);
    const operation = pendingSelection(root, "candidate:select:maintenance-race");
    const replacement = pendingSelection(root, "candidate:select:maintenance-race-replaced");
    const sharedKey = pendingProjectOperationKey(userId, projectId);
    const markerKey = `${sharedKey}:candidate-selection-maintenance`;
    sessionStorage.setItem(sharedKey, JSON.stringify(operation));
    sessionStorage.setItem(markerKey, JSON.stringify(operation));
    vi.spyOn(api, "listGenerationCandidateVersions").mockResolvedValue(list([root]));
    vi.spyOn(api, "getGenerationCandidateVersion").mockResolvedValue(root);
    const byKey = vi.spyOn(api, "getGenerationCandidateSelectionByKey").mockRejectedValue(new Error("network"));
    const post = vi.spyOn(api, "selectGenerationCandidate");
    renderSelectionWorkspace(vi.fn().mockResolvedValue(noSelection));
    const check = await screen.findByRole("button", { name: "按原编号核对采用状态" });
    expect(byKey).toHaveBeenCalledTimes(1);
    sessionStorage.setItem(markerKey, JSON.stringify(replacement));
    fireEvent.click(check);
    expect(await screen.findByText(/维护期本地标记在核对前已与当前恢复线索身份不一致/)).toBeInTheDocument();
    expect(byKey).toHaveBeenCalledTimes(1);
    expect(post).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: "再次确认原请求" })).not.toBeInTheDocument();
    expect(sessionStorage.getItem(sharedKey)).toBe(JSON.stringify(operation));
    expect(sessionStorage.getItem(markerKey)).toBe(JSON.stringify(replacement));
  });

  it("compare-clears the exact v6 when the maintenance marker cannot be stored, so reload cannot authorize original retry", async () => {
    const root = await candidate(rootContent, "generated", 1);
    vi.spyOn(api, "listGenerationCandidateVersions").mockResolvedValue(list([root]));
    vi.spyOn(api, "getGenerationCandidateVersion").mockResolvedValue(root);
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const storagePrototype = Object.getPrototypeOf(sessionStorage) as Storage;
    const originalSetItem = storagePrototype.setItem;
    vi.spyOn(storagePrototype, "setItem").mockImplementation(function (this: Storage, key, value) {
      if (key.endsWith(":candidate-selection-maintenance")) {
        throw new DOMException("marker blocked", "QuotaExceededError");
      }
      return originalSetItem.call(this, key, value);
    });
    const post = vi.spyOn(api, "selectGenerationCandidate").mockRejectedValue(new ApiError(503, {
      detail: "维护中", code: "PROJECT_WRITE_FROZEN", retryable: true,
      recommended_action: "retry_later",
    }));
    const byKey = vi.spyOn(api, "getGenerationCandidateSelectionByKey").mockRejectedValue(new ApiError(404, {
      detail: "未找到", code: "GENERATION_CANDIDATE_SELECTION_OPERATION_NOT_FOUND",
      retryable: true, recommended_action: "retry_original_candidate_selection",
    }));
    const first = renderSelectionWorkspace(vi.fn().mockResolvedValue(noSelection));
    fireEvent.click(await screen.findByRole("button", { name: "采用此版本" }));
    fireEvent.click(screen.getByRole("button", { name: "确认更新章节采用版本" }));
    expect(await screen.findByText(/原恢复线索已按完整身份安全移除/)).toBeInTheDocument();
    expect(sessionStorage.getItem(pendingProjectOperationKey(userId, projectId))).toBeNull();
    first.unmount();
    renderSelectionWorkspace(vi.fn().mockResolvedValue(noSelection));
    await screen.findByRole("button", { name: "采用此版本" });
    expect(byKey).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: "再次确认原请求" })).not.toBeInTheDocument();
    expect(post).toHaveBeenCalledTimes(1);
  });

  it("keeps v6 pending until the target list and detail both strictly confirm the receipt", async () => {
    const root = await candidate(rootContent, "generated", 1);
    const targetList = deferred<ReturnType<typeof list>>();
    const targetDetail = deferred<typeof root>();
    const listSpy = vi.spyOn(api, "listGenerationCandidateVersions")
      .mockResolvedValueOnce(list([root]))
      .mockReturnValueOnce(targetList.promise);
    const detailSpy = vi.spyOn(api, "getGenerationCandidateVersion")
      .mockResolvedValueOnce(root)
      .mockReturnValueOnce(targetDetail.promise);
    const refresh = vi.fn().mockResolvedValue(selectedCurrent(root));
    vi.spyOn(api, "selectGenerationCandidate").mockImplementation(async (_project, _chapter, payload) =>
      selectionReceipt(root, payload.operation_key));
    renderSelectionWorkspace(refresh);
    fireEvent.click(await screen.findByRole("button", { name: "采用此版本" }));
    fireEvent.click(screen.getByRole("button", { name: "确认更新章节采用版本" }));
    await waitFor(() => expect(listSpy).toHaveBeenCalledTimes(2));
    expect(refresh).not.toHaveBeenCalled();
    expect(sessionStorage.getItem(pendingProjectOperationKey(userId, projectId))).toContain("candidate_selection");
    targetList.resolve(list([root]));
    await waitFor(() => expect(detailSpy).toHaveBeenCalledTimes(2));
    expect(sessionStorage.getItem(pendingProjectOperationKey(userId, projectId))).not.toBeNull();
    targetDetail.resolve({ ...root, word_count: root.word_count + 1 });
    expect(await screen.findByText(/候选版本正文完整性校验失败|候选详情与冻结采用目标不一致/)).toBeInTheDocument();
    expect(refresh).not.toHaveBeenCalled();
    expect(sessionStorage.getItem(pendingProjectOperationKey(userId, projectId))).not.toBeNull();
    expect(screen.queryByText(/章节采用版本已更新/)).not.toBeInTheDocument();
  });

  it("aborts a deferred selection POST on unmount without refresh, clear, or late UI", async () => {
    const root = await candidate(rootContent, "generated", 1);
    const lateReceipt = deferred<ReturnType<typeof selectionReceipt>>();
    vi.spyOn(api, "listGenerationCandidateVersions").mockResolvedValue(list([root]));
    vi.spyOn(api, "getGenerationCandidateVersion").mockResolvedValue(root);
    const refresh = vi.fn().mockResolvedValue(selectedCurrent(root));
    const select = vi.spyOn(api, "selectGenerationCandidate").mockReturnValue(lateReceipt.promise);
    const rendered = renderSelectionWorkspace(refresh);
    fireEvent.click(await screen.findByRole("button", { name: "采用此版本" }));
    fireEvent.click(screen.getByRole("button", { name: "确认更新章节采用版本" }));
    await waitFor(() => expect(select).toHaveBeenCalledTimes(1));
    const signal = select.mock.calls[0][3];
    const operationKey = select.mock.calls[0][2].operation_key;
    rendered.unmount();
    expect(signal?.aborted).toBe(true);
    await act(async () => {
      lateReceipt.resolve(selectionReceipt(root, operationKey));
      await Promise.resolve();
    });
    expect(refresh).not.toHaveBeenCalled();
    expect(sessionStorage.getItem(pendingProjectOperationKey(userId, projectId))).not.toBeNull();
  });

  it("aborts deferred by-key recovery on unmount without refresh or clearing v6", async () => {
    const root = await candidate(rootContent, "generated", 1);
    const operationKey = "candidate:select:deferred-by-key";
    const operation = {
      schema_version: 6,
      workspace: "candidate_selection",
      user_id: userId,
      project_id: projectId,
      chapter_id: chapterId,
      run_id: runId,
      operation_key: operationKey,
      payload: {
        operation_key: operationKey, expected_selection_version: 0,
        target_run_id: runId, target_candidate_id: root.id,
        expected_candidate_version_no: root.version_no,
        expected_candidate_checksum: root.content_checksum,
        expected_context_checksum: run.context_checksum,
      },
      expected_previous: { state: "none", selection_version: 0, run_id: null, context_checksum: null, candidate: null },
      expected_target: selectionItem(root),
      created_at: now,
    };
    sessionStorage.setItem(pendingProjectOperationKey(userId, projectId), JSON.stringify(operation));
    vi.spyOn(api, "listGenerationCandidateVersions").mockResolvedValue(list([root]));
    vi.spyOn(api, "getGenerationCandidateVersion").mockResolvedValue(root);
    const lateReceipt = deferred<ReturnType<typeof selectionReceipt>>();
    const byKey = vi.spyOn(api, "getGenerationCandidateSelectionByKey").mockReturnValue(lateReceipt.promise);
    const refresh = vi.fn().mockResolvedValue(selectedCurrent(root));
    const rendered = renderSelectionWorkspace(refresh);
    await waitFor(() => expect(byKey).toHaveBeenCalledTimes(1));
    const signal = byKey.mock.calls[0][3];
    rendered.unmount();
    expect(signal?.aborted).toBe(true);
    await act(async () => {
      lateReceipt.resolve(selectionReceipt(root, operationKey));
      await Promise.resolve();
    });
    expect(refresh).not.toHaveBeenCalled();
    expect(sessionStorage.getItem(pendingProjectOperationKey(userId, projectId))).toContain(operationKey);
  });

  it.each([
    [409, "GENERATION_PLANNING_CHAPTER_ARCHIVED", false, "restore_planning_chapter"],
    [503, "PROJECT_WRITE_FROZEN", true, "retry_later"],
    [409, "GENERATION_CANDIDATE_SELECTION_FUTURE_ERROR", false, "future_action"],
  ] as const)("fails closed for exact or unknown API error %s/%s without by-key", async (status, code, retryable, action) => {
    const root = await candidate(rootContent, "generated", 1);
    vi.spyOn(api, "listGenerationCandidateVersions").mockResolvedValue(list([root]));
    vi.spyOn(api, "getGenerationCandidateVersion").mockResolvedValue(root);
    vi.spyOn(api, "selectGenerationCandidate").mockRejectedValue(new ApiError(status, {
      detail: "采用失败",
      code,
      retryable,
      recommended_action: action,
    }));
    const byKey = vi.spyOn(api, "getGenerationCandidateSelectionByKey");
    if (code === "PROJECT_WRITE_FROZEN") {
      byKey.mockRejectedValue(new ApiError(404, {
        detail: "维护期间未找到",
        code: "GENERATION_CANDIDATE_SELECTION_OPERATION_NOT_FOUND",
        retryable: true,
        recommended_action: "retry_original_candidate_selection",
      }));
    }
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderSelectionWorkspace(vi.fn().mockResolvedValue(noSelection));
    fireEvent.click(await screen.findByRole("button", { name: "采用此版本" }));
    fireEvent.click(screen.getByRole("button", { name: "确认更新章节采用版本" }));
    await screen.findByText(code === "GENERATION_PLANNING_CHAPTER_ARCHIVED"
      ? /章节已归档，不能修改采用版本/
      : code === "PROJECT_WRITE_FROZEN"
        ? /项目正在维护/
        : /服务端错误契约不能证明结果未知/);
    expect(byKey).not.toHaveBeenCalled();
    expect(sessionStorage.getItem(pendingProjectOperationKey(userId, projectId))).not.toBeNull();
    if (code === "PROJECT_WRITE_FROZEN") {
      fireEvent.click(screen.getByRole("button", { name: "按原编号核对采用状态" }));
      await waitFor(() => expect(byKey).toHaveBeenCalledTimes(1));
      expect(screen.queryByRole("button", { name: "再次确认原请求" })).not.toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: "放弃维护期失败线索" }));
      await waitFor(() => expect(sessionStorage.getItem(
        pendingProjectOperationKey(userId, projectId)
      )).toBeNull());
    } else {
      expect(screen.queryByRole("button", { name: "按原编号核对采用状态" })).not.toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: "明确放弃本地恢复线索" }));
      await waitFor(() => expect(sessionStorage.getItem(
        pendingProjectOperationKey(userId, projectId)
      )).toBeNull());
    }
  });

  it("retains a version-conflict pending record when the authoritative version did not change", async () => {
    const root = await candidate(rootContent, "generated", 1);
    vi.spyOn(api, "listGenerationCandidateVersions").mockResolvedValue(list([root]));
    vi.spyOn(api, "getGenerationCandidateVersion").mockResolvedValue(root);
    vi.spyOn(api, "selectGenerationCandidate").mockRejectedValue(new ApiError(409, {
      detail: "版本冲突", code: "GENERATION_CANDIDATE_SELECTION_VERSION_CONFLICT",
      retryable: false, recommended_action: "reload_candidate_selection",
    }));
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderSelectionWorkspace(vi.fn().mockResolvedValue(noSelection));
    fireEvent.click(await screen.findByRole("button", { name: "采用此版本" }));
    fireEvent.click(screen.getByRole("button", { name: "确认更新章节采用版本" }));
    expect(await screen.findByText(/权威版本号尚未变化/)).toBeInTheDocument();
    expect(sessionStorage.getItem(pendingProjectOperationKey(userId, projectId))).not.toBeNull();
    expect(screen.queryByRole("button", { name: "基于最新采用状态重新确认" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "明确放弃本地恢复线索" }));
    expect(sessionStorage.getItem(pendingProjectOperationKey(userId, projectId))).toBeNull();
  });

  it("binds the final current confirmation to selected_at and changed_by", async () => {
    const root = await candidate(rootContent, "generated", 1);
    vi.spyOn(api, "listGenerationCandidateVersions").mockResolvedValue(list([root]));
    vi.spyOn(api, "getGenerationCandidateVersion").mockResolvedValue(root);
    vi.spyOn(api, "selectGenerationCandidate").mockImplementation(async (_project, _chapter, payload) =>
      selectionReceipt(root, payload.operation_key));
    const mismatched = { ...selectedCurrent(root), selected_at: "2026-08-13T11:31:00Z" };
    renderSelectionWorkspace(vi.fn().mockResolvedValue(mismatched));
    fireEvent.click(await screen.findByRole("button", { name: "采用此版本" }));
    fireEvent.click(screen.getByRole("button", { name: "确认更新章节采用版本" }));
    expect(await screen.findByText(/章节采用状态与本次严格回执不一致/)).toBeInTheDocument();
    expect(sessionStorage.getItem(pendingProjectOperationKey(userId, projectId))).not.toBeNull();
  });

  it("labels a selected candidate from another run without exposing internal identifiers", async () => {
    const root = await candidate(rootContent, "generated", 1);
    const otherRunId = id("other-run");
    vi.spyOn(api, "listGenerationCandidateVersions").mockResolvedValue(list([root]));
    vi.spyOn(api, "getGenerationCandidateVersion").mockResolvedValue(root);
    const current: GenerationCandidateSelectionCurrentResponse = {
      ...selectedCurrent(root),
      run_id: otherRunId,
    };
    renderSelectionWorkspace(vi.fn().mockResolvedValue(current), current);
    expect(await screen.findByText(/采用版本来自另一条检查记录/)).toBeInTheDocument();
    const link = screen.getByRole("link", { name: "查看章节采用版本" });
    expect(link).toHaveAttribute("href", expect.stringContaining("generation_run="));
    expect(screen.getByText(/模型生成候选；根来源：模型生成候选/)).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent(otherRunId);
  });

  it("handles already-selected and target-changed responses with GET-only reconciliation", async () => {
    const root = await candidate(rootContent, "generated", 1);
    const listSpy = vi.spyOn(api, "listGenerationCandidateVersions").mockResolvedValue(list([root]));
    vi.spyOn(api, "getGenerationCandidateVersion").mockResolvedValue(root);
    const refresh = vi.fn().mockResolvedValue(selectedCurrent(root));
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const select = vi.spyOn(api, "selectGenerationCandidate").mockRejectedValueOnce(new ApiError(409, {
      detail: "已经采用", code: "GENERATION_CANDIDATE_ALREADY_SELECTED",
      retryable: false, recommended_action: "reload_candidate_selection",
    }));
    const byKey = vi.spyOn(api, "getGenerationCandidateSelectionByKey");
    const first = renderSelectionWorkspace(refresh);
    fireEvent.click(await screen.findByRole("button", { name: "采用此版本" }));
    fireEvent.click(screen.getByRole("button", { name: "确认更新章节采用版本" }));
    expect(await screen.findByText(/已经是章节采用版本/)).toBeInTheDocument();
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(listSpy).toHaveBeenCalledTimes(1);
    expect(byKey).not.toHaveBeenCalled();
    expect(sessionStorage.getItem(pendingProjectOperationKey(userId, projectId))).toBeNull();
    first.unmount();
    sessionStorage.clear();

    select.mockRejectedValueOnce(new ApiError(409, {
      detail: "目标变化", code: "GENERATION_CANDIDATE_SELECTION_TARGET_CHANGED",
      retryable: false, recommended_action: "reload_generation_candidate_versions",
    }));
    renderSelectionWorkspace(refresh);
    fireEvent.click(await screen.findByRole("button", { name: "采用此版本" }));
    fireEvent.click(screen.getByRole("button", { name: "确认更新章节采用版本" }));
    expect(await screen.findByText(/已重新读取权威候选列表与采用状态/)).toBeInTheDocument();
    expect(refresh).toHaveBeenCalledTimes(2);
    expect(listSpy.mock.calls.length).toBeGreaterThanOrEqual(3);
    expect(byKey).not.toHaveBeenCalled();
    expect(sessionStorage.getItem(pendingProjectOperationKey(userId, projectId))).not.toBeNull();
    expect(screen.queryByRole("button", { name: "按原编号核对采用状态" })).not.toBeInTheDocument();
    const abandon = screen.getByRole("button", { name: "明确放弃失效线索" });
    fireEvent.click(abandon);
    expect(sessionStorage.getItem(pendingProjectOperationKey(userId, projectId))).toBeNull();
    expect(screen.queryByRole("button", { name: "基于最新采用状态重新确认" })).not.toBeInTheDocument();
    expect(select).toHaveBeenCalledTimes(2);
  });

  it("requires a strict version reselect after abandoning an invalid target before any new POST", async () => {
    const root = await candidate(rootContent, "generated", 1);
    vi.spyOn(api, "listGenerationCandidateVersions").mockResolvedValue(list([root]));
    const detail = vi.spyOn(api, "getGenerationCandidateVersion").mockResolvedValue(root);
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const post = vi.spyOn(api, "selectGenerationCandidate").mockRejectedValueOnce(new ApiError(409, {
      detail: "目标变化", code: "GENERATION_CANDIDATE_SELECTION_TARGET_CHANGED",
      retryable: false, recommended_action: "reload_generation_candidate_versions",
    }));
    renderSelectionWorkspace(vi.fn().mockResolvedValue(noSelection));
    fireEvent.click(await screen.findByRole("button", { name: "采用此版本" }));
    fireEvent.click(screen.getByRole("button", { name: "确认更新章节采用版本" }));
    fireEvent.click(await screen.findByRole("button", { name: "明确放弃失效线索" }));
    const staleAdopt = screen.getByRole("button", { name: "采用此版本" });
    expect(staleAdopt).toBeDisabled();
    fireEvent.click(staleAdopt);
    expect(post).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: /^版本 1 模型生成候选/ }));
    await waitFor(() => expect(detail).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.getByRole("button", { name: "采用此版本" })).toBeEnabled());
    expect(post).toHaveBeenCalledTimes(1);
  });

  it("keeps a by-key transport failure manually retryable without authorizing a POST", async () => {
    const root = await candidate(rootContent, "generated", 1);
    const operationKey = "candidate:select:transport-recovery";
    sessionStorage.setItem(pendingProjectOperationKey(userId, projectId), JSON.stringify({
      schema_version: 6, workspace: "candidate_selection", user_id: userId,
      project_id: projectId, chapter_id: chapterId, run_id: runId, operation_key: operationKey,
      payload: { operation_key: operationKey, expected_selection_version: 0, target_run_id: runId,
        target_candidate_id: root.id, expected_candidate_version_no: 1,
        expected_candidate_checksum: root.content_checksum, expected_context_checksum: run.context_checksum },
      expected_previous: { state: "none", selection_version: 0, run_id: null, context_checksum: null, candidate: null },
      expected_target: selectionItem(root), created_at: now,
    }));
    vi.spyOn(api, "listGenerationCandidateVersions").mockResolvedValue(list([root]));
    vi.spyOn(api, "getGenerationCandidateVersion").mockResolvedValue(root);
    const byKey = vi.spyOn(api, "getGenerationCandidateSelectionByKey").mockRejectedValue(new Error("network"));
    const post = vi.spyOn(api, "selectGenerationCandidate");
    renderSelectionWorkspace(vi.fn().mockResolvedValue(noSelection));
    const check = await screen.findByRole("button", { name: "按原编号核对采用状态" });
    expect(byKey).toHaveBeenCalledTimes(1);
    fireEvent.click(check);
    await waitFor(() => expect(byKey).toHaveBeenCalledTimes(2));
    expect(screen.getByRole("button", { name: "按原编号核对采用状态" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "再次确认原请求" })).not.toBeInTheDocument();
    expect(post).not.toHaveBeenCalled();
  });

  it("requires explicit abandonment after an operation conflict before offering a new-key confirmation", async () => {
    const root = await candidate(rootContent, "generated", 1);
    const concurrent = await candidate(editedContent, "manual_edit", 2);
    vi.spyOn(api, "listGenerationCandidateVersions").mockResolvedValue(list([root]));
    vi.spyOn(api, "getGenerationCandidateVersion").mockResolvedValue(root);
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const select = vi.spyOn(api, "selectGenerationCandidate").mockRejectedValue(new ApiError(409, {
      detail: "编号冲突", code: "GENERATION_CANDIDATE_SELECTION_OPERATION_CONFLICT",
      retryable: false, recommended_action: "start_new_candidate_selection",
    }));
    const refresh = vi.fn().mockResolvedValue(selectedCurrent(concurrent, 1));
    renderSelectionWorkspace(refresh);
    fireEvent.click(await screen.findByRole("button", { name: "采用此版本" }));
    fireEvent.click(screen.getByRole("button", { name: "确认更新章节采用版本" }));
    const abandon = await screen.findByRole("button", { name: "明确放弃冲突线索" });
    expect(select).toHaveBeenCalledTimes(1);
    expect(sessionStorage.getItem(pendingProjectOperationKey(userId, projectId))).not.toBeNull();
    fireEvent.click(abandon);
    await waitFor(() => expect(sessionStorage.getItem(
      pendingProjectOperationKey(userId, projectId)
    )).toBeNull());
    expect(refresh).toHaveBeenCalledTimes(1);
    const restart = await screen.findByRole("button", { name: "基于最新采用状态重新确认" });
    fireEvent.click(restart);
    expect(screen.getByRole("alertdialog", { name: "确认改用此候选" })).toBeInTheDocument();
    expect(select).toHaveBeenCalledTimes(1);
    const confirm = screen.getByRole("button", { name: "确认更新章节采用版本" });
    await waitFor(() => expect(confirm).toBeEnabled());
    fireEvent.click(confirm);
    await waitFor(() => expect(select).toHaveBeenCalledTimes(2));
    expect(select.mock.calls[1][2].expected_selection_version).toBe(1);
    expect(select.mock.calls[1][2].operation_key).not.toBe(select.mock.calls[0][2].operation_key);
  });

  it("keeps an unknown-API recovery clue locked when authoritative current refresh fails during abandonment", async () => {
    const root = await candidate(rootContent, "generated", 1);
    vi.spyOn(api, "listGenerationCandidateVersions").mockResolvedValue(list([root]));
    vi.spyOn(api, "getGenerationCandidateVersion").mockResolvedValue(root);
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const post = vi.spyOn(api, "selectGenerationCandidate").mockRejectedValue(new ApiError(409, {
      detail: "未知冲突", code: "GENERATION_CANDIDATE_SELECTION_FUTURE_ERROR",
      retryable: false, recommended_action: "future_action",
    }));
    const refresh = vi.fn().mockRejectedValue(new Error("权威状态暂不可读"));
    renderSelectionWorkspace(refresh);
    fireEvent.click(await screen.findByRole("button", { name: "采用此版本" }));
    fireEvent.click(screen.getByRole("button", { name: "确认更新章节采用版本" }));
    fireEvent.click(await screen.findByRole("button", { name: "明确放弃本地恢复线索" }));
    expect(await screen.findByText(/未能重新读取权威采用状态/)).toBeInTheDocument();
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(sessionStorage.getItem(pendingProjectOperationKey(userId, projectId))).not.toBeNull();
    expect(screen.queryByRole("button", { name: "基于最新采用状态重新确认" })).not.toBeInTheDocument();
    expect(post).toHaveBeenCalledTimes(1);
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
    expect(await screen.findByText("检查标记为需作者人工核对。", { exact: false })).toBeInTheDocument();
    expect(firstSignal?.aborted).toBe(true);
    lateParentAudit.resolve(auditFor(root));
    await Promise.resolve();
    expect(screen.getByText("检查标记为需作者人工核对。", { exact: false })).toBeInTheDocument();
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
    expect(await screen.findByText("检查标记为需作者人工核对。", { exact: false })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "基于此候选编辑" }));
    fireEvent.change(screen.getByLabelText("编辑副本"), { target: { value: editedContent } });
    fireEvent.click(screen.getByRole("button", { name: "保存为新候选版本" }));
    await screen.findByText("第一章 · 候选版本 2");
    expect(screen.queryByText("检查标记为需作者人工核对。", { exact: false })).not.toBeInTheDocument();
    expect(screen.getByText("正在读取检查…")).toBeInTheDocument();
    childAudit.resolve(auditFor(manual));
    expect(await screen.findByText("检查标记为需作者人工核对。", { exact: false })).toBeInTheDocument();
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
    expect(await screen.findByText("检查标记为需作者人工核对。", { exact: false })).toBeInTheDocument();
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
    const announcement = await screen.findByText("检查标记为需作者人工核对。", { exact: false });
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
