import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as apiModule from "@/services/api";
import { saveDraft } from "@/services/maintenanceDrafts";
import type { LoreReviewDetail, LoreReviewListItem } from "@/types/lore";
import LoreReviewPanel from "./LoreReviewPanel";

const item: LoreReviewListItem = {
  id: "review-1",
  origin: "system_scan",
  kind: "possible_conflict",
  detection_state: "active",
  review_status: "pending",
  needs_review: true,
  lock_version: 1,
  evidence_revision: 1,
  left: {
    id: "left-1", name: "林岚", type: { key: "character", display_name: "角色" },
    summary: "旧摘要", lifecycle_status: "active", enabled: true,
  },
  right: {
    id: "right-1", name: "林岚", type: { key: "character", display_name: "角色" },
    summary: "新摘要", lifecycle_status: "active", enabled: true,
  },
  primary_reason: "名称和类型相同，但性格字段的已提供内容不同",
  stale: false,
  merge_allowed: false,
  merge_block_reason: "请先完成人工判断",
  updated_at: "2026-08-06T08:00:00Z",
};

const detail: LoreReviewDetail = {
  ...item,
  rule_key: "same_normalized_name_same_type",
  rule_version: 1,
  left_snapshot: {
    ...item.left,
    payload: { personality: "谨慎" },
    field_states: { personality: "provided" },
    content_version: 1,
    sources: [{
      id: "source-left", kind: "manual", label: "手动创建", is_primary: true,
      created_at: "2026-08-06T08:00:00Z", reference: null,
      excerpt: "林岚性格谨慎。", confirmation_status: "provided",
    }],
  },
  right_snapshot: {
    ...item.right,
    payload: { personality: "冲动" },
    field_states: { personality: "provided" },
    content_version: 2,
    sources: [{
      id: "source-right", kind: "document_import", label: "文档导入", is_primary: true,
      created_at: "2026-08-06T08:00:00Z", reference: "第一章",
      excerpt: "林岚行事冲动。", confirmation_status: "provided",
    }],
  },
  evidence: [{
    field_key: "personality", label: "性格", comparison: "different",
    left_value: "谨慎", right_value: "冲动",
  }],
  decided_evidence_revision: null,
  history: [],
};

function renderPanel(overrides: Partial<React.ComponentProps<typeof LoreReviewPanel>> = {}) {
  return render(<LoreReviewPanel
    projectId="project-1"
    userId="user-1"
    readOnly={false}
    mergeCommitEnabled={true}
    loreTypes={[{
      id: "type-1", key: "character", display_name: "角色", description: "",
      field_schema: [{ key: "personality", label: "性格", control: "textarea", value_type: "string", help: "", order: 1, required: false }],
      is_builtin: true, schema_revision: 1, status: "active",
      created_at: "2026-08-06T08:00:00Z", updated_at: "2026-08-06T08:00:00Z",
    }]}
    onDirtyChange={vi.fn()}
    onBusyChange={vi.fn()}
    onOpenElement={vi.fn()}
    onOverviewRefresh={vi.fn()}
    {...overrides}
  />);
}

const manualElements = [{
  id: "left-1", type: { key: "character", display_name: "角色" }, name: "林岚", summary: "",
  confirmation_status: "confirmed", lifecycle_status: "active", enabled: true, generation_eligible: true,
  source_summary: "手动创建", current_version: 1, revision: 1, lock_version: 3,
  updated_at: "2026-08-07T00:00:00Z", relation_count: 0,
}, {
  id: "right-2", type: { key: "location", display_name: "地点" }, name: "北境", summary: "",
  confirmation_status: "confirmed", lifecycle_status: "active", enabled: false, generation_eligible: false,
  source_summary: "文档导入", current_version: 2, revision: 2, lock_version: 4,
  updated_at: "2026-08-07T00:00:00Z", relation_count: 0,
}];

function authorReportDetail(): LoreReviewDetail {
  return {
    ...detail,
    id: "manual-1",
    origin: "author_report",
    rule_key: "manual_pair_review",
    kind: "possible_conflict",
    left: { ...detail.left, id: "left-1" },
    right: { ...detail.right, id: "right-2", name: "北境", type: { key: "location", display_name: "地点" } },
    primary_reason: "作者创建的可能冲突线索",
    merge_allowed: false,
    merge_block_reason: "类型不同，需先统一类型或分别处理",
    evidence: [{
      field_key: "author_report", label: "用户说明", comparison: "author_report",
      left_value: null, right_value: null, statement: "出生地与地点规则可能冲突。",
    }],
  };
}

