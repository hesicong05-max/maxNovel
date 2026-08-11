import { describe, expect, it } from "vitest";
import { generationRunContractError } from "./generationRuns";
import type { GenerationRunResponse } from "@/types/generation";

const id = (value: string) => value.padEnd(32, value).slice(0, 32);
const projectId = id("project");
const planId = id("plan");
const chapterId = id("chapter");
const elementId = id("element");
const typeId = id("type");

function run(): GenerationRunResponse {
  return {
    id: id("run"), project_id: projectId, plan_id: planId, planning_chapter_id: chapterId,
    operation_key: "planning:generation_prepare:12345678", replayed: false,
    status: "prepared", execution_mode: "preflight_only", ai_invoked: false, billing_effect: "none",
    structure_version: 2, assignment_version: 3, chapter_lock_version: 4, context_schema_version: 1,
    context_checksum: "a".repeat(64), context_size_bytes: 1024,
    created_at: "2026-08-11T05:00:00Z", updated_at: "2026-08-11T05:00:00Z",
    context_manifest: {
      schema_version: 1, project_id: projectId, plan_id: planId,
      versions: { structure: 2, assignment: 3, chapter_lock: 4 },
      part: { id: id("part"), title: "第一篇", description: "", position: 1, lock_version: 1 },
      chapter: { id: chapterId, title: "第一章", summary: "起点", target_word_count: 2000, position: 1, lock_version: 4 },
      elements: [{
        element_id: elementId,
        type: { id: typeId, key: "character", display_name: "角色", schema_revision: 1 },
        version: { id: id("version"), element_id: elementId, type_id: typeId, version_no: 7, name: "沈星", summary: "主角", payload: {}, field_states: {}, source_id: null },
        assignment_sources: [{ assignment_id: id("assignment"), scope_type: "chapter", scope_target_id: chapterId, scope_title: "第一章", assignment_lock_version: 1, assigned_at_content_version: 7 }],
      }],
      relations: [], warnings: [], foreshadow_actions: { supported: false, items: [] },
      counts: { elements: 1, relations: 0, warnings: 0 },
    },
  };
}

const expected = {
  projectId,
  chapterId,
  operationKey: "planning:generation_prepare:12345678",
  payload: {
    operation_key: "planning:generation_prepare:12345678",
    expected_structure_version: 2,
    expected_assignment_version: 3,
    expected_chapter_lock_version: 4,
  },
};

describe("generation run runtime contract", () => {
  it("accepts a matching zero-AI durable preflight receipt", () => {
    expect(generationRunContractError(run(), expected)).toBeNull();
  });

  it("fails closed when billing, chapter identity, or request versions differ", () => {
    expect(generationRunContractError({ ...run(), billing_effect: "charged" }, expected)).toContain("零 AI、零费用");
    expect(generationRunContractError({ ...run(), planning_chapter_id: id("other") }, expected)).toContain("不一致");
    expect(generationRunContractError({ ...run(), assignment_version: 9 }, expected)).toContain("原请求版本");
  });

  it("rejects count drift, duplicate elements, and unsupported foreshadow actions", () => {
    const countDrift = run();
    countDrift.context_manifest.counts.elements = 2;
    expect(generationRunContractError(countDrift, expected)).toContain("计数不一致");

    const duplicate = run();
    duplicate.context_manifest.elements.push(duplicate.context_manifest.elements[0]);
    duplicate.context_manifest.counts.elements = 2;
    expect(generationRunContractError(duplicate, expected)).toContain("无效或重复");

    const foreshadow = run() as unknown as Record<string, unknown>;
    (foreshadow.context_manifest as Record<string, unknown>).foreshadow_actions = { supported: true, items: [{}] };
    expect(generationRunContractError(foreshadow, expected)).toContain("伏笔动作");
  });
});
