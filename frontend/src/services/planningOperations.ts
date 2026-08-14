import { pendingProjectOperationKey } from "@/services/pendingProjectOperations";

export type PlanningOperationAction =
  | "part_create"
  | "part_update"
  | "part_archive"
  | "part_restore"
  | "chapter_create"
  | "chapter_update"
  | "chapter_archive"
  | "chapter_restore"
  | "structure_reorder"
  | "assignment_create"
  | "assignment_remove"
  | "assignment_restore"
  | "generation_prepare";

export interface PendingPlanningOperation<T extends object = Record<string, unknown>> {
  schema_version: 1;
  user_id: string;
  project_id: string;
  operation_key: string;
  action: PlanningOperationAction;
  target_id: string | null;
  payload: T;
  created_at: string;
}

export type PendingPlanningOperationLoad =
  | { status: "missing" }
  | { status: "available"; operation: PendingPlanningOperation }
  | { status: "foreign"; workspace: "foreshadow" | "generation_execution" | "technical_demo_execution" | "candidate_manual_edit" }
  | { status: "corrupt" }
  | { status: "unavailable" };

export function createPlanningOperationKey(action: string): string {
  if (!globalThis.crypto?.randomUUID) {
    throw new Error("当前浏览器无法生成安全操作编号，已停止写入。");
  }
  return `planning:${action}:${globalThis.crypto.randomUUID()}`;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value);
}

function isString(value: unknown): value is string {
  return typeof value === "string" && value.length > 0;
}

function isPositiveInteger(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= 1;
}

function isOperationKey(value: unknown): value is string {
  return isString(value) && value.length >= 8 && value.length <= 128 && /^[A-Za-z0-9._:-]+$/.test(value);
}

function isStableId(value: unknown): value is string {
  return typeof value === "string" && value.length === 32;
}

function hasExactKeys(value: Record<string, unknown>, keys: string[]): boolean {
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  return actual.length === expected.length && actual.every((key, index) => key === expected[index]);
}

function hasOnlyKeys(value: Record<string, unknown>, keys: string[]): boolean {
  const allowed = new Set(keys);
  return Object.keys(value).every((key) => allowed.has(key));
}

function isTargetWordCount(value: unknown): value is number {
  return isPositiveInteger(value) && value >= 500 && value <= 10_000;
}

function isScope(value: unknown): boolean {
  return value === "novel" || value === "part" || value === "chapter";
}

function isAction(value: unknown): value is PlanningOperationAction {
  return [
    "part_create", "part_update", "part_archive", "part_restore",
    "chapter_create", "chapter_update", "chapter_archive", "chapter_restore",
    "structure_reorder", "assignment_create", "assignment_remove", "assignment_restore",
    "generation_prepare",
  ].includes(String(value));
}

function hasStructureVersion(payload: Record<string, unknown>): boolean {
  return isPositiveInteger(payload.expected_structure_version);
}

function validPayload(action: PlanningOperationAction, payload: Record<string, unknown>): boolean {
  if (!isOperationKey(payload.operation_key)) return false;
  if (action === "part_create") {
    return hasExactKeys(payload, ["operation_key", "expected_structure_version", "title", "description"])
      && hasStructureVersion(payload) && isString(payload.title) && payload.title.length <= 200
      && typeof payload.description === "string" && payload.description.length <= 10_000;
  }
  if (action === "part_update") {
    return hasExactKeys(payload, ["operation_key", "expected_structure_version", "expected_lock_version", "title", "description"])
      && hasStructureVersion(payload) && isPositiveInteger(payload.expected_lock_version)
      && isString(payload.title) && payload.title.length <= 200
      && typeof payload.description === "string" && payload.description.length <= 10_000;
  }
  if (["part_archive", "part_restore", "chapter_archive", "chapter_restore"].includes(action)) {
    return hasExactKeys(payload, ["operation_key", "expected_structure_version"])
      && hasStructureVersion(payload);
  }
  if (action === "chapter_create") {
    return hasExactKeys(payload, ["operation_key", "expected_structure_version", "title", "summary", "target_word_count"])
      && hasStructureVersion(payload) && isString(payload.title) && payload.title.length <= 200
      && typeof payload.summary === "string" && payload.summary.length <= 20_000
      && (payload.target_word_count === null || isTargetWordCount(payload.target_word_count));
  }
  if (action === "chapter_update") {
    const allowed = ["operation_key", "expected_structure_version", "expected_lock_version", "title", "summary", "target_word_count", "clear_target_word_count"];
    if (!hasOnlyKeys(payload, allowed) || !hasStructureVersion(payload) || !isPositiveInteger(payload.expected_lock_version)) return false;
    const hasUpdate = payload.title !== undefined || payload.summary !== undefined
      || payload.target_word_count !== undefined || payload.clear_target_word_count === true;
    if (!hasUpdate || (payload.target_word_count !== undefined && payload.clear_target_word_count === true)) return false;
    return (payload.title === undefined || (isString(payload.title) && payload.title.length <= 200))
      && (payload.summary === undefined || (typeof payload.summary === "string" && payload.summary.length <= 20_000))
      && (payload.target_word_count === undefined || isTargetWordCount(payload.target_word_count))
      && (payload.clear_target_word_count === undefined || typeof payload.clear_target_word_count === "boolean");
  }
  if (action === "structure_reorder") {
    return hasExactKeys(payload, ["operation_key", "expected_structure_version", "parts"])
      && hasStructureVersion(payload) && Array.isArray(payload.parts)
      && payload.parts.every((item) => isRecord(item) && hasExactKeys(item, ["part_id", "chapter_ids"]) && isString(item.part_id)
        && Array.isArray(item.chapter_ids) && item.chapter_ids.every(isString));
  }
  if (action === "assignment_create") {
    return hasExactKeys(payload, ["operation_key", "expected_assignment_version", "element_id", "expected_element_content_version", "scope_type", "scope_target_id"])
      && isPositiveInteger(payload.expected_assignment_version)
      && isString(payload.element_id)
      && isPositiveInteger(payload.expected_element_content_version)
      && isScope(payload.scope_type)
      && isString(payload.scope_target_id);
  }
  if (action === "generation_prepare") {
    return hasExactKeys(payload, [
      "operation_key",
      "expected_structure_version",
      "expected_assignment_version",
      "expected_chapter_lock_version",
    ])
      && isPositiveInteger(payload.expected_structure_version)
      && isPositiveInteger(payload.expected_assignment_version)
      && isPositiveInteger(payload.expected_chapter_lock_version);
  }
  return hasExactKeys(payload, ["operation_key", "expected_assignment_version", "expected_lock_version", "scope_type", "scope_target_id"])
    && isPositiveInteger(payload.expected_assignment_version)
    && isPositiveInteger(payload.expected_lock_version)
    && isScope(payload.scope_type)
    && isString(payload.scope_target_id);
}