async function chooseManualPair() {
  await userEvent.click(screen.getByRole("button", { name: "新建人工线索" }));
  const resultGroups = await screen.findAllByRole("group", { name: /设定搜索结果/ });
  await userEvent.click(await within(resultGroups[0]).findByRole("button", { name: /林岚/ }));
  await userEvent.click(await within(resultGroups[1]).findByRole("button", { name: /北境/ }));
  await userEvent.type(screen.getByLabelText(/需要复核的具体说明/), "出生地与地点规则可能冲突。");
}

describe("LoreReviewPanel", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.spyOn(apiModule, "api", "get").mockReturnValue({
      ...apiModule.api,
      listLoreReviews: vi.fn().mockResolvedValue({
        items: [item], next_cursor: null, has_more: false, total: 1,
      }),
      getLoreReview: vi.fn().mockResolvedValue(detail),
      scanLoreReviews: vi.fn().mockResolvedValue({
        created: 1, updated: 0, unchanged: 0, marked_stale: 0,
        active_total: 1, pending_total: 1, truncated: false, rescan_required: false,
      }),
      decideLoreReview: vi.fn(),
    });
  });

  it("keeps the studio shell read-only while browsing the queue and comparison", async () => {
    const createManualLoreReview = vi.fn();
    const previewLoreMerge = vi.fn();
    const commitLoreMerge = vi.fn();
    vi.spyOn(apiModule, "api", "get").mockReturnValue({
      ...apiModule.api,
      createManualLoreReview,
      previewLoreMerge,
      commitLoreMerge,
    });
    const api = apiModule.api;
    const { container } = renderPanel();

    const panel = screen.getByRole("region", { name: "重复与冲突线索" });
    expect(panel).toHaveClass("lore-review-panel");
    expect(panel.querySelector(":scope > .lore-review-filters")).toBeInTheDocument();
    expect(panel.querySelector(":scope > .lore-review-workspace > .lore-list")).toBeInTheDocument();
    expect(panel.querySelector(":scope > .lore-review-workspace > .lore-review-detail")).toBeInTheDocument();

    await userEvent.click(await screen.findByRole("button", { name: /林岚 ↔ 林岚/ }));
    expect(await screen.findByRole("region", { name: "两项设定对比" })).toBeInTheDocument();
    await userEvent.click(screen.getAllByText("查看原始来源")[0]);

    expect(api.listLoreReviews).toHaveBeenCalledTimes(1);
    expect(api.getLoreReview).toHaveBeenCalledTimes(1);
    expect(api.scanLoreReviews).not.toHaveBeenCalled();
    expect(api.decideLoreReview).not.toHaveBeenCalled();
    expect(createManualLoreReview).not.toHaveBeenCalled();
    expect(previewLoreMerge).not.toHaveBeenCalled();
    expect(commitLoreMerge).not.toHaveBeenCalled();
    expect(container.querySelector(".lore-review-decision")).toBeInTheDocument();
    expect(container.querySelector(".lore-manual-review-entry")).toBeInTheDocument();
  });

  it("labels the clue as unconfirmed and shows versioned sources", async () => {
    renderPanel();
    await userEvent.click(await screen.findByRole("button", { name: /林岚 ↔ 林岚/ }));
    expect(await screen.findByRole("heading", { name: "核对这条设定线索" })).toBeInTheDocument();
    expect(screen.getByText("系统扫描发现的是可能性，以下内容尚未被人工确认。")).toBeInTheDocument();
    expect(screen.getByText("线索仅用于引导人工复核，不自动代表事实矛盾或重复设定。")).toBeInTheDocument();
    expect(screen.getByText(/内容版本 1/)).toBeInTheDocument();
    expect(screen.getByText(/内容版本 2/)).toBeInTheDocument();
    await userEvent.click(screen.getAllByText("查看原始来源")[0]);
    expect(screen.getByText("林岚性格谨慎。")).toBeInTheDocument();
  });

  it("freezes an idempotent decision before confirmation and explains no automatic merge", async () => {
    const decide = vi.fn().mockResolvedValue({
      suggestion: {
        ...detail,
        review_status: "confirmed_duplicate",
        needs_review: false,
        lock_version: 2,
        decided_evidence_revision: 1,
        history: [{
          id: "event-1", previous_status: "pending", new_status: "confirmed_duplicate",
          evidence_revision: 1, note: "同一角色", applied: true,
          performed_by: "user-1", created_at: "2026-08-06T09:00:00Z",
        }],
      },
      replayed: false,
      applied: true,
      next_pending_id: null,
    });
    vi.spyOn(apiModule, "api", "get").mockReturnValue({
      ...apiModule.api,
      decideLoreReview: decide,
    });
    renderPanel();
    await userEvent.click(await screen.findByRole("button", { name: /林岚 ↔ 林岚/ }));
    await screen.findByRole("heading", { name: "核对这条设定线索" });
    await userEvent.selectOptions(screen.getByLabelText("判断"), "confirmed_duplicate");
    await userEvent.type(screen.getByLabelText(/备注/), "同一角色");
    await userEvent.click(screen.getByRole("button", { name: "记录判断" }));
    expect(screen.getByRole("alertdialog")).toHaveTextContent("不会自动合并、删除、停用或改写");
    await userEvent.click(screen.getByRole("button", { name: "确认记录" }));
    await waitFor(() => expect(decide).toHaveBeenCalledTimes(1));
    const input = decide.mock.calls[0][2];
    expect(input.operation_key).toMatch(/^review-/);
    expect(input.expected_version).toBe(1);
    expect(input.expected_evidence_revision).toBe(1);
    expect(await screen.findByText(/人工判断已记录；不会自动合并/)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("heading", { name: "核对这条设定线索" })).toHaveFocus());
  });

  it("traps focus in the decision dialog and returns Escape or cancel to the same trigger", async () => {
    const api = apiModule.api;
    renderPanel();
    await userEvent.click(await screen.findByRole("button", { name: /林岚 ↔ 林岚/ }));
    await screen.findByRole("heading", { name: "核对这条设定线索" });
    await userEvent.selectOptions(screen.getByLabelText("判断"), "deferred");
    const trigger = screen.getByRole("button", { name: "记录判断" });
    await userEvent.click(trigger);

    const dialog = screen.getByRole("alertdialog");
    const cancel = within(dialog).getByRole("button", { name: "取消" });
    const confirm = within(dialog).getByRole("button", { name: "确认记录" });
    await waitFor(() => expect(cancel).toHaveFocus());
    await userEvent.tab({ shift: true });
    expect(confirm).toHaveFocus();
    await userEvent.tab();
    expect(cancel).toHaveFocus();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    await waitFor(() => expect(trigger).toHaveFocus());
    expect(api.decideLoreReview).not.toHaveBeenCalled();

    await userEvent.click(trigger);
    const reopened = screen.getByRole("alertdialog");
    await userEvent.click(within(reopened).getByRole("button", { name: "取消" }));
    await waitFor(() => expect(trigger).toHaveFocus());
    expect(screen.getByRole("button", { name: "记录判断" })).toBe(trigger);
    expect(api.decideLoreReview).not.toHaveBeenCalled();
  });

  it("returns from detail to the original clue card", async () => {
    renderPanel();
    const card = await screen.findByRole("button", { name: /林岚 ↔ 林岚/ });
    await userEvent.click(card);
    await screen.findByRole("heading", { name: "核对这条设定线索" });
    await userEvent.click(screen.getByRole("button", { name: "← 返回线索列表" }));
    await waitFor(() => expect(card).toHaveFocus());
    expect(screen.queryByRole("heading", { name: "核对这条设定线索" })).not.toBeInTheDocument();
  });

  it("falls back to the review-list heading when the original card is disabled", async () => {
    let finishScan!: () => void;
    const scan = vi.fn().mockImplementation(() => new Promise<void>((resolve) => { finishScan = resolve; }));
    vi.spyOn(apiModule, "api", "get").mockReturnValue({ ...apiModule.api, scanLoreReviews: scan });
    renderPanel();
    const card = await screen.findByRole("button", { name: /林岚 ↔ 林岚/ });
    await userEvent.click(card);
    await screen.findByRole("heading", { name: "核对这条设定线索" });
    await userEvent.click(screen.getByRole("button", { name: "扫描正式设定" }));
    expect(card).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "← 返回线索列表" }));
    await waitFor(() => expect(screen.getByRole("heading", { name: "复核线索" })).toHaveFocus());
    finishScan();
    await waitFor(() => expect(screen.getByRole("button", { name: "扫描正式设定" })).toBeEnabled());
  });

  it("keeps the selected detail and focuses the error when draft removal fails", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderPanel();
    await userEvent.click(await screen.findByRole("button", { name: /林岚 ↔ 林岚/ }));
    await screen.findByRole("heading", { name: "核对这条设定线索" });
    await userEvent.selectOptions(screen.getByLabelText("判断"), "deferred");
    const removeItem = vi.spyOn(window.localStorage, "removeItem").mockImplementationOnce(() => {
      throw new Error("storage blocked");
    });
    await userEvent.click(screen.getByRole("button", { name: "← 返回线索列表" }));
    const alert = await screen.findByRole("alert");
    await waitFor(() => expect(alert).toHaveFocus());
    expect(screen.getByRole("heading", { name: "核对这条设定线索" })).toBeInTheDocument();
    removeItem.mockRestore();
  });

  it("keeps the frozen request and focuses the error after a maintenance response", async () => {
    const decide = vi.fn().mockRejectedValue(new apiModule.ApiError(503, { detail: "仓库维护中" }));
    vi.spyOn(apiModule, "api", "get").mockReturnValue({ ...apiModule.api, decideLoreReview: decide });
    renderPanel();
    await userEvent.click(await screen.findByRole("button", { name: /林岚 ↔ 林岚/ }));
    await screen.findByRole("heading", { name: "核对这条设定线索" });
    await userEvent.selectOptions(screen.getByLabelText("判断"), "deferred");
    await userEvent.click(screen.getByRole("button", { name: "记录判断" }));
    const trigger = screen.getByRole("button", { name: "记录判断" });
    await userEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "确认记录" }));
    const alert = await screen.findByRole("alert");
    await waitFor(() => expect(alert).toHaveFocus());
    expect(trigger).not.toHaveFocus();
    expect(decide).toHaveBeenCalledTimes(1);
    expect(decide.mock.calls[0][2].operation_key).toMatch(/^review-/);
    expect(screen.getByRole("button", { name: "使用相同请求安全重试" })).toBe(trigger);
  });

  it("keeps the selected clue and current focus when discarding the draft is declined", async () => {
    const decide = apiModule.api.decideLoreReview;
    vi.spyOn(window, "confirm").mockReturnValue(false);
    renderPanel();
    const card = await screen.findByRole("button", { name: /林岚 ↔ 林岚/ });
    await userEvent.click(card);
    await screen.findByRole("heading", { name: "核对这条设定线索" });
    await userEvent.selectOptions(screen.getByLabelText("判断"), "deferred");
    const back = screen.getByRole("button", { name: "← 返回线索列表" });
    await userEvent.click(back);
    expect(back).toHaveFocus();
    expect(screen.getByRole("heading", { name: "核对这条设定线索" })).toBeInTheDocument();
    expect(card).toHaveAttribute("aria-current", "true");
    expect(card).not.toHaveFocus();
    expect(screen.getByRole("heading", { name: "复核线索" })).not.toHaveFocus();
    expect(decide).not.toHaveBeenCalled();
  });

  it("blocks decisions when evidence is stale", async () => {
    vi.spyOn(apiModule, "api", "get").mockReturnValue({
      ...apiModule.api,
      getLoreReview: vi.fn().mockResolvedValue({ ...detail, stale: true }),
    });
    renderPanel();
    await userEvent.click(await screen.findByRole("button", { name: /林岚 ↔ 林岚/ }));
    expect(await screen.findByText("对比依据已变化，请重新扫描后再判断。")).toBeInTheDocument();
    expect(screen.getByLabelText("判断")).toBeDisabled();
    expect(screen.getByRole("button", { name: "记录判断" })).toBeDisabled();
  });

  it("runs a non-destructive explicit scan", async () => {
    const scan = vi.fn().mockResolvedValue({
      created: 1, updated: 0, unchanged: 0, marked_stale: 0,
      active_total: 1, pending_total: 1, truncated: false, rescan_required: false,
    });
    vi.spyOn(apiModule, "api", "get").mockReturnValue({ ...apiModule.api, scanLoreReviews: scan });
    renderPanel();
    await userEvent.click(await screen.findByRole("button", { name: "扫描正式设定" }));
    await waitFor(() => expect(scan).toHaveBeenCalledWith("project-1"));
    expect(await screen.findByText(/扫描完成：新增 1 条/)).toBeInTheDocument();
  });

  it("creates a clearly labelled author report with a frozen idempotency key", async () => {
    const elements = [{
      id: "left-1", type: { key: "character", display_name: "角色" }, name: "林岚", summary: "",
      confirmation_status: "confirmed", lifecycle_status: "active", enabled: true, generation_eligible: true,
      source_summary: "手动创建", current_version: 1, revision: 1, lock_version: 3,
      updated_at: "2026-08-07T00:00:00Z", relation_count: 0,
    }, {
      id: "right-2", type: { key: "location", display_name: "地点" }, name: "北境", summary: "",
      confirmation_status: "confirmed", lifecycle_status: "active", enabled: false, generation_eligible: false,
      source_summary: "文档导入", current_version: 2, revision: 2, lock_version: 4,
      updated_at: "2026-08-07T00:00:00Z", relation_count: 0,
    }];
    const manualDetail: LoreReviewDetail = {
      ...detail,
      id: "manual-1",
      origin: "author_report",
      rule_key: "manual_pair_review",
      kind: "possible_conflict",
      left: { ...detail.left, id: "left-1" },
      right: { ...detail.right, id: "right-2", name: "北境", type: { key: "location", display_name: "地点" } },
      primary_reason: "作者创建的可能冲突线索",
      merge_allowed: false,
      merge_block_reason: "类型不同，需先统一类型或分别处理",
      evidence: [{
        field_key: "author_report", label: "用户说明", comparison: "author_report",
        left_value: null, right_value: null, statement: "出生地与地点规则可能冲突。",
      }],
    };
    const createManual = vi.fn().mockResolvedValue({
      suggestion: manualDetail, replayed: false, created: true, reused: false,
    });
    vi.spyOn(apiModule, "api", "get").mockReturnValue({
      ...apiModule.api,
      listLoreElements: vi.fn().mockResolvedValue({ items: elements, total: 2, next_cursor: null, has_more: false, facets: {}, migration_status: {} }),
      createManualLoreReview: createManual,
    });
    renderPanel();
    await userEvent.click(screen.getByRole("button", { name: "新建人工线索" }));
    const resultGroups = await screen.findAllByRole("group", { name: /设定搜索结果/ });
    await userEvent.click(await within(resultGroups[0]).findByRole("button", { name: /林岚/ }));
    await userEvent.click(await within(resultGroups[1]).findByRole("button", { name: /北境/ }));
    expect(screen.getByText(/跨类型线索/)).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText(/需要复核的具体说明/), "出生地与地点规则可能冲突。");
    await userEvent.click(screen.getByRole("button", { name: "记录人工线索" }));
    await waitFor(() => expect(createManual).toHaveBeenCalledTimes(1));
    const input = createManual.mock.calls[0][1];
    expect(input.operation_key).toMatch(/^manual-review-/);
    expect(input.left_expected_lock_version).toBe(3);
    expect(input.right_expected_lock_version).toBe(4);
    expect(await screen.findByText(/人工线索已创建/)).toBeInTheDocument();
  });

  it("freezes all request inputs after an unknown result and retries the exact request", async () => {
    const createManual = vi.fn()
      .mockRejectedValueOnce(new Error("connection lost"))
      .mockResolvedValueOnce({
        suggestion: authorReportDetail(), replayed: true, created: true, reused: false,
      });
    vi.spyOn(apiModule, "api", "get").mockReturnValue({
      ...apiModule.api,
      listLoreElements: vi.fn().mockResolvedValue({ items: manualElements, total: 2, next_cursor: null, has_more: false, facets: {}, migration_status: {} }),
      createManualLoreReview: createManual,
    });
    renderPanel();
    await chooseManualPair();
    await userEvent.click(screen.getByRole("button", { name: "记录人工线索" }));
    expect(await screen.findByText(/网络结果不确定/)).toBeInTheDocument();
    expect(screen.getByLabelText("左侧设定")).toBeDisabled();
    expect(screen.getByLabelText("右侧设定")).toBeDisabled();
    expect(screen.getByLabelText(/需要复核的具体说明/)).toBeDisabled();
    const frozenGroups = screen.getAllByRole("group", { name: /设定搜索结果/ });
    expect(frozenGroups.flatMap((group) => within(group).getAllByRole("button")).every((button) => button.hasAttribute("disabled"))).toBe(true);
    const firstInput = createManual.mock.calls[0][1];
    await userEvent.click(screen.getByRole("button", { name: "使用相同请求安全重试" }));
    await waitFor(() => expect(createManual).toHaveBeenCalledTimes(2));
    expect(createManual.mock.calls[1][1]).toEqual(firstInput);
    expect(await screen.findByText(/先前已安全记录/)).toBeInTheDocument();
  });

  it("opens the existing clue when the same pair already has a different report", async () => {
    const existing = {
      ...authorReportDetail(), id: "review-existing",
      review_status: "confirmed_conflict" as const, needs_review: false,
      decided_evidence_revision: 1,
    };
    const getLoreReview = vi.fn().mockResolvedValue(existing);
    vi.spyOn(apiModule, "api", "get").mockReturnValue({
      ...apiModule.api,
      listLoreElements: vi.fn().mockResolvedValue({ items: manualElements, total: 2, next_cursor: null, has_more: false, facets: {}, migration_status: {} }),
      createManualLoreReview: vi.fn().mockRejectedValue(new apiModule.ApiError(409, {
        detail: "这两项设定已有不同的用户线索",
        code: "LORE_MANUAL_REVIEW_PAIR_CONFLICT",
        suggestion_id: "review-existing",
      })),
      getLoreReview,
      listLoreReviews: vi.fn().mockResolvedValue({
        items: [existing], next_cursor: null, has_more: false, total: 1,
      }),
    });
    renderPanel();
    await chooseManualPair();
    await userEvent.click(screen.getByRole("button", { name: "记录人工线索" }));
    await waitFor(() => expect(getLoreReview).toHaveBeenCalledWith("project-1", "review-existing"));
    expect(await screen.findByRole("heading", { name: "核对这条设定线索" })).toBeInTheDocument();
    expect(screen.getByText(/已有另一条人工线索，已打开原记录；本次没有覆盖/)).toBeInTheDocument();
    expect(screen.getByLabelText("处理状态")).toHaveValue("confirmed_conflict");
  });

  it("keeps a processed identical clue open after safe reuse", async () => {
    const existing = {
      ...authorReportDetail(), id: "review-reused",
      review_status: "not_an_issue" as const, needs_review: false,
      decided_evidence_revision: 1,
    };
    vi.spyOn(apiModule, "api", "get").mockReturnValue({
      ...apiModule.api,
      listLoreElements: vi.fn().mockResolvedValue({ items: manualElements, total: 2, next_cursor: null, has_more: false, facets: {}, migration_status: {} }),
      createManualLoreReview: vi.fn().mockResolvedValue({
        suggestion: existing, replayed: false, created: false, reused: true,
      }),
      getLoreReview: vi.fn().mockResolvedValue(existing),
      listLoreReviews: vi.fn().mockResolvedValue({
        items: [existing], next_cursor: null, has_more: false, total: 1,
      }),
    });
    renderPanel();
    await chooseManualPair();
    await userEvent.click(screen.getByRole("button", { name: "记录人工线索" }));
    expect(await screen.findByText(/已有相同的人工线索，已安全复用/)).toBeInTheDocument();
    expect(screen.getByLabelText("处理状态")).toHaveValue("not_an_issue");
    expect(await screen.findByRole("heading", { name: "核对这条设定线索" })).toBeInTheDocument();
  });

  it("fails closed on a structurally invalid stored manual draft until it is cleared", async () => {
    const scope = {
      userId: "user-1", projectId: "project-1", kind: "lore-manual-review", objectId: "new",
    } as const;
    expect(saveDraft(scope, { unexpected: "payload" }, null).status).toBe("saved");
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderPanel();
    expect(await screen.findByText(/人工线索草稿已损坏/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "新建人工线索" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "清除损坏草稿" }));
    expect(screen.getByRole("button", { name: "新建人工线索" })).toBeEnabled();
  });
});
