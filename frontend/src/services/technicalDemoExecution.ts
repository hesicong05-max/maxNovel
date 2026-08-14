import { api } from "@/services/api";
import { pendingProjectOperationKey } from "@/services/pendingProjectOperations";
import type { TechnicalDemoCandidateResponse, TechnicalDemoCapabilityResponse, TechnicalDemoExecuteInput, TechnicalDemoExecutionResponse } from "@/types/demo";

const CAPABILITY_KEYS = ["schema_version", "execution_mode", "fixture_version", "adapter_schema_version", "content_spec_version", "project_id", "planning_chapter_id", "run_id", "context_checksum", "fixed_response", "ai_invoked", "billing_effect", "usage_status", "capability_checksum"];
const EXECUTION_KEYS = ["schema_version", "execution_mode", "fixture_version", "adapter_schema_version", "content_spec_version", "project_id", "planning_chapter_id", "run_id", "operation_key", "context_checksum", "capability_checksum", "execution_id", "candidate_id", "status", "replayed", "ai_invoked", "billing_effect", "usage_status", "created_at", "completed_at"];
const CANDIDATE_KEYS = ["schema_version", "id", "project_id", "run_id", "planning_chapter_id", "source_technical_demo_execution_id", "parent_candidate_id", "version_no", "origin_kind", "title", "content", "content_format", "content_checksum", "content_size_bytes", "word_count", "created_by", "ai_invoked", "billing_effect", "usage_status", "created_at"];
const PENDING_KEYS = ["schema_version", "workspace", "user_id", "project_id", "chapter_id", "run_id", "operation_key", "payload", "created_at"];
const INPUT_KEYS = ["operation_key", "expected_context_checksum", "expected_capability_checksum", "fixture_version", "confirm_technical_demo"];

export interface PendingTechnicalDemoExecution { schema_version: 4; workspace: "technical_demo_execution"; user_id: string; project_id: string; chapter_id: string; run_id: string; operation_key: string; payload: TechnicalDemoExecuteInput; created_at: string; }
export type PendingTechnicalDemoLoad = { status: "missing" } | { status: "available"; operation: PendingTechnicalDemoExecution } | { status: "foreign"; workspace: "planning" | "foreshadow" | "generation_execution" | "candidate_manual_edit" | "candidate_selection" } | { status: "corrupt" } | { status: "unavailable" };
export interface TechnicalIdentity { projectId: string; chapterId: string; runId: string; operationKey?: string; contextChecksum: string; capabilityChecksum?: string; executionId?: string; candidateId?: string; userId?: string; chapterTitle?: string; }
export class TechnicalDemoContractError extends Error { constructor(message: string) { super(message); this.name = "TechnicalDemoContractError"; } }
const record = (value: unknown): value is Record<string, unknown> => !!value && typeof value === "object" && !Array.isArray(value);
const exact = (value: Record<string, unknown>, keys: string[]) => Object.keys(value).sort().join("|") === [...keys].sort().join("|");
const id = (value: unknown): value is string => typeof value === "string" && /^[A-Za-z0-9]{32}$/.test(value);
const key = (value: unknown): value is string => typeof value === "string" && value.length >= 8 && value.length <= 128 && /^[A-Za-z0-9._:-]+$/.test(value);
const checksum = (value: unknown): value is string => typeof value === "string" && /^[0-9a-f]{64}$/.test(value);
const timestamp = (value: unknown): value is string => typeof value === "string" && Number.isFinite(Date.parse(value));
function fail(message: string): never { throw new TechnicalDemoContractError(message); }

