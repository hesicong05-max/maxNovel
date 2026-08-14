export interface GenerationRunPrepareInput {
  operation_key: string;
  expected_structure_version: number;
  expected_assignment_version: number;
  expected_chapter_lock_version: number;
}

export interface GenerationContextVersions {
  structure: number;
  assignment: number;
  chapter_lock: number;
}

export interface GenerationContextPart {
  id: string;
  title: string;
  description: string;
  position: number;
  lock_version: number;
}

export interface GenerationContextChapter {
  id: string;
  title: string;
  summary: string;
  target_word_count: number | null;
  position: number;
  lock_version: number;
}

export interface GenerationContextType {
  id: string;
  key: string;
  display_name: string;
  schema_revision: number;
}

export interface GenerationContextElementVersion {
  id: string;
  element_id: string;
  type_id: string;
  version_no: number;
  name: string;
  summary: string;
  payload: Record<string, unknown>;
  field_states: Record<string, unknown>;
  source_id: string | null;
}

export type GenerationContextScopeType = "novel" | "part" | "chapter";

export interface GenerationContextAssignmentSource {
  assignment_id: string;
  scope_type: GenerationContextScopeType;
  scope_target_id: string;
  scope_title: string;
  assignment_lock_version: number;
  assigned_at_content_version: number;
}

export interface GenerationContextElement {
  element_id: string;
  type: GenerationContextType;
  version: GenerationContextElementVersion;
  assignment_sources: GenerationContextAssignmentSource[];
}

export interface GenerationContextRelationVersion {
  id: string;
  relation_id: string;
  version_no: number;
  source_element_id: string;
  target_element_id: string;
  relation_key: string;
  forward_label: string;
  reverse_label: string;
  description: string;
  metadata: Record<string, unknown>;
  status: "active";
}

export interface GenerationContextRelation {
  relation_id: string;
  version: GenerationContextRelationVersion;
}

export type GenerationContextWarning =
  | { code: "CHAPTER_SUMMARY_EMPTY"; element_id: null }
  | { code: "LORE_CHANGED_SINCE_ASSIGNMENT"; element_id: string };

export interface GenerationContextForeshadowActions {
  supported: false;
  items: never[];
}

export interface GenerationContextCounts {
  elements: number;
  relations: number;
  warnings: number;
}

export interface GenerationContextManifest {
  schema_version: 1;
  project_id: string;
  plan_id: string;
  versions: GenerationContextVersions;
  part: GenerationContextPart;
  chapter: GenerationContextChapter;
  elements: GenerationContextElement[];
  relations: GenerationContextRelation[];
  foreshadow_actions: GenerationContextForeshadowActions;
  warnings: GenerationContextWarning[];
  counts: GenerationContextCounts;
}

export interface GenerationRunResponse {
  id: string;
  project_id: string;
  plan_id: string;
  planning_chapter_id: string;
  operation_key: string;
  replayed: boolean;
  status: "prepared";
  execution_mode: "preflight_only";
  ai_invoked: false;
  billing_effect: "none";
  structure_version: number;
  assignment_version: number;
  chapter_lock_version: number;
  context_schema_version: number;
  context_manifest: GenerationContextManifest;
  context_checksum: string;
  context_size_bytes: number;
  created_at: string;
  updated_at: string;
}

export interface GenerationCapabilityResponse {
  schema_version: 1;
  provider_name: string;
  model_name: string;
  max_output_tokens: number;
  input_limit_availability: "unavailable";
  max_input_tokens: null;
  price_availability: "unavailable";
  capability_checksum: string;
}

export interface GenerationAttemptExecuteInput {
  operation_key: string;
  expected_context_checksum: string;
  expected_capability_checksum: string;
  confirm_model_call: true;
}

export type GenerationAttemptStatus =
  | "reserved"
  | "calling"
  | "succeeded"
  | "failed"
  | "outcome_unknown";

export type GenerationUsage =
  | {
      status: "reported";
      input_tokens: number;
      output_tokens: number;
      total_tokens: number;
    }
  | {
      status: "unavailable" | "unknown";
      input_tokens: null;
      output_tokens: null;
      total_tokens: null;
    };

export interface GenerationAttemptError {
  code: string;
  message: string;
  retryable: false;
  recommended_action:
    | "inspect_failure"
    | "keep_unknown_result"
    | "start_new_confirmed_attempt";
}

