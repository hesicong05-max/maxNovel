import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as apiModule from "@/services/api";
import type { LoreElementDetail, LoreMergeOperation, LoreMergePreviewResponse, LoreReviewDetail, LoreTypeDefinition } from "@/types/lore";
import LoreMergeWizard from "./LoreMergeWizard";

const loreType: LoreTypeDefinition = {
  id: "type-1", key: "character", display_name: "角色", description: "",
  field_schema: [{ key: "personality", label: "性格", control: "textarea", value_type: "string", help: "", order: 1, required: false }],
  is_builtin: true, schema_revision: 1, status: "active",
  created_at: "2026-08-07T00:00:00Z", updated_at: "2026-08-07T00:00:00Z",
};

const left: LoreElementDetail = {
  id: "left-1", type: { key: "character", display_name: "角色" }, name: "林岚", summary: "旧摘要",
  confirmation_status: "confirmed", lifecycle_status: "active", enabled: true, generation_eligible: true,
  source_summary: "手动创建", current_version: 1, revision: 1, lock_version: 1,
  updated_at: "2026-08-07T00:00:00Z", relation_count: 0,
  payload: { personality: "谨慎" }, field_states: { personality: "provided" },
  field_definitions: loreType.field_schema, sources: [], version_count: 1, read_only: false,
};

const right: LoreElementDetail = {
  ...left, id: "right-1", name: "林岚（旧）", summary: "新摘要", current_version: 2, lock_version: 2,
  payload: { personality: "冲动" }, source_summary: "文档导入",
};

const detail: LoreReviewDetail = {
  id: "review-1", origin: "system_scan", kind: "possible_duplicate", detection_state: "active",
  review_status: "confirmed_duplicate", needs_review: false, lock_version: 2, evidence_revision: 1,
  left: { id: left.id, name: left.name, type: left.type, summary: left.summary, lifecycle_status: "active", enabled: true },
  right: { id: right.id, name: right.name, type: right.type, summary: right.summary, lifecycle_status: "active", enabled: true },
  primary_reason: "名称相同", stale: false, merge_allowed: true, merge_block_reason: null, updated_at: "2026-08-07T00:00:00Z",
  rule_key: "same_name", rule_version: 1,
  left_snapshot: { ...left, field_states: { personality: "provided" }, content_version: left.current_version, sources: [] },
  right_snapshot: { ...right, field_states: { personality: "provided" }, content_version: right.current_version, sources: [] },
  evidence: [], decided_evidence_revision: 1, history: [],
};

const preview: LoreMergePreviewResponse = {
  suggestion_id: detail.id,
  survivor: detail.left_snapshot,
  merged: detail.right_snapshot,
  final_name: left.name,
  final_summary: left.summary,
  final_payload: { personality: "冲动" },
  final_field_states: { personality: "provided" },
  selection_snapshot: {},
  source_impact: { survivor_source_count: 1, merged_source_count: 1, preserved_total: 2, exact_duplicate_pairs: 0, strategy: "preserve_in_place" },
  relation_plan: [], blockers: [], would_be_generation_eligible: true,
  preview_token: "signed.preview", expires_at: "2026-08-07T01:00:00Z", commit_available: true,
};

const operation: LoreMergeOperation = {
  id: "merge-op-1", project_id: "project-1", operation_key: "merge-operation-key-1", suggestion_id: detail.id,
  evidence_revision: 1, survivor_element_id: left.id, merged_element_id: right.id,
  survivor_before_content_version: 1, survivor_before_lock_version: 1,
  survivor_after_content_version: 2, survivor_after_lock_version: 2,
  merged_before_content_version: 2, merged_before_lock_version: 2, merged_after_lock_version: 3,
  selection_snapshot: {}, impact_summary: { physical_deletions: 0 }, relation_actions: [],
  created_at: "2026-08-07T00:30:00Z", replayed: false,
};

function renderWizard(overrides: Partial<React.ComponentProps<typeof LoreMergeWizard>> = {}) {
  const onMerged = vi.fn();
  render(<LoreMergeWizard
    projectId="project-1" userId="user-1" detail={detail} loreTypes={[loreType]}
    enabled readOnly={false} onDirtyChange={vi.fn()} onBusyChange={vi.fn()} onMerged={onMerged}
    {...overrides}
  />);
  return { onMerged };
}

