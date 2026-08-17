export interface DemoFixtureCounts {
  setting_type_count: 6;
  element_count: 7;
  source_count: 7;
  relation_count: 3;
  part_count: 1;
  chapter_count: 2;
  assignment_count: 7;
  foreshadow_lifecycle_count: 1;
  foreshadow_plan_count: 2;
  foreshadow_fact_count: 0;
}

export interface DemoFixtureCurrentResponse {
  schema_version: 1;
  fixture_version: 1;
  mode: "technical_demo_fixture";
  environment: "non_production";
  state: "missing" | "ready" | "diverged";
  can_bootstrap: boolean;
  preserved: boolean;
  project_id: string | null;
  plan_id: string | null;
  part_id: string | null;
  chapter_id: string | null;
  element_id: string | null;
  assignment_id: string | null;
  second_chapter_id: string | null;
  foreshadow_element_id: string | null;
  foreshadow_lifecycle_id: string | null;
  counts: DemoFixtureCounts | null;
  next_path: string | null;
  recommended_action: "bootstrap_fixture" | "open_fixture" | "preserve_existing_fixture";
}

export interface DemoFixtureBootstrapResponse {
  schema_version: 1;
  fixture_version: 1;
  mode: "technical_demo_fixture";
  environment: "non_production";
  state: "ready";
  replayed: boolean;
  project_id: string;
  plan_id: string;
  part_id: string;
  chapter_id: string;
  element_id: string;
  assignment_id: string;
  next_path: string;
}

export interface TechnicalDemoCapabilityResponse {
  schema_version: 1;
  execution_mode: "technical_demo";
  fixture_version: 1;
  adapter_schema_version: 1;
  content_spec_version: 1;
  project_id: string;
  planning_chapter_id: string;
  run_id: string;
  context_checksum: string;
  fixed_response: true;
  ai_invoked: false;
  billing_effect: "none";
  usage_status: "not_applicable";
  capability_checksum: string;
}

export interface TechnicalDemoExecuteInput {
  operation_key: string;
  expected_context_checksum: string;
  expected_capability_checksum: string;
  fixture_version: 1;
  confirm_technical_demo: true;
}

export interface TechnicalDemoExecutionResponse {
  schema_version: 1;
  execution_mode: "technical_demo";
  fixture_version: 1;
  adapter_schema_version: 1;
  content_spec_version: 1;
  project_id: string;
  planning_chapter_id: string;
  run_id: string;
  operation_key: string;
  context_checksum: string;
  capability_checksum: string;
  execution_id: string;
  candidate_id: string;
  status: "succeeded";
  replayed: boolean;
  ai_invoked: false;
  billing_effect: "none";
  usage_status: "not_applicable";
  created_at: string;
  completed_at: string;
}

export interface TechnicalDemoCandidateResponse {
  schema_version: 1;
  id: string;
  project_id: string;
  run_id: string;
  planning_chapter_id: string;
  source_technical_demo_execution_id: string;
  parent_candidate_id: null;
  version_no: number;
  origin_kind: "technical_demo";
  title: string;
  content: string;
  content_format: "plain_text";
  content_checksum: string;
  content_size_bytes: number;
  word_count: number;
  created_by: string;
  ai_invoked: false;
  billing_effect: "none";
  usage_status: "not_applicable";
  created_at: string;
}
