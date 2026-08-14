import { api } from "@/services/api";
import { parseCandidateVersionListItem } from "@/services/candidateVersionOperations";
import { pendingProjectOperationKey } from "@/services/pendingProjectOperations";
import type {
  GenerationCandidateSelectionCurrentResponse,
  GenerationCandidateSelectionInput,
  GenerationCandidateSelectionOperationResponse,
  GenerationCandidateSelectionSnapshot,
  GenerationCandidateVersionListItem,
} from "@/types/generation";

const INPUT_KEYS = [
  "operation_key", "expected_selection_version", "target_run_id",
  "target_candidate_id", "expected_candidate_version_no",
  "expected_candidate_checksum", "expected_context_checksum",
];
const SNAPSHOT_KEYS = [
  "state", "selection_version", "run_id", "context_checksum", "candidate",
];
const CURRENT_KEYS = [
  "schema_version", "project_id", "planning_chapter_id", "state",
  "selection_version", "run_id", "context_checksum", "candidate",
  "selected_at", "changed_by",
];
const RECEIPT_KEYS = [
  "schema_version", "project_id", "planning_chapter_id", "operation_key",
  "replayed", "changed", "ai_invoked", "billing_effect", "usage_status",
  "previous", "result", "selected_at", "changed_by",
];
const PENDING_KEYS = [
  "schema_version", "workspace", "user_id", "project_id", "chapter_id",
  "run_id", "operation_key", "payload", "expected_previous", "expected_target",
  "created_at",
];

export interface CandidateSelectionIdentity {
  userId: string;
  projectId: string;
  chapterId: string;
}

export interface PendingCandidateSelection {
  schema_version: 6;
  workspace: "candidate_selection";
  user_id: string;
  project_id: string;
  chapter_id: string;
  run_id: string;
  operation_key: string;
  payload: GenerationCandidateSelectionInput;
  expected_previous: GenerationCandidateSelectionSnapshot;
  expected_target: GenerationCandidateVersionListItem;
  created_at: string;
}

export type PendingCandidateSelectionLoad =
  | { status: "missing" }
  | { status: "available"; operation: PendingCandidateSelection }
  | {
      status: "foreign";
      workspace:
        | "planning"
        | "foreshadow"
        | "generation_execution"
        | "technical_demo_execution"
        | "candidate_manual_edit";
    }
  | { status: "corrupt" }
  | { status: "unavailable" };

export class CandidateSelectionContractError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "CandidateSelectionContractError";
  }
}

const fail = (message: string): never => {
  throw new CandidateSelectionContractError(message);
};
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
const nonNegative = (value: unknown): value is number =>
  typeof value === "number" && Number.isInteger(value) && value >= 0;
const positive = (value: unknown): value is number =>
  typeof value === "number" && Number.isInteger(value) && value >= 1;
const timestamp = (value: unknown): value is string =>
  typeof value === "string" && Number.isFinite(Date.parse(value));
const validIdentity = (value: CandidateSelectionIdentity): boolean =>
  stableId(value.userId) && stableId(value.projectId) && stableId(value.chapterId);

function sameValue(left: unknown, right: unknown): boolean {
  if (Object.is(left, right)) return true;
  if (Array.isArray(left) || Array.isArray(right)) {
    return Array.isArray(left) && Array.isArray(right)
      && left.length === right.length
      && left.every((value, index) => sameValue(value, right[index]));
  }
  if (!record(left) || !record(right)) return false;
  const leftKeys = Object.keys(left).sort();
  const rightKeys = Object.keys(right).sort();
  return leftKeys.length === rightKeys.length
    && leftKeys.every((key, index) => key === rightKeys[index]
      && sameValue(left[key], right[key]));
}

function parseSelectionCandidate(
  value: unknown,
  expected: CandidateSelectionIdentity,
  runId: string
): GenerationCandidateVersionListItem {
  if (!record(value) || typeof value.title !== "string"
    || value.title.length < 1 || value.title.length > 200) {
    return fail("候选采用快照的标题无效。");
  }
  return parseCandidateVersionListItem(value, {
    userId: expected.userId,
    projectId: expected.projectId,
    chapterId: expected.chapterId,
    runId,
    chapterTitle: value.title,
  });
}

