export interface LoreFacetCount {
  key: string;
  label: string;
  count: number;
}

export interface LoreMigrationStatus {
  storage_mode: string;
  state: string;
  read_only: boolean;
}

export interface LoreOverview {
  formal_total: number;
  confirmed_active: number;
  pending_review: number;
  needs_attention: number;
  disabled: number;
  archived: number;
  migration_status: LoreMigrationStatus;
  capabilities: {
    candidate_review: boolean;
    candidate_accept: boolean;
    formal_create: boolean;
    formal_conflict_tracking: boolean;
    search_fields: string[];
  };
  count_definitions: Record<string, Record<string, unknown>>;
}

export interface LoreElementListItem {
  id: string;
  type: { key: string; display_name: string };
  name: string;
  summary: string;
  confirmation_status: string;
  lifecycle_status: string;
  enabled: boolean;
  generation_eligible: boolean;
  source_summary: string;
  current_version: number;
  revision: number;
  lock_version: number;
  updated_at: string;
  relation_count: number;
}

export interface LoreElementDetail extends LoreElementListItem {
  payload: Record<string, unknown>;
  field_states: Record<string, string>;
  field_definitions: LoreFieldDefinition[];
  sources: Array<{
    id: string | null;
    kind: string;
    label: string;
    is_primary: boolean;
    created_at: string;
    excerpt: string | null;
    reference: string | null;
  }>;
  version_count: number;
  read_only: boolean;
}

export interface LoreElementUpdateInput {
  expected_version: number;
  name: string;
  summary: string;
  payload: Record<string, string | null>;
  field_states: Record<string, LoreFieldState>;
}

export interface LoreElementStateInput {
  expected_version: number;
  reason?: string;
}

export interface LoreElementWriteResponse {
  id: string;
  type: { key: string; display_name: string };
  name: string;
  summary: string;
  confirmation_status: string;
  lifecycle_status: string;
  enabled: boolean;
  generation_eligible: boolean;
  lock_version: number;
  content_version: number;
  payload_schema_revision: number;
  payload: Record<string, unknown>;
  field_states: Record<string, LoreFieldState>;
  field_definitions: LoreFieldDefinition[];
  sources: LoreElementDetail["sources"];
  relation_count: number;
  binding_count: number;
  created_at: string;
  updated_at: string;
}

export interface LoreSourceInput {
  kind: string;
  reference?: string | null;
  locator?: Record<string, unknown>;
  excerpt?: string | null;
  is_primary?: boolean;
  confirmation_status?: "provided" | "needs_confirmation";
}

export interface LoreElementCreateInput {
  operation_key: string;
  type_key: string;
  name: string;
  summary: string;
  payload: Record<string, string | null>;
  field_states: Record<string, LoreFieldState>;
  sources: LoreSourceInput[];
}

export interface LoreElementCreateResponse extends LoreElementWriteResponse {
  replayed: boolean;
}

export interface LoreFieldDefinition {
  key: string;
  label: string;
  control: string;
  value_type: string;
  help: string;
  order: number;
  required: boolean;
}

export interface LoreTypeDefinition {
  id: string;
  key: string;
  display_name: string;
  description: string;
  field_schema: LoreFieldDefinition[];
  is_builtin: boolean;
  schema_revision: number;
  status: "active" | "archived";
  created_at: string;
  updated_at: string;
}

export interface LoreTypesResponse {
  items: LoreTypeDefinition[];
  total: number;
}

export interface LoreListResponse {
  items: LoreElementListItem[];
  next_cursor: string | null;
  has_more: boolean;
  total: number;
  facets: {
    types: LoreFacetCount[];
    confirmation_statuses: LoreFacetCount[];
    sources: LoreFacetCount[];
    lifecycle_statuses: LoreFacetCount[];
    enabled_statuses: LoreFacetCount[];
    relation_statuses: LoreFacetCount[];
  };
  migration_status: LoreMigrationStatus;
}

export interface LoreCandidate {
  id: string;
  batch_id: string;
  ordinal: number;
  type_key: string | null;
  type_display_name: string | null;
  name: string | null;
  summary: string;
  payload: Record<string, string | null>;
  field_states: Record<string, LoreFieldState>;
  relation_suggestions: Array<Record<string, unknown>>;
  duplicate_conflict_suggestions: LoreCandidateSuggestion[];
  suggestion_resolutions: Record<string, LoreSuggestionResolution>;
  user_overrides: Record<string, unknown>;
  status: "pending_review" | "accepted" | "rejected" | "failed";
  needs_attention: boolean;
  disabled_reasons: string[];
  revision: number;
  accepted_element_id: string | null;
  error_code: string | null;
  can_accept: boolean;
  actions: LoreCandidateActions;
  created_at: string;
  updated_at: string;
  evidence: Array<{
    id: string;
    field_key: string;
    label: string;
    value: string | null;
    extracted_value: string | null;
    current_value: string | null;
    current_state: LoreFieldState;
    value_origin: "ai_extraction" | "user_override" | "user_cleared";
    state: LoreFieldState;
    excerpt: string | null;
    locator: Record<string, unknown>;
    excerpt_hash: string | null;
    source_hash: string;
    is_name: boolean;
  }>;
}

export type LoreFieldState = "provided" | "unknown" | "needs_confirmation";

export type LoreSuggestionResolution =
  | "accept_as_new"
  | "dismissed"
  | "deferred";

export interface LoreCandidateSuggestion {
  suggestion_id: string;
  kind: "possible_duplicate" | "possible_conflict" | string;
  target_element_id?: string;
  target_candidate_ordinal?: number;
  target_name?: string;
  target_type_key?: string;
  differing_fields?: string[];
  resolution_status?: string;
}

export interface LoreCandidateActions {
  can_edit: boolean;
  can_accept: boolean;
  can_reject: boolean;
  can_open_element: boolean;
  disabled_reasons: Record<string, string[]>;
}

export interface LoreCandidateEditInput {
  expected_version: number;
  type_key: string;
  name: string | null;
  summary: string;
  payload: Record<string, string | null>;
  field_states: Record<string, LoreFieldState>;
  suggestion_resolutions: Record<string, LoreSuggestionResolution>;
}

export interface LoreCandidateActionInput {
  expected_version: number;
  suggestion_resolutions: Record<string, LoreSuggestionResolution>;
}

export interface LoreCandidateActionResponse {
  candidate: LoreCandidate;
  action_result:
    | "accepted"
    | "already_accepted"
    | "rejected"
    | "already_rejected";
  replayed: boolean;
  accepted_element_id: string | null;
  remaining_pending_count: number;
  next_pending_candidate_id: string | null;
}

export interface LoreCandidateInboxResponse {
  items: LoreCandidate[];
  next_cursor: string | null;
  has_more: boolean;
  total: number;
  applied_filters: Record<string, unknown>;
  query_signature: string;
}

export interface LoreElementFilters {
  q?: string;
  type?: string;
  confirmation_status?: string;
  source_kind?: string;
  lifecycle_status?: string;
  enabled?: boolean;
  has_relation?: boolean;
  cursor?: string;
  limit?: number;
}

export interface LoreCandidateFilters {
  q?: string;
  status?: string;
  type?: string;
  needs_attention?: boolean;
  cursor?: string;
  limit?: number;
}
