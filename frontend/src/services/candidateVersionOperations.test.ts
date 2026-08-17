import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "./api";
import {
  CandidateVersionContractError,
  candidateMatchesManualEditParent,
  clearCandidateManualEditDraft,
  clearCorruptCandidateManualEditDraft,
  clearPendingCandidateManualEdit,
  loadCandidateManualEditDraft,
  isPendingCandidateManualEdit,
  listCandidateVersions,
  loadPendingCandidateManualEdit,
  parseCandidateManualEditInput,
  parseCandidateManualEditResponse,
  parseCandidateVersionDetail,
  parseCandidateVersionList,
  readCandidateManualEditByKey,
  requestCandidateManualEdit,
  replaceCandidateManualEditDraft,
  saveCandidateManualEditDraft,
  savePendingCandidateManualEdit,
  type CandidateVersionIdentity,
  type CandidateManualEditDraft,
  type PendingCandidateManualEdit,
} from "./candidateVersionOperations";
import type { GenerationCandidateVersionDetail } from "@/types/generation";
import { loadPendingForeshadowOperation } from "./foreshadowOperations";
import { loadPendingGenerationExecution } from "./generationExecution";
import { loadPendingPlanningOperation } from "./planningOperations";
import { loadPendingTechnicalDemoExecution } from "./technicalDemoExecution";
import { pendingProjectOperationKey } from "./pendingProjectOperations";

const id = (seed: string) => seed.padEnd(32, seed).slice(0, 32);
const userId = id("user");
const projectId = id("project");
const chapterId = id("chapter");
const runId = id("run");
const rootId = id("root");
const manualId = id("manual");
const now = "2026-08-13T11:00:00Z";
const identity: CandidateVersionIdentity = {
  userId,
  projectId,
  chapterId,
  runId,
  chapterTitle: "第一章",
};
const input = {
  operation_key: "candidate:manual-edit:12345678",
  parent_candidate_id: rootId,
  expected_parent_version_no: 1,
  expected_parent_checksum: "a".repeat(64),
  expected_context_checksum: "b".repeat(64),
  content: "沈星走进星门，记下了新的航标。",
} as const;

async function checksum(content: string) {
  const bytes = new TextEncoder().encode(content);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest), (byte) =>
    byte.toString(16).padStart(2, "0")
  ).join("");
}

function item(origin: "generated" | "technical_demo" | "manual_edit", version: number) {
  const candidateId = origin === "manual_edit" ? manualId : rootId;
  const rootOrigin = origin === "technical_demo" ? "technical_demo" : "generated";
  return {
    id: candidateId,
    version_no: version,
    origin_kind: origin,
    parent_candidate_id: origin === "manual_edit" ? rootId : null,
    parent_version_no: origin === "manual_edit" ? 1 : null,
    root_candidate_id: rootId,
    root_origin_kind: rootOrigin,
    ai_invoked_for_this_version: origin === "generated",
    billing_effect_for_this_version: origin === "generated" ? "possible" : "none",
    usage_status_for_this_version: origin === "generated" ? "unavailable" : "not_applicable",
    title: "第一章",
    content_checksum: "a".repeat(64),
    content_size_bytes: 30,
    word_count: 10,
    created_by: userId,
    created_at: now,
  } as const;
}

async function detail() {
  const bytes = new TextEncoder().encode(input.content);
  return {
    ...item("manual_edit", 2),
    project_id: projectId,
    run_id: runId,
    planning_chapter_id: chapterId,
    content: input.content,
    content_format: "plain_text" as const,
    content_checksum: await checksum(input.content),
    content_size_bytes: bytes.byteLength,
    word_count: input.content.match(/[A-Za-z0-9]+|[\u3400-\u4dbf\u4e00-\u9fff]/g)?.length ?? 0,
  };
}

function pending(): PendingCandidateManualEdit {
  return {
    schema_version: 5,
    workspace: "candidate_manual_edit",
    user_id: userId,
    project_id: projectId,
    chapter_id: chapterId,
    run_id: runId,
    operation_key: input.operation_key,
    payload: input,
    created_at: now,
  };
}

