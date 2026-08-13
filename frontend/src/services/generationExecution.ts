import { api } from "@/services/api";
import { pendingProjectOperationKey } from "@/services/pendingProjectOperations";
import type {
  GenerationAttemptExecuteInput,
  GenerationAttemptResponse,
  GenerationCandidateResponse,
  GenerationCapabilityResponse,
} from "@/types/generation";

const CAPABILITY_KEYS = [
  "schema_version",
  "provider_name",
  "model_name",
  "max_output_tokens",
  "input_limit_availability",
  "max_input_tokens",
  "price_availability",
  "capability_checksum",
];
const ATTEMPT_KEYS = [
  "id",
  "project_id",
  "run_id",
  "planning_chapter_id",
  "operation_key",
  "replayed",
  "status",
  "execution_mode",
  "billing_confirmed",
  "ai_invoked",
  "billing_effect",
  "capability",
  "model_name",
  "prompt_schema_version",
  "prompt_checksum",
  "context_checksum",
  "lock_version",
  "usage",
  "candidate_id",
  "error",
  "claimed_at",
  "completed_at",
  "created_at",
  "updated_at",
];
const CANDIDATE_KEYS = [
  "id",
  "project_id",
  "run_id",
  "planning_chapter_id",
  "source_attempt_id",
  "parent_candidate_id",
  "version_no",
  "origin_kind",
  "title",
  "content",
  "content_format",
  "content_checksum",
  "content_size_bytes",
  "word_count",
  "created_by",
  "created_at",
];
const EXECUTE_KEYS = [
  "operation_key",
  "expected_context_checksum",
  "expected_capability_checksum",
  "confirm_model_call",
];
const PENDING_KEYS = [
  "schema_version",
  "workspace",
  "user_id",
  "project_id",
  "chapter_id",
  "run_id",
  "operation_key",
  "payload",
  "created_at",
];

export class GenerationExecutionContractError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "GenerationExecutionContractError";
  }
}

function fail(message: string): never {
  throw new GenerationExecutionContractError(message);
}

function record(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value);
}

function exact(value: Record<string, unknown>, keys: string[]): boolean {
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  return actual.length === expected.length
    && actual.every((key, index) => key === expected[index]);
}

function stableId(value: unknown): value is string {
  return typeof value === "string" && /^[a-zA-Z0-9]{32}$/.test(value);
}

function operationKey(value: unknown): value is string {
  return typeof value === "string"
    && value.length >= 8
    && value.length <= 128
    && /^[A-Za-z0-9._:-]+$/.test(value);
}

function checksum(value: unknown): value is string {
  return typeof value === "string" && /^[0-9a-f]{64}$/.test(value);
}

function positiveInteger(value: unknown, max?: number): value is number {
  return typeof value === "number"
    && Number.isInteger(value)
    && value >= 1
    && (max === undefined || value <= max);
}

function nonNegativeInteger(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= 0;
}

function timestamp(value: unknown): value is string {
  return typeof value === "string" && Number.isFinite(Date.parse(value));
}

export function parseGenerationCapability(
  value: unknown
): GenerationCapabilityResponse {
  if (!record(value) || !exact(value, CAPABILITY_KEYS)) {
    return fail("模型能力响应字段不完整。");
  }
  if (
    value.schema_version !== 1
    || typeof value.provider_name !== "string"
    || value.provider_name.length < 1
    || value.provider_name.length > 80
    || typeof value.model_name !== "string"
    || value.model_name.length < 1
    || value.model_name.length > 200
    || !positiveInteger(value.max_output_tokens, 1_000_000)
    || value.input_limit_availability !== "unavailable"
    || value.max_input_tokens !== null
    || value.price_availability !== "unavailable"
    || !checksum(value.capability_checksum)
  ) {
    return fail("模型能力响应的版本、上限或校验值无效。");
  }
  return value as unknown as GenerationCapabilityResponse;
}

export function parseGenerationExecuteInput(
  value: unknown
): GenerationAttemptExecuteInput {
  if (!record(value) || !exact(value, EXECUTE_KEYS)) {
    return fail("生成执行请求字段不完整。");
  }
  if (
    !operationKey(value.operation_key)
    || !checksum(value.expected_context_checksum)
    || !checksum(value.expected_capability_checksum)
    || value.confirm_model_call !== true
  ) {
    return fail("生成执行请求的确认信息无效。");
  }
  return value as unknown as GenerationAttemptExecuteInput;
}

