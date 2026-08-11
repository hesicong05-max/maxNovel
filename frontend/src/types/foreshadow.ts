export type ForeshadowState = "unplanted" | "planted" | "pending_resolution" | "resolved";
export type ForeshadowLifecycleStatus = "active" | "archived";
export type ForeshadowActionKind = "plant" | "resolve";
export type ForeshadowFactKind = "planted" | "resolved";

export interface ForeshadowElementSnapshot {
  id: string;
  name: string;
  summary: string;
  confirmation_status: "candidate" | "confirmed" | "rejected";
  lifecycle_status: "active" | "archived" | "merged";
  enabled: boolean;
  content_version: number;
  lock_version: number;
}

export interface ForeshadowTargetSnapshot {
  target_type: "part" | "chapter";
  target_id: string;
  title: string;
  status: "active" | "archived";
  part_id: string | null;
  position: number;
}

export interface ForeshadowPlanItem {
  id: string;
  action_kind: ForeshadowActionKind;
  target: ForeshadowTargetSnapshot;
  condition_text: string;
  note: string;
  status: "active" | "cancelled";
  lock_version: number;
  created_at: string;
  updated_at: string;
}

export interface ForeshadowFact {
  id: string;
  fact_kind: ForeshadowFactKind;
  chapter: ForeshadowTargetSnapshot;
  note: string;
  status: "active" | "retracted";
  lock_version: number;
  created_at: string;
  retracted_at: string | null;
}

export interface ForeshadowLifecycle {
  id: string;
  project_id: string;
  plan_id: string;
  status: ForeshadowLifecycleStatus;
  state: ForeshadowState;
  lock_version: number;
  element: ForeshadowElementSnapshot;
  plans: ForeshadowPlanItem[];
  facts: ForeshadowFact[];
  created_at: string;
  updated_at: string;
}

export interface ForeshadowStateCounts {
  unplanted: number;
  planted: number;
  pending_resolution: number;
  resolved: number;
}

export interface ForeshadowListResponse {
  items: ForeshadowLifecycle[];
  counts: ForeshadowStateCounts;
  next_cursor: string | null;
}

export interface ForeshadowListFilters {
  status?: ForeshadowLifecycleStatus;
  state?: ForeshadowState;
  after?: string;
  limit?: number;
}

export type ForeshadowEventKind =
  | "create"
  | "archive"
  | "restore"
  | "plan_create"
  | "plan_cancel"
  | "plan_restore"
  | "fact_record"
  | "fact_retract";

export interface ForeshadowEvent {
  id: string;
  event_kind: ForeshadowEventKind;
  plan_item_id: string | null;
  fact_id: string | null;
  previous_lifecycle_version: number;
  new_lifecycle_version: number;
  metadata: Record<string, unknown>;
  created_at: string;
}

export interface ForeshadowHistoryResponse {
  lifecycle_id: string;
  items: ForeshadowEvent[];
}

export type ForeshadowOperationType =
  | "foreshadow_bind"
  | "foreshadow_archive"
  | "foreshadow_restore"
  | "foreshadow_plan_create"
  | "foreshadow_plan_cancel"
  | "foreshadow_plan_restore"
  | "foreshadow_fact_record"
  | "foreshadow_fact_retract";

export interface ForeshadowMutationReceipt {
  receipt_id: string;
  operation_key: string;
  operation_type: ForeshadowOperationType;
  replayed: boolean;
  project_id: string;
  lifecycle_id: string;
  previous_lifecycle_version: number;
  new_lifecycle_version: number;
  event_id: string;
  lifecycle: ForeshadowLifecycle;
  created_at: string;
}

export interface ForeshadowBindInput {
  operation_key: string;
  element_id: string;
  expected_structure_version: number;
  expected_element_lock_version: number;
}

export interface ForeshadowLifecycleInput {
  operation_key: string;
  expected_lifecycle_version: number;
}

export interface ForeshadowRestoreInput extends ForeshadowLifecycleInput {
  expected_structure_version: number;
  expected_element_lock_version: number;
}

export interface ForeshadowPlanCreateInput extends ForeshadowLifecycleInput {
  expected_structure_version: number;
  action_kind: ForeshadowActionKind;
  target_type: "part" | "chapter";
  target_id: string;
  expected_target_lock_version: number;
  condition_text: string;
  note: string;
}

export interface ForeshadowPlanStateInput extends ForeshadowLifecycleInput {
  expected_structure_version: number;
  expected_item_lock_version: number;
}

export interface ForeshadowFactCreateInput extends ForeshadowLifecycleInput {
  expected_structure_version: number;
  fact_kind: ForeshadowFactKind;
  chapter_id: string;
  expected_chapter_lock_version: number;
  note: string;
}

export interface ForeshadowFactRetractInput extends ForeshadowLifecycleInput {
  expected_fact_lock_version: number;
  reason: string;
}

export type ForeshadowWritePayload =
  | ForeshadowBindInput
  | ForeshadowLifecycleInput
  | ForeshadowRestoreInput
  | ForeshadowPlanCreateInput
  | ForeshadowPlanStateInput
  | ForeshadowFactCreateInput
  | ForeshadowFactRetractInput;