function validTarget(action: PlanningOperationAction, target: unknown, payload: Record<string, unknown>): boolean {
  if (action === "part_create") return target === null;
  if (action === "structure_reorder") return target === null || isString(target);
  if (action === "assignment_create") return isString(target) && target === payload.element_id;
  if (action === "generation_prepare") return isStableId(target);
  return isString(target);
}

export function savePendingPlanningOperation<T extends object>(
  operation: PendingPlanningOperation<T>
): boolean {
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

export function loadPendingPlanningOperation(
  userId: string,
  projectId: string
): PendingPlanningOperationLoad {
  let raw: string | null;
  try {
    raw = sessionStorage.getItem(pendingProjectOperationKey(userId, projectId));
  } catch {
    return { status: "unavailable" };
  }
  if (!raw) return { status: "missing" };
  try {
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    if (parsed.schema_version === 2 && parsed.workspace === "foreshadow") {
      return { status: "foreign", workspace: "foreshadow" };
    }
    if (parsed.schema_version === 3 && parsed.workspace === "generation_execution") {
      return { status: "foreign", workspace: "generation_execution" };
    }
    if (parsed.schema_version === 4 && parsed.workspace === "technical_demo_execution") {
      return { status: "foreign", workspace: "technical_demo_execution" };
    }
    if (parsed.schema_version === 5 && parsed.workspace === "candidate_manual_edit") {
      return { status: "foreign", workspace: "candidate_manual_edit" };
    }
    const value = parsed as Partial<PendingPlanningOperation>;
    if (
      !hasExactKeys(value as Record<string, unknown>, ["schema_version", "user_id", "project_id", "operation_key", "action", "target_id", "payload", "created_at"]) ||
      value.schema_version !== 1 ||
      value.user_id !== userId ||
      value.project_id !== projectId ||
      !isOperationKey(value.operation_key) ||
      !isAction(value.action) ||
      !isRecord(value.payload) ||
      typeof value.created_at !== "string" || !Number.isFinite(Date.parse(value.created_at)) ||
      value.payload.operation_key !== value.operation_key ||
      !validTarget(value.action, value.target_id, value.payload) ||
      !validPayload(value.action, value.payload)
    ) {
      return { status: "corrupt" };
    }
    return { status: "available", operation: value as PendingPlanningOperation };
  } catch {
    return { status: "corrupt" };
  }
}

export function clearPendingPlanningOperation(userId: string, projectId: string): boolean {
  try {
    sessionStorage.removeItem(pendingProjectOperationKey(userId, projectId));
    return true;
  } catch {
    return false;
  }
}

export function shouldKeepPlanningOperation(error: unknown): boolean {
  if (!(error instanceof Error)) return true;
  const typed = error as Error & { status?: number; outcomeUnknown?: boolean };
  if (typed.outcomeUnknown) return true;
  return typed.status === undefined || (typed.status >= 500 && typed.status !== 503);
}
