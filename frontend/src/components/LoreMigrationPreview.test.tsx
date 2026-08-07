import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as apiModule from "@/services/api";
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
  beforeEach(() => vi.restoreAllMocks());

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
});
