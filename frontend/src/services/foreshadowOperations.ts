import { pendingProjectOperationKey } from "@/services/pendingProjectOperations";
import type {
  ForeshadowBindInput,
  ForeshadowFactCreateInput,
  ForeshadowFactRetractInput,
  ForeshadowLifecycleInput,
  ForeshadowOperationType,
  ForeshadowPlanCreateInput,
  ForeshadowPlanStateInput,
  ForeshadowRestoreInput,
  ForeshadowWritePayload,
} from "@/types/foreshadow";

export interface PendingForeshadowOperation<T extends ForeshadowWritePayload = ForeshadowWritePayload> {
  schema_version: 2;
  workspace: "foreshadow";
  user_id: string;
  project_id: string;
  operation_key: string;
  operation_type: ForeshadowOperationType;
  lifecycle_id: string | null;
  resource_id: string | null;
  payload: T;
  created_at: string;
}

export type PendingForeshadowLoad =
  | { status: "missing" }
  | { status: "available"; operation: PendingForeshadowOperation }
  | { status: "foreign"; workspace: "planning" | "generation_execution" }
  | { status: "corrupt" }
  | { status: "unavailable" };

function record(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value);
}

function exact(value: Record<string, unknown>, keys: string[]): boolean {
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  return actual.length === expected.length && actual.every((key, index) => key === expected[index]);
}

function positive(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= 1;
}

function stableId(value: unknown): value is string {
  return typeof value === "string" && value.length === 32;
}

function operationKey(value: unknown): value is string {
  return typeof value === "string" && value.length >= 8 && value.length <= 128 && /^[A-Za-z0-9._:-]+$/.test(value);
}

function onlyText(value: unknown, max = 2_000): value is string {
  return typeof value === "string" && value.length <= max;
}

function validPayload(type: ForeshadowOperationType, payload: Record<string, unknown>): boolean {
  if (!operationKey(payload.operation_key)) return false;
  if (type === "foreshadow_bind") {
    return exact(payload, ["operation_key", "element_id", "expected_structure_version", "expected_element_lock_version"])
      && stableId(payload.element_id) && positive(payload.expected_structure_version) && positive(payload.expected_element_lock_version);
  }
  if (type === "foreshadow_archive") {
    return exact(payload, ["operation_key", "expected_lifecycle_version"]) && positive(payload.expected_lifecycle_version);
  }
  if (type === "foreshadow_restore") {
    return exact(payload, ["operation_key", "expected_lifecycle_version", "expected_structure_version", "expected_element_lock_version"])
      && positive(payload.expected_lifecycle_version) && positive(payload.expected_structure_version) && positive(payload.expected_element_lock_version);
  }
  if (type === "foreshadow_plan_create") {
    return exact(payload, [
      "operation_key", "expected_lifecycle_version", "expected_structure_version", "action_kind", "target_type",
      "target_id", "expected_target_lock_version", "condition_text", "note",
    ]) && positive(payload.expected_lifecycle_version) && positive(payload.expected_structure_version)
      && (payload.action_kind === "plant" || payload.action_kind === "resolve")
      && (payload.target_type === "part" || payload.target_type === "chapter") && stableId(payload.target_id)
      && positive(payload.expected_target_lock_version) && onlyText(payload.condition_text) && onlyText(payload.note)
      && (payload.action_kind !== "resolve" || String(payload.condition_text).trim().length > 0);
  }
  if (type === "foreshadow_plan_cancel" || type === "foreshadow_plan_restore") {
    return exact(payload, ["operation_key", "expected_lifecycle_version", "expected_structure_version", "expected_item_lock_version"])
      && positive(payload.expected_lifecycle_version) && positive(payload.expected_structure_version) && positive(payload.expected_item_lock_version);
  }
  if (type === "foreshadow_fact_record") {
    return exact(payload, [
      "operation_key", "expected_lifecycle_version", "expected_structure_version", "fact_kind", "chapter_id",
      "expected_chapter_lock_version", "note",
    ]) && positive(payload.expected_lifecycle_version) && positive(payload.expected_structure_version)
      && (payload.fact_kind === "planted" || payload.fact_kind === "resolved") && stableId(payload.chapter_id)
      && positive(payload.expected_chapter_lock_version) && onlyText(payload.note);
  }
  return exact(payload, ["operation_key", "expected_lifecycle_version", "expected_fact_lock_version", "reason"])
    && positive(payload.expected_lifecycle_version) && positive(payload.expected_fact_lock_version)
    && typeof payload.reason === "string" && payload.reason.trim().length > 0 && payload.reason.length <= 2_000;
}

