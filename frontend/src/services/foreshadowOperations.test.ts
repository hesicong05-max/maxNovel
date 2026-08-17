import { beforeEach, describe, expect, it, vi } from "vitest";
import { loadPendingPlanningOperation } from "./planningOperations";
import {
  clearPendingForeshadowOperation,
  createForeshadowOperationKey,
  loadPendingForeshadowOperation,
  savePendingForeshadowOperation,
  type PendingForeshadowOperation,
} from "./foreshadowOperations";

const id = (value: string) => value.padEnd(32, value).slice(0, 32);

function pending(): PendingForeshadowOperation {
  const operationKey = "foreshadow_plan_cancel:key12345";
  return {
    schema_version: 2, workspace: "foreshadow", user_id: "user-1", project_id: "project-1",
    operation_key: operationKey, operation_type: "foreshadow_plan_cancel", lifecycle_id: id("lifecycle"), resource_id: id("item"),
    payload: { operation_key: operationKey, expected_lifecycle_version: 2, expected_structure_version: 3, expected_item_lock_version: 1 },
    created_at: "2026-08-11T08:00:00Z",
  };
}

describe("foreshadow pending operations", () => {
  beforeEach(() => { sessionStorage.clear(); vi.restoreAllMocks(); });

  it("stores a strict v2 operation and exposes it as foreign to planning", () => {
    const value = pending();
    expect(savePendingForeshadowOperation(value)).toBe(true);
    expect(loadPendingForeshadowOperation("user-1", "project-1")).toEqual({ status: "available", operation: value });
    expect(loadPendingPlanningOperation("user-1", "project-1")).toEqual({ status: "foreign", workspace: "foreshadow" });
  });

  it("refuses to overwrite another operation in the shared slot", () => {
    const first = pending();
    expect(savePendingForeshadowOperation(first)).toBe(true);
    expect(savePendingForeshadowOperation({ ...first, operation_key: "foreshadow_plan_cancel:other123", payload: { ...first.payload, operation_key: "foreshadow_plan_cancel:other123" } })).toBe(false);
    expect(loadPendingForeshadowOperation("user-1", "project-1")).toEqual({ status: "available", operation: first });
  });

  it("fails closed for unknown fields, invalid ids and payload drift", () => {
    const value = pending();
    sessionStorage.setItem("novel_pending_planning_operation_v1:user-1:project-1", JSON.stringify({ ...value, extra: true }));
    expect(loadPendingForeshadowOperation("user-1", "project-1")).toEqual({ status: "corrupt" });
    sessionStorage.setItem("novel_pending_planning_operation_v1:user-1:project-1", JSON.stringify({ ...value, resource_id: "short" }));
    expect(loadPendingForeshadowOperation("user-1", "project-1")).toEqual({ status: "corrupt" });
    sessionStorage.setItem("novel_pending_planning_operation_v1:user-1:project-1", JSON.stringify({ ...value, payload: { ...value.payload, operation_key: "different" } }));
    expect(loadPendingForeshadowOperation("user-1", "project-1")).toEqual({ status: "corrupt" });
  });

  it("recognizes legacy planning as foreign and clears only the matching key", () => {
    sessionStorage.setItem("novel_pending_planning_operation_v1:user-1:project-1", JSON.stringify({ schema_version: 1 }));
    expect(loadPendingForeshadowOperation("user-1", "project-1")).toEqual({ status: "foreign", workspace: "planning" });
    sessionStorage.clear();
    const value = pending();
    savePendingForeshadowOperation(value);
    expect(clearPendingForeshadowOperation("user-1", "project-1", "wrong")).toBe(false);
    expect(clearPendingForeshadowOperation("user-1", "project-1", value.operation_key)).toBe(true);
    expect(loadPendingForeshadowOperation("user-1", "project-1")).toEqual({ status: "missing" });
  });

  it("recognizes generation v3 as foreign and never overwrites it", () => {
    const key = "novel_pending_planning_operation_v1:user-1:project-1";
    const generation = {
      schema_version: 3,
      workspace: "generation_execution",
      operation_key: "generation:execute:12345678",
    };
    sessionStorage.setItem(key, JSON.stringify(generation));
    expect(loadPendingForeshadowOperation("user-1", "project-1")).toEqual({
      status: "foreign",
      workspace: "generation_execution",
    });
    expect(savePendingForeshadowOperation(pending())).toBe(false);
    expect(JSON.parse(sessionStorage.getItem(key)!)).toEqual(generation);

    const planning = {
      schema_version: 1,
      operation_key: "planning:write:12345678",
    };
    sessionStorage.setItem(key, JSON.stringify(planning));
    expect(savePendingForeshadowOperation(pending())).toBe(false);
    expect(JSON.parse(sessionStorage.getItem(key)!)).toEqual(planning);
  });

  it("reports unavailable storage and requires secure random keys", () => {
    vi.spyOn(Object.getPrototypeOf(sessionStorage) as Storage, "getItem").mockImplementation(() => { throw new Error("blocked"); });
    expect(loadPendingForeshadowOperation("user-1", "project-1")).toEqual({ status: "unavailable" });
    vi.restoreAllMocks();
    expect(createForeshadowOperationKey("foreshadow_bind")).toMatch(/^foreshadow_bind:/);
  });

  it("recognizes candidate selection v6 as foreign", () => {
    sessionStorage.setItem(
      "novel_pending_planning_operation_v1:user-1:project-1",
      JSON.stringify({ schema_version: 6, workspace: "candidate_selection" })
    );
    expect(loadPendingForeshadowOperation("user-1", "project-1")).toEqual({
      status: "foreign", workspace: "candidate_selection",
    });
  });
});
