import { beforeEach, describe, expect, it, vi } from "vitest";
import { loadPendingForeshadowOperation } from "./foreshadowOperations";
import { loadPendingGenerationExecution } from "./generationExecution";
import { loadPendingPlanningOperation } from "./planningOperations";
import { pendingProjectOperationKey } from "./pendingProjectOperations";
import { TechnicalDemoContractError, clearPendingTechnicalDemoExecution, loadPendingTechnicalDemoExecution, parseTechnicalDemoCandidate, parseTechnicalDemoCapability, parseTechnicalDemoExecution, savePendingTechnicalDemoExecution, type PendingTechnicalDemoExecution } from "./technicalDemoExecution";

const id = (seed: string) => seed.padEnd(32, seed).slice(0, 32);
const userId = id("user"), projectId = id("project"), chapterId = id("chapter"), runId = id("run");
const contextChecksum = "a".repeat(64), capabilityChecksum = "b".repeat(64);
const operation: PendingTechnicalDemoExecution = { schema_version: 4, workspace: "technical_demo_execution", user_id: userId, project_id: projectId, chapter_id: chapterId, run_id: runId, operation_key: "technical-demo:execute:12345678", payload: { operation_key: "technical-demo:execute:12345678", expected_context_checksum: contextChecksum, expected_capability_checksum: capabilityChecksum, fixture_version: 1, confirm_technical_demo: true }, created_at: "2026-08-13T08:00:00Z" };
const capability = { schema_version: 1, execution_mode: "technical_demo", fixture_version: 1, adapter_schema_version: 1, content_spec_version: 1, project_id: projectId, planning_chapter_id: chapterId, run_id: runId, context_checksum: contextChecksum, fixed_response: true, ai_invoked: false, billing_effect: "none", usage_status: "not_applicable", capability_checksum: capabilityChecksum } as const;
const execution = { schema_version: 1, execution_mode: "technical_demo", fixture_version: 1, adapter_schema_version: 1, content_spec_version: 1, project_id: projectId, planning_chapter_id: chapterId, run_id: runId, operation_key: operation.operation_key, context_checksum: contextChecksum, capability_checksum: capabilityChecksum, execution_id: id("execution"), candidate_id: id("candidate"), status: "succeeded", replayed: false, ai_invoked: false, billing_effect: "none", usage_status: "not_applicable", created_at: "2026-08-13T08:00:00Z", completed_at: "2026-08-13T08:00:01Z" } as const;

describe("technical demo execution contracts", () => {
  beforeEach(() => { sessionStorage.clear(); vi.restoreAllMocks(); });
  it("strictly validates capability and terminal-only succeeded execution", () => {
    expect(parseTechnicalDemoCapability(capability, { projectId, chapterId, runId, contextChecksum })).toEqual(capability);
    expect(parseTechnicalDemoExecution(execution, { projectId, chapterId, runId, operationKey: operation.operation_key, contextChecksum, capabilityChecksum })).toEqual(execution);
    expect(() => parseTechnicalDemoExecution({ ...execution, status: "calling" }, { projectId, chapterId, runId, operationKey: operation.operation_key, contextChecksum, capabilityChecksum })).toThrow(TechnicalDemoContractError);
    expect(() => parseTechnicalDemoCapability({ ...capability, provider_name: "fake" }, { projectId, chapterId, runId, contextChecksum })).toThrow(TechnicalDemoContractError);
  });
  it("validates candidate identity, bytes, SHA-256, and zero-AI facts", async () => {
    const content = "这是固定技术模拟候选，不调用人工智能。";
    const bytes = new TextEncoder().encode(content);
    const digest = await crypto.subtle.digest("SHA-256", bytes);
    const content_checksum = Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
    const word_count = content.match(/[A-Za-z0-9]+|[\u3400-\u4dbf\u4e00-\u9fff]/g)?.length ?? 0;
    const candidate = { schema_version: 1, id: execution.candidate_id, project_id: projectId, run_id: runId, planning_chapter_id: chapterId, source_technical_demo_execution_id: execution.execution_id, parent_candidate_id: null, version_no: 1, origin_kind: "technical_demo", title: "第一章", content, content_format: "plain_text", content_checksum, content_size_bytes: bytes.byteLength, word_count, created_by: userId, ai_invoked: false, billing_effect: "none", usage_status: "not_applicable", created_at: "2026-08-13T08:00:01Z" } as const;
    const expected = { projectId, chapterId, runId, executionId: execution.execution_id, candidateId: execution.candidate_id, contextChecksum, userId, chapterTitle: "第一章" };
    await expect(parseTechnicalDemoCandidate(candidate, expected)).resolves.toEqual(candidate);
    await expect(parseTechnicalDemoCandidate({ ...candidate, content: `${content}篡改` }, expected)).rejects.toThrow(TechnicalDemoContractError);
    await expect(parseTechnicalDemoCandidate({ ...candidate, word_count: word_count + 1 }, expected)).rejects.toThrow(TechnicalDemoContractError);
    await expect(parseTechnicalDemoCandidate({ ...candidate, title: "第二章" }, expected)).rejects.toThrow(TechnicalDemoContractError);
  });
  it("stores v4 without overwriting and all older workspaces recognize it as foreign", () => {
    expect(savePendingTechnicalDemoExecution(operation)).toBe(true);
    expect(loadPendingTechnicalDemoExecution(userId, projectId)).toEqual({ status: "available", operation });
    expect(loadPendingPlanningOperation(userId, projectId)).toEqual({ status: "foreign", workspace: "technical_demo_execution" });
    expect(loadPendingForeshadowOperation(userId, projectId)).toEqual({ status: "foreign", workspace: "technical_demo_execution" });
    expect(loadPendingGenerationExecution(userId, projectId)).toEqual({ status: "foreign", workspace: "technical_demo_execution" });
    sessionStorage.setItem(pendingProjectOperationKey(userId, projectId), JSON.stringify({ ...operation, operation_key: "technical-demo:execute:other123" }));
    expect(savePendingTechnicalDemoExecution(operation)).toBe(false);
  });
  it("compare-clears only the exact v4 operation", () => {
    expect(savePendingTechnicalDemoExecution(operation)).toBe(true);
    expect(clearPendingTechnicalDemoExecution(userId, projectId, "technical-demo:execute:wrong123")).toBe(false);
    expect(loadPendingTechnicalDemoExecution(userId, projectId).status).toBe("available");
    expect(clearPendingTechnicalDemoExecution(userId, projectId, operation.operation_key)).toBe(true);
    expect(loadPendingTechnicalDemoExecution(userId, projectId)).toEqual({ status: "missing" });
  });
});
