import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "./api";
import {
  GenerationExecutionContractError,
  clearPendingGenerationExecution,
  isPendingGenerationExecution,
  loadPendingGenerationExecution,
  parseGenerationAttempt,
  parseGenerationCandidate,
  parseGenerationCapability,
  parseGenerationExecuteInput,
  readGenerationAttemptByKey,
  readGenerationCandidate,
  readGenerationCapability,
  requestGenerationAttempt,
  savePendingGenerationExecution,
  type PendingGenerationExecution,
} from "./generationExecution";
import type {
  GenerationAttemptExecuteInput,
  GenerationAttemptResponse,
  GenerationCapabilityResponse,
} from "@/types/generation";

const id = (value: string) => value.padEnd(32, value).slice(0, 32);
const projectId = id("project");
const runId = id("run");
const chapterId = id("chapter");
const attemptId = id("attempt");
const candidateId = id("candidate");
const userId = id("user");
const operationKey = "generation:execute:12345678";
const contextChecksum = "a".repeat(64);
const capabilityChecksum = "b".repeat(64);
const now = "2026-08-13T08:00:00Z";

function capability(): GenerationCapabilityResponse {
  return {
    schema_version: 1,
    provider_name: "deepseek",
    model_name: "deepseek-chat",
    max_output_tokens: 4096,
    input_limit_availability: "unavailable",
    max_input_tokens: null,
    price_availability: "unavailable",
    capability_checksum: capabilityChecksum,
  };
}

function executeInput(): GenerationAttemptExecuteInput {
  return {
    operation_key: operationKey,
    expected_context_checksum: contextChecksum,
    expected_capability_checksum: capabilityChecksum,
    confirm_model_call: true,
  };
}

function attempt(
  status: GenerationAttemptResponse["status"] = "reserved"
): GenerationAttemptResponse {
  const value: GenerationAttemptResponse = {
    id: attemptId,
    project_id: projectId,
    run_id: runId,
    planning_chapter_id: chapterId,
    operation_key: operationKey,
    replayed: false,
    status,
    execution_mode: "single_call",
    billing_confirmed: true,
    ai_invoked: false,
    billing_effect: "none",
    capability: capability(),
    model_name: "deepseek-chat",
    prompt_schema_version: 1,
    prompt_checksum: "c".repeat(64),
    context_checksum: contextChecksum,
    lock_version: 1,
    usage: {
      status: "unavailable",
      input_tokens: null,
      output_tokens: null,
      total_tokens: null,
    },
    candidate_id: null,
    error: null,
    claimed_at: null,
    completed_at: null,
    created_at: now,
    updated_at: now,
  };
  if (status === "calling") {
    value.ai_invoked = true;
    value.billing_effect = "possible";
    value.usage = {
      status: "unknown",
      input_tokens: null,
      output_tokens: null,
      total_tokens: null,
    };
    value.claimed_at = now;
  } else if (status === "succeeded") {
    value.ai_invoked = true;
    value.billing_effect = "possible";
    value.usage = {
      status: "reported",
      input_tokens: 10,
      output_tokens: 20,
      total_tokens: 30,
    };
    value.claimed_at = now;
    value.completed_at = now;
    value.candidate_id = candidateId;
  } else if (status === "failed") {
    value.completed_at = now;
    value.error = {
      code: "LLM_CONFIGURATION_CHANGED",
      message: "能力已改变",
      retryable: false,
      recommended_action: "inspect_failure",
    };
  } else if (status === "outcome_unknown") {
    value.ai_invoked = true;
    value.billing_effect = "possible";
    value.usage = {
      status: "unknown",
      input_tokens: null,
      output_tokens: null,
      total_tokens: null,
    };
    value.claimed_at = now;
    value.completed_at = now;
    value.error = {
      code: "LLM_OUTCOME_UNKNOWN",
      message: "结果未知",
      retryable: false,
      recommended_action: "keep_unknown_result",
    };
  }
  return value;
}

const expectedAttempt = {
  projectId,
  runId,
  chapterId,
  operationKey,
  contextChecksum,
  capabilityChecksum,
};