export interface GenerationAttemptResponse {
  id: string;
  project_id: string;
  run_id: string;
  planning_chapter_id: string;
  operation_key: string;
  replayed: boolean;
  status: GenerationAttemptStatus;
  execution_mode: "single_call";
  billing_confirmed: true;
  ai_invoked: boolean;
  billing_effect: "none" | "possible";
  capability: GenerationCapabilityResponse;
  model_name: string;
  prompt_schema_version: number;
  prompt_checksum: string;
  context_checksum: string;
  lock_version: number;
  usage: GenerationUsage;
  candidate_id: string | null;
  error: GenerationAttemptError | null;
  claimed_at: string | null;
  completed_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface GenerationCandidateResponse {
  id: string;
  project_id: string;
  run_id: string;
  planning_chapter_id: string;
  source_attempt_id: string;
  parent_candidate_id: null;
  version_no: number;
  origin_kind: "generated";
  title: string;
  content: string;
  content_format: "plain_text";
  content_checksum: string;
  content_size_bytes: number;
  word_count: number;
  created_by: string;
  created_at: string;
}

export type GenerationCandidateAuditStatus = "pass" | "review";

export interface GenerationCandidateAuditIntegrity {
  status: GenerationCandidateAuditStatus;
  content_size_bytes: number;
  word_count: number;
  storage_limit_bytes: 262144;
  storage_limit_reached: boolean;
}

export interface GenerationCandidateAuditTargetLength {
  status: GenerationCandidateAuditStatus | "not_applicable";
  actual_word_count: number;
  target_word_count: number | null;
  minimum_word_count: number | null;
  maximum_word_count: number | null;
}

export interface GenerationCandidateAuditPreparation {
  status: GenerationCandidateAuditStatus;
  warnings: GenerationContextWarning[];
}

export interface GenerationCandidateAuditTermEvidence {
  term: string;
  excerpt: string;
  start_offset: number;
  end_offset: number;
}

export interface GenerationCandidateAuditTerms {
  status: GenerationCandidateAuditStatus;
  items: GenerationCandidateAuditTermEvidence[];
  truncated: boolean;
}

export interface GenerationCandidateAuditContextElement {
  element_id: string;
  type_key: string;
  type_display_name: string;
  name: string;
  version_no: number;
}

export interface GenerationCandidateAuditContextSummary {
  element_count: number;
  relation_count: number;
  warning_count: number;
  elements: GenerationCandidateAuditContextElement[];
  foreshadow_actions_supported: false;
  foreshadow_action_count: 0;
}

export interface GenerationCandidateAuditResponse {
  schema_version: 1;
  ruleset_version: 1;
  project_id: string;
  run_id: string;
  planning_chapter_id: string;
  candidate_id: string;
  candidate_version: number;
  candidate_checksum: string;
  context_checksum: string;
  status: GenerationCandidateAuditStatus;
  integrity: GenerationCandidateAuditIntegrity;
  target_length: GenerationCandidateAuditTargetLength;
  preparation: GenerationCandidateAuditPreparation;
  unrecognized_explicit_terms: GenerationCandidateAuditTerms;
  context_summary: GenerationCandidateAuditContextSummary;
}

export type GenerationCandidateVersionOrigin =
  | "generated"
  | "technical_demo"
  | "manual_edit";

export interface GenerationCandidateManualEditInput {
  operation_key: string;
  parent_candidate_id: string;
  expected_parent_version_no: number;
  expected_parent_checksum: string;
  expected_context_checksum: string;
  content: string;
}

export interface GenerationCandidateVersionListItem {
  id: string;
  version_no: number;
  origin_kind: GenerationCandidateVersionOrigin;
  parent_candidate_id: string | null;
  parent_version_no: number | null;
  root_candidate_id: string;
  root_origin_kind: "generated" | "technical_demo";
  ai_invoked_for_this_version: boolean;
  billing_effect_for_this_version: "none" | "possible";
  usage_status_for_this_version: "reported" | "unavailable" | "not_applicable";
  title: string;
  content_checksum: string;
  content_size_bytes: number;
  word_count: number;
  created_by: string;
  created_at: string;
}

export interface GenerationCandidateVersionDetail
  extends GenerationCandidateVersionListItem {
  project_id: string;
  run_id: string;
  planning_chapter_id: string;
  content: string;
  content_format: "plain_text";
}

export interface GenerationCandidateVersionListResponse {
  schema_version: 1;
  project_id: string;
  run_id: string;
  planning_chapter_id: string;
  items: GenerationCandidateVersionListItem[];
  next_cursor: string | null;
  has_more: boolean;
}

export interface GenerationCandidateManualEditResponse {
  schema_version: 1;
  replayed: boolean;
  ai_invoked: false;
  billing_effect: "none";
  usage_status: "not_applicable";
  candidate: GenerationCandidateVersionDetail;
}
