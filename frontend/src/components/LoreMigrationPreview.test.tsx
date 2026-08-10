import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as apiModule from "@/services/api";
import { draftStorageKey, saveDraft } from "@/services/maintenanceDrafts";
import type { LoreMigrationPreviewResponse } from "@/types/lore";
import LoreMigrationPreview from "./LoreMigrationPreview";

const report: LoreMigrationPreviewResponse = {
  preview_schema_version: 1,
  mapping_version: 1,
  project_id: "project-1",
  storage_mode: "legacy",
  source_checksum: "a".repeat(64),
  semantic_result_checksum: "b".repeat(64),
  checked_at: "2026-08-07T04:40:00Z",
  overall_status: "review_required",
  dry_run: true,
  read_only: true,
  writes_performed: 0,
  commit_available: false,
  counts: {
    legacy_total: 2,
    mappable: 1,
    review_required: 1,
    possible_conflict: 0,
    blocked: 0,
  },
  by_legacy_category: { characters: 1, special_settings: 1 },
  by_target_type: { character: 1 },
  items: [{
    legacy_category: "characters",
    legacy_index: 0,
      legacy_id: null,
      item_fingerprint: "1".repeat(64),
    planned_element_id: "element-1",
    proposed_type_key: "character",
    name: "林岚",
    classification: "mappable",
    reason_codes: [],
    source_locator: "worldviews:project-1:characters:0",
    source_kind: "manual",
    source_label: "手动创建",
    exact_excerpt_available: false,
    original_value: { name: "林岚", personality: "沉稳" },
    mapped_fields: { personality: "沉稳" },
    unmapped_fields: [],
  }, {
    legacy_category: "special_settings",
    legacy_index: 0,
      legacy_id: null,
      item_fingerprint: "2".repeat(64),
    planned_element_id: "element-2",
    proposed_type_key: null,
    name: "夜禁",
    classification: "review_required",
    reason_codes: ["type_confirmation_required"],
    source_locator: "worldviews:project-1:special_settings:0",
    source_kind: "manual",
    source_label: "手动创建",
    exact_excerpt_available: false,
    original_value: { name: "夜禁" },
    mapped_fields: {},
    unmapped_fields: [],
  }],
  issues: [],
};

function mockPreview(value: Promise<LoreMigrationPreviewResponse>) {
  const getLoreMigrationPreview = vi.fn().mockReturnValue(value);
  vi.spyOn(apiModule, "api", "get").mockReturnValue({
    ...apiModule.api,
    getLoreMigrationPreview,
  });
  return getLoreMigrationPreview;
}