describe("candidate version strict contracts", () => {
  beforeEach(() => {
    sessionStorage.clear();
    vi.restoreAllMocks();
  });

  it("accepts generated, technical and manual list provenance without inventing usage", () => {
    const value = {
      schema_version: 1,
      project_id: projectId,
      run_id: runId,
      planning_chapter_id: chapterId,
      items: [item("manual_edit", 3), item("technical_demo", 2), item("generated", 1)],
      next_cursor: null,
      has_more: false,
    };
    expect(parseCandidateVersionList(value, identity)).toEqual(value);
    expect(() => parseCandidateVersionList({
      ...value,
      items: [{ ...item("manual_edit", 3), ai_invoked_for_this_version: true }],
    }, identity)).toThrow(CandidateVersionContractError);
    expect(() => parseCandidateVersionList({ ...value, unexpected: true }, identity))
      .toThrow(CandidateVersionContractError);
  });

  it("recomputes UTF-8 bytes, word count and checksum for detail", async () => {
    const value = await detail();
    await expect(parseCandidateVersionDetail(value, { ...identity, candidateId: manualId }))
      .resolves.toEqual(value);
    await expect(parseCandidateVersionDetail({ ...value, content: `${value.content}篡改` }, {
      ...identity,
      candidateId: manualId,
    })).rejects.toThrow(CandidateVersionContractError);
  });

  it("binds a manual child to generated, technical and manual parent roots", async () => {
    const child = await detail();
    const generatedParent = {
      ...child,
      id: rootId,
      version_no: 1,
      origin_kind: "generated",
      parent_candidate_id: null,
      parent_version_no: null,
      root_candidate_id: rootId,
      root_origin_kind: "generated",
      content_checksum: input.expected_parent_checksum,
    } as GenerationCandidateVersionDetail;
    expect(candidateMatchesManualEditParent(child, generatedParent, pending(), identity)).toBe(true);

    const technicalParent = {
      ...generatedParent,
      origin_kind: "technical_demo",
      root_origin_kind: "technical_demo",
    } as GenerationCandidateVersionDetail;
    const technicalChild = {
      ...child,
      root_origin_kind: "technical_demo",
    } as GenerationCandidateVersionDetail;
    expect(candidateMatchesManualEditParent(technicalChild, technicalParent, pending(), identity)).toBe(true);

    const manualParentId = id("manual-parent");
    const manualParent = {
      ...child,
      id: manualParentId,
      version_no: 2,
      parent_candidate_id: rootId,
      parent_version_no: 1,
      root_candidate_id: rootId,
      root_origin_kind: "generated",
    } as GenerationCandidateVersionDetail;
    const manualOperation = {
      ...pending(),
      payload: {
        ...pending().payload,
        parent_candidate_id: manualParentId,
        expected_parent_version_no: 2,
        expected_parent_checksum: manualParent.content_checksum,
      },
    };
    const manualChild = {
      ...child,
      id: id("manual-child"),
      version_no: 3,
      parent_candidate_id: manualParentId,
      parent_version_no: 2,
      root_candidate_id: rootId,
      root_origin_kind: "generated",
    } as GenerationCandidateVersionDetail;
    expect(candidateMatchesManualEditParent(manualChild, manualParent, manualOperation, identity)).toBe(true);
    expect(candidateMatchesManualEditParent(
      { ...manualChild, root_candidate_id: id("fake-root") },
      manualParent,
      manualOperation,
      identity
    )).toBe(false);
  });

  it("fails closed for blank, zero-word, oversized and unchanged manual content", () => {
    expect(parseCandidateManualEditInput(input, "原文")).toEqual(input);
    for (const content of [" \n", "🚀🚀", "一".repeat(262_145), input.content]) {
      expect(() => parseCandidateManualEditInput(
        { ...input, content },
        content === input.content ? input.content : undefined
      )).toThrow(CandidateVersionContractError);
    }
  });

  it("binds a manual receipt to the frozen parent and exact content", async () => {
    const candidate = await detail();
    const response = {
      schema_version: 1,
      replayed: false,
      ai_invoked: false,
      billing_effect: "none",
      usage_status: "not_applicable",
      candidate,
    } as const;
    await expect(parseCandidateManualEditResponse(response, identity, input))
      .resolves.toEqual(response);
    await expect(parseCandidateManualEditResponse({
      ...response,
      candidate: { ...candidate, parent_candidate_id: id("other") },
    }, identity, input)).rejects.toThrow(CandidateVersionContractError);
  });

  it("uses adapters only after strict parsing", async () => {
    const candidate = await detail();
    const list = {
      schema_version: 1,
      project_id: projectId,
      run_id: runId,
      planning_chapter_id: chapterId,
      items: [item("manual_edit", 2)],
      next_cursor: null,
      has_more: false,
    } as const;
    const receipt = {
      schema_version: 1,
      replayed: false,
      ai_invoked: false,
      billing_effect: "none",
      usage_status: "not_applicable",
      candidate,
    } as const;
    vi.spyOn(api, "listGenerationCandidateVersions").mockResolvedValue(list);
    vi.spyOn(api, "createGenerationCandidateManualEdit").mockResolvedValue(receipt);
    vi.spyOn(api, "getGenerationCandidateManualEditByKey").mockResolvedValue({
      ...receipt,
      replayed: true,
    });
    expect((await listCandidateVersions(identity)).items).toHaveLength(1);
    expect((await requestCandidateManualEdit(identity, input)).candidate.id).toBe(manualId);
    expect((await readCandidateManualEditByKey(identity, pending())).replayed).toBe(true);
  });
});

