import { beforeEach, describe, expect, it } from "vitest";
import {
  clearPendingPlanningOperation,
  createPlanningOperationKey,
  loadPendingPlanningOperation,
  savePendingPlanningOperation,
  shouldKeepPlanningOperation,
} from "./planningOperations";

describe("planning operation recovery", () => {
  beforeEach(() => sessionStorage.clear());

  it("isolates pending operations by user and project", () => {
    const operation = {
      schema_version: 1 as const,
      user_id: "user-1",
      project_id: "project-1",
      operation_key: createPlanningOperationKey("part_create"),
      action: "part_create",
      target_id: null,
      payload: { operation_key: "same-key", title: "第一篇" },
      created_at: "2026-08-10T00:00:00.000Z",
    };
    expect(savePendingPlanningOperation(operation)).toBe(true);
    expect(loadPendingPlanningOperation("user-1", "project-1")).toEqual(operation);
    expect(loadPendingPlanningOperation("user-2", "project-1")).toBeNull();
    expect(loadPendingPlanningOperation("user-1", "project-2")).toBeNull();
    expect(clearPendingPlanningOperation("user-1", "project-1")).toBe(true);
    expect(loadPendingPlanningOperation("user-1", "project-1")).toBeNull();
  });

  it("keeps ambiguous network and server results but clears explicit client failures", () => {
    expect(shouldKeepPlanningOperation(new TypeError("network failed"))).toBe(true);
    expect(shouldKeepPlanningOperation(Object.assign(new Error("server"), { status: 500 }))).toBe(true);
    expect(shouldKeepPlanningOperation(Object.assign(new Error("conflict"), { status: 409 }))).toBe(false);
  });

  it("ignores malformed or foreign stored values", () => {
    sessionStorage.setItem("novel_pending_planning_operation_v1:u:p", "{broken");
    expect(loadPendingPlanningOperation("u", "p")).toBeNull();
  });
});
