import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as apiModule from "@/services/api";
import LoreMergeHistory from "./LoreMergeHistory";

describe("LoreMergeHistory", () => {
  beforeEach(() => {
    vi.spyOn(apiModule, "api", "get").mockReturnValue({
      ...apiModule.api,
      listLoreElementMergeHistory: vi.fn().mockResolvedValue({
        items: [{
          id: "operation-1", project_id: "project-1", operation_key: "merge-key-123456",
          suggestion_id: "suggestion-1", evidence_revision: 1,
          survivor_element_id: "left-1", merged_element_id: "right-1",
          survivor_before_content_version: 1, survivor_before_lock_version: 1,
          survivor_after_content_version: 2, survivor_after_lock_version: 2,
          merged_before_content_version: 1, merged_before_lock_version: 1, merged_after_lock_version: 2,
          selection_snapshot: {}, impact_summary: {
            element_names: { survivor: "林岚", merged: "林岚（旧）" },
            source_impact: { preserved_total: 2 }, physical_deletions: 0,
          },
          relation_actions: [], created_at: "2026-08-07T00:00:00Z", replayed: false,
        }],
      }),
    });
  });

  it("shows an auditable non-destructive history from either element", async () => {
    render(<LoreMergeHistory projectId="project-1" elementId="right-1" />);
    await userEvent.click(screen.getByRole("button", { name: "查看合并历史" }));
    expect(await screen.findByText("林岚（旧） → 林岚")).toBeInTheDocument();
    expect(screen.getByText(/当前设定为被合并项/)).toBeInTheDocument();
    expect(screen.getByText("没有")).toBeInTheDocument();
    expect(screen.getByText("这里是不可变审计记录，不是撤销入口。")).toBeInTheDocument();
  });
});