describe("shared pending candidate manual edit v5", () => {
  beforeEach(() => sessionStorage.clear());

  it("round-trips, refuses overwrite and compare-clears only the exact v5 operation", () => {
    expect(isPendingCandidateManualEdit(pending(), userId, projectId)).toBe(true);
    expect(savePendingCandidateManualEdit(pending())).toBe(true);
    expect(savePendingCandidateManualEdit(pending())).toBe(true);
    expect(loadPendingCandidateManualEdit(userId, projectId)).toEqual({
      status: "available",
      operation: pending(),
    });
    expect(clearPendingCandidateManualEdit({ ...pending(), created_at: "2026-08-13T11:00:01Z" }))
      .toBe(false);
    expect(clearPendingCandidateManualEdit({
      ...pending(),
      payload: { ...pending().payload, content: "同编号但不同载荷" },
    })).toBe(false);
    expect(loadPendingCandidateManualEdit(userId, projectId).status).toBe("available");
    expect(clearPendingCandidateManualEdit(pending())).toBe(true);
    expect(loadPendingCandidateManualEdit(userId, projectId)).toEqual({ status: "missing" });
  });

  it("is recognized as foreign by v1-v4 and recognizes every older workspace", () => {
    expect(savePendingCandidateManualEdit(pending())).toBe(true);
    expect(loadPendingPlanningOperation(userId, projectId)).toEqual({
      status: "foreign", workspace: "candidate_manual_edit",
    });
    expect(loadPendingForeshadowOperation(userId, projectId)).toEqual({
      status: "foreign", workspace: "candidate_manual_edit",
    });
    expect(loadPendingGenerationExecution(userId, projectId)).toEqual({
      status: "foreign", workspace: "candidate_manual_edit",
    });
    expect(loadPendingTechnicalDemoExecution(userId, projectId)).toEqual({
      status: "foreign", workspace: "candidate_manual_edit",
    });

    const key = pendingProjectOperationKey(userId, projectId);
    for (const [older, workspace] of [
      [{ schema_version: 1, operation_key: "planning:key:12345678" }, "planning"],
      [{ schema_version: 2, workspace: "foreshadow" }, "foreshadow"],
      [{ schema_version: 3, workspace: "generation_execution" }, "generation_execution"],
      [{ schema_version: 4, workspace: "technical_demo_execution" }, "technical_demo_execution"],
    ] as const) {
      sessionStorage.setItem(key, JSON.stringify(older));
      expect(loadPendingCandidateManualEdit(userId, projectId)).toEqual({
        status: "foreign", workspace,
      });
      expect(savePendingCandidateManualEdit(pending())).toBe(false);
    }
  });

  it("fails closed for corrupt or unavailable storage", () => {
    sessionStorage.setItem(
      pendingProjectOperationKey(userId, projectId),
      JSON.stringify({ ...pending(), payload: { ...input, content: "  " } })
    );
    expect(loadPendingCandidateManualEdit(userId, projectId)).toEqual({ status: "corrupt" });
    vi.spyOn(Object.getPrototypeOf(sessionStorage) as Storage, "getItem")
      .mockImplementation(() => { throw new DOMException("blocked", "SecurityError"); });
    expect(loadPendingCandidateManualEdit(userId, projectId)).toEqual({ status: "unavailable" });
    expect(savePendingCandidateManualEdit(pending())).toBe(false);
  });
});