async function digest(content: string): Promise<string> {
  const value = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(content));
  return Array.from(new Uint8Array(value), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

describe("generation execution runtime contracts", () => {
  it("accepts only the exact eight-field capability wire contract", () => {
    expect(parseGenerationCapability(capability())).toEqual(capability());
    for (const invalid of [
      { ...capability(), unexpected: true },
      { ...capability(), max_input_tokens: 100_000 },
      { ...capability(), input_limit_availability: "available" },
      { ...capability(), price_availability: "available" },
      { ...capability(), capability_checksum: "B".repeat(64) },
    ]) {
      expect(() => parseGenerationCapability(invalid)).toThrow(
        GenerationExecutionContractError
      );
    }
  });

  it("requires an exact execute payload and literal confirmation", () => {
    expect(parseGenerationExecuteInput(executeInput())).toEqual(executeInput());
    expect(() => parseGenerationExecuteInput({
      ...executeInput(),
      confirm_model_call: false,
    })).toThrow(GenerationExecutionContractError);
    expect(() => parseGenerationExecuteInput({
      ...executeInput(),
      unexpected: true,
    })).toThrow(GenerationExecutionContractError);
  });

  it.each([
    "reserved", "calling", "succeeded", "failed", "outcome_unknown",
  ] as const)("accepts the strict %s attempt state", (status) => {
    expect(parseGenerationAttempt(attempt(status), expectedAttempt).status).toBe(status);
  });

  it("accepts the post-call failed shape but rejects cross-state combinations", () => {
    const postCallFailure = attempt("failed");
    postCallFailure.ai_invoked = true;
    postCallFailure.billing_effect = "possible";
    postCallFailure.claimed_at = now;
    expect(parseGenerationAttempt(postCallFailure, expectedAttempt).status).toBe("failed");

    const invalidValues = [
      { ...attempt("reserved"), ai_invoked: true },
      { ...attempt("calling"), candidate_id: candidateId },
      { ...attempt("succeeded"), candidate_id: null },
      { ...attempt("failed"), claimed_at: now },
      { ...attempt("outcome_unknown"), usage: attempt("reserved").usage },
      { ...attempt("succeeded"), unexpected: true },
      { ...attempt("succeeded"), model_name: "other-model" },
      { ...attempt("succeeded"), context_checksum: "d".repeat(64) },
    ];
    for (const invalid of invalidValues) {
      expect(() => parseGenerationAttempt(invalid, expectedAttempt)).toThrow(
        GenerationExecutionContractError
      );
    }
  });

  it("rejects fabricated or inconsistent usage", () => {
    const invalidTotal = attempt("succeeded");
    invalidTotal.usage = {
      status: "reported",
      input_tokens: 10,
      output_tokens: 20,
      total_tokens: 31,
    };
    expect(() => parseGenerationAttempt(invalidTotal, expectedAttempt)).toThrow();

    const fabricatedUnknown = attempt("outcome_unknown") as unknown as Record<string, unknown>;
    fabricatedUnknown.usage = {
      status: "unknown",
      input_tokens: 0,
      output_tokens: 0,
      total_tokens: 0,
    };
    expect(() => parseGenerationAttempt(fabricatedUnknown, expectedAttempt)).toThrow();
  });

  it("verifies candidate identity, UTF-8 bytes, words, and SHA-256 before returning content", async () => {
    const content = "星港 gate";
    const value = {
      id: candidateId,
      project_id: projectId,
      run_id: runId,
      planning_chapter_id: chapterId,
      source_attempt_id: attemptId,
      parent_candidate_id: null,
      version_no: 1,
      origin_kind: "generated",
      title: "第一章",
      content,
      content_format: "plain_text",
      content_checksum: await digest(content),
      content_size_bytes: new TextEncoder().encode(content).byteLength,
      word_count: 3,
      created_by: userId,
      created_at: now,
    };
    const expected = {
      projectId,
      runId,
      chapterId,
      attemptId,
      candidateId,
      userId,
      chapterTitle: "第一章",
    };
    expect((await parseGenerationCandidate(value, expected)).content).toBe(content);
    for (const invalid of [
      { ...value, project_id: id("foreign") },
      { ...value, source_attempt_id: id("other") },
      { ...value, content_checksum: "f".repeat(64) },
      { ...value, content_size_bytes: value.content_size_bytes + 1 },
      { ...value, word_count: 2 },
      { ...value, unexpected: true },
    ]) {
      await expect(parseGenerationCandidate(invalid, expected)).rejects.toBeInstanceOf(
        GenerationExecutionContractError
      );
    }
  });
});

describe("strict generation API adapters", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("parses all four adapter responses and binds expected identities", async () => {
    vi.spyOn(api, "getGenerationCapability").mockResolvedValue(capability());
    vi.spyOn(api, "executeGenerationAttempt").mockResolvedValue(attempt("succeeded"));
    vi.spyOn(api, "getGenerationAttemptByKey").mockResolvedValue({
      ...attempt("succeeded"), replayed: true,
    });
    const content = "星港 gate";
    const candidate = {
      id: candidateId, project_id: projectId, run_id: runId,
      planning_chapter_id: chapterId, source_attempt_id: attemptId,
      parent_candidate_id: null, version_no: 1, origin_kind: "generated",
      title: "第一章", content, content_format: "plain_text",
      content_checksum: await digest(content),
      content_size_bytes: new TextEncoder().encode(content).byteLength,
      word_count: 3, created_by: userId, created_at: now,
    };
    vi.spyOn(api, "getGenerationCandidate").mockResolvedValue(candidate);

    expect((await readGenerationCapability(projectId)).provider_name).toBe("deepseek");
    expect((await requestGenerationAttempt(
      projectId, runId, chapterId, executeInput()
    )).status).toBe("succeeded");
    expect((await readGenerationAttemptByKey(expectedAttempt)).replayed).toBe(true);
    expect((await readGenerationCandidate({
      projectId, runId, chapterId, attemptId, candidateId, userId,
      chapterTitle: "第一章",
    })).content).toBe(content);
  });
});

