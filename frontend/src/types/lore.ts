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
  field_definitions: Array<{
    key: string;
    label: string;
    order: number;
  }>;
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
  type_key: string | null;
  type_display_name: string | null;
  name: string | null;
  summary: string;
  status: "pending_review" | "accepted" | "rejected" | "failed";
  needs_attention: boolean;
  disabled_reasons: string[];
  revision: number;
  evidence: Array<{
    id: string;
    field_key: string;
    label: string;
    current_value: string | null;
    current_state: "provided" | "unknown" | "needs_confirmation";
    value_origin: "ai_extraction" | "user_override" | "user_cleared";
    excerpt: string | null;
  }>;
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
