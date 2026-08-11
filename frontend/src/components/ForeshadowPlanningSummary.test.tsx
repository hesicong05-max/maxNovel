import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as apiModule from "@/services/api";
import ForeshadowPlanningSummary from "./ForeshadowPlanningSummary";

const id = (value: string) => value.padEnd(32, value).slice(0, 32);
const projectId = id("project");
const counts = { unplanted: 2, planted: 3, pending_resolution: 4, resolved: 5 };

describe("ForeshadowPlanningSummary", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("reads active and archived counts without exposing write controls", async () => {
    const listForeshadows = vi.fn()
      .mockResolvedValueOnce({ items: [], counts, next_cursor: null })
      .mockResolvedValueOnce({ items: [], counts: { unplanted: 1, planted: 1, pending_resolution: 0, resolved: 0 }, next_cursor: null });
    vi.spyOn(apiModule, "api", "get").mockReturnValue({ ...apiModule.api, listForeshadows } as typeof apiModule.api);
    render(<MemoryRouter><ForeshadowPlanningSummary projectId={projectId} /></MemoryRouter>);
    expect(await screen.findByText(/未埋入/)).toHaveTextContent("2");
    expect(screen.getByText(/已归档/)).toHaveTextContent("2");
    expect(screen.getByRole("link", { name: "管理伏笔" })).toHaveAttribute("href", `/project/${projectId}/plan/foreshadows`);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("keeps the planning deep link when the isolated summary read fails", async () => {
    vi.spyOn(apiModule, "api", "get").mockReturnValue({ ...apiModule.api, listForeshadows: vi.fn().mockRejectedValue(new Error("offline")) } as typeof apiModule.api);
    render(<MemoryRouter><ForeshadowPlanningSummary projectId={projectId} /></MemoryRouter>);
    expect(await screen.findByText(/不影响章节规划/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "管理伏笔" })).toBeInTheDocument();
  });
});
