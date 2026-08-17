import { api } from "@/services/api";
import { pendingProjectOperationKey } from "@/services/pendingProjectOperations";
import type {
  GenerationCandidateManualEditInput,
  GenerationCandidateManualEditResponse,
  GenerationCandidateVersionDetail,
  GenerationCandidateVersionListItem,
  GenerationCandidateVersionListResponse,
} from "@/types/generation";

const INPUT_KEYS = [
  "operation_key", "parent_candidate_id", "expected_parent_version_no",
  "expected_parent_checksum", "expected_context_checksum", "content",
];
const ITEM_KEYS = [
  "id", "version_no", "origin_kind", "parent_candidate_id", "parent_version_no",
  "root_candidate_id", "root_origin_kind", "ai_invoked_for_this_version",
  "billing_effect_for_this_version", "usage_status_for_this_version", "title",
  "content_checksum", "content_size_bytes", "word_count", "created_by", "created_at",
];
const DETAIL_KEYS = [
  ...ITEM_KEYS, "project_id", "run_id", "planning_chapter_id", "content", "content_format",
];
const LIST_KEYS = [
  "schema_version", "project_id", "run_id", "planning_chapter_id", "items",
  "next_cursor", "has_more",
];
const RESPONSE_KEYS = [
  "schema_version", "replayed", "ai_invoked", "billing_effect", "usage_status", "candidate",
];
const PENDING_KEYS = [
  "schema_version", "workspace", "user_id", "project_id", "chapter_id", "run_id",
  "operation_key", "payload", "created_at",
];
const DRAFT_KEYS = [
  "schema_version", "workspace", "user_id", "project_id", "chapter_id", "run_id",
  "parent_candidate_id", "parent_version_no", "parent_checksum", "context_checksum",
  "content", "updated_at",
];
const DRAFT_KEY_PREFIX = "novel_candidate_manual_edit_draft_v1";

export interface CandidateVersionIdentity {
  userId: string;
  projectId: string;
  chapterId: string;
  runId: string;
  chapterTitle: string;
  candidateId?: string;
}

export interface PendingCandidateManualEdit {
  schema_version: 5;
  workspace: "candidate_manual_edit";
  user_id: string;
  project_id: string;
  chapter_id: string;
  run_id: string;
  operation_key: string;
  payload: GenerationCandidateManualEditInput;
  created_at: string;
}

export interface CandidateManualEditDraft {
  schema_version: 1;
  workspace: "candidate_manual_edit_draft";
  user_id: string;
  project_id: string;
  chapter_id: string;
  run_id: string;
  parent_candidate_id: string;
  parent_version_no: number;
  parent_checksum: string;
  context_checksum: string;
  content: string;
  updated_at: string;
}

export type CandidateManualEditDraftLoad =
  | { status: "missing" }
  | { status: "available"; draft: CandidateManualEditDraft }
  | { status: "foreign"; draft: CandidateManualEditDraft }
  | { status: "corrupt" }
  | { status: "unavailable" };

export type PendingCandidateManualEditLoad =
  | { status: "missing" }
  | { status: "available"; operation: PendingCandidateManualEdit }
  | {
      status: "foreign";
      workspace: "planning" | "foreshadow" | "generation_execution" | "technical_demo_execution" | "candidate_selection";
    }
  | { status: "corrupt" }
  | { status: "unavailable" };

export class CandidateVersionContractError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "CandidateVersionContractError";
  }
}

const record = (value: unknown): value is Record<string, unknown> =>
  !!value && typeof value === "object" && !Array.isArray(value);
const exact = (value: Record<string, unknown>, keys: string[]) => {
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  return actual.length === expected.length
    && actual.every((key, index) => key === expected[index]);
};
const stableId = (value: unknown): value is string =>
  typeof value === "string" && /^[A-Za-z0-9]{32}$/.test(value);
const operationKey = (value: unknown): value is string =>
  typeof value === "string" && value.length >= 8 && value.length <= 128
  && /^[A-Za-z0-9._:-]+$/.test(value);
const checksum = (value: unknown): value is string =>
  typeof value === "string" && /^[0-9a-f]{64}$/.test(value);
const positive = (value: unknown, max?: number): value is number =>
  typeof value === "number" && Number.isInteger(value) && value >= 1
  && (max === undefined || value <= max);
