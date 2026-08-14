import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { loadPendingCandidateManualEdit } from "./candidateVersionOperations";
import {
  CandidateSelectionContractError,
  candidateSelectionSnapshotFromCurrent,
  clearPendingCandidateSelection,
  loadPendingCandidateSelection,
  parseCandidateSelectionCurrent,
  parseCandidateSelectionInput,
  parseCandidateSelectionReceipt,
  savePendingCandidateSelection,
  type CandidateSelectionIdentity,
  type PendingCandidateSelection,
} from "./candidateSelectionOperations";
import { loadPendingForeshadowOperation } from "./foreshadowOperations";
import { loadPendingGenerationExecution } from "./generationExecution";
import { pendingProjectOperationKey } from "./pendingProjectOperations";
import { loadPendingPlanningOperation } from "./planningOperations";
import { loadPendingTechnicalDemoExecution } from "./technicalDemoExecution";
import type {
  GenerationCandidateSelectionSnapshot,
  GenerationCandidateVersionListItem,
  GenerationCandidateVersionOrigin,
} from "@/types/generation";

const id = (seed: string) => `${seed}${"x".repeat(32)}`.slice(0, 32);
const userId = id("user");
const projectId = id("project");
const chapterId = id("chapter");
const runId = id("run");
const candidateId = id("candidate");
const checksum = "a".repeat(64);
const contextChecksum = "b".repeat(64);
const identity: CandidateSelectionIdentity = {
  userId, projectId, chapterId,
};

function candidate(
  origin: GenerationCandidateVersionOrigin = "generated",
  overrides: Partial<GenerationCandidateVersionListItem> = {}
): GenerationCandidateVersionListItem {
  const manual = origin === "manual_edit";
  const technical = origin === "technical_demo";
  return {
    id: candidateId,
    version_no: manual ? 2 : 1,
    origin_kind: origin,
    parent_candidate_id: manual ? id("parent") : null,
    parent_version_no: manual ? 1 : null,
    root_candidate_id: manual ? id("root") : candidateId,
    root_origin_kind: technical ? "technical_demo" : "generated",
    ai_invoked_for_this_version: origin === "generated",
    billing_effect_for_this_version: origin === "generated" ? "possible" : "none",
    usage_status_for_this_version:
      origin === "generated" ? "reported" : "not_applicable",
    title: "第一章",
    content_checksum: checksum,
    content_size_bytes: 12,
    word_count: 4,
    created_by: userId,
    created_at: "2026-08-14T06:00:00Z",
    ...overrides,
  } as GenerationCandidateVersionListItem;
}

const none: GenerationCandidateSelectionSnapshot = {
  state: "none",
  selection_version: 0,
  run_id: null,
  context_checksum: null,
  candidate: null,
};

function input(
  expectedVersion = 0,
  target = candidate(),
  targetRunId = runId
) {
  return {
    operation_key: "candidate:select:12345678",
    expected_selection_version: expectedVersion,
    target_run_id: targetRunId,
    target_candidate_id: target.id,
    expected_candidate_version_no: target.version_no,
    expected_candidate_checksum: target.content_checksum,
    expected_context_checksum: contextChecksum,
  };
}

function pending(
  expectedPrevious: GenerationCandidateSelectionSnapshot = none,
  target = candidate(),
  targetRunId = runId
): PendingCandidateSelection {
  return {
    schema_version: 6,
    workspace: "candidate_selection",
    user_id: userId,
    project_id: projectId,
    chapter_id: chapterId,
    run_id: targetRunId,
    operation_key: "candidate:select:12345678",
    payload: input(expectedPrevious.selection_version, target, targetRunId),
    expected_previous: expectedPrevious,
    expected_target: target,
    created_at: "2026-08-14T06:01:00Z",
  };
}

