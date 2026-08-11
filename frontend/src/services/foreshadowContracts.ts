import type {
  ForeshadowEvent,
  ForeshadowFact,
  ForeshadowHistoryResponse,
  ForeshadowLifecycle,
  ForeshadowListResponse,
  ForeshadowMutationReceipt,
  ForeshadowOperationType,
  ForeshadowPlanItem,
  ForeshadowState,
  ForeshadowTargetSnapshot,
} from "@/types/foreshadow";

type RecordValue = Record<string, unknown>;

const states = new Set<ForeshadowState>(["unplanted", "planted", "pending_resolution", "resolved"]);
const operationTypes = new Set<ForeshadowOperationType>([
  "foreshadow_bind", "foreshadow_archive", "foreshadow_restore", "foreshadow_plan_create",
  "foreshadow_plan_cancel", "foreshadow_plan_restore", "foreshadow_fact_record", "foreshadow_fact_retract",
]);
const eventKinds = new Set([
  "create", "archive", "restore", "plan_create", "plan_cancel", "plan_restore", "fact_record", "fact_retract",
]);

function record(value: unknown): value is RecordValue {
  return !!value && typeof value === "object" && !Array.isArray(value);
}

function exact(value: RecordValue, keys: string[]): boolean {
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  return actual.length === expected.length && actual.every((key, index) => key === expected[index]);
}

function text(value: unknown): value is string {
  return typeof value === "string";
}

function id(value: unknown): value is string {
  return typeof value === "string" && value.length === 32;
}

function positive(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= 1;
}

function nonnegative(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= 0;
}

function timestamp(value: unknown): value is string {
  return typeof value === "string" && Number.isFinite(Date.parse(value));
}

function target(value: unknown, chapterOnly = false): value is ForeshadowTargetSnapshot {
  if (!record(value) || !exact(value, ["target_type", "target_id", "title", "status", "part_id", "position"])) return false;
  if (value.target_type !== "part" && value.target_type !== "chapter") return false;
  if (chapterOnly && value.target_type !== "chapter") return false;
  return id(value.target_id) && text(value.title) && (value.status === "active" || value.status === "archived")
    && (value.part_id === null || id(value.part_id)) && positive(value.position);
}

function planItem(value: unknown): value is ForeshadowPlanItem {
  return record(value)
    && exact(value, ["id", "action_kind", "target", "condition_text", "note", "status", "lock_version", "created_at", "updated_at"])
    && id(value.id) && (value.action_kind === "plant" || value.action_kind === "resolve")
    && target(value.target) && text(value.condition_text) && text(value.note)
    && (value.status === "active" || value.status === "cancelled") && positive(value.lock_version)
    && timestamp(value.created_at) && timestamp(value.updated_at);
}

function fact(value: unknown): value is ForeshadowFact {
  return record(value)
    && exact(value, ["id", "fact_kind", "chapter", "note", "status", "lock_version", "created_at", "retracted_at"])
    && id(value.id) && (value.fact_kind === "planted" || value.fact_kind === "resolved")
    && target(value.chapter, true) && text(value.note) && (value.status === "active" || value.status === "retracted")
    && positive(value.lock_version) && timestamp(value.created_at)
    && (value.retracted_at === null || timestamp(value.retracted_at));
}

export function isForeshadowLifecycle(value: unknown, projectId?: string): value is ForeshadowLifecycle {
  if (!record(value) || !exact(value, [
    "id", "project_id", "plan_id", "status", "state", "lock_version", "element", "plans", "facts", "created_at", "updated_at",
  ])) return false;
  if (!id(value.id) || !id(value.project_id) || !id(value.plan_id) || (projectId !== undefined && value.project_id !== projectId)) return false;
  if ((value.status !== "active" && value.status !== "archived") || !states.has(value.state as ForeshadowState) || !positive(value.lock_version)) return false;
  if (!record(value.element) || !exact(value.element, [
    "id", "name", "summary", "confirmation_status", "lifecycle_status", "enabled", "content_version", "lock_version",
  ])) return false;
  if (!id(value.element.id) || !text(value.element.name) || !text(value.element.summary)
    || !["candidate", "confirmed", "rejected"].includes(String(value.element.confirmation_status))
    || !["active", "archived", "merged"].includes(String(value.element.lifecycle_status))
    || typeof value.element.enabled !== "boolean" || !positive(value.element.content_version) || !positive(value.element.lock_version)) return false;
  return Array.isArray(value.plans) && value.plans.every(planItem)
    && Array.isArray(value.facts) && value.facts.every(fact)
    && timestamp(value.created_at) && timestamp(value.updated_at);
}