const timestamp = (value: unknown): value is string =>
  typeof value === "string" && Number.isFinite(Date.parse(value));
const wordCount = (content: string) =>
  content.match(/[A-Za-z0-9]+|[\u3400-\u4dbf\u4e00-\u9fff]/g)?.length ?? 0;
const fail = (message: string): never => {
  throw new CandidateVersionContractError(message);
};

function candidateDraftKey(userId: string, projectId: string): string {
  return `${DRAFT_KEY_PREFIX}:${userId}:${projectId}`;
}

export function isCandidateManualEditDraft(value: unknown): value is CandidateManualEditDraft {
  return record(value) && exact(value, DRAFT_KEYS)
    && value.schema_version === 1 && value.workspace === "candidate_manual_edit_draft"
    && stableId(value.user_id) && stableId(value.project_id) && stableId(value.chapter_id)
    && stableId(value.run_id) && stableId(value.parent_candidate_id)
    && positive(value.parent_version_no) && checksum(value.parent_checksum)
    && checksum(value.context_checksum) && typeof value.content === "string"
    && timestamp(value.updated_at);
}

export function saveCandidateManualEditDraft(draft: CandidateManualEditDraft): boolean {
  if (!isCandidateManualEditDraft(draft)) return false;
  try {
    const key = candidateDraftKey(draft.user_id, draft.project_id);
    const existingRaw = sessionStorage.getItem(key);
    if (existingRaw !== null) {
      const existing: unknown = JSON.parse(existingRaw);
      if (!isCandidateManualEditDraft(existing)
        || existing.user_id !== draft.user_id
        || existing.project_id !== draft.project_id
        || existing.chapter_id !== draft.chapter_id
        || existing.run_id !== draft.run_id
        || existing.parent_candidate_id !== draft.parent_candidate_id
        || existing.parent_version_no !== draft.parent_version_no
        || existing.parent_checksum !== draft.parent_checksum
        || existing.context_checksum !== draft.context_checksum) return false;
    }
    sessionStorage.setItem(key, JSON.stringify(draft));
    return true;
  } catch {
    return false;
  }
}

export function loadCandidateManualEditDraft(
  expected: CandidateVersionIdentity
): CandidateManualEditDraftLoad {
  let raw: string | null;
  try {
    raw = sessionStorage.getItem(candidateDraftKey(expected.userId, expected.projectId));
  } catch {
    return { status: "unavailable" };
  }
  if (!raw) return { status: "missing" };
  try {
    const value: unknown = JSON.parse(raw);
    if (!isCandidateManualEditDraft(value)) return { status: "corrupt" };
    if (value.user_id !== expected.userId || value.project_id !== expected.projectId
      || value.chapter_id !== expected.chapterId || value.run_id !== expected.runId) {
      return { status: "foreign", draft: value };
    }
    return { status: "available", draft: value };
  } catch {
    return { status: "corrupt" };
  }
}

export function clearCandidateManualEditDraft(
  expected: CandidateManualEditDraft
): boolean {
  try {
    const key = candidateDraftKey(expected.user_id, expected.project_id);
    const raw = sessionStorage.getItem(key);
    if (!raw) return true;
    const value: unknown = JSON.parse(raw);
    if (!isCandidateManualEditDraft(value)
      || value.user_id !== expected.user_id || value.project_id !== expected.project_id
      || value.chapter_id !== expected.chapter_id || value.run_id !== expected.run_id
      || value.parent_candidate_id !== expected.parent_candidate_id
      || value.parent_version_no !== expected.parent_version_no
      || value.parent_checksum !== expected.parent_checksum
      || value.context_checksum !== expected.context_checksum
      || value.content !== expected.content) return false;
    sessionStorage.removeItem(key);
    return true;
  } catch {
    return false;
  }
}

