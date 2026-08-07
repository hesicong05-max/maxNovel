import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as apiModule from "@/services/api";
import { draftStorageKey, saveDraft, type DraftScope } from "@/services/maintenanceDrafts";
import type {
  LoreMigrationCommitInput,
  LoreMigrationOperation,
  LoreMigrationPreviewResponse,
} from "@/types/lore";
import LoreMigrationUpgrade, { type StoredMigrationDraft } from "./LoreMigrationUpgrade";

const report: LoreMigrationPreviewResponse = {
  preview_schema_version: 1,
  mapping_version: 1,
  project_id: "project-1",
  storage_mode: "legacy",
  source_checksum: "a".repeat(64),
  semantic_result_checksum: "b".repeat(64),
  checked_at: "2026-08-07T08:00:00Z",
  overall_status: "ready",
  dry_run: true,
  read_only: true,
  writes_performed: 0,
  commit_available: true,
  counts: { legacy_total: 3, mappable: 3, review_required: 0, possible_conflict: 0, blocked: 0 },
  by_legacy_category: { characters: 3 },
  by_target_type: { character: 3 },
  items: [],
  issues: [],
};

const input: LoreMigrationCommitInput = {
  operation_key: "lore-migration:operation-0001",
  preview_schema_version: 1,
  mapping_version: 1,
  expected_source_checksum: "a".repeat(64),
  expected_semantic_result_checksum: "b".repeat(64),
  confirm_legacy_retained_no_automatic_rollback: true,
};

const operation: LoreMigrationOperation = {
  id: "operation-1",
  project_id: "project-1",
  operation_key: input.operation_key,
  status: "ready",
  source_checksum: input.expected_source_checksum,
  preview_schema_version: 1,
  mapping_version: 1,
  semantic_result_checksum: input.expected_semantic_result_checksum,
  result_checksum: "c".repeat(64),
  migration_id: "migration-1",
  error_code: null,
  counts: { elements: 3 },
  started_at: "2026-08-07T08:00:01Z",
  updated_at: "2026-08-07T08:00:02Z",
  completed_at: "2026-08-07T08:00:02Z",
  replayed: false,
};

const migrationScope: DraftScope = {
  userId: "user-1",
  projectId: "project-1",
  kind: "lore-migration",
  objectId: "legacy-to-relational-v1",
};

function stored(phase: StoredMigrationDraft["phase"]): StoredMigrationDraft {
  return { version: 1, phase, input, checkedAt: report.checked_at, legacyTotal: 3 };
}

function mockApi(overrides: Partial<typeof apiModule.api> = {}) {
  const value = {
    ...apiModule.api,
    commitLoreMigration: vi.fn().mockImplementation((_: string, request: LoreMigrationCommitInput) => Promise.resolve({
      ...operation,
      operation_key: request.operation_key,
    })),
    getLoreMigrationOperationByKey: vi.fn().mockResolvedValue(operation),
    getLoreMigrationPreview: vi.fn().mockResolvedValue(report),
    ...overrides,
  };
  vi.spyOn(apiModule, "api", "get").mockReturnValue(value);
  return value;
}

function renderUpgrade(preview: LoreMigrationPreviewResponse | null = report) {
  const onUpgraded = vi.fn();
  const onRequestPreviewReload = vi.fn();
  render(<LoreMigrationUpgrade
    projectId="project-1"
    userId="user-1"
    report={preview}
    onUpgraded={onUpgraded}
    onRequestPreviewReload={onRequestPreviewReload}
  />);
  return { onUpgraded, onRequestPreviewReload };
}

