import { api } from "@/services/api";
import type { DemoFixtureBootstrapResponse, DemoFixtureCounts, DemoFixtureCurrentResponse } from "@/types/demo";

const CURRENT_KEYS = ["schema_version", "fixture_version", "mode", "environment", "state", "can_bootstrap", "preserved", "project_id", "plan_id", "part_id", "chapter_id", "element_id", "assignment_id", "second_chapter_id", "foreshadow_element_id", "foreshadow_lifecycle_id", "counts", "next_path", "recommended_action"];
const BOOTSTRAP_KEYS = ["schema_version", "fixture_version", "mode", "environment", "state", "replayed", "project_id", "plan_id", "part_id", "chapter_id", "element_id", "assignment_id", "next_path"];
const COUNT_KEYS = ["setting_type_count", "element_count", "source_count", "relation_count", "part_count", "chapter_count", "assignment_count", "foreshadow_lifecycle_count", "foreshadow_plan_count", "foreshadow_fact_count"];

export class DemoFixtureContractError extends Error { constructor(message: string) { super(message); this.name = "DemoFixtureContractError"; } }
const record = (value: unknown): value is Record<string, unknown> => !!value && typeof value === "object" && !Array.isArray(value);
const exact = (value: Record<string, unknown>, keys: string[]) => Object.keys(value).sort().join("|") === [...keys].sort().join("|");
const id = (value: unknown): value is string => typeof value === "string" && /^[A-Za-z0-9]{32}$/.test(value);
function fail(message: string): never { throw new DemoFixtureContractError(message); }

function parseCounts(value: unknown): DemoFixtureCounts {
  if (!record(value) || !exact(value, COUNT_KEYS)
    || value.setting_type_count !== 6 || value.element_count !== 7 || value.source_count !== 7
    || value.relation_count !== 3 || value.part_count !== 1 || value.chapter_count !== 2
    || value.assignment_count !== 7 || value.foreshadow_lifecycle_count !== 1
    || value.foreshadow_plan_count !== 2 || value.foreshadow_fact_count !== 0) fail("技术演示计数契约无效。");
  return value as unknown as DemoFixtureCounts;
}

export function parseDemoFixtureCurrent(value: unknown): DemoFixtureCurrentResponse {
  if (!record(value) || !exact(value, CURRENT_KEYS) || value.schema_version !== 1 || value.fixture_version !== 1
    || value.mode !== "technical_demo_fixture" || value.environment !== "non_production"
    || !["missing", "ready", "diverged"].includes(String(value.state))) fail("技术演示状态响应无效。");
  const anchors = [value.project_id, value.plan_id, value.part_id, value.chapter_id, value.element_id, value.assignment_id, value.second_chapter_id, value.foreshadow_element_id, value.foreshadow_lifecycle_id];
  if (value.state === "missing") {
    if (value.can_bootstrap !== true || value.preserved !== false || anchors.some((item) => item !== null) || value.counts !== null || value.next_path !== null || value.recommended_action !== "bootstrap_fixture") fail("技术演示缺失状态形态无效。");
  } else if (value.state === "ready") {
    if (value.can_bootstrap !== false || value.preserved !== false || !anchors.every(id)
      || value.next_path !== `/project/${value.project_id}/lore`
      || value.recommended_action !== "open_fixture") fail("技术演示就绪状态形态无效。");
    parseCounts(value.counts);
  } else if (value.can_bootstrap !== false || value.preserved !== true || (value.project_id !== null && !id(value.project_id)) || anchors.slice(1).some((item) => item !== null) || value.counts !== null || value.next_path !== null || value.recommended_action !== "preserve_existing_fixture") fail("技术演示分歧状态形态无效。");
  return value as unknown as DemoFixtureCurrentResponse;
}

export function parseDemoFixtureBootstrap(value: unknown): DemoFixtureBootstrapResponse {
  if (!record(value) || !exact(value, BOOTSTRAP_KEYS) || value.schema_version !== 1 || value.fixture_version !== 1
    || value.mode !== "technical_demo_fixture" || value.environment !== "non_production" || value.state !== "ready"
    || typeof value.replayed !== "boolean" || ![value.project_id, value.plan_id, value.part_id, value.chapter_id, value.element_id, value.assignment_id].every(id)
    || value.next_path !== `/project/${value.project_id}/lore`) fail("技术演示初始化响应无效。");
  return value as unknown as DemoFixtureBootstrapResponse;
}

export async function readDemoFixture(signal?: AbortSignal) { return parseDemoFixtureCurrent(await api.getDemoFixture(signal)); }
export async function bootstrapDemoFixture() { return parseDemoFixtureBootstrap(await api.bootstrapDemoFixture({ fixture_version: 1, operation_key: "demo:v1:bootstrap" })); }