export function replaceCandidateManualEditDraft(
  expected: CandidateManualEditDraft,
  replacement: CandidateManualEditDraft
): boolean {
  if (!isCandidateManualEditDraft(expected) || !isCandidateManualEditDraft(replacement)
    || expected.user_id !== replacement.user_id
    || expected.project_id !== replacement.project_id) return false;
  try {
    const key = candidateDraftKey(expected.user_id, expected.project_id);
    const raw = sessionStorage.getItem(key);
    if (!raw) return false;
    const value: unknown = JSON.parse(raw);
    if (!isCandidateManualEditDraft(value)
      || value.user_id !== expected.user_id || value.project_id !== expected.project_id
      || value.chapter_id !== expected.chapter_id || value.run_id !== expected.run_id
      || value.parent_candidate_id !== expected.parent_candidate_id
      || value.parent_version_no !== expected.parent_version_no
      || value.parent_checksum !== expected.parent_checksum
      || value.context_checksum !== expected.context_checksum
      || value.content !== expected.content) return false;
    sessionStorage.setItem(key, JSON.stringify(replacement));
    return true;
  } catch {
    return false;
  }
}

export function clearCorruptCandidateManualEditDraft(
  userId: string,
  projectId: string
): boolean {
  try {
    const key = candidateDraftKey(userId, projectId);
    const raw = sessionStorage.getItem(key);
    if (!raw) return true;
    let value: unknown;
    try {
      value = JSON.parse(raw);
    } catch {
      sessionStorage.removeItem(key);
      return true;
    }
    if (isCandidateManualEditDraft(value)) return false;
    sessionStorage.removeItem(key);
    return true;
  } catch {
    return false;
  }
}

async function sha256(bytes: Uint8Array): Promise<string> {
  const digest = await crypto.subtle.digest(
    "SHA-256",
    bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer
  );
  return Array.from(new Uint8Array(digest), (byte) =>
    byte.toString(16).padStart(2, "0")
  ).join("");
}

export function createCandidateManualEditOperationKey(): string {
  if (!globalThis.crypto?.randomUUID) {
    return fail("当前浏览器无法生成安全另存编号。");
  }
  return `candidate:manual-edit:${globalThis.crypto.randomUUID()}`;
}

export function parseCandidateManualEditInput(
  value: unknown,
  parentContent?: string
): GenerationCandidateManualEditInput {
  if (!record(value) || !exact(value, INPUT_KEYS)
    || !operationKey(value.operation_key) || !stableId(value.parent_candidate_id)
    || !positive(value.expected_parent_version_no) || !checksum(value.expected_parent_checksum)
    || !checksum(value.expected_context_checksum) || typeof value.content !== "string") {
    return fail("候选手工另存请求无效。");
  }
  const bytes = new TextEncoder().encode(value.content);
  if (!value.content.trim() || wordCount(value.content) < 1 || bytes.byteLength > 262_144) {
    return fail("候选正文为空或超出 262144 字节安全上限。");
  }
  if (parentContent !== undefined && value.content === parentContent) {
    return fail("候选正文没有修改，不会创建新版本。");
  }
  return value as unknown as GenerationCandidateManualEditInput;
}

export function parseCandidateVersionListItem(
  value: unknown,
  expected: CandidateVersionIdentity
): GenerationCandidateVersionListItem {
  if (!record(value) || !exact(value, ITEM_KEYS) || !stableId(value.id)
    || !positive(value.version_no) || !stableId(value.root_candidate_id)
    || value.title !== expected.chapterTitle || !checksum(value.content_checksum)
    || !positive(value.content_size_bytes, 262_144) || !positive(value.word_count)
    || value.created_by !== expected.userId || !timestamp(value.created_at)) {
    return fail("候选版本列表项的身份或内容元数据无效。");
  }
  if (value.origin_kind === "manual_edit") {
    if (!stableId(value.parent_candidate_id) || !positive(value.parent_version_no)
      || value.parent_candidate_id === value.id || value.root_candidate_id === value.id
      || value.parent_version_no >= value.version_no || value.ai_invoked_for_this_version !== false
      || value.billing_effect_for_this_version !== "none"
      || value.usage_status_for_this_version !== "not_applicable") {
      return fail("手工另存候选的版本血缘或本次费用语义无效。");
    }
  } else if (value.origin_kind === "generated") {
    if (value.parent_candidate_id !== null || value.parent_version_no !== null
      || value.root_candidate_id !== value.id || value.root_origin_kind !== "generated"
      || value.ai_invoked_for_this_version !== true
      || value.billing_effect_for_this_version !== "possible"
      || !["reported", "unavailable"].includes(String(value.usage_status_for_this_version))) {
      return fail("模型生成根候选的来源无效。");
    }
  } else if (value.origin_kind === "technical_demo") {
    if (value.parent_candidate_id !== null || value.parent_version_no !== null
      || value.root_candidate_id !== value.id || value.root_origin_kind !== "technical_demo"
      || value.ai_invoked_for_this_version !== false
      || value.billing_effect_for_this_version !== "none"
      || value.usage_status_for_this_version !== "not_applicable") {
      return fail("技术模拟根候选的来源无效。");
    }
  } else {
    return fail("候选版本来源类型无效。");
  }
  if (value.origin_kind === "manual_edit"
    && value.root_origin_kind !== "generated"
    && value.root_origin_kind !== "technical_demo") {
    return fail("手工另存候选的根来源无效。");
  }
  return value as unknown as GenerationCandidateVersionListItem;
}