export function createTechnicalDemoOperationKey(): string { if (!crypto?.randomUUID) throw new Error("当前浏览器无法生成安全技术模拟编号。"); return `technical-demo:execute:${crypto.randomUUID()}`; }
export function parseTechnicalDemoInput(value: unknown): TechnicalDemoExecuteInput {
  if (!record(value) || !exact(value, INPUT_KEYS) || !key(value.operation_key) || !checksum(value.expected_context_checksum) || !checksum(value.expected_capability_checksum) || value.fixture_version !== 1 || value.confirm_technical_demo !== true) fail("技术模拟请求契约无效。");
  return value as unknown as TechnicalDemoExecuteInput;
}
export function parseTechnicalDemoCapability(value: unknown, expected: Omit<TechnicalIdentity, "operationKey">): TechnicalDemoCapabilityResponse {
  if (!record(value) || !exact(value, CAPABILITY_KEYS) || value.schema_version !== 1 || value.execution_mode !== "technical_demo" || value.fixture_version !== 1 || value.adapter_schema_version !== 1 || value.content_spec_version !== 1
    || value.project_id !== expected.projectId || value.planning_chapter_id !== expected.chapterId || value.run_id !== expected.runId || value.context_checksum !== expected.contextChecksum
    || value.fixed_response !== true || value.ai_invoked !== false || value.billing_effect !== "none" || value.usage_status !== "not_applicable" || !checksum(value.capability_checksum)) fail("技术模拟能力响应无效。");
  return value as unknown as TechnicalDemoCapabilityResponse;
}
export function parseTechnicalDemoExecution(value: unknown, expected: TechnicalIdentity): TechnicalDemoExecutionResponse {
  if (!record(value) || !exact(value, EXECUTION_KEYS) || value.schema_version !== 1 || value.execution_mode !== "technical_demo" || value.fixture_version !== 1 || value.adapter_schema_version !== 1 || value.content_spec_version !== 1
    || value.project_id !== expected.projectId || value.planning_chapter_id !== expected.chapterId || value.run_id !== expected.runId || value.operation_key !== expected.operationKey || value.context_checksum !== expected.contextChecksum || value.capability_checksum !== expected.capabilityChecksum
    || !id(value.execution_id) || !id(value.candidate_id) || value.status !== "succeeded" || typeof value.replayed !== "boolean" || value.ai_invoked !== false || value.billing_effect !== "none" || value.usage_status !== "not_applicable" || !timestamp(value.created_at) || !timestamp(value.completed_at)) fail("技术模拟执行响应无效。");
  return value as unknown as TechnicalDemoExecutionResponse;
}
export async function parseTechnicalDemoCandidate(value: unknown, expected: TechnicalIdentity): Promise<TechnicalDemoCandidateResponse> {
  if (!record(value) || !exact(value, CANDIDATE_KEYS) || value.schema_version !== 1 || value.id !== expected.candidateId || value.project_id !== expected.projectId || value.run_id !== expected.runId || value.planning_chapter_id !== expected.chapterId || value.source_technical_demo_execution_id !== expected.executionId
    || value.parent_candidate_id !== null || !Number.isInteger(value.version_no) || Number(value.version_no) < 1 || value.origin_kind !== "technical_demo" || typeof expected.chapterTitle !== "string" || value.title !== expected.chapterTitle || typeof value.content !== "string" || !String(value.content).trim() || value.content_format !== "plain_text" || !checksum(value.content_checksum)
    || !Number.isInteger(value.content_size_bytes) || Number(value.content_size_bytes) < 1 || Number(value.content_size_bytes) > 262144 || !Number.isInteger(value.word_count) || Number(value.word_count) < 1 || value.created_by !== expected.userId || value.ai_invoked !== false || value.billing_effect !== "none" || value.usage_status !== "not_applicable" || !timestamp(value.created_at)) fail("技术模拟候选响应无效。");
  const bytes = new TextEncoder().encode(value.content as string);
  if (bytes.byteLength !== value.content_size_bytes) fail("技术模拟候选大小校验失败。");
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  const actual = Array.from(new Uint8Array(digest), (item) => item.toString(16).padStart(2, "0")).join("");
  if (actual !== value.content_checksum) fail("技术模拟候选完整性校验失败。");
  const actualWordCount = (value.content as string).match(/[A-Za-z0-9]+|[\u3400-\u4dbf\u4e00-\u9fff]/g)?.length ?? 0;
  if (actualWordCount !== value.word_count) fail("技术模拟候选字数校验失败。");
  return value as unknown as TechnicalDemoCandidateResponse;
}

