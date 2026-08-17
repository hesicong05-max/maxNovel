import { beforeEach, describe, expect, it, vi } from "vitest";
import * as apiModule from "@/services/api";
import { DemoFixtureContractError, bootstrapDemoFixture, parseDemoFixtureBootstrap, parseDemoFixtureCurrent, readDemoFixture } from "./demoFixture";

const id = (seed: string) => seed.padEnd(32, seed).slice(0, 32);
const counts = { setting_type_count: 6, element_count: 7, source_count: 7, relation_count: 3, part_count: 1, chapter_count: 2, assignment_count: 7, foreshadow_lifecycle_count: 1, foreshadow_plan_count: 2, foreshadow_fact_count: 0 } as const;
const missing = { schema_version: 1, fixture_version: 1, mode: "technical_demo_fixture", environment: "non_production", state: "missing", can_bootstrap: true, preserved: false, project_id: null, plan_id: null, part_id: null, chapter_id: null, element_id: null, assignment_id: null, second_chapter_id: null, foreshadow_element_id: null, foreshadow_lifecycle_id: null, counts: null, next_path: null, recommended_action: "bootstrap_fixture" } as const;
const ready = { ...missing, state: "ready", can_bootstrap: false, project_id: id("project"), plan_id: id("plan"), part_id: id("part"), chapter_id: id("chapter"), element_id: id("element"), assignment_id: id("assignment"), second_chapter_id: id("second"), foreshadow_element_id: id("foreshadow"), foreshadow_lifecycle_id: id("lifecycle"), counts, next_path: `/project/${id("project")}/lore`, recommended_action: "open_fixture" } as const;

describe("demo fixture contracts", () => {
  beforeEach(() => vi.restoreAllMocks());
  it("accepts only the three exact server-authoritative states", () => {
    expect(parseDemoFixtureCurrent(missing).state).toBe("missing");
    expect(parseDemoFixtureCurrent(ready).state).toBe("ready");
    expect(parseDemoFixtureCurrent({ ...missing, state: "diverged", can_bootstrap: false, preserved: true, project_id: id("project"), recommended_action: "preserve_existing_fixture" }).state).toBe("diverged");
    expect(() => parseDemoFixtureCurrent({ ...ready, extra: true })).toThrow(DemoFixtureContractError);
    expect(() => parseDemoFixtureCurrent({ ...ready, counts: { ...counts, element_count: 8 } })).toThrow(DemoFixtureContractError);
    expect(() => parseDemoFixtureCurrent({ ...ready, next_path: `/project/${id("project")}/plan/chapters` })).toThrow(DemoFixtureContractError);
    expect(() => parseDemoFixtureBootstrap({ schema_version: 1, fixture_version: 1, mode: "technical_demo_fixture", environment: "non_production", state: "ready", replayed: false, project_id: id("project"), plan_id: id("plan"), part_id: id("part"), chapter_id: id("chapter"), element_id: id("element"), assignment_id: id("assignment"), next_path: `/project/${id("project")}` })).toThrow(DemoFixtureContractError);
  });
  it("reads current with GET and bootstraps only after an explicit POST", async () => {
    const bootstrap = { schema_version: 1, fixture_version: 1, mode: "technical_demo_fixture", environment: "non_production", state: "ready", replayed: false, project_id: id("project"), plan_id: id("plan"), part_id: id("part"), chapter_id: id("chapter"), element_id: id("element"), assignment_id: id("assignment"), next_path: `/project/${id("project")}/lore` } as const;
    const api = { ...apiModule.api, getDemoFixture: vi.fn().mockResolvedValue(missing), bootstrapDemoFixture: vi.fn().mockResolvedValue(bootstrap) };
    vi.spyOn(apiModule, "api", "get").mockReturnValue(api);
    expect((await readDemoFixture()).state).toBe("missing");
    expect(api.bootstrapDemoFixture).not.toHaveBeenCalled();
    expect((await bootstrapDemoFixture()).project_id).toBe(id("project"));
    expect(api.bootstrapDemoFixture).toHaveBeenCalledWith({ fixture_version: 1, operation_key: "demo:v1:bootstrap" });
  });
});