export function parseCandidateSelectionInput(
  value: unknown
): GenerationCandidateSelectionInput {
  if (!record(value) || !exact(value, INPUT_KEYS)
    || !operationKey(value.operation_key)
    || !nonNegative(value.expected_selection_version)
    || !stableId(value.target_run_id) || !stableId(value.target_candidate_id)
    || !positive(value.expected_candidate_version_no)
    || !checksum(value.expected_candidate_checksum)
    || !checksum(value.expected_context_checksum)) {
    return fail("候选采用请求无效。");
  }
  return value as unknown as GenerationCandidateSelectionInput;
}

function parseSnapshot(
  value: unknown,
  expected: CandidateSelectionIdentity
): GenerationCandidateSelectionSnapshot {
  if (!record(value) || !exact(value, SNAPSHOT_KEYS)) {
    return fail("候选采用快照字段不完整。");
  }
  if (value.state === "none") {
    if (value.selection_version !== 0 || value.run_id !== null
      || value.context_checksum !== null || value.candidate !== null) {
      return fail("未采用快照形态无效。");
    }
    return value as unknown as GenerationCandidateSelectionSnapshot;
  }
  if (value.state !== "selected" || !positive(value.selection_version)
    || !stableId(value.run_id) || !checksum(value.context_checksum)) {
    return fail("已采用快照形态无效。");
  }
  const candidate = parseSelectionCandidate(value.candidate, expected, value.run_id);
  return { ...value, candidate } as GenerationCandidateSelectionSnapshot;
}

export function parseCandidateSelectionCurrent(
  value: unknown,
  expected: CandidateSelectionIdentity
): GenerationCandidateSelectionCurrentResponse {
  if (!validIdentity(expected) || !record(value) || !exact(value, CURRENT_KEYS)
    || value.schema_version !== 1
    || value.project_id !== expected.projectId
    || value.planning_chapter_id !== expected.chapterId) {
    return fail("章节采用状态响应无效。");
  }
  const snapshot = parseSnapshot({
    state: value.state,
    selection_version: value.selection_version,
    run_id: value.run_id,
    context_checksum: value.context_checksum,
    candidate: value.candidate,
  }, expected);
  if (snapshot.state === "none") {
    if (value.selected_at !== null || value.changed_by !== null) {
      return fail("未采用状态包含非法操作信息。");
    }
  } else if (!timestamp(value.selected_at) || value.changed_by !== expected.userId) {
    return fail("已采用状态的操作信息无效。");
  }
  return { ...value, candidate: snapshot.candidate } as
    GenerationCandidateSelectionCurrentResponse;
}

export function candidateSelectionSnapshotFromCurrent(
  current: GenerationCandidateSelectionCurrentResponse
): GenerationCandidateSelectionSnapshot {
  return {
    state: current.state,
    selection_version: current.selection_version,
    run_id: current.run_id,
    context_checksum: current.context_checksum,
    candidate: current.candidate,
  } as GenerationCandidateSelectionSnapshot;
}

export function parseCandidateSelectionReceipt(
  value: unknown,
  expected: CandidateSelectionIdentity,
  operation: PendingCandidateSelection
): GenerationCandidateSelectionOperationResponse {
  if (!validIdentity(expected)
    || !isPendingCandidateSelection(
      operation, expected.userId, expected.projectId
    )
    || operation.chapter_id !== expected.chapterId
    || !record(value) || !exact(value, RECEIPT_KEYS) || value.schema_version !== 1
    || value.project_id !== expected.projectId
    || value.planning_chapter_id !== expected.chapterId
    || value.operation_key !== operation.operation_key
    || typeof value.replayed !== "boolean" || value.changed !== true
    || value.ai_invoked !== false || value.billing_effect !== "none"
    || value.usage_status !== "not_applicable"
    || !timestamp(value.selected_at) || value.changed_by !== expected.userId) {
    return fail("候选采用回执的身份或诚信字段无效。");
  }
  const previous = parseSnapshot(value.previous, expected);
  const result = parseSnapshot(value.result, expected);
  const payload = parseCandidateSelectionInput(operation.payload);
  if (!sameValue(previous, operation.expected_previous)
    || previous.selection_version !== payload.expected_selection_version
    || result.state !== "selected"
    || result.selection_version !== previous.selection_version + 1
    || result.run_id !== operation.run_id
    || result.run_id !== payload.target_run_id
    || result.context_checksum !== payload.expected_context_checksum
    || !sameValue(result.candidate, operation.expected_target)
    || result.candidate.id !== payload.target_candidate_id
    || result.candidate.version_no !== payload.expected_candidate_version_no
    || result.candidate.content_checksum !== payload.expected_candidate_checksum) {
    return fail("候选采用回执与冻结请求或前置状态不一致。");
  }
  return { ...value, previous, result } as
    GenerationCandidateSelectionOperationResponse;
}