export function isPendingTechnicalDemoExecution(value: unknown, userId?: string, projectId?: string): value is PendingTechnicalDemoExecution {
  if (!record(value) || !exact(value, PENDING_KEYS) || value.schema_version !== 4 || value.workspace !== "technical_demo_execution" || !id(value.user_id) || !id(value.project_id) || !id(value.chapter_id) || !id(value.run_id) || !key(value.operation_key) || !timestamp(value.created_at) || (userId && value.user_id !== userId) || (projectId && value.project_id !== projectId)) return false;
  try { return parseTechnicalDemoInput(value.payload).operation_key === value.operation_key; } catch { return false; }
}
export function savePendingTechnicalDemoExecution(operation: PendingTechnicalDemoExecution): boolean { if (!isPendingTechnicalDemoExecution(operation, operation.user_id, operation.project_id)) return false; try { const storageKey = pendingProjectOperationKey(operation.user_id, operation.project_id); const serialized = JSON.stringify(operation); const existing = sessionStorage.getItem(storageKey); if (existing !== null) return existing === serialized; sessionStorage.setItem(storageKey, serialized); return true; } catch { return false; } }
export function loadPendingTechnicalDemoExecution(userId: string, projectId: string): PendingTechnicalDemoLoad { let raw: string | null; try { raw = sessionStorage.getItem(pendingProjectOperationKey(userId, projectId)); } catch { return { status: "unavailable" }; } if (!raw) return { status: "missing" }; try { const value = JSON.parse(raw) as Record<string, unknown>; if (value.schema_version === 1 && value.workspace === undefined) return { status: "foreign", workspace: "planning" }; if (value.schema_version === 2 && value.workspace === "foreshadow") return { status: "foreign", workspace: "foreshadow" }; if (value.schema_version === 3 && value.workspace === "generation_execution") return { status: "foreign", workspace: "generation_execution" }; if (value.schema_version === 5 && value.workspace === "candidate_manual_edit") return { status: "foreign", workspace: "candidate_manual_edit" }; if (value.schema_version === 6 && value.workspace === "candidate_selection") return { status: "foreign", workspace: "candidate_selection" }; return isPendingTechnicalDemoExecution(value, userId, projectId) ? { status: "available", operation: value } : { status: "corrupt" }; } catch { return { status: "corrupt" }; } }
export function clearPendingTechnicalDemoExecution(userId: string, projectId: string, operationKey: string): boolean { try { const storageKey = pendingProjectOperationKey(userId, projectId); const raw = sessionStorage.getItem(storageKey); if (!raw) return true; const value: unknown = JSON.parse(raw); if (!isPendingTechnicalDemoExecution(value, userId, projectId) || value.operation_key !== operationKey) return false; sessionStorage.removeItem(storageKey); return true; } catch { return false; } }

export async function readTechnicalDemoCapability(expected: Omit<TechnicalIdentity, "operationKey">, signal?: AbortSignal) { return parseTechnicalDemoCapability(await api.getTechnicalDemoCapability(expected.projectId, expected.runId, signal), expected); }
export async function requestTechnicalDemoExecution(expected: TechnicalIdentity, payload: TechnicalDemoExecuteInput) { return parseTechnicalDemoExecution(await api.executeTechnicalDemo(expected.projectId, expected.runId, payload), expected); }
export async function readTechnicalDemoExecutionByKey(expected: TechnicalIdentity, signal?: AbortSignal) { return parseTechnicalDemoExecution(await api.getTechnicalDemoExecutionByKey(expected.projectId, expected.operationKey!, signal), expected); }
export async function readTechnicalDemoCandidate(expected: TechnicalIdentity, signal?: AbortSignal) { return parseTechnicalDemoCandidate(await api.getTechnicalDemoCandidate(expected.projectId, expected.candidateId!, signal), expected); }