describe("current-tab candidate manual edit draft", () => {
  beforeEach(() => sessionStorage.clear());

  function draft(): CandidateManualEditDraft {
    return {
      schema_version: 1,
      workspace: "candidate_manual_edit_draft",
      user_id: userId,
      project_id: projectId,
      chapter_id: chapterId,
      run_id: runId,
      parent_candidate_id: rootId,
      parent_version_no: 1,
      parent_checksum: "a".repeat(64),
      context_checksum: "b".repeat(64),
      content: "尚未提交的候选草稿。",
      updated_at: now,
    };
  }

  it("binds a tab draft to chapter, run, parent version/checksum and context", () => {
    expect(saveCandidateManualEditDraft(draft())).toBe(true);
    expect(loadCandidateManualEditDraft(identity)).toEqual({ status: "available", draft: draft() });
    expect(loadCandidateManualEditDraft({ ...identity, chapterId: id("other-chapter") }))
      .toEqual({ status: "foreign", draft: draft() });
    expect(clearCandidateManualEditDraft({ ...draft(), content: "不同草稿" })).toBe(false);
    expect(clearCandidateManualEditDraft(draft())).toBe(true);
    expect(loadCandidateManualEditDraft(identity)).toEqual({ status: "missing" });
  });

  it("updates only the same draft identity and refuses foreign parent/context overwrite", () => {
    expect(saveCandidateManualEditDraft(draft())).toBe(true);
    const updated = { ...draft(), content: "同父版本的新编辑内容。", updated_at: "2026-08-13T11:01:00Z" };
    expect(saveCandidateManualEditDraft(updated)).toBe(true);
    expect(saveCandidateManualEditDraft({ ...updated, context_checksum: "c".repeat(64) })).toBe(false);
    expect(saveCandidateManualEditDraft({ ...updated, parent_version_no: 2 })).toBe(false);
    expect(loadCandidateManualEditDraft(identity)).toEqual({ status: "available", draft: updated });
    expect(clearCandidateManualEditDraft({ ...updated, content: "同父但不同内容" })).toBe(false);
    expect(loadCandidateManualEditDraft(identity)).toEqual({ status: "available", draft: updated });
    const rebased = {
      ...updated,
      parent_candidate_id: id("newparent"),
      parent_version_no: 2,
      parent_checksum: "d".repeat(64),
    };
    expect(replaceCandidateManualEditDraft({ ...updated, content: "错误旧内容" }, rebased)).toBe(false);
    expect(replaceCandidateManualEditDraft(updated, rebased)).toBe(true);
    expect(loadCandidateManualEditDraft(identity)).toEqual({ status: "available", draft: rebased });
  });

  it("fails closed for malformed or unavailable tab-draft storage", () => {
    expect(saveCandidateManualEditDraft({ ...draft(), context_checksum: "bad" })).toBe(false);
    sessionStorage.setItem(
      `novel_candidate_manual_edit_draft_v1:${userId}:${projectId}`,
      JSON.stringify({ ...draft(), unexpected: true })
    );
    expect(loadCandidateManualEditDraft(identity)).toEqual({ status: "corrupt" });
    vi.spyOn(Object.getPrototypeOf(sessionStorage) as Storage, "getItem")
      .mockImplementation(() => { throw new DOMException("blocked", "SecurityError"); });
    expect(loadCandidateManualEditDraft(identity)).toEqual({ status: "unavailable" });
  });

  it("clears only a corrupt browser draft and never a valid foreign draft", () => {
    const key = `novel_candidate_manual_edit_draft_v1:${userId}:${projectId}`;
    sessionStorage.setItem(key, "{broken");
    expect(clearCorruptCandidateManualEditDraft(userId, projectId)).toBe(true);
    expect(sessionStorage.getItem(key)).toBeNull();
    expect(saveCandidateManualEditDraft(draft())).toBe(true);
    expect(clearCorruptCandidateManualEditDraft(userId, projectId)).toBe(false);
    expect(loadCandidateManualEditDraft(identity)).toEqual({ status: "available", draft: draft() });
  });
});