export function parseCandidateVersionList(
  value: unknown,
  expected: CandidateVersionIdentity
): GenerationCandidateVersionListResponse {
  if (!record(value) || !exact(value, LIST_KEYS) || value.schema_version !== 1
    || value.project_id !== expected.projectId || value.run_id !== expected.runId
    || value.planning_chapter_id !== expected.chapterId || !Array.isArray(value.items)
    || value.items.length > 50 || typeof value.has_more !== "boolean") {
    return fail("候选版本列表响应无效。");
  }
  const items = value.items.map((item) => parseCandidateVersionListItem(item, expected));
  const versions = items.map((item) => item.version_no);
  if (versions.some((version, index) => index > 0 && versions[index - 1] <= version)
    || new Set(versions).size !== versions.length) {
    return fail("候选版本列表顺序无效。");
  }
  if (value.has_more) {
    if (typeof value.next_cursor !== "string" || !/^\d+$/.test(value.next_cursor)
      || items.length === 0 || Number(value.next_cursor) !== items.at(-1)?.version_no) {
      return fail("候选版本列表游标无效。");
    }
  } else if (value.next_cursor !== null) {
    return fail("候选版本列表游标状态无效。");
  }
  return { ...value, items } as GenerationCandidateVersionListResponse;
}

export async function parseCandidateVersionDetail(
  value: unknown,
  expected: CandidateVersionIdentity
): Promise<GenerationCandidateVersionDetail> {
  if (!record(value) || !exact(value, DETAIL_KEYS)
    || value.project_id !== expected.projectId || value.run_id !== expected.runId
    || value.planning_chapter_id !== expected.chapterId
    || (expected.candidateId !== undefined && value.id !== expected.candidateId)
    || typeof value.content !== "string" || !value.content.trim()
    || value.content_format !== "plain_text") {
    return fail("候选版本详情身份或正文无效。");
  }
  const item = parseCandidateVersionListItem(
    Object.fromEntries(ITEM_KEYS.map((key) => [key, value[key]])),
    expected
  );
  const bytes = new TextEncoder().encode(value.content);
  if (bytes.byteLength !== item.content_size_bytes
    || wordCount(value.content) !== item.word_count
    || await sha256(bytes) !== item.content_checksum) {
    return fail("候选版本正文完整性校验失败。");
  }
  return value as unknown as GenerationCandidateVersionDetail;
}

export async function parseCandidateManualEditResponse(
  value: unknown,
  expected: CandidateVersionIdentity,
  input: GenerationCandidateManualEditInput
): Promise<GenerationCandidateManualEditResponse> {
  if (!record(value) || !exact(value, RESPONSE_KEYS) || value.schema_version !== 1
    || typeof value.replayed !== "boolean" || value.ai_invoked !== false
    || value.billing_effect !== "none" || value.usage_status !== "not_applicable") {
    return fail("手工另存响应语义无效。");
  }
  const candidate = await parseCandidateVersionDetail(value.candidate, expected);
  if (candidate.origin_kind !== "manual_edit"
    || candidate.parent_candidate_id !== input.parent_candidate_id
    || candidate.parent_version_no !== input.expected_parent_version_no
    || candidate.content !== input.content) {
    return fail("手工另存响应与已确认的父版本或正文不一致。");
  }
  return { ...value, candidate } as GenerationCandidateManualEditResponse;
}

