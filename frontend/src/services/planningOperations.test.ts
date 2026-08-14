import { beforeEach, describe, expect, it, vi } from "vitest";
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
    const operationKey = createPlanningOperationKey("part_create");
    const operation = {
      schema_version: 1 as const,
      user_id: "user-1",
      project_id: "project-1",
      operation_key: operationKey,
      action: "part_create" as const,
      target_id: null,
      payload: { operation_key: operationKey, expected_structure_version: 1, title: "第一篇", description: "" },
      created_at: "2026-08-10T00:00:00.000Z",
    };
    expect(savePendingPlanningOperation(operation)).toBe(true);
    expect(loadPendingPlanningOperation("user-1", "project-1")).toEqual({ status: "available", operation });
    expect(loadPendingPlanningOperation("user-2", "project-1")).toEqual({ status: "missing" });
    expect(loadPendingPlanningOperation("user-1", "project-2")).toEqual({ status: "missing" });
    expect(clearPendingPlanningOperation("user-1", "project-1")).toBe(true);
    expect(loadPendingPlanningOperation("user-1", "project-1")).toEqual({ status: "missing" });
  });

  it("keeps ambiguous network and server results but clears explicit client failures", () => {
    expect(shouldKeepPlanningOperation(new TypeError("network failed"))).toBe(true);
    expect(shouldKeepPlanningOperation(Object.assign(new Error("server"), { status: 500 }))).toBe(true);
    expect(shouldKeepPlanningOperation(Object.assign(new Error("conflict"), { status: 409 }))).toBe(false);
  });

  it("fails closed for malformed or unsupported stored values", () => {
    sessionStorage.setItem("novel_pending_planning_operation_v1:u:p", "{broken");
    expect(loadPendingPlanningOperation("u", "p")).toEqual({ status: "corrupt" });
    sessionStorage.setItem("novel_pending_planning_operation_v1:u:p", JSON.stringify({
      schema_version: 1, user_id: "u", project_id: "p", operation_key: "planning:unknown:12345678",
      action: "unknown_action", target_id: null, payload: { operation_key: "planning:unknown:12345678" }, created_at: "now",
    }));
    expect(loadPendingPlanningOperation("u", "p")).toEqual({ status: "corrupt" });
  });

  it("validates assignment recovery identity and exact payload", () => {
    const operation = {
      schema_version: 1 as const,
      user_id: "u",
      project_id: "p",
      operation_key: "planning:assignment_remove:12345678",
      action: "assignment_remove" as const,
      target_id: "assignment-1",
      payload: {
        operation_key: "planning:assignment_remove:12345678",
        expected_assignment_version: 2,
        expected_lock_version: 1,
        scope_type: "chapter",
        scope_target_id: "chapter-1",
      },
      created_at: "2026-08-11T00:00:00Z",
    };
    expect(savePendingPlanningOperation(operation)).toBe(true);
    expect(loadPendingPlanningOperation("u", "p")).toEqual({ status: "available", operation });
    sessionStorage.setItem("novel_pending_planning_operation_v1:u:p", JSON.stringify({
      ...operation,
      payload: { ...operation.payload, operation_key: "different-key" },
    }));
    expect(loadPendingPlanningOperation("u", "p")).toEqual({ status: "corrupt" });
  });

  it("rejects extra fields, incomplete chapter updates, and mismatched assignment targets", () => {
    const base = {
      schema_version: 1 as const,
      user_id: "u",
      project_id: "p",
      operation_key: "planning:assignment_create:strict-12345678",
      action: "assignment_create" as const,
      target_id: "element-1",
      payload: {
        operation_key: "planning:assignment_create:strict-12345678",
        expected_assignment_version: 1,
        element_id: "element-1",
        expected_element_content_version: 1,
        scope_type: "novel",
        scope_target_id: "p",
      },
      created_at: "2026-08-11T00:00:00Z",
    };
    sessionStorage.setItem("novel_pending_planning_operation_v1:u:p", JSON.stringify({ ...base, unexpected: true }));
    expect(loadPendingPlanningOperation("u", "p")).toEqual({ status: "corrupt" });
    sessionStorage.setItem("novel_pending_planning_operation_v1:u:p", JSON.stringify({ ...base, payload: { ...base.payload, unexpected: true } }));
    expect(loadPendingPlanningOperation("u", "p")).toEqual({ status: "corrupt" });
    sessionStorage.setItem("novel_pending_planning_operation_v1:u:p", JSON.stringify({ ...base, target_id: "different-element" }));
    expect(loadPendingPlanningOperation("u", "p")).toEqual({ status: "corrupt" });
    sessionStorage.setItem("novel_pending_planning_operation_v1:u:p", JSON.stringify({
      ...base,
      operation_key: "planning:chapter_update:strict-12345678",
      action: "chapter_update",
      target_id: "chapter-1",
      payload: {
        operation_key: "planning:chapter_update:strict-12345678",
        expected_structure_version: 1,
        expected_lock_version: 1,
      },
    }));
    expect(loadPendingPlanningOperation("u", "p")).toEqual({ status: "corrupt" });
  });

  it("strictly validates a generation prepare command and chapter target", () => {
    const chapterId = "c".repeat(32);
    const operation = {
      schema_version: 1 as const,
      user_id: "u",
      project_id: "p",
      operation_key: "planning:generation_prepare:12345678",
      action: "generation_prepare" as const,
      target_id: chapterId,
      payload: {
        operation_key: "planning:generation_prepare:12345678",
        expected_structure_version: 3,
        expected_assignment_version: 2,
        expected_chapter_lock_version: 1,
      },
      created_at: "2026-08-11T13:50:00Z",
    };

    expect(savePendingPlanningOperation(operation)).toBe(true);
    expect(loadPendingPlanningOperation("u", "p")).toEqual({
      status: "available",
      operation,
    });

    for (const invalid of [
      { ...operation, target_id: "chapter-1" },
      { ...operation, target_id: null },
      { ...operation, payload: { ...operation.payload, unexpected: true } },
      { ...operation, payload: { ...operation.payload, expected_structure_version: 0 } },
      { ...operation, payload: { ...operation.payload, expected_assignment_version: 1.5 } },
      { ...operation, payload: { ...operation.payload, expected_chapter_lock_version: -1 } },
      { ...operation, payload: { ...operation.payload, operation_key: "different:key" } },
    ]) {
      sessionStorage.setItem(
        "novel_pending_planning_operation_v1:u:p",
        JSON.stringify(invalid)
      );
      expect(loadPendingPlanningOperation("u", "p")).toEqual({ status: "corrupt" });
    }
  });

  it("recognizes generation v3 as foreign and refuses to overwrite shared work", () => {
    const key = "novel_pending_planning_operation_v1:u:p";
    const generation = {
      schema_version: 3,
      workspace: "generation_execution",
      operation_key: "generation:execute:12345678",
    };
    sessionStorage.setItem(key, JSON.stringify(generation));
    expect(loadPendingPlanningOperation("u", "p")).toEqual({
      status: "foreign",
      workspace: "generation_execution",
    });
    const operation = {
      schema_version: 1 as const,
      user_id: "u",
      project_id: "p",
      operation_key: "planning:part_create:12345678",
      action: "part_create" as const,
      target_id: null,
      payload: {
        operation_key: "planning:part_create:12345678",
        expected_structure_version: 1,
        title: "第一篇",
        description: "",
      },
      created_at: "2026-08-13T08:00:00Z",
    };
    expect(savePendingPlanningOperation(operation)).toBe(false);
    expect(JSON.parse(sessionStorage.getItem(key)!)).toEqual(generation);

    const foreshadow = {
      schema_version: 2,
      workspace: "foreshadow",
      operation_key: "foreshadow:write:12345678",
    };
    sessionStorage.setItem(key, JSON.stringify(foreshadow));
    expect(savePendingPlanningOperation(operation)).toBe(false);
    expect(JSON.parse(sessionStorage.getItem(key)!)).toEqual(foreshadow);

    sessionStorage.clear();
    expect(savePendingPlanningOperation(operation)).toBe(true);
    expect(savePendingPlanningOperation(operation)).toBe(true);
    expect(savePendingPlanningOperation({
      ...operation,
      operation_key: "planning:part_create:other123",
      payload: { ...operation.payload, operation_key: "planning:part_create:other123" },
    })).toBe(false);
    expect(loadPendingPlanningOperation("u", "p")).toEqual({
      status: "available",
      operation,
    });
  });

  it("distinguishes unavailable session storage from a missing operation", () => {
    vi.stubGlobal("sessionStorage", {
      getItem: () => { throw new DOMException("blocked", "SecurityError"); },
    });
    expect(loadPendingPlanningOperation("u", "p")).toEqual({ status: "unavailable" });
    vi.unstubAllGlobals();
  });

  it("recognizes candidate selection v6 as foreign", () => {
    sessionStorage.setItem(
      "novel_pending_planning_operation_v1:u:p",
      JSON.stringify({ schema_version: 6, workspace: "candidate_selection" })
    );
    expect(loadPendingPlanningOperation("u", "p")).toEqual({
      status: "foreign", workspace: "candidate_selection",
    });
  });
});
