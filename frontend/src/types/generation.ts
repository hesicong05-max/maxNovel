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