describe("LoreMigrationUpgrade", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
    localStorage.clear();
  });

  it("requires explicit acknowledgement, persists first, and submits only once", async () => {
    const api = mockApi();
    const { onUpgraded } = renderUpgrade();

    await userEvent.click(await screen.findByRole("button", { name: "升级为设定仓库" }));
    expect(screen.getByRole("alertdialog", { name: "确认升级为设定仓库？" })).toBeInTheDocument();
    const confirm = screen.getByRole("button", { name: "确认并开始升级" });
    expect(confirm).toBeDisabled();
    await userEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(confirm);
    fireEvent.click(confirm);

    await screen.findByRole("heading", { name: "设定仓库升级完成" });
    expect(api.commitLoreMigration).toHaveBeenCalledTimes(1);
    expect(api.commitLoreMigration).toHaveBeenCalledWith("project-1", expect.objectContaining({
      operation_key: expect.stringMatching(/^lore-migration:/),
      confirm_legacy_retained_no_automatic_rollback: true,
    }));
    expect(onUpgraded).toHaveBeenCalledOnce();
    expect(localStorage.getItem(draftStorageKey(migrationScope))).toBeNull();
  });

  it("blocks submission while an unresolved worldview draft exists", async () => {
    const api = mockApi();
    saveDraft({ ...migrationScope, kind: "worldview", objectId: "worldview" }, { name: "未保存" }, null);
    renderUpgrade();

    await userEvent.click(await screen.findByRole("button", { name: "升级为设定仓库" }));
    expect(await screen.findByText(/尚未处理的世界观草稿/)).toBeInTheDocument();
    expect(api.commitLoreMigration).not.toHaveBeenCalled();
  });

  it("recovers a refresh by GET with the original key and never POSTs", async () => {
    saveDraft(migrationScope, stored("outcome_unknown"), null);
    const api = mockApi({ getLoreMigrationOperationByKey: vi.fn().mockResolvedValue({ ...operation, replayed: true }) });
    renderUpgrade(null);

    expect(await screen.findByText(/此前已经完成，没有重复创建设定/)).toBeInTheDocument();
    expect(api.getLoreMigrationOperationByKey).toHaveBeenCalledWith("project-1", input.operation_key);
    expect(api.commitLoreMigration).not.toHaveBeenCalled();
  });

  it("retries a missing receipt only after the frozen preview still matches", async () => {
    saveDraft(migrationScope, stored("outcome_unknown"), null);
    const api = mockApi({
      getLoreMigrationOperationByKey: vi.fn().mockRejectedValue(new apiModule.ApiError(404, {
        detail: "not found",
        code: "LORE_MIGRATION_OPERATION_NOT_FOUND",
      })),
    });
    renderUpgrade();

    await userEvent.click(await screen.findByRole("button", { name: "使用原请求安全重试" }));
    await screen.findByRole("heading", { name: "设定仓库升级完成" });
    expect(api.getLoreMigrationPreview).toHaveBeenCalledWith("project-1");
    expect(api.commitLoreMigration).toHaveBeenCalledWith("project-1", input);
  });

  it("fails closed for a corrupt local operation record", async () => {
    localStorage.setItem(draftStorageKey(migrationScope), "{broken");
    const api = mockApi();
    renderUpgrade();

    expect(await screen.findByText(/本机记录缺失或损坏/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "升级为设定仓库" })).not.toBeInTheDocument();
    expect(api.commitLoreMigration).not.toHaveBeenCalled();
  });

  it("does not expose a write entry outside a ready safe window", async () => {
    mockApi();
    renderUpgrade({ ...report, commit_available: false });
    expect(await screen.findByText(/尚未开放安全升级窗口/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "升级为设定仓库" })).not.toBeInTheDocument();
  });

  it("fails closed when the browser cannot generate a reliable UUID", async () => {
    const api = mockApi();
    vi.spyOn(globalThis.crypto, "randomUUID").mockImplementation(() => {
      throw new Error("unavailable");
    });
    renderUpgrade();

    await userEvent.click(await screen.findByRole("button", { name: "升级为设定仓库" }));
    expect(await screen.findByText(/无法生成可靠的安全请求标识/)).toBeInTheDocument();
    expect(api.commitLoreMigration).not.toHaveBeenCalled();
    expect(localStorage.getItem(draftStorageKey(migrationScope))).toBeNull();
  });

  it("keeps manual outcome checking available after validating polling is exhausted", async () => {
    vi.useFakeTimers();
    saveDraft(migrationScope, stored("outcome_unknown"), null);
    const validating = { ...operation, status: "validating" as const, completed_at: null };
    const api = mockApi({ getLoreMigrationOperationByKey: vi.fn().mockResolvedValue(validating) });
    renderUpgrade();

    await act(async () => { await vi.advanceTimersByTimeAsync(6_000); });
    expect(screen.getByRole("button", { name: "继续核对升级结果" })).toBeEnabled();
    expect(api.getLoreMigrationOperationByKey).toHaveBeenCalled();
    expect(api.commitLoreMigration).not.toHaveBeenCalled();
    vi.useRealTimers();
  });

  it("does not reinterpret an unrelated 404 as a retryable missing receipt", async () => {
    saveDraft(migrationScope, stored("outcome_unknown"), null);
    const api = mockApi({
      getLoreMigrationOperationByKey: vi.fn().mockRejectedValue(new apiModule.ApiError(404, {
        detail: "project missing",
        code: "LORE_MIGRATION_PROJECT_MISSING",
      })),
    });
    renderUpgrade();

    expect(await screen.findByText(/当前项目或升级记录无法核对/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "使用原请求安全重试" })).not.toBeInTheDocument();
    expect(api.commitLoreMigration).not.toHaveBeenCalled();
  });

  it("reconciles a concurrent conflict by GET without creating a new request", async () => {
    const commit = vi.fn().mockRejectedValue(new apiModule.ApiError(409, {
      detail: "concurrent",
      code: "LORE_MIGRATION_CONCURRENT_CONFLICT",
      retryable: true,
    }));
    const get = vi.fn().mockImplementation((_: string, key: string) => Promise.resolve({
      ...operation,
      operation_key: key,
      replayed: true,
    }));
    const api = mockApi({ commitLoreMigration: commit, getLoreMigrationOperationByKey: get });
    renderUpgrade();

    await userEvent.click(await screen.findByRole("button", { name: "升级为设定仓库" }));
    await userEvent.click(screen.getByRole("checkbox"));
    await userEvent.click(screen.getByRole("button", { name: "确认并开始升级" }));

    expect(await screen.findByRole("heading", { name: "设定仓库升级完成" })).toBeInTheDocument();
    expect(api.commitLoreMigration).toHaveBeenCalledTimes(1);
    const originalKey = commit.mock.calls[0][1].operation_key;
    expect(get).toHaveBeenCalledWith("project-1", originalKey);
  });

  it("preserves the same request during known maintenance and unknown outcomes", async () => {
    const commit = vi.fn()
      .mockRejectedValueOnce(new apiModule.ApiError(503, { detail: "maintenance" }))
      .mockRejectedValueOnce(new apiModule.ApiError(503, { detail: "unknown", outcome_unknown: true }));
    mockApi({ commitLoreMigration: commit });
    renderUpgrade();

    await userEvent.click(await screen.findByRole("button", { name: "升级为设定仓库" }));
    await userEvent.click(screen.getByRole("checkbox"));
    await userEvent.click(screen.getByRole("button", { name: "确认并开始升级" }));
    await userEvent.click(await screen.findByRole("button", { name: "使用原请求安全重试" }));
    expect(await screen.findByRole("button", { name: "继续核对升级结果" })).toBeInTheDocument();
    const persisted = JSON.parse(localStorage.getItem(draftStorageKey(migrationScope))!);
    expect(persisted.payload.input.operation_key).toBe(commit.mock.calls[0][1].operation_key);
    expect(commit).toHaveBeenCalledTimes(2);
    expect(commit.mock.calls[1][1].operation_key).toBe(commit.mock.calls[0][1].operation_key);
  });

  it("closes the alert dialog with Escape and restores focus to the trigger", async () => {
    mockApi();
    renderUpgrade();
    const trigger = await screen.findByRole("button", { name: "升级为设定仓库" });
    await userEvent.click(trigger);
    const dialog = screen.getByRole("alertdialog");
    fireEvent.keyDown(dialog, { key: "Escape" });
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: "升级为设定仓库" })).toHaveFocus());
  });
});