export function candidateMatchesManualEditParent(
  child: GenerationCandidateVersionDetail,
  parent: GenerationCandidateVersionDetail,
  operation: PendingCandidateManualEdit,
  identity: CandidateVersionIdentity
): boolean {
  return child.origin_kind === "manual_edit"
    && child.parent_candidate_id === parent.id
    && child.parent_version_no === parent.version_no
    && child.root_candidate_id === parent.root_candidate_id
    && child.root_origin_kind === parent.root_origin_kind
    && child.title === parent.title
    && child.created_by === identity.userId
    && child.project_id === identity.projectId
    && child.run_id === identity.runId
    && child.planning_chapter_id === identity.chapterId
    && operation.payload.parent_candidate_id === parent.id
    && operation.payload.expected_parent_version_no === parent.version_no
    && operation.payload.expected_parent_checksum === parent.content_checksum;
}

export function isPendingCandidateManualEdit(
  value: unknown,
  userId?: string,
  projectId?: string
): value is PendingCandidateManualEdit {
  if (!record(value) || !exact(value, PENDING_KEYS) || value.schema_version !== 5
    || value.workspace !== "candidate_manual_edit" || !stableId(value.user_id)
    || !stableId(value.project_id) || !stableId(value.chapter_id) || !stableId(value.run_id)
    || !operationKey(value.operation_key) || !timestamp(value.created_at)
    || (userId !== undefined && value.user_id !== userId)
    || (projectId !== undefined && value.project_id !== projectId)) return false;
  try {
    return parseCandidateManualEditInput(value.payload).operation_key === value.operation_key;
  } catch {
    return false;
  }
}

export function savePendingCandidateManualEdit(operation: PendingCandidateManualEdit): boolean {
  if (!isPendingCandidateManualEdit(operation, operation.user_id, operation.project_id)) return false;
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

export function loadPendingCandidateManualEdit(
  userId: string,
  projectId: string
): PendingCandidateManualEditLoad {
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
    if (value.schema_version === 3 && value.workspace === "generation_execution") {
      return { status: "foreign", workspace: "generation_execution" };
    }
    if (value.schema_version === 4 && value.workspace === "technical_demo_execution") {
      return { status: "foreign", workspace: "technical_demo_execution" };
    }
    if (value.schema_version === 6 && value.workspace === "candidate_selection") {
      return { status: "foreign", workspace: "candidate_selection" };
    }
    return isPendingCandidateManualEdit(value, userId, projectId)
      ? { status: "available", operation: value }
      : { status: "corrupt" };
  } catch {
    return { status: "corrupt" };
  }
}

export function clearPendingCandidateManualEdit(
  expected: PendingCandidateManualEdit
): boolean {
  try {
    const key = pendingProjectOperationKey(expected.user_id, expected.project_id);
    const raw = sessionStorage.getItem(key);
    if (!raw) return true;
    const value: unknown = JSON.parse(raw);
    if (!isPendingCandidateManualEdit(value, expected.user_id, expected.project_id)
      || JSON.stringify(value) !== JSON.stringify(expected)) return false;
    sessionStorage.removeItem(key);
    return true;
  } catch {
    return false;
  }
}

export async function listCandidateVersions(
  expected: CandidateVersionIdentity,
  options?: { limit?: number; beforeVersionNo?: number; signal?: AbortSignal }
) {
  return parseCandidateVersionList(
    await api.listGenerationCandidateVersions(expected.projectId, expected.runId, options),
    expected
  );
}

export async function readCandidateVersion(
  expected: CandidateVersionIdentity,
  signal?: AbortSignal
) {
  if (!expected.candidateId) return fail("缺少要读取的候选版本。");
  return parseCandidateVersionDetail(
    await api.getGenerationCandidateVersion(
      expected.projectId, expected.runId, expected.candidateId, signal
    ),
    expected
  );
}

export async function requestCandidateManualEdit(
  expected: CandidateVersionIdentity,
  input: GenerationCandidateManualEditInput
) {
  const payload = parseCandidateManualEditInput(input);
  return parseCandidateManualEditResponse(
    await api.createGenerationCandidateManualEdit(expected.projectId, expected.runId, payload),
    expected,
    payload
  );
}

export async function readCandidateManualEditByKey(
  expected: CandidateVersionIdentity,
  operation: PendingCandidateManualEdit,
  signal?: AbortSignal
) {
  return parseCandidateManualEditResponse(
    await api.getGenerationCandidateManualEditByKey(
      expected.projectId, expected.runId, operation.operation_key, signal
    ),
    expected,
    operation.payload
  );
}
