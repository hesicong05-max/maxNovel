import { api } from "@/services/api";
import { pendingProjectOperationKey } from "@/services/pendingProjectOperations";
import type {
  GenerationAttemptExecuteInput,
  GenerationAttemptResponse,
  GenerationCandidateAuditResponse,
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
const AUDIT_KEYS = [
  "schema_version",
  "ruleset_version",
  "project_id",
  "run_id",
  "planning_chapter_id",
  "candidate_id",
  "candidate_version",
  "candidate_checksum",
  "context_checksum",
  "status",
  "integrity",
  "target_length",
  "preparation",
  "unrecognized_explicit_terms",
  "context_summary",
];
const AUDIT_INTEGRITY_KEYS = [
  "status", "content_size_bytes", "word_count", "storage_limit_bytes",
  "storage_limit_reached",
];
const AUDIT_TARGET_KEYS = [
  "status", "actual_word_count", "target_word_count", "minimum_word_count",
  "maximum_word_count",
];
const AUDIT_PREPARATION_KEYS = ["status", "warnings"];
const AUDIT_TERMS_KEYS = ["status", "items", "truncated"];
const AUDIT_TERM_KEYS = ["term", "excerpt", "start_offset", "end_offset"];
const AUDIT_CONTEXT_KEYS = [
  "element_count", "relation_count", "warning_count", "elements",
  "foreshadow_actions_supported", "foreshadow_action_count",
];
const AUDIT_CONTEXT_ELEMENT_KEYS = [
  "element_id", "type_key", "type_display_name", "name", "version_no",
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

export interface ExpectedGenerationCandidateAudit {
  projectId: string;
  runId: string;
  chapterId: string;
  candidate: Pick<GenerationCandidateResponse,
    "id" | "version_no" | "content" | "content_checksum" | "content_size_bytes" | "word_count">;
  contextChecksum: string;
  targetWordCount: number | null;
  elements: Array<{
    elementId: string;
    typeKey: string;
    typeDisplayName: string;
    name: string;
    versionNo: number;
  }>;
  relationCount: number;
  warnings: Array<{ code: string; element_id: string | null }>;
}

function auditStatus(value: unknown): value is "pass" | "review" {
  return value === "pass" || value === "review";
}

export function parseGenerationCandidateAudit(
  value: unknown,
  expected: ExpectedGenerationCandidateAudit
): GenerationCandidateAuditResponse {
  if (!record(value) || !exact(value, AUDIT_KEYS)) {
    return fail("候选检查结果字段不完整。");
  }
  const candidate = expected.candidate;
  if (
    value.schema_version !== 1
    || value.ruleset_version !== 1
    || value.project_id !== expected.projectId
    || value.run_id !== expected.runId
    || value.planning_chapter_id !== expected.chapterId
    || value.candidate_id !== candidate.id
    || value.candidate_version !== candidate.version_no
    || value.candidate_checksum !== candidate.content_checksum
    || value.context_checksum !== expected.contextChecksum
    || !auditStatus(value.status)
  ) return fail("候选检查结果与当前候选或上下文不一致。");

  if (!record(value.integrity) || !exact(value.integrity, AUDIT_INTEGRITY_KEYS)) {
    return fail("候选完整性检查字段无效。");
  }
  const limitReached = candidate.content_size_bytes === 262_144;
  const integrityStatus = limitReached ? "review" : "pass";
  if (
    value.integrity.status !== integrityStatus
    || value.integrity.content_size_bytes !== candidate.content_size_bytes
    || value.integrity.word_count !== candidate.word_count
    || value.integrity.storage_limit_bytes !== 262_144
    || value.integrity.storage_limit_reached !== limitReached
  ) return fail("候选完整性检查与权威候选不一致。");

  if (!record(value.target_length) || !exact(value.target_length, AUDIT_TARGET_KEYS)) {
    return fail("目标字数检查字段无效。");
  }
  if (value.target_length.actual_word_count !== candidate.word_count) {
    return fail("目标字数检查与候选字数不一致。");
  }
  let targetStatus: "pass" | "review" | "not_applicable";
  let minimum: number | null = null;
  let maximum: number | null = null;
  if (expected.targetWordCount === null) {
    targetStatus = "not_applicable";
  } else {
    minimum = Math.max(1, Math.floor(expected.targetWordCount * 0.7));
    maximum = Math.ceil(expected.targetWordCount * 1.3);
    targetStatus = candidate.word_count >= minimum && candidate.word_count <= maximum
      ? "pass" : "review";
  }
  if (
    value.target_length.status !== targetStatus
    || value.target_length.target_word_count !== expected.targetWordCount
    || value.target_length.minimum_word_count !== minimum
    || value.target_length.maximum_word_count !== maximum
  ) return fail("目标字数检查边界与当前章节不一致。");

  if (!record(value.preparation) || !exact(value.preparation, AUDIT_PREPARATION_KEYS)
    || !Array.isArray(value.preparation.warnings)) {
    return fail("生成准备提示检查字段无效。");
  }
  if (
    value.preparation.status !== (expected.warnings.length ? "review" : "pass")
    || JSON.stringify(value.preparation.warnings) !== JSON.stringify(expected.warnings)
  ) return fail("候选检查没有使用冻结的生成准备提示。");

  if (!record(value.unrecognized_explicit_terms)
    || !exact(value.unrecognized_explicit_terms, AUDIT_TERMS_KEYS)
    || !Array.isArray(value.unrecognized_explicit_terms.items)
    || typeof value.unrecognized_explicit_terms.truncated !== "boolean"
    || value.unrecognized_explicit_terms.items.length > 20) {
    return fail("候选专名提示字段无效。");
  }
  const seenTerms = new Set<string>();
  const candidateCodePoints = Array.from(candidate.content);
  for (const item of value.unrecognized_explicit_terms.items) {
    const termLength = typeof item.term === "string" ? Array.from(item.term).length : 0;
    const excerptLength = typeof item.excerpt === "string" ? Array.from(item.excerpt).length : 0;
    const matchedToken = nonNegativeInteger(item.start_offset) && positiveInteger(item.end_offset)
      ? candidateCodePoints.slice(item.start_offset, item.end_offset).join("")
      : "";
    const matchedInner = matchedToken.startsWith("《") && matchedToken.endsWith("》")
      ? matchedToken.slice(1, -1).trim()
      : "";
    if (!record(item) || !exact(item, AUDIT_TERM_KEYS)
      || typeof item.term !== "string" || termLength < 1 || termLength > 80
      || typeof item.excerpt !== "string" || excerptLength < 1 || excerptLength > 208
      || !nonNegativeInteger(item.start_offset) || !positiveInteger(item.end_offset)
      || item.end_offset <= item.start_offset || item.end_offset > candidateCodePoints.length
      || matchedInner !== item.term
      || !item.excerpt.includes(matchedToken)
      || seenTerms.has(item.term)) {
      return fail("候选专名提示证据无效。");
    }
    seenTerms.add(item.term);
  }
  const termStatus = value.unrecognized_explicit_terms.items.length ? "review" : "pass";
  if (value.unrecognized_explicit_terms.status !== termStatus
    || (value.unrecognized_explicit_terms.truncated
      && value.unrecognized_explicit_terms.items.length !== 20)) {
    return fail("候选专名提示状态无效。");
  }

  if (!record(value.context_summary) || !exact(value.context_summary, AUDIT_CONTEXT_KEYS)
    || !Array.isArray(value.context_summary.elements)
    || value.context_summary.element_count !== expected.elements.length
    || value.context_summary.relation_count !== expected.relationCount
    || value.context_summary.warning_count !== expected.warnings.length
    || value.context_summary.foreshadow_actions_supported !== false
    || value.context_summary.foreshadow_action_count !== 0
    || value.context_summary.elements.length !== expected.elements.length) {
    return fail("候选检查的冻结设定摘要无效。");
  }
  for (const [index, item] of value.context_summary.elements.entries()) {
    const expectedItem = expected.elements[index];
    if (!record(item) || !exact(item, AUDIT_CONTEXT_ELEMENT_KEYS)
      || item.element_id !== expectedItem.elementId
      || item.type_key !== expectedItem.typeKey
      || item.type_display_name !== expectedItem.typeDisplayName
      || item.name !== expectedItem.name
      || item.version_no !== expectedItem.versionNo) {
      return fail("候选检查的冻结设定身份无效。");
    }
  }
  const overallReview = [integrityStatus, targetStatus, value.preparation.status, termStatus]
    .includes("review");
  if (value.status !== (overallReview ? "review" : "pass")) {
    return fail("候选检查总状态与分项状态不一致。");
  }
  return value as unknown as GenerationCandidateAuditResponse;
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

export async function readGenerationCandidateAudit(
  expected: ExpectedGenerationCandidateAudit,
  signal?: AbortSignal
): Promise<GenerationCandidateAuditResponse> {
  return parseGenerationCandidateAudit(
    await api.getGenerationCandidateAudit(
      expected.projectId,
      expected.candidate.id,
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
  | { status: "foreign"; workspace: "planning" | "foreshadow" | "technical_demo_execution" | "candidate_manual_edit" | "candidate_selection" }
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
    if (value.schema_version === 4 && value.workspace === "technical_demo_execution") {
      return { status: "foreign", workspace: "technical_demo_execution" };
    }
    if (value.schema_version === 5 && value.workspace === "candidate_manual_edit") {
      return { status: "foreign", workspace: "candidate_manual_edit" };
    }
    if (value.schema_version === 6 && value.workspace === "candidate_selection") {
      return { status: "foreign", workspace: "candidate_selection" };
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
