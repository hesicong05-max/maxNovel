import { describe, expect, it } from "vitest";
import { parseForeshadowHistory, parseForeshadowList, parseForeshadowReceipt } from "./foreshadowContracts";

const id = (value: string) => value.padEnd(32, value).slice(0, 32);
const projectId = id("project");
const lifecycleId = id("lifecycle");
const now = "2026-08-11T08:00:00Z";

function lifecycle() {
  return {
    id: lifecycleId, project_id: projectId, plan_id: id("plan"), status: "active", state: "unplanted", lock_version: 1,
    element: { id: id("element"), name: "黑羽", summary: "未解释的羽毛", confirmation_status: "confirmed", lifecycle_status: "active", enabled: true, content_version: 1, lock_version: 2 },
    plans: [], facts: [], created_at: now, updated_at: now,
  };
}

describe("foreshadow runtime contracts", () => {
  it("accepts an exact list and rejects cross-project or extra fields", () => {
    const value = { items: [lifecycle()], counts: { unplanted: 1, planted: 0, pending_resolution: 0, resolved: 0 }, next_cursor: null };
    expect(parseForeshadowList(value, projectId).items[0].id).toBe(lifecycleId);
    expect(() => parseForeshadowList({ ...value, extra: true }, projectId)).toThrow(/列表响应/);
    expect(() => parseForeshadowList({ ...value, items: [{ ...lifecycle(), project_id: id("other") }] }, projectId)).toThrow(/身份/);
  });

  it("rejects malformed fact targets and unknown states", () => {
    const malformed = lifecycle();
    malformed.facts = [{ id: id("fact"), fact_kind: "planted", chapter: { target_type: "part", target_id: id("part"), title: "第一篇", status: "active", part_id: null, position: 1 }, note: "", status: "active", lock_version: 1, created_at: now, retracted_at: null }] as never[];
    expect(() => parseForeshadowList({ items: [malformed], counts: { unplanted: 1, planted: 0, pending_resolution: 0, resolved: 0 }, next_cursor: null }, projectId)).toThrow();
    expect(() => parseForeshadowList({ items: [{ ...lifecycle(), state: "strengthened" }], counts: { unplanted: 1, planted: 0, pending_resolution: 0, resolved: 0 }, next_cursor: null }, projectId)).toThrow();
  });

  it("cross-checks receipt identity, operation type, lifecycle and version", () => {
    const value = {
      receipt_id: id("receipt"), operation_key: "foreshadow_bind:key12345", operation_type: "foreshadow_bind", replayed: false,
      project_id: projectId, lifecycle_id: lifecycleId, previous_lifecycle_version: 0, new_lifecycle_version: 1,
      event_id: id("event"), lifecycle: lifecycle(), created_at: now,
    };
    const expected = { projectId, operationKey: value.operation_key, operationType: "foreshadow_bind" as const, lifecycleId: null, elementId: lifecycle().element.id };
    expect(parseForeshadowReceipt(value, expected).lifecycle.id).toBe(lifecycleId);
    expect(() => parseForeshadowReceipt({ ...value, operation_type: "foreshadow_archive" }, expected)).toThrow(/原请求/);
    expect(() => parseForeshadowReceipt({ ...value, new_lifecycle_version: 2 }, expected)).toThrow(/生命周期版本/);
    expect(() => parseForeshadowReceipt({ ...value, lifecycle: { ...lifecycle(), id: id("other") } }, expected)).toThrow(/生命周期版本|原请求/);
    expect(() => parseForeshadowReceipt({ ...value, lifecycle: { ...lifecycle(), element: { ...lifecycle().element, id: id("wrong-element") } } }, expected)).toThrow(/设定身份/);
  });

  it("validates history identity and exact events", () => {
    const value = { lifecycle_id: lifecycleId, items: [{ id: id("event"), event_kind: "create", plan_item_id: null, fact_id: null, previous_lifecycle_version: 0, new_lifecycle_version: 1, metadata: {}, created_at: now }] };
    expect(parseForeshadowHistory(value, lifecycleId).items).toHaveLength(1);
    expect(() => parseForeshadowHistory({ ...value, lifecycle_id: id("other") }, lifecycleId)).toThrow(/历史/);
    expect(() => parseForeshadowHistory({ ...value, items: [{ ...value.items[0], event_kind: "deleted" }] }, lifecycleId)).toThrow(/历史/);
  });
});