function current(
  origin: GenerationCandidateVersionOrigin = "generated",
  overrides: Partial<GenerationCandidateVersionListItem> = {}
) {
  return {
    schema_version: 1,
    project_id: projectId,
    planning_chapter_id: chapterId,
    state: "selected",
    selection_version: 1,
    run_id: runId,
    context_checksum: contextChecksum,
    candidate: candidate(origin, overrides),
    selected_at: "2026-08-14T06:02:00Z",
    changed_by: userId,
  } as const;
}

function receipt(
  target = candidate(),
  previous: GenerationCandidateSelectionSnapshot = none
) {
  return {
    schema_version: 1,
    project_id: projectId,
    planning_chapter_id: chapterId,
    operation_key: "candidate:select:12345678",
    replayed: false,
    changed: true,
    ai_invoked: false,
    billing_effect: "none",
    usage_status: "not_applicable",
    previous,
    result: {
      state: "selected",
      selection_version: previous.selection_version + 1,
      run_id: runId,
      context_checksum: contextChecksum,
      candidate: target,
    },
    selected_at: "2026-08-14T06:02:00Z",
    changed_by: userId,
  } as const;
}

describe("candidate selection strict contracts and pending v6", () => {
  beforeEach(() => sessionStorage.clear());
  afterEach(() => vi.restoreAllMocks());

  it("accepts only the exact seven-field command", () => {
    expect(parseCandidateSelectionInput(input())).toEqual(input());
    expect(() => parseCandidateSelectionInput({ ...input(), extra: true }))
      .toThrow(CandidateSelectionContractError);
    const { target_run_id: _removed, ...missing } = input();
    expect(() => parseCandidateSelectionInput(missing))
      .toThrow(CandidateSelectionContractError);
    expect(() => parseCandidateSelectionInput({
      ...input(), expected_selection_version: -1,
    })).toThrow(CandidateSelectionContractError);
  });

  it.each(["generated", "technical_demo", "manual_edit"] as const)(
    "strictly parses %s current and receipt without mixing source semantics",
    (origin) => {
      const selected = parseCandidateSelectionCurrent(current(origin), identity);
      expect(selected.state).toBe("selected");
      expect(selected.candidate?.origin_kind).toBe(origin);
      expect(candidateSelectionSnapshotFromCurrent(selected)).toEqual({
        state: "selected",
        selection_version: 1,
        run_id: runId,
        context_checksum: contextChecksum,
        candidate: candidate(origin),
      });

      const target = candidate(origin);
      const operation = pending(none, target);
      const parsed = parseCandidateSelectionReceipt(
        receipt(target), identity, operation
      );
      expect(parsed.result.candidate.origin_kind).toBe(origin);
      expect(parsed.previous).toEqual(none);
    }
  );

  it("accepts an old-run candidate title after the current chapter title changed", () => {
    const oldTitleTarget = candidate("generated", { title: "旧章名" });
    const selected = parseCandidateSelectionCurrent(
      current("generated", { title: "旧章名" }), identity
    );
    expect(selected.candidate?.title).toBe("旧章名");
    expect(parseCandidateSelectionReceipt(
      receipt(oldTitleTarget), identity, pending(none, oldTitleTarget)
    ).result.candidate.title).toBe("旧章名");
  });

  it("strictly accepts a re-selection receipt with a selected expected_previous", () => {
    const previousId = id("previous");
    const previous: GenerationCandidateSelectionSnapshot = {
      state: "selected",
      selection_version: 3,
      run_id: id("oldrun"),
      context_checksum: "e".repeat(64),
      candidate: candidate("generated", {
        id: previousId,
        root_candidate_id: previousId,
        title: "旧章名",
        content_checksum: "f".repeat(64),
      }),
    };
    const target = candidate("manual_edit");
    const parsed = parseCandidateSelectionReceipt(
      receipt(target, previous), identity, pending(previous, target)
    );
    expect(parsed.previous).toEqual(previous);
    expect(parsed.result.selection_version).toBe(4);
  });

  it("strictly parses the initial none current union", () => {
    const value = {
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
    } as const;
    expect(parseCandidateSelectionCurrent(value, identity)).toEqual(value);
    expect(() => parseCandidateSelectionCurrent({
      ...value, selected_at: "2026-08-14T06:02:00Z",
    }, identity)).toThrow(CandidateSelectionContractError);
  });

  it("rejects extra, missing, wrong-shape and frozen previous/result drift", () => {
    const operation = pending();
    expect(() => parseCandidateSelectionCurrent({
      ...current(), extra: true,
    }, identity)).toThrow(CandidateSelectionContractError);
    const { changed_by: _removed, ...missing } = current();
    expect(() => parseCandidateSelectionCurrent(missing, identity))
      .toThrow(CandidateSelectionContractError);
    expect(() => parseCandidateSelectionCurrent({
      ...current(), selection_version: 0,
    }, identity)).toThrow(CandidateSelectionContractError);
    expect(() => parseCandidateSelectionReceipt({
      ...receipt(), previous: { ...none, selection_version: 1 },
    }, identity, operation)).toThrow(CandidateSelectionContractError);
    expect(() => parseCandidateSelectionReceipt({
      ...receipt(), extra: true,
    }, identity, operation)).toThrow(CandidateSelectionContractError);
    const { operation_key: _missingKey, ...missingReceipt } = receipt();
    expect(() => parseCandidateSelectionReceipt(
      missingReceipt, identity, operation
    )).toThrow(CandidateSelectionContractError);
    expect(() => parseCandidateSelectionReceipt({
      ...receipt(), result: { ...receipt().result, context_checksum: "c".repeat(64) },
    }, identity, operation)).toThrow(CandidateSelectionContractError);
  });

  it("rejects every frozen previous and result identity drift", () => {
    const previousId = id("previous");
    const previous: GenerationCandidateSelectionSnapshot = {
      state: "selected",
      selection_version: 2,
      run_id: id("oldrun"),
      context_checksum: "e".repeat(64),
      candidate: candidate("generated", {
        id: previousId,
        root_candidate_id: previousId,
        title: "旧章名",
        content_checksum: "f".repeat(64),
      }),
    };
    const target = candidate("manual_edit");
    const operation = pending(previous, target);
    const valid = receipt(target, previous);
    const previousDrifts = [
      { ...previous, run_id: id("wrongrun") },
      { ...previous, context_checksum: "1".repeat(64) },
      { ...previous, selection_version: 1 },
      { ...previous, candidate: { ...previous.candidate, content_checksum: "2".repeat(64) } },
      { ...previous, candidate: { ...previous.candidate, title: "被篡改的旧标题" } },
    ];
    for (const drift of previousDrifts) {
      expect(() => parseCandidateSelectionReceipt(
        { ...valid, previous: drift }, identity, operation
      )).toThrow(CandidateSelectionContractError);
    }

    expect(() => parseCandidateSelectionReceipt({
      ...valid, operation_key: "candidate:select:different",
    }, identity, operation)).toThrow(CandidateSelectionContractError);
    const otherId = id("othercandidate");
    for (const drift of [
      { ...valid.result, candidate: { ...valid.result.candidate, id: otherId } },
      { ...valid.result, candidate: { ...valid.result.candidate, version_no: 3 } },
      { ...valid.result, candidate: { ...valid.result.candidate, content_checksum: "3".repeat(64) } },
      { ...valid.result, candidate: { ...valid.result.candidate, title: "另一个标题" } },
    ]) {
      expect(() => parseCandidateSelectionReceipt(
        { ...valid, result: drift }, identity, operation
      )).toThrow(CandidateSelectionContractError);
    }
  });

  it("loads v6 as corrupt when top-level run or key disagrees with its payload", () => {
    const key = pendingProjectOperationKey(userId, projectId);
    for (const invalid of [
      { ...pending(), run_id: id("wrongrun") },
      { ...pending(), operation_key: "candidate:select:different" },
    ]) {
      sessionStorage.setItem(key, JSON.stringify(invalid));
      expect(loadPendingCandidateSelection(userId, projectId)).toEqual({
        status: "corrupt",
      });
    }
  });

  it("stores v6 without overwriting and all v1-v5 loaders recognize it as foreign", () => {
    const operation = pending();
    expect(savePendingCandidateSelection(operation)).toBe(true);
    expect(loadPendingCandidateSelection(userId, projectId)).toEqual({
      status: "available", operation,
    });
    expect(loadPendingPlanningOperation(userId, projectId)).toEqual({
      status: "foreign", workspace: "candidate_selection",
    });
    expect(loadPendingForeshadowOperation(userId, projectId)).toEqual({
      status: "foreign", workspace: "candidate_selection",
    });
    expect(loadPendingGenerationExecution(userId, projectId)).toEqual({
      status: "foreign", workspace: "candidate_selection",
    });
    expect(loadPendingTechnicalDemoExecution(userId, projectId)).toEqual({
      status: "foreign", workspace: "candidate_selection",
    });
    expect(loadPendingCandidateManualEdit(userId, projectId)).toEqual({
      status: "foreign", workspace: "candidate_selection",
    });
  });

  it.each([
    [{ schema_version: 1 }, "planning"],
    [{ schema_version: 2, workspace: "foreshadow" }, "foreshadow"],
    [{ schema_version: 3, workspace: "generation_execution" }, "generation_execution"],
    [{ schema_version: 4, workspace: "technical_demo_execution" }, "technical_demo_execution"],
    [{ schema_version: 5, workspace: "candidate_manual_edit" }, "candidate_manual_edit"],
  ] as const)("recognizes older shared record %j as foreign", (value, workspace) => {
    sessionStorage.setItem(
      pendingProjectOperationKey(userId, projectId), JSON.stringify(value)
    );
    expect(loadPendingCandidateSelection(userId, projectId)).toEqual({
      status: "foreign", workspace,
    });
    expect(savePendingCandidateSelection(pending())).toBe(false);
  });

  it("fails closed for corrupt and unavailable shared storage", () => {
    const key = pendingProjectOperationKey(userId, projectId);
    sessionStorage.setItem(key, JSON.stringify({ ...pending(), extra: true }));
    expect(loadPendingCandidateSelection(userId, projectId)).toEqual({
      status: "corrupt",
    });
    vi.spyOn(Object.getPrototypeOf(sessionStorage) as Storage, "getItem")
      .mockImplementation(() => { throw new DOMException("blocked", "SecurityError"); });
    expect(loadPendingCandidateSelection(userId, projectId)).toEqual({
      status: "unavailable",
    });
  });

  it("fails closed when shared storage cannot save or compare-clear", () => {
    const storage = Object.getPrototypeOf(sessionStorage) as Storage;
    vi.spyOn(storage, "setItem")
      .mockImplementationOnce(() => { throw new DOMException("full", "QuotaExceededError"); });
    expect(savePendingCandidateSelection(pending())).toBe(false);
    vi.restoreAllMocks();

    expect(savePendingCandidateSelection(pending())).toBe(true);
    vi.spyOn(storage, "removeItem")
      .mockImplementationOnce(() => { throw new DOMException("blocked", "SecurityError"); });
    expect(clearPendingCandidateSelection(pending())).toBe(false);
    expect(loadPendingCandidateSelection(userId, projectId).status).toBe("available");
  });

  it("compare-clears the complete v6 record and refuses same-key payload drift", () => {
    const operation = pending();
    expect(savePendingCandidateSelection(operation)).toBe(true);
    const drift = {
      ...operation,
      payload: { ...operation.payload, expected_candidate_checksum: "d".repeat(64) },
    };
    expect(savePendingCandidateSelection(drift)).toBe(false);
    expect(clearPendingCandidateSelection(drift)).toBe(false);
    expect(loadPendingCandidateSelection(userId, projectId).status).toBe("available");
    expect(clearPendingCandidateSelection(operation)).toBe(true);
    expect(loadPendingCandidateSelection(userId, projectId)).toEqual({ status: "missing" });
  });
});