describe("LoreMigrationPreview", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
  });

  it("states zero-write boundaries and shows filterable item evidence", async () => {
    mockPreview(Promise.resolve(report));
    render(<LoreMigrationPreview projectId="project-1" userId="user-1" onBack={vi.fn()} onUpgraded={vi.fn()} />);

    expect(await screen.findByText("预检完成，仍有资料需要确认")).toBeInTheDocument();
    expect(screen.getByText("预检本身只检查数据，不会自动迁移。")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /立即迁移|确认迁移/ })).not.toBeInTheDocument();
    expect(screen.getByText("林岚")).toBeInTheDocument();
    expect(screen.getByText("夜禁")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /待确认/ }));
    expect(screen.queryByText("林岚")).not.toBeInTheDocument();
    expect(screen.getByText("夜禁")).toBeInTheDocument();
  });

  it("shows only the submitted migration stage and preserves its success receipt until exit", async () => {
    const operationKey = "lore-migration:preview-stage-0001";
    saveDraft({
      userId: "user-1",
      projectId: "project-1",
      kind: "lore-migration",
      objectId: "legacy-to-relational-v1",
    }, {
      version: 1,
      phase: "outcome_unknown",
      checkedAt: report.checked_at,
      legacyTotal: 2,
      input: {
        operation_key: operationKey,
        preview_schema_version: 1,
        mapping_version: 1,
        expected_source_checksum: "a".repeat(64),
        expected_semantic_result_checksum: "b".repeat(64),
        confirm_legacy_retained_no_automatic_rollback: true,
      },
    }, null);
    const getLoreMigrationPreview = vi.fn();
    vi.spyOn(apiModule, "api", "get").mockReturnValue({
      ...apiModule.api,
      getLoreMigrationPreview,
      getLoreMigrationOperationByKey: vi.fn().mockResolvedValue({
        id: "operation-1",
        project_id: "project-1",
        operation_key: operationKey,
        status: "ready",
        source_checksum: "a".repeat(64),
        preview_schema_version: 1,
        mapping_version: 1,
        semantic_result_checksum: "b".repeat(64),
        result_checksum: "c".repeat(64),
        migration_id: "migration-1",
        error_code: null,
        counts: { elements: 2 },
        started_at: "2026-08-07T08:00:01Z",
        updated_at: "2026-08-07T08:00:02Z",
        completed_at: "2026-08-07T08:00:02Z",
        replayed: true,
      }),
    });

    render(<LoreMigrationPreview projectId="project-1" userId="user-1" onBack={vi.fn()} onUpgraded={vi.fn()} />);

    expect(await screen.findByRole("heading", { name: "设定仓库升级完成" })).toBeInTheDocument();
    expect(screen.getByText("已迁移 2 项设定。")).toBeInTheDocument();
    expect(screen.queryByText("预检已通过")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "重新检查" })).not.toBeInTheDocument();
    expect(getLoreMigrationPreview).not.toHaveBeenCalled();
  });

  it("shows project-level blockers as a successful inspection result", async () => {
    mockPreview(Promise.resolve({
      ...report,
      overall_status: "blocked",
      issues: [{
        case_id: "case-1",
        severity: "blocked",
        reason_code: "existing_migration_state",
        legacy_category: null,
        legacy_index: null,
        message: "检测到既有迁移状态记录",
        recommended_action: "由开发者审查迁移历史。",
      }],
    }));
    render(<LoreMigrationPreview projectId="project-1" userId="user-1" onBack={vi.fn()} onUpgraded={vi.fn()} />);

    expect(await screen.findByText("本次预检未通过")).toBeInTheDocument();
    expect(screen.getByText("检测到既有迁移状态")).toBeInTheDocument();
    expect(screen.getByText("由开发者审查迁移历史。")).toBeInTheDocument();
  });

  it("explains maintenance and supports a safe manual retry", async () => {
    const getLoreMigrationPreview = vi.fn()
      .mockRejectedValueOnce(new apiModule.ApiError(503, { detail: "维护中" }))
      .mockResolvedValueOnce(report);
    vi.spyOn(apiModule, "api", "get").mockReturnValue({
      ...apiModule.api,
      getLoreMigrationPreview,
    });
    render(<LoreMigrationPreview projectId="project-1" userId="user-1" onBack={vi.fn()} onUpgraded={vi.fn()} />);

    expect(await screen.findByText(/服务暂不可用，预检未执行/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "重新检查" }));
    await waitFor(() => expect(getLoreMigrationPreview).toHaveBeenCalledTimes(2));
    expect(await screen.findByText("预检完成，仍有资料需要确认")).toBeInTheDocument();
  });

  it("returns to the repository without implying a migration", async () => {
    mockPreview(Promise.resolve(report));
    const onBack = vi.fn();
    render(<LoreMigrationPreview projectId="project-1" userId="user-1" onBack={onBack} onUpgraded={vi.fn()} />);
    await screen.findByText("预检完成，仍有资料需要确认");

    await userEvent.click(screen.getByRole("button", { name: "← 返回设定仓库" }));
    expect(onBack).toHaveBeenCalledOnce();
  });

  it("offers a safe editor handoff only for content-editable issues", async () => {
    const onEditItem = vi.fn();
    mockPreview(Promise.resolve({
      ...report,
      overall_status: "blocked",
      items: [{
        ...report.items[0],
        name: "",
        classification: "blocked",
        reason_codes: ["missing_name"],
      }, report.items[1]],
    }));
    render(
      <LoreMigrationPreview
        projectId="project-1"
        userId="user-1"
        onBack={vi.fn()}
        onUpgraded={vi.fn()}
        onEditItem={onEditItem}
      />
    );

    await screen.findByText("本次预检未通过");
    await userEvent.click(screen.getByRole("button", { name: "去修改这项原资料" }));
    expect(onEditItem).toHaveBeenCalledWith(
      "characters",
      0,
      "1".repeat(64),
      "a".repeat(64)
    );
    expect(screen.getAllByText("系统计划如何整理（仅预览）").length).toBeGreaterThan(0);
  });

  it("requires an explicit type choice, freezes the request, and only reloads preview after saving", async () => {
    const getLoreMigrationPreview = vi.fn().mockResolvedValue(report);
    const decideLoreMigrationResolution = vi.fn().mockResolvedValue({
      resolution: {
        id: "c".repeat(32),
        legacy_category: "special_settings",
        legacy_index: 0,
        reason_code: "type_confirmation_required",
        decision_code: "confirm_type",
        decision_payload: { type_key: "rule" },
        status: "active",
        lock_version: 1,
        created_at: "2026-08-07T10:00:00Z",
        updated_at: "2026-08-07T10:00:00Z",
      },
      operation_key: "migration-resolution:test-0001",
      replayed: false,
    });
    vi.spyOn(apiModule, "api", "get").mockReturnValue({
      ...apiModule.api,
      getLoreMigrationPreview,
      decideLoreMigrationResolution,
    });
    vi.spyOn(globalThis.crypto, "randomUUID").mockReturnValue("12345678-1234-4234-8234-123456789abc");
    render(<LoreMigrationPreview projectId="project-1" userId="user-1" onBack={vi.fn()} onUpgraded={vi.fn()} />);

    await screen.findByText("预检完成，仍有资料需要确认");
    await userEvent.click(screen.getByRole("button", { name: "选择迁移后的模块类型" }));
    expect(screen.getByRole("combobox", { name: /迁移后的模块类型/ })).toHaveValue("");

    await userEvent.click(screen.getByRole("button", { name: "保存迁移决定" }));
    expect(await screen.findByText("请先作出明确选择；系统不会替你默认确认。")).toBeInTheDocument();
    expect(decideLoreMigrationResolution).not.toHaveBeenCalled();

    await userEvent.selectOptions(screen.getByRole("combobox", { name: /迁移后的模块类型/ }), "rule");
    await userEvent.click(screen.getByRole("button", { name: "保存迁移决定" }));
    await waitFor(() => expect(decideLoreMigrationResolution).toHaveBeenCalledOnce());
    expect(decideLoreMigrationResolution.mock.calls[0][1]).toMatchObject({
      expected_source_checksum: "a".repeat(64),
      expected_semantic_result_checksum: "b".repeat(64),
      item_fingerprint: "2".repeat(64),
      reason_code: "type_confirmation_required",
      decision_code: "confirm_type",
      decision_payload: { type_key: "rule" },
      expected_resolution_version: null,
    });
    await waitFor(() => expect(getLoreMigrationPreview).toHaveBeenCalledTimes(2));
    expect(localStorage.length).toBe(0);
  });

  it("shows effective type and source labels without leaking internal keys", async () => {
    mockPreview(Promise.resolve({
      ...report,
      items: [{
        ...report.items[1],
        effective_proposed_type_key: "rule",
        effective_source_kind: "hybrid",
        effective_reason_codes: [],
      }],
      counts: { legacy_total: 1, mappable: 1, review_required: 0, possible_conflict: 0, blocked: 0 },
    }));
    render(<LoreMigrationPreview projectId="project-1" userId="user-1" onBack={vi.fn()} onUpgraded={vi.fn()} />);

    expect(await screen.findByText("规则与限制")).toBeInTheDocument();
    expect(screen.getByText("文档导入与手动补充（作者已确认）")).toBeInTheDocument();
    expect(screen.queryByText("rule")).not.toBeInTheDocument();
    expect(screen.queryByText("hybrid")).not.toBeInTheDocument();
  });

  it("uses the author's effective imported source and requires the current complete source to be opened", async () => {
    const evidenceReport: LoreMigrationPreviewResponse = {
      ...report,
      items: [{
        ...report.items[1],
        source_kind: "imported",
        source_label: "文档导入",
        effective_source_kind: "imported",
        reason_codes: ["raw_text_excerpt_unverified"],
        effective_reason_codes: ["raw_text_excerpt_unverified"],
      }],
      counts: { legacy_total: 1, mappable: 0, review_required: 1, possible_conflict: 0, blocked: 0 },
    };
    const getLoreMigrationPreview = vi.fn().mockResolvedValue(evidenceReport);
    const getWorldview = vi.fn().mockResolvedValue({
      id: "worldview-1",
      characters: [], geography: [], factions: [], power_system: [], history: [], conflicts: [], special_settings: [],
      parsed_elements: [], source: "manual", source_checksum: "a".repeat(64), raw_text: "第一段\n第二段",
    });
    const decideLoreMigrationResolution = vi.fn();
    vi.spyOn(apiModule, "api", "get").mockReturnValue({
      ...apiModule.api,
      getLoreMigrationPreview,
      getWorldview,
      decideLoreMigrationResolution,
    });
    render(<LoreMigrationPreview projectId="project-1" userId="user-1" onBack={vi.fn()} onUpgraded={vi.fn()} />);

    await screen.findByText("预检完成，仍有资料需要确认");
    await userEvent.click(screen.getByRole("button", { name: "人工确认未精确定位的原文来源" }));
    const checkbox = screen.getByRole("checkbox", { name: /我已阅读完整原文/ });
    expect(checkbox).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "加载并查看完整导入原文" }));
    const evidence = await screen.findByText("完整导入原文（未定位到本项）");
    expect(checkbox).toBeDisabled();
    await userEvent.click(evidence);
    expect(checkbox).toBeEnabled();
    expect(screen.getByText((_, element) => (
      element?.tagName === "PRE" && element.textContent === "第一段\n第二段"
    ))).toBeInTheDocument();
    expect(decideLoreMigrationResolution).not.toHaveBeenCalled();
  });

  it("does not offer imported-source evidence when the effective source is manual", async () => {
    const manualReport: LoreMigrationPreviewResponse = {
      ...report,
      items: [{
        ...report.items[1],
        source_kind: "manual",
        effective_source_kind: "manual",
        reason_codes: ["raw_text_excerpt_unverified"],
        effective_reason_codes: ["raw_text_excerpt_unverified"],
      }],
      counts: { legacy_total: 1, mappable: 0, review_required: 1, possible_conflict: 0, blocked: 0 },
    };
    const getWorldview = vi.fn();
    vi.spyOn(apiModule, "api", "get").mockReturnValue({
      ...apiModule.api,
      getLoreMigrationPreview: vi.fn().mockResolvedValue(manualReport),
      getWorldview,
    });
    render(<LoreMigrationPreview projectId="project-1" userId="user-1" onBack={vi.fn()} onUpgraded={vi.fn()} />);

    await screen.findByText("预检完成，仍有资料需要确认");
    await userEvent.click(screen.getByRole("button", { name: "人工确认未精确定位的原文来源" }));
    expect(screen.getByRole("button", { name: "加载并查看完整导入原文" })).toBeDisabled();
    expect(screen.getByText(/未被作者确认为导入资料/)).toBeInTheDocument();
    expect(getWorldview).not.toHaveBeenCalled();
  });

  it("clears only the confirmed corrupt item draft", async () => {
    const itemScope = { userId: "user-1", projectId: "project-1", kind: "lore-migration-resolution" as const, objectId: "2".repeat(64) };
    const otherScope = { ...itemScope, objectId: "9".repeat(64) };
    localStorage.setItem(draftStorageKey(itemScope), "damaged");
    localStorage.setItem(draftStorageKey(otherScope), "keep-me");
    vi.spyOn(window, "confirm").mockReturnValue(true);
    mockPreview(Promise.resolve(report));
    render(<LoreMigrationPreview projectId="project-1" userId="user-1" onBack={vi.fn()} onUpgraded={vi.fn()} />);

    await screen.findByText(/本地恢复记录已损坏/);
    await userEvent.click(screen.getByRole("button", { name: "清除这项损坏记录" }));
    expect(localStorage.getItem(draftStorageKey(itemScope))).toBeNull();
    expect(localStorage.getItem(draftStorageKey(otherScope))).toBe("keep-me");
    expect(screen.getByText(/其他草稿和旧世界观没有改变/)).toBeInTheDocument();
  });

  it("treats a structurally valid decide request for another item as corrupt without posting", async () => {
    const itemScope = { userId: "user-1", projectId: "project-1", kind: "lore-migration-resolution" as const, objectId: "2".repeat(64) };
    saveDraft(itemScope, {
      action: "decide",
      input: {
        operation_key: "migration-resolution:other-item",
        preview_schema_version: 1,
        mapping_version: 1,
        expected_source_checksum: "a".repeat(64),
        expected_semantic_result_checksum: "b".repeat(64),
        item_fingerprint: "9".repeat(64),
        group_fingerprint: null,
        legacy_category: "characters",
        legacy_index: 9,
        reason_code: "type_confirmation_required",
        decision_code: "confirm_type",
        decision_payload: { type_key: "rule" },
        expected_resolution_version: null,
      },
    }, "a".repeat(64));
    const decideLoreMigrationResolution = vi.fn();
    vi.spyOn(apiModule, "api", "get").mockReturnValue({
      ...apiModule.api,
      getLoreMigrationPreview: vi.fn().mockResolvedValue(report),
      decideLoreMigrationResolution,
    });
    render(<LoreMigrationPreview projectId="project-1" userId="user-1" onBack={vi.fn()} onUpgraded={vi.fn()} />);

    expect(await screen.findByText(/本地恢复记录已损坏/)).toBeInTheDocument();
    expect(decideLoreMigrationResolution).not.toHaveBeenCalled();
  });

  it("treats a revoke request for another resolution as corrupt without posting", async () => {
    const activeResolution = {
      id: "c".repeat(32),
      legacy_category: "special_settings",
      legacy_index: 0,
      reason_code: "type_confirmation_required",
      decision_code: "confirm_type",
      decision_payload: { type_key: "rule" },
      status: "active" as const,
      lock_version: 1,
      created_at: "2026-08-07T10:00:00Z",
      updated_at: "2026-08-07T10:00:00Z",
    };
    const resolvedReport: LoreMigrationPreviewResponse = {
      ...report,
      items: [{ ...report.items[1], resolution_states: [activeResolution] }],
      counts: { legacy_total: 1, mappable: 0, review_required: 1, possible_conflict: 0, blocked: 0 },
    };
    const itemScope = { userId: "user-1", projectId: "project-1", kind: "lore-migration-resolution" as const, objectId: "2".repeat(64) };
    saveDraft(itemScope, {
      action: "revoke",
      resolutionId: "d".repeat(32),
      input: {
        operation_key: "migration-resolution:other-resolution",
        expected_source_checksum: "a".repeat(64),
        expected_resolution_version: 1,
      },
    }, "a".repeat(64));
    const revokeLoreMigrationResolution = vi.fn();
    vi.spyOn(apiModule, "api", "get").mockReturnValue({
      ...apiModule.api,
      getLoreMigrationPreview: vi.fn().mockResolvedValue(resolvedReport),
      revokeLoreMigrationResolution,
    });
    render(<LoreMigrationPreview projectId="project-1" userId="user-1" onBack={vi.fn()} onUpgraded={vi.fn()} />);

    expect(await screen.findByText(/本地恢复记录已损坏/)).toBeInTheDocument();
    expect(revokeLoreMigrationResolution).not.toHaveBeenCalled();
  });

  it("freezes an unknown revoke and reuses the same operation on manual recovery", async () => {
    const activeResolution = {
      id: "c".repeat(32),
      legacy_category: "special_settings",
      legacy_index: 0,
      reason_code: "type_confirmation_required",
      decision_code: "confirm_type",
      decision_payload: { type_key: "rule" },
      status: "active" as const,
      lock_version: 1,
      created_at: "2026-08-07T10:00:00Z",
      updated_at: "2026-08-07T10:00:00Z",
      applies: true,
    };
    const resolvedReport: LoreMigrationPreviewResponse = {
      ...report,
      items: [{
        ...report.items[1],
        effective_proposed_type_key: "rule",
        effective_reason_codes: [],
        resolution_states: [activeResolution],
      }],
      counts: { legacy_total: 1, mappable: 1, review_required: 0, possible_conflict: 0, blocked: 0 },
    };
    const getLoreMigrationPreview = vi.fn().mockResolvedValue(resolvedReport);
    const revokeLoreMigrationResolution = vi.fn()
      .mockRejectedValueOnce(new Error("network"))
      .mockResolvedValueOnce({ resolution: { ...activeResolution, status: "revoked" }, operation_key: "same", replayed: true });
    vi.spyOn(apiModule, "api", "get").mockReturnValue({
      ...apiModule.api,
      getLoreMigrationPreview,
      revokeLoreMigrationResolution,
    });
    vi.spyOn(globalThis.crypto, "randomUUID").mockReturnValue("12345678-1234-4234-8234-123456789abc");
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const view = render(<LoreMigrationPreview projectId="project-1" userId="user-1" onBack={vi.fn()} onUpgraded={vi.fn()} />);

    await screen.findByText("已作出决定");
    await userEvent.click(screen.getByRole("button", { name: "撤销决定" }));
    expect(await screen.findByText(/无法确认撤销是否完成/)).toBeInTheDocument();
    const firstCall = revokeLoreMigrationResolution.mock.calls[0];
    view.unmount();
    render(<LoreMigrationPreview projectId="project-1" userId="user-1" onBack={vi.fn()} onUpgraded={vi.fn()} />);
    await userEvent.click(await screen.findByRole("button", { name: "使用原请求核对撤销结果" }));
    await waitFor(() => expect(revokeLoreMigrationResolution).toHaveBeenCalledTimes(2));
    expect(revokeLoreMigrationResolution.mock.calls[1]).toEqual(firstCall);
  });
});