export function parseForeshadowList(value: unknown, projectId: string): ForeshadowListResponse {
  if (!record(value) || !exact(value, ["items", "counts", "next_cursor"]) || !Array.isArray(value.items)
    || !value.items.every((item) => isForeshadowLifecycle(item, projectId)) || !record(value.counts)
    || !exact(value.counts, ["unplanted", "planted", "pending_resolution", "resolved"])
    || !Object.values(value.counts).every(nonnegative) || (value.next_cursor !== null && !id(value.next_cursor))) {
    throw new Error("伏笔列表响应不完整或身份不一致，已停止相关写入。");
  }
  return value as unknown as ForeshadowListResponse;
}

export function parseForeshadowLifecycle(value: unknown, projectId: string, lifecycleId?: string): ForeshadowLifecycle {
  if (!isForeshadowLifecycle(value, projectId) || (lifecycleId !== undefined && value.id !== lifecycleId)) {
    throw new Error("伏笔详情响应不完整或身份不一致，已停止相关写入。");
  }
  return value;
}

function event(value: unknown): value is ForeshadowEvent {
  return record(value) && exact(value, [
    "id", "event_kind", "plan_item_id", "fact_id", "previous_lifecycle_version", "new_lifecycle_version", "metadata", "created_at",
  ]) && id(value.id) && eventKinds.has(String(value.event_kind))
    && (value.plan_item_id === null || id(value.plan_item_id)) && (value.fact_id === null || id(value.fact_id))
    && nonnegative(value.previous_lifecycle_version) && positive(value.new_lifecycle_version)
    && record(value.metadata) && timestamp(value.created_at);
}

export function parseForeshadowHistory(value: unknown, lifecycleId: string): ForeshadowHistoryResponse {
  if (!record(value) || !exact(value, ["lifecycle_id", "items"]) || value.lifecycle_id !== lifecycleId
    || !Array.isArray(value.items) || !value.items.every(event)) {
    throw new Error("伏笔历史响应不完整或身份不一致。");
  }
  return value as unknown as ForeshadowHistoryResponse;
}

export interface ExpectedForeshadowReceipt {
  projectId: string;
  operationKey: string;
  operationType: ForeshadowOperationType;
  lifecycleId?: string | null;
  elementId?: string | null;
}

export function parseForeshadowReceipt(value: unknown, expected: ExpectedForeshadowReceipt): ForeshadowMutationReceipt {
  if (!record(value) || !exact(value, [
    "receipt_id", "operation_key", "operation_type", "replayed", "project_id", "lifecycle_id",
    "previous_lifecycle_version", "new_lifecycle_version", "event_id", "lifecycle", "created_at",
  ]) || !id(value.receipt_id) || value.operation_key !== expected.operationKey
    || !operationTypes.has(value.operation_type as ForeshadowOperationType) || value.operation_type !== expected.operationType
    || typeof value.replayed !== "boolean" || value.project_id !== expected.projectId || !id(value.lifecycle_id)
    || (expected.lifecycleId && value.lifecycle_id !== expected.lifecycleId)
    || !nonnegative(value.previous_lifecycle_version) || !positive(value.new_lifecycle_version) || !id(value.event_id)
    || !timestamp(value.created_at) || !isForeshadowLifecycle(value.lifecycle, expected.projectId)) {
    throw new Error("伏笔操作收据损坏或与原请求不一致，已停止重试。");
  }
  const lifecycle = value.lifecycle as unknown as ForeshadowLifecycle;
  if (lifecycle.id !== value.lifecycle_id || lifecycle.lock_version !== value.new_lifecycle_version) {
    throw new Error("伏笔操作收据的生命周期版本不一致，已停止重试。");
  }
  if (expected.operationType === "foreshadow_bind" && (!expected.elementId || lifecycle.element.id !== expected.elementId)) {
    throw new Error("伏笔操作收据的设定身份与原请求不一致，已停止重试。");
  }
  return value as unknown as ForeshadowMutationReceipt;
}