describe("LoreMergeWizard", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.spyOn(apiModule, "api", "get").mockReturnValue({
      ...apiModule.api,
      getLoreElement: vi.fn().mockImplementation((_projectId, elementId) => Promise.resolve(elementId === left.id ? left : right)),
      previewLoreMerge: vi.fn().mockResolvedValue(preview),
      commitLoreMerge: vi.fn().mockResolvedValue(operation),
      getLoreMergeOperationByKey: vi.fn(),
    });
  });

  it("keeps the collapsed merge entry passive", () => {
    renderWizard();

    const entry = screen.getByRole("region", { name: "合并重复设定" });
    expect(entry).toHaveClass("lore-merge-entry");
    expect(entry.querySelector(":scope > .lore-note")).toBeInTheDocument();
    expect(apiModule.api.getLoreElement).not.toHaveBeenCalled();
    expect(apiModule.api.previewLoreMerge).not.toHaveBeenCalled();
    expect(apiModule.api.commitLoreMerge).not.toHaveBeenCalled();
    expect(apiModule.api.getLoreMergeOperationByKey).not.toHaveBeenCalled();
  });

  it("renders an explicit preview as a read-only impact receipt", async () => {
    const getLoreElement = vi.fn().mockImplementation((_projectId, elementId) => Promise.resolve(elementId === left.id ? left : right));
    const previewLoreMerge = vi.fn().mockResolvedValue(preview);
    const commitLoreMerge = vi.fn();
    const getLoreMergeOperationByKey = vi.fn();
    vi.spyOn(apiModule, "api", "get").mockReturnValue({
      ...apiModule.api,
      getLoreElement,
      previewLoreMerge,
      commitLoreMerge,
      getLoreMergeOperationByKey,
    });
    renderWizard();

    await userEvent.click(screen.getByRole("button", { name: "开始合并" }));
    await userEvent.click(await screen.findByLabelText(/保留“林岚”/));
    await userEvent.click(screen.getByLabelText("采用保留项：林岚"));
    await userEvent.click(screen.getByLabelText("采用保留项：旧摘要"));
    await userEvent.click(screen.getByLabelText("采用另一项：冲动"));
    await userEvent.click(screen.getByRole("button", { name: "生成合并预览" }));

    const heading = await screen.findByRole("heading", { name: "检查合并结果" });
    const receipt = heading.closest(".lore-merge-preview");
    expect(receipt).not.toBeNull();
    expect(receipt?.querySelector(":scope > dl")).toBeInTheDocument();
    expect(receipt?.querySelector(":scope > details")).toBeInTheDocument();
    expect(receipt?.querySelector(":scope > .lore-merge-relations")).toBeInTheDocument();
    expect(receipt?.querySelector(":scope > .lore-meta")).toBeInTheDocument();
    expect(receipt?.querySelector(":scope > .lore-merge-actions")).toBeInTheDocument();
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(getLoreElement).toHaveBeenCalledTimes(2);
    expect(previewLoreMerge).toHaveBeenCalledTimes(1);
    expect(commitLoreMerge).not.toHaveBeenCalled();
    expect(getLoreMergeOperationByKey).not.toHaveBeenCalled();
  });

  it("requires an explicit keeper and every field choice before preview", async () => {
    renderWizard();
    await userEvent.click(screen.getByRole("button", { name: "开始合并" }));
    expect(await screen.findByRole("group", { name: "选择保留项（不默认选择）" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "生成合并预览" })).toBeDisabled();
    await userEvent.click(screen.getByLabelText(/保留“林岚”/));
    await userEvent.click(screen.getByLabelText("采用保留项：林岚"));
    await userEvent.click(screen.getByLabelText("采用保留项：旧摘要"));
    await userEvent.click(screen.getByLabelText("采用另一项：冲动"));
    expect(screen.getByRole("button", { name: "生成合并预览" })).toBeEnabled();
  });

  it("previews, confirms and commits without describing the loser as deleted", async () => {
    const commit = vi.fn().mockResolvedValue(operation);
    vi.spyOn(apiModule, "api", "get").mockReturnValue({
      ...apiModule.api,
      getLoreElement: vi.fn().mockImplementation((_projectId, elementId) => Promise.resolve(elementId === left.id ? left : right)),
      previewLoreMerge: vi.fn().mockResolvedValue(preview),
      commitLoreMerge: commit,
      getLoreMergeOperationByKey: vi.fn(),
    });
    const { onMerged } = renderWizard();
    await userEvent.click(screen.getByRole("button", { name: "开始合并" }));
    await userEvent.click(await screen.findByLabelText(/保留“林岚”/));
    await userEvent.click(screen.getByLabelText("采用保留项：林岚"));
    await userEvent.click(screen.getByLabelText("采用保留项：旧摘要"));
    await userEvent.click(screen.getByLabelText("采用另一项：冲动"));
    await userEvent.click(screen.getByRole("button", { name: "生成合并预览" }));
    expect(await screen.findByRole("heading", { name: "检查合并结果" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "继续确认合并" }));
    expect(screen.getByRole("alertdialog")).toHaveTextContent("不会物理删除");
    await userEvent.click(screen.getByRole("button", { name: "确认合并，暂不可自动撤销" }));
    await waitFor(() => expect(commit).toHaveBeenCalledTimes(1));
    expect(onMerged).toHaveBeenCalledWith(left.id, expect.stringContaining("没有删除"));
    expect(commit.mock.calls[0][2].operation_key).toMatch(/^merge-/);
  });

  it("freezes an unknown outcome and verifies the original operation key", async () => {
    const check = vi.fn().mockResolvedValue({ ...operation, replayed: true });
    vi.spyOn(apiModule, "api", "get").mockReturnValue({
      ...apiModule.api,
      getLoreElement: vi.fn().mockImplementation((_projectId, elementId) => Promise.resolve(elementId === left.id ? left : right)),
      previewLoreMerge: vi.fn().mockResolvedValue(preview),
      commitLoreMerge: vi.fn().mockRejectedValue(new TypeError("network")),
      getLoreMergeOperationByKey: check,
    });
    const { onMerged } = renderWizard();
    await userEvent.click(screen.getByRole("button", { name: "开始合并" }));
    await userEvent.click(await screen.findByLabelText(/保留“林岚”/));
    await userEvent.click(screen.getByLabelText("采用保留项：林岚"));
    await userEvent.click(screen.getByLabelText("采用保留项：旧摘要"));
    await userEvent.click(screen.getByLabelText("采用另一项：冲动"));
    await userEvent.click(screen.getByRole("button", { name: "生成合并预览" }));
    await userEvent.click(await screen.findByRole("button", { name: "继续确认合并" }));
    await userEvent.click(screen.getByRole("button", { name: "确认合并，暂不可自动撤销" }));
    const verify = await screen.findByRole("button", { name: "核对合并结果" });
    await userEvent.click(verify);
    await waitFor(() => expect(check).toHaveBeenCalledTimes(1));
    expect(check.mock.calls[0][1]).toMatch(/^merge-/);
    expect(onMerged).toHaveBeenCalledWith(left.id, expect.stringContaining("此前已经完成"));
  });

  it("keeps the stale-version warning visible while refreshing both endpoints", async () => {
    const getLoreElement = vi.fn().mockImplementation((_projectId, elementId) => Promise.resolve(elementId === left.id ? left : right));
    vi.spyOn(apiModule, "api", "get").mockReturnValue({
      ...apiModule.api,
      getLoreElement,
      previewLoreMerge: vi.fn().mockRejectedValue(new apiModule.ApiError(409, { detail: "设定版本已经变化" })),
      commitLoreMerge: vi.fn(),
      getLoreMergeOperationByKey: vi.fn(),
    });
    renderWizard();
    await userEvent.click(screen.getByRole("button", { name: "开始合并" }));
    await userEvent.click(await screen.findByLabelText(/保留“林岚”/));
    await userEvent.click(screen.getByLabelText("采用保留项：林岚"));
    await userEvent.click(screen.getByLabelText("采用保留项：旧摘要"));
    await userEvent.click(screen.getByLabelText("采用另一项：冲动"));
    await userEvent.click(screen.getByRole("button", { name: "生成合并预览" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("设定版本已经变化");
    await waitFor(() => expect(getLoreElement).toHaveBeenCalledTimes(4));
    expect(screen.getByLabelText("采用另一项：冲动")).toBeChecked();
  });
});
