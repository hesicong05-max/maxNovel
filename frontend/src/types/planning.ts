export type PlanningNodeStatus = "active" | "archived";

export interface PlanningChapter {
  id: string;
  project_id: string;
  plan_id: string;
  part_id: string;
  title: string;
  summary: string;
  target_word_count: number | null;
  position: number;
  status: PlanningNodeStatus;
  lock_version: number;
  created_at: string;
  updated_at: string;
}

export interface PlanningPart {
  id: string;
  project_id: string;
  plan_id: string;
  title: string;
  description: string;
  position: number;
  status: PlanningNodeStatus;
  lock_version: number;
  created_at: string;
  updated_at: string;
  chapters: PlanningChapter[];
}

export interface NovelPlan {
  id: string;
  project_id: string;
  status: PlanningNodeStatus;
  structure_version: number;
  assignment_version: number;
  created_at: string;
  updated_at: string;
  parts: PlanningPart[];
}

export interface PlanningOperationCommand {
  operation_key: string;
  expected_structure_version: number;
}

export interface PlanningPartCreateInput extends PlanningOperationCommand {
  title: string;
  description: string;
}

export interface PlanningPartUpdateInput extends PlanningPartCreateInput {
  expected_lock_version: number;
}

export interface PlanningChapterCreateInput extends PlanningOperationCommand {
  title: string;
  summary: string;
  target_word_count: number | null;
}

export interface PlanningChapterUpdateInput extends PlanningOperationCommand {
  expected_lock_version: number;
  title?: string;
  summary?: string;
  target_word_count?: number;
  clear_target_word_count?: boolean;
}

export interface PlanningNodeStateInput extends PlanningOperationCommand {}

export interface PlanningReorderInput extends PlanningOperationCommand {
  parts: Array<{ part_id: string; chapter_ids: string[] }>;
}

export interface PlanningMutationReceipt {
  receipt_kind: "structure";
  receipt_id: string;
  operation_key: string;
  operation_type: string;
  replayed: boolean;
  changed: boolean;
  project_id: string;
  plan_id: string;
  previous_structure_version: number;
  new_structure_version: number;
  affected_node: Record<string, unknown> | null;
  placement: Record<string, unknown> | null;
  structure: Record<string, unknown> | null;
  created_at: string;
}

export type PlanningScopeType = "novel" | "part" | "chapter";

export interface PlanningScopeSnapshot {
  scope_type: PlanningScopeType;
  scope_target_id: string;
  title: string;
  status: PlanningNodeStatus;
  part_id: string | null;
}

export interface PlanningAssignmentSnapshot {
  id: string;
  element_id: string;
  scope: PlanningScopeSnapshot;
  status: "active" | "removed";
  lock_version: number;
  assigned_at_content_version: number;
  current_content_version: number;
  content_changed_since_assignment: boolean;
  element: Record<string, unknown>;
  generation_eligible: boolean;
  ineligible_reasons: string[];
  created_at: string;
  updated_at: string;
}

export interface PlanningAssignmentMutationReceipt {
  receipt_kind: "assignment";
  receipt_id: string;
  operation_key: string;
  operation_type: "assignment_create" | "assignment_remove" | "assignment_restore";
  replayed: boolean;
  changed: boolean;
  project_id: string;
  plan_id: string;
  previous_assignment_version: number;
  new_assignment_version: number;
  assignment: PlanningAssignmentSnapshot;
  event_id: string;
  created_at: string;
}

export type PlanningOperationReceipt =
  | PlanningMutationReceipt
  | PlanningAssignmentMutationReceipt;