export function createCandidateSelectionOperationKey(): string {
  if (!globalThis.crypto?.randomUUID) {
    return fail("当前浏览器无法生成安全采用编号。");
  }
  return `candidate:select:${globalThis.crypto.randomUUID()}`;
}

export function isPendingCandidateSelection(
  value: unknown,
  userId?: string,
  projectId?: string
): value is PendingCandidateSelection {
  if (!record(value) || !exact(value, PENDING_KEYS) || value.schema_version !== 6
    || value.workspace !== "candidate_selection" || !stableId(value.user_id)
    || !stableId(value.project_id) || !stableId(value.chapter_id)
    || !stableId(value.run_id) || !operationKey(value.operation_key)
    || !timestamp(value.created_at)
    || (userId !== undefined && value.user_id !== userId)
    || (projectId !== undefined && value.project_id !== projectId)) return false;
  try {
    const payload = parseCandidateSelectionInput(value.payload);
    const identity = {
      userId: value.user_id,
      projectId: value.project_id,
      chapterId: value.chapter_id,
    };
    const expected = parseSnapshot(value.expected_previous, identity);
    const target = parseSelectionCandidate(value.expected_target, identity, value.run_id);
    return payload.operation_key === value.operation_key
      && payload.target_run_id === value.run_id
      && payload.expected_selection_version === expected.selection_version
      && payload.target_candidate_id === target.id
      && payload.expected_candidate_version_no === target.version_no
      && payload.expected_candidate_checksum === target.content_checksum;
  } catch {
    return false;
  }
}

export function savePendingCandidateSelection(
  operation: PendingCandidateSelection
): boolean {
  if (!isPendingCandidateSelection(
    operation, operation.user_id, operation.project_id
  )) return false;
  try {
    const key = pendingProjectOperationKey(operation.user_id, operation.project_id);
    const serialized = JSON.stringify(operation);
    const existing = sessionStorage.getItem(key);
    if (existing !== null) {
      const value: unknown = JSON.parse(existing);
      return isPendingCandidateSelection(
        value, operation.user_id, operation.project_id
      ) && sameValue(value, operation);
    }
    sessionStorage.setItem(key, serialized);
    return true;
  } catch {
    return false;
  }
}

export function loadPendingCandidateSelection(
  userId: string,
  projectId: string
): PendingCandidateSelectionLoad {
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
    if (value.schema_version === 5 && value.workspace === "candidate_manual_edit") {
      return { status: "foreign", workspace: "candidate_manual_edit" };
    }
    return isPendingCandidateSelection(value, userId, projectId)
      ? { status: "available", operation: value }
      : { status: "corrupt" };
  } catch {
    return { status: "corrupt" };
  }
}

export function clearPendingCandidateSelection(
  expected: PendingCandidateSelection
): boolean {
  try {
    const key = pendingProjectOperationKey(expected.user_id, expected.project_id);
    const raw = sessionStorage.getItem(key);
    if (!raw) return true;
    const value: unknown = JSON.parse(raw);
    if (!isPendingCandidateSelection(
      value, expected.user_id, expected.project_id
    ) || !sameValue(value, expected)) return false;
    sessionStorage.removeItem(key);
    return true;
  } catch {
    return false;
  }
}

export async function readCandidateSelectionCurrent(
  expected: CandidateSelectionIdentity,
  signal?: AbortSignal
) {
  return parseCandidateSelectionCurrent(
    await api.getGenerationCandidateSelection(
      expected.projectId, expected.chapterId, signal
    ),
    expected
  );
}

export async function requestCandidateSelection(
  expected: CandidateSelectionIdentity,
  operation: PendingCandidateSelection,
  signal?: AbortSignal
) {
  const payload = parseCandidateSelectionInput(operation.payload);
  return parseCandidateSelectionReceipt(
    await api.selectGenerationCandidate(
      expected.projectId, expected.chapterId, payload, signal
    ),
    expected,
    operation
  );
}

export async function readCandidateSelectionByKey(
  expected: CandidateSelectionIdentity,
  operation: PendingCandidateSelection,
  signal?: AbortSignal
) {
  return parseCandidateSelectionReceipt(
    await api.getGenerationCandidateSelectionByKey(
      expected.projectId, expected.chapterId, operation.operation_key, signal
    ),
    expected,
    operation
  );
}