export interface ExpectedGenerationAttempt {
  projectId: string;
  runId: string;
  chapterId: string;
  operationKey: string;
  contextChecksum?: string;
  capabilityChecksum?: string;
}

function validUsage(value: unknown, status: string): boolean {
  if (!record(value) || !exact(value, [
    "status", "input_tokens", "output_tokens", "total_tokens",
  ])) return false;
  if (value.status === "reported") {
    return status === "succeeded"
      && nonNegativeInteger(value.input_tokens)
      && nonNegativeInteger(value.output_tokens)
      && nonNegativeInteger(value.total_tokens)
      && value.total_tokens === value.input_tokens + value.output_tokens;
  }
  if (value.status !== "unavailable" && value.status !== "unknown") return false;
  if (
    value.input_tokens !== null
    || value.output_tokens !== null
    || value.total_tokens !== null
  ) return false;
  if (status === "reserved" || status === "failed") {
    return value.status === "unavailable";
  }
  if (status === "calling" || status === "outcome_unknown") {
    return value.status === "unknown";
  }
  return status === "succeeded" && value.status === "unavailable";
}

function validError(value: unknown, status: string): boolean {
  if (status !== "failed" && status !== "outcome_unknown") return value === null;
  if (!record(value) || !exact(value, [
    "code", "message", "retryable", "recommended_action",
  ])) return false;
  return typeof value.code === "string"
    && value.code.length >= 1
    && value.code.length <= 80
    && typeof value.message === "string"
    && value.retryable === false
    && value.recommended_action === (
      status === "outcome_unknown" ? "keep_unknown_result" : "inspect_failure"
    );
}

function validAttemptState(value: Record<string, unknown>): boolean {
  const status = value.status;
  if (status === "reserved") {
    return value.ai_invoked === false
      && value.billing_effect === "none"
      && value.claimed_at === null
      && value.completed_at === null
      && value.candidate_id === null;
  }
  if (status === "calling") {
    return value.ai_invoked === true
      && value.billing_effect === "possible"
      && timestamp(value.claimed_at)
      && value.completed_at === null
      && value.candidate_id === null;
  }
  if (status === "succeeded") {
    return value.ai_invoked === true
      && value.billing_effect === "possible"
      && timestamp(value.claimed_at)
      && timestamp(value.completed_at)
      && stableId(value.candidate_id);
  }
  if (status === "failed") {
    const beforeCall = value.ai_invoked === false
      && value.billing_effect === "none"
      && value.claimed_at === null;
    const afterCall = value.ai_invoked === true
      && value.billing_effect === "possible"
      && timestamp(value.claimed_at);
    return (beforeCall || afterCall)
      && timestamp(value.completed_at)
      && value.candidate_id === null;
  }
  if (status === "outcome_unknown") {
    return value.ai_invoked === true
      && value.billing_effect === "possible"
      && timestamp(value.claimed_at)
      && timestamp(value.completed_at)
      && value.candidate_id === null;
  }
  return false;
}

export function parseGenerationAttempt(
  value: unknown,
  expected: ExpectedGenerationAttempt
): GenerationAttemptResponse {
  if (!record(value) || !exact(value, ATTEMPT_KEYS)) {
    return fail("生成执行记录字段不完整。");
  }
  if (
    value.project_id !== expected.projectId
    || value.run_id !== expected.runId
    || value.planning_chapter_id !== expected.chapterId
    || value.operation_key !== expected.operationKey
    || !stableId(value.id)
    || !stableId(value.project_id)
    || !stableId(value.run_id)
    || !stableId(value.planning_chapter_id)
    || !operationKey(value.operation_key)
    || typeof value.replayed !== "boolean"
    || value.execution_mode !== "single_call"
    || value.billing_confirmed !== true
    || !positiveInteger(value.prompt_schema_version)
    || !checksum(value.prompt_checksum)
    || !checksum(value.context_checksum)
    || !positiveInteger(value.lock_version)
    || !timestamp(value.created_at)
    || !timestamp(value.updated_at)
  ) {
    return fail("生成执行记录的身份、版本或时间无效。");
  }
  if (
    expected.contextChecksum !== undefined
    && value.context_checksum !== expected.contextChecksum
  ) return fail("生成执行记录与已确认上下文不一致。");
  const capability = parseGenerationCapability(value.capability);
  if (
    value.model_name !== capability.model_name
    || (
      expected.capabilityChecksum !== undefined
      && capability.capability_checksum !== expected.capabilityChecksum
    )
  ) return fail("生成执行记录与已确认模型能力不一致。");
  if (
    !validAttemptState(value)
    || !validUsage(value.usage, String(value.status))
    || !validError(value.error, String(value.status))
  ) return fail("生成执行记录的状态组合无效。");
  return value as unknown as GenerationAttemptResponse;
}