function pending(): PendingGenerationExecution {
  return {
    schema_version: 3,
    workspace: "generation_execution",
    user_id: userId,
    project_id: projectId,
    chapter_id: chapterId,
    run_id: runId,
    operation_key: operationKey,
    payload: executeInput(),
    created_at: now,
  };
}

describe("shared generation execution pending v3", () => {
  beforeEach(() => sessionStorage.clear());

  it("round-trips exact v3 and compare-clears only the matching operation", () => {
    expect(isPendingGenerationExecution(pending(), userId, projectId)).toBe(true);
    expect(savePendingGenerationExecution(pending())).toBe(true);
    expect(savePendingGenerationExecution(pending())).toBe(true);
    expect(loadPendingGenerationExecution(userId, projectId)).toEqual({
      status: "available",
      operation: pending(),
    });
    expect(clearPendingGenerationExecution(userId, projectId, "other:key:12345678")).toBe(false);
    expect(loadPendingGenerationExecution(userId, projectId).status).toBe("available");
    expect(clearPendingGenerationExecution(userId, projectId, operationKey)).toBe(true);
    expect(loadPendingGenerationExecution(userId, projectId)).toEqual({ status: "missing" });
  });

  it("isolates users/projects and refuses same-key payload replacement", () => {
    expect(savePendingGenerationExecution(pending())).toBe(true);
    expect(loadPendingGenerationExecution(id("other-user"), projectId)).toEqual({ status: "missing" });
    expect(loadPendingGenerationExecution(userId, id("other-project"))).toEqual({ status: "missing" });
    const changed = {
      ...pending(),
      payload: {
        ...executeInput(),
        expected_capability_checksum: "e".repeat(64),
      },
    };
    expect(savePendingGenerationExecution(changed)).toBe(false);
    expect(loadPendingGenerationExecution(userId, projectId)).toEqual({
      status: "available",
      operation: pending(),
    });
  });

  it("recognizes v1/v2 as foreign and never overwrites either workspace", () => {
    const key = `novel_pending_planning_operation_v1:${userId}:${projectId}`;
    const planning = { schema_version: 1, operation_key: "planning:write:12345678" };
    sessionStorage.setItem(key, JSON.stringify(planning));
    expect(loadPendingGenerationExecution(userId, projectId)).toEqual({
      status: "foreign", workspace: "planning",
    });
    expect(savePendingGenerationExecution(pending())).toBe(false);
    expect(JSON.parse(sessionStorage.getItem(key)!)).toEqual(planning);

    const foreshadow = {
      schema_version: 2,
      workspace: "foreshadow",
      operation_key: "foreshadow:write:12345678",
    };
    sessionStorage.setItem(key, JSON.stringify(foreshadow));
    expect(loadPendingGenerationExecution(userId, projectId)).toEqual({
      status: "foreign", workspace: "foreshadow",
    });
    expect(savePendingGenerationExecution(pending())).toBe(false);
    expect(JSON.parse(sessionStorage.getItem(key)!)).toEqual(foreshadow);
  });

  it("fails closed for payload drift, extra keys, corrupt JSON, and unavailable storage", () => {
    const key = `novel_pending_planning_operation_v1:${userId}:${projectId}`;
    for (const invalid of [
      { ...pending(), operation_key: "different:key:12345678" },
      { ...pending(), payload: { ...executeInput(), confirm_model_call: false } },
      { ...pending(), unexpected: true },
    ]) {
      sessionStorage.setItem(key, JSON.stringify(invalid));
      expect(loadPendingGenerationExecution(userId, projectId)).toEqual({ status: "corrupt" });
      expect(clearPendingGenerationExecution(userId, projectId, operationKey)).toBe(false);
    }
    sessionStorage.setItem(key, "{broken");
    expect(loadPendingGenerationExecution(userId, projectId)).toEqual({ status: "corrupt" });

    vi.spyOn(Object.getPrototypeOf(sessionStorage) as Storage, "getItem")
      .mockImplementationOnce(() => { throw new DOMException("blocked", "SecurityError"); });
    expect(loadPendingGenerationExecution(userId, projectId)).toEqual({ status: "unavailable" });
  });

  it("returns false without mutation when storage save or clear is unavailable", () => {
    vi.spyOn(Object.getPrototypeOf(sessionStorage) as Storage, "getItem")
      .mockImplementation(() => { throw new DOMException("blocked", "SecurityError"); });
    expect(savePendingGenerationExecution(pending())).toBe(false);
    expect(clearPendingGenerationExecution(userId, projectId, operationKey)).toBe(false);
  });
});