export function isPendingForeshadowOperation(value: unknown, userId?: string, projectId?: string): value is PendingForeshadowOperation {
  if (!record(value) || !exact(value, [
    "schema_version", "workspace", "user_id", "project_id", "operation_key", "operation_type",
    "lifecycle_id", "resource_id", "payload", "created_at",
  ]) || value.schema_version !== 2 || value.workspace !== "foreshadow"
    || (userId !== undefined && value.user_id !== userId) || (projectId !== undefined && value.project_id !== projectId)
    || typeof value.user_id !== "string" || typeof value.project_id !== "string" || !operationKey(value.operation_key)
    || !record(value.payload) || value.payload.operation_key !== value.operation_key
    || !Object.values(FORESHADOW_OPERATION_TYPES).includes(value.operation_type as ForeshadowOperationType)
    || typeof value.created_at !== "string" || !Number.isFinite(Date.parse(value.created_at))) return false;
  const type = value.operation_type as ForeshadowOperationType;
  if (!validPayload(type, value.payload)) return false;
  if (type === "foreshadow_bind") return value.lifecycle_id === null && stableId(value.resource_id) && value.resource_id === value.payload.element_id;
  if (!stableId(value.lifecycle_id)) return false;
  if (["foreshadow_plan_cancel", "foreshadow_plan_restore", "foreshadow_fact_retract"].includes(type)) return stableId(value.resource_id);
  return value.resource_id === null;
}

export const FORESHADOW_OPERATION_TYPES: Record<string, ForeshadowOperationType> = {
  bind: "foreshadow_bind",
  archive: "foreshadow_archive",
  restore: "foreshadow_restore",
  plan_create: "foreshadow_plan_create",
  plan_cancel: "foreshadow_plan_cancel",
  plan_restore: "foreshadow_plan_restore",
  fact_record: "foreshadow_fact_record",
  fact_retract: "foreshadow_fact_retract",
};

export function createForeshadowOperationKey(type: ForeshadowOperationType): string {
  if (!globalThis.crypto?.randomUUID) throw new Error("当前浏览器无法生成安全操作编号，已停止写入。");
  return `${type}:${globalThis.crypto.randomUUID()}`;
}

export function savePendingForeshadowOperation<T extends ForeshadowWritePayload>(operation: PendingForeshadowOperation<T>): boolean {
  if (!isPendingForeshadowOperation(operation, operation.user_id, operation.project_id)) return false;
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

export function loadPendingForeshadowOperation(userId: string, projectId: string): PendingForeshadowLoad {
  let raw: string | null;
  try {
    raw = sessionStorage.getItem(pendingProjectOperationKey(userId, projectId));
  } catch {
    return { status: "unavailable" };
  }
  if (!raw) return { status: "missing" };
  try {
    const value = JSON.parse(raw) as { schema_version?: unknown; workspace?: unknown };
    if (value.schema_version === 1 && value.workspace === undefined) return { status: "foreign", workspace: "planning" };
    if (value.schema_version === 3 && value.workspace === "generation_execution") {
      return { status: "foreign", workspace: "generation_execution" };
    }
    if (!isPendingForeshadowOperation(value, userId, projectId)) return { status: "corrupt" };
    return { status: "available", operation: value };
  } catch {
    return { status: "corrupt" };
  }
}

export function clearPendingForeshadowOperation(userId: string, projectId: string, operationKeyValue: string): boolean {
  try {
    const key = pendingProjectOperationKey(userId, projectId);
    const raw = sessionStorage.getItem(key);
    if (!raw) return true;
    const value = JSON.parse(raw) as { operation_key?: unknown };
    if (value.operation_key !== operationKeyValue) return false;
    sessionStorage.removeItem(key);
    return true;
  } catch {
    return false;
  }
}

export function pendingPayload<T extends ForeshadowWritePayload>(operation: PendingForeshadowOperation): T {
  return operation.payload as T;
}

export type {
  ForeshadowBindInput,
  ForeshadowLifecycleInput,
  ForeshadowRestoreInput,
  ForeshadowPlanCreateInput,
  ForeshadowPlanStateInput,
  ForeshadowFactCreateInput,
  ForeshadowFactRetractInput,
};