export interface ExpectedGenerationCandidate {
  projectId: string;
  runId: string;
  chapterId: string;
  attemptId: string;
  candidateId: string;
  userId: string;
  chapterTitle: string;
}

function candidateWordCount(content: string): number {
  return content.match(/[A-Za-z0-9]+|[\u3400-\u4dbf\u4e00-\u9fff]/g)?.length ?? 0;
}

async function sha256(value: Uint8Array): Promise<string> {
  if (!globalThis.crypto?.subtle) {
    return fail("当前环境无法安全校验生成候选。");
  }
  const bytes = value.buffer.slice(
    value.byteOffset,
    value.byteOffset + value.byteLength
  ) as ArrayBuffer;
  const digest = await globalThis.crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

export async function parseGenerationCandidate(
  value: unknown,
  expected: ExpectedGenerationCandidate
): Promise<GenerationCandidateResponse> {
  if (!record(value) || !exact(value, CANDIDATE_KEYS)) {
    return fail("生成候选字段不完整。");
  }
  if (
    value.id !== expected.candidateId
    || value.project_id !== expected.projectId
    || value.run_id !== expected.runId
    || value.planning_chapter_id !== expected.chapterId
    || value.source_attempt_id !== expected.attemptId
    || value.created_by !== expected.userId
    || value.title !== expected.chapterTitle
    || !stableId(value.id)
    || !stableId(value.project_id)
    || !stableId(value.run_id)
    || !stableId(value.planning_chapter_id)
    || !stableId(value.source_attempt_id)
    || !stableId(value.created_by)
    || value.parent_candidate_id !== null
    || !positiveInteger(value.version_no)
    || value.origin_kind !== "generated"
    || typeof value.title !== "string"
    || typeof value.content !== "string"
    || value.content.trim().length < 1
    || value.content_format !== "plain_text"
    || !checksum(value.content_checksum)
    || !positiveInteger(value.content_size_bytes, 262_144)
    || !positiveInteger(value.word_count)
    || !timestamp(value.created_at)
  ) return fail("生成候选的身份、来源或格式无效。");
  const bytes = new TextEncoder().encode(value.content);
  if (
    bytes.byteLength !== value.content_size_bytes
    || candidateWordCount(value.content) !== value.word_count
    || await sha256(bytes) !== value.content_checksum
  ) return fail("生成候选内容校验失败，已停止展示。");
  return value as unknown as GenerationCandidateResponse;
}

export async function readGenerationCapability(
  projectId: string,
  signal?: AbortSignal
): Promise<GenerationCapabilityResponse> {
  return parseGenerationCapability(await api.getGenerationCapability(projectId, signal));
}

export async function requestGenerationAttempt(
  projectId: string,
  runId: string,
  chapterId: string,
  input: GenerationAttemptExecuteInput
): Promise<GenerationAttemptResponse> {
  const payload = parseGenerationExecuteInput(input);
  return parseGenerationAttempt(
    await api.executeGenerationAttempt(projectId, runId, payload),
    {
      projectId,
      runId,
      chapterId,
      operationKey: payload.operation_key,
      contextChecksum: payload.expected_context_checksum,
      capabilityChecksum: payload.expected_capability_checksum,
    }
  );
}

export async function readGenerationAttemptByKey(
  expected: ExpectedGenerationAttempt,
  signal?: AbortSignal
): Promise<GenerationAttemptResponse> {
  return parseGenerationAttempt(
    await api.getGenerationAttemptByKey(
      expected.projectId,
      expected.operationKey,
      signal
    ),
    expected
  );
}

export async function readGenerationCandidate(
  expected: ExpectedGenerationCandidate,
  signal?: AbortSignal
): Promise<GenerationCandidateResponse> {
  return parseGenerationCandidate(
    await api.getGenerationCandidate(
      expected.projectId,
      expected.candidateId,
      signal
    ),
    expected
  );
}

export interface PendingGenerationExecution {
  schema_version: 3;
  workspace: "generation_execution";
  user_id: string;
  project_id: string;
  chapter_id: string;
  run_id: string;
  operation_key: string;
  payload: GenerationAttemptExecuteInput;
  created_at: string;
}

export type PendingGenerationExecutionLoad =
  | { status: "missing" }
  | { status: "available"; operation: PendingGenerationExecution }
  | { status: "foreign"; workspace: "planning" | "foreshadow" }
  | { status: "corrupt" }
  | { status: "unavailable" };

export function createGenerationExecutionKey(): string {
  if (!globalThis.crypto?.randomUUID) {
    return fail("当前浏览器无法生成安全执行编号。");
  }
  return `generation:execute:${globalThis.crypto.randomUUID()}`;
}

export function isPendingGenerationExecution(
  value: unknown,
  userId?: string,
  projectId?: string
): value is PendingGenerationExecution {
  if (!record(value) || !exact(value, PENDING_KEYS)) return false;
  if (
    value.schema_version !== 3
    || value.workspace !== "generation_execution"
    || (userId !== undefined && value.user_id !== userId)
    || (projectId !== undefined && value.project_id !== projectId)
    || !stableId(value.user_id)
    || !stableId(value.project_id)
    || !stableId(value.chapter_id)
    || !stableId(value.run_id)
    || !operationKey(value.operation_key)
    || !timestamp(value.created_at)
  ) return false;
  try {
    const payload = parseGenerationExecuteInput(value.payload);
    return payload.operation_key === value.operation_key;
  } catch {
    return false;
  }
}

export function savePendingGenerationExecution(
  operation: PendingGenerationExecution
): boolean {
  if (!isPendingGenerationExecution(
    operation,
    operation.user_id,
    operation.project_id
  )) return false;
  try {
    const key = pendingProjectOperationKey(operation.user_id, operation.project_id);
    const serialized = JSON.stringify(operation);
    const existing = sessionStorage.getItem(key);
    if (existing !== null) return existing === serialized;
    sessionStorage.setItem(key, serialized);
    return true;
  } catch {
    return false;
  }
}

export function loadPendingGenerationExecution(
  userId: string,
  projectId: string
): PendingGenerationExecutionLoad {
  let raw: string | null;
  try {
    raw = sessionStorage.getItem(pendingProjectOperationKey(userId, projectId));
  } catch {
    return { status: "unavailable" };
  }
  if (!raw) return { status: "missing" };
  try {
    const value = JSON.parse(raw) as Record<string, unknown>;
    if (value.schema_version === 1 && value.workspace === undefined) {
      return { status: "foreign", workspace: "planning" };
    }
    if (value.schema_version === 2 && value.workspace === "foreshadow") {
      return { status: "foreign", workspace: "foreshadow" };
    }
    if (!isPendingGenerationExecution(value, userId, projectId)) {
      return { status: "corrupt" };
    }
    return { status: "available", operation: value };
  } catch {
    return { status: "corrupt" };
  }
}

export function clearPendingGenerationExecution(
  userId: string,
  projectId: string,
  operationKeyValue: string
): boolean {
  try {
    const key = pendingProjectOperationKey(userId, projectId);
    const raw = sessionStorage.getItem(key);
    if (!raw) return true;
    const value: unknown = JSON.parse(raw);
    if (
      !isPendingGenerationExecution(value, userId, projectId)
      || value.operation_key !== operationKeyValue
    ) return false;
    sessionStorage.removeItem(key);
    return true;
  } catch {
    return false;
  }
}
