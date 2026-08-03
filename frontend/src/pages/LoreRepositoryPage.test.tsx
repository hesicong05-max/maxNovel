import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as apiModule from "@/services/api";
import type { LoreListResponse, LoreOverview } from "@/types/lore";
import LoreRepositoryPage from "./LoreRepositoryPage";

const overview: LoreOverview = {
  formal_total: 1,
  confirmed_active: 1,
  pending_review: 2,
  needs_attention: 1,
  disabled: 0,
  archived: 0,
  migration_status: { storage_mode: "relational", state: "ready", read_only: false },
  capabilities: {
    candidate_review: true,
    candidate_accept: false,
    formal_conflict_tracking: false,
    search_fields: ["name", "summary"],
  },
  count_definitions: {},
};

function formalResponse(name = "林渊"): LoreListResponse {
  return {
    items: [{
      id: `item-${name}`,
      type: { key: "character", display_name: "角色" },
      name,
      summary: "来自寒川城的追查者",
      confirmation_status: "confirmed",
      lifecycle_status: "active",
      enabled: true,
      generation_eligible: true,
      source_summary: "用户原文",
      current_version: 1,
      revision: 1,
      lock_version: 1,
      updated_at: "2026-08-03T00:00:00Z",
      relation_count: 2,
    }],
    next_cursor: null,
    has_more: false,
    total: 1,
    facets: {
      types: [{ key: "character", label: "角色", count: 1 }],
      confirmation_statuses: [{ key: "confirmed", label: "已确认", count: 1 }],
      sources: [{ key: "manual", label: "手动创建", count: 1 }],
      lifecycle_statuses: [{ key: "active", label: "使用中", count: 1 }],
      enabled_statuses: [],
      relation_statuses: [],
    },
    migration_status: { storage_mode: "relational", state: "ready", read_only: false },
  };
}

const candidateResponse = {
  items: [{
    id: "candidate-1",
    batch_id: "batch-1",
    type_key: "location",
    type_display_name: "地点",
    name: "寒川城",
    summary: "北境城邦",
    status: "pending_review",
    needs_attention: true,
    disabled_reasons: ["fields_need_confirmation"],
    revision: 1,
    evidence: [{
      id: "evidence-1",
      field_key: "name",
      label: "名称",
      current_value: "寒川城",
      current_state: "provided" as const,
      value_origin: "ai_extraction" as const,
      excerpt: "林渊进入寒川城。",
    }],
  }],
  next_cursor: null,
  has_more: false,
  total: 1,
  applied_filters: {},
  query_signature: "sig-1",
};

function renderPage(entry = "/project/project-1/lore") {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <Routes>
        <Route path="/project/:id/lore" element={<LoreRepositoryPage />} />
      </Routes>
    </MemoryRouter>
  );
}

describe("LoreRepositoryPage", () => {
  beforeEach(() => {
    vi.spyOn(apiModule, "api", "get").mockReturnValue({
      ...apiModule.api,
      getLoreOverview: vi.fn().mockResolvedValue(overview),
      listLoreElements: vi.fn().mockResolvedValue(formalResponse()),
      getLoreElement: vi.fn().mockResolvedValue({
        ...formalResponse().items[0],
        payload: { appearance: "黑发" },
        field_states: { appearance: "provided" },
        field_definitions: [{ key: "appearance", label: "外貌", order: 10 }],
        sources: [{ id: "source-1", kind: "manual", label: "manual", is_primary: true, created_at: "2026-08-03T00:00:00Z", excerpt: "林渊黑发。", reference: null }],
        version_count: 1,
        read_only: true,
      }),
      listLoreCandidates: vi.fn().mockResolvedValue(candidateResponse),
    });
  });

  it("shows formal lore, source detail, and the read-only capability boundary", async () => {
    renderPage();

    expect(await screen.findByText("林渊")).toBeInTheDocument();
    expect(screen.getByText(/正式接纳功能将在后续安全写入阶段开放/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /林渊/ }));

    expect(await screen.findByText("黑发")).toBeInTheDocument();
    expect(screen.getByText("林渊黑发。")).toBeInTheDocument();
  });

  it("opens the attention inbox from the overview without calling a write API", async () => {
    renderPage();
    await screen.findByText("林渊");

    await userEvent.click(screen.getByRole("button", { name: /1\s+需要关注/ }));

    expect(await screen.findByText("寒川城")).toBeInTheDocument();
    expect(apiModule.api.listLoreCandidates).toHaveBeenCalledWith(
      "project-1",
      expect.objectContaining({ needs_attention: true }),
      expect.any(AbortSignal)
    );
    expect(screen.queryByRole("button", { name: /接纳/ })).not.toBeInTheDocument();
  });

  it("sends source and confirmation filters and preserves the disabled count definition", async () => {
    renderPage();
    await screen.findByText("林渊");

    await userEvent.selectOptions(screen.getByLabelText("确认状态"), "confirmed");
    await waitFor(() => expect(apiModule.api.listLoreElements).toHaveBeenLastCalledWith(
      "project-1",
      expect.objectContaining({ confirmation_status: "confirmed" }),
      expect.any(AbortSignal)
    ));
    await userEvent.selectOptions(screen.getByLabelText("原始来源"), "manual");
    await waitFor(() => expect(apiModule.api.listLoreElements).toHaveBeenLastCalledWith(
      "project-1",
      expect.objectContaining({ confirmation_status: "confirmed", source_kind: "manual" }),
      expect.any(AbortSignal)
    ));

    await userEvent.click(screen.getByRole("button", { name: /0\s+已停用/ }));
    await waitFor(() => expect(apiModule.api.listLoreElements).toHaveBeenLastCalledWith(
      "project-1",
      expect.objectContaining({ enabled: false, lifecycle_status: "active" }),
      expect.any(AbortSignal)
    ));
  });

  it("ignores a stale list response after a newer search finishes", async () => {
    let resolveOld: (value: LoreListResponse) => void = () => {};
    const oldRequest = new Promise<LoreListResponse>((resolve) => { resolveOld = resolve; });
    const list = vi.fn().mockImplementation((_projectId: string, filters: { q?: string }) =>
      filters.q ? Promise.resolve(formalResponse("新结果")) : oldRequest
    );
    vi.spyOn(apiModule, "api", "get").mockReturnValue({
      ...apiModule.api,
      getLoreOverview: vi.fn().mockResolvedValue(overview),
      listLoreElements: list,
      getLoreElement: vi.fn(),
      listLoreCandidates: vi.fn(),
    });
    renderPage();

    await userEvent.type(screen.getByLabelText("搜索名称或摘要"), "新");
    await userEvent.click(screen.getByRole("button", { name: "搜索" }));
    expect(await screen.findByText("新结果")).toBeInTheDocument();

    await act(async () => resolveOld(formalResponse("旧结果")));
    await waitFor(() => expect(screen.queryByText("旧结果")).not.toBeInTheDocument());
    expect(screen.getByText("新结果")).toBeInTheDocument();
  });

  it("does not append a stale load-more page after filters change", async () => {
    let resolveMore: (value: LoreListResponse) => void = () => {};
    const moreRequest = new Promise<LoreListResponse>((resolve) => { resolveMore = resolve; });
    const first = { ...formalResponse("首页设定"), next_cursor: "cursor-1", has_more: true };
    const list = vi.fn().mockImplementation((_projectId: string, filters: { q?: string; cursor?: string }) => {
      if (filters.cursor) return moreRequest;
      if (filters.q) return Promise.resolve(formalResponse("筛选后设定"));
      return Promise.resolve(first);
    });
    vi.spyOn(apiModule, "api", "get").mockReturnValue({
      ...apiModule.api,
      getLoreOverview: vi.fn().mockResolvedValue(overview),
      listLoreElements: list,
      getLoreElement: vi.fn(),
      listLoreCandidates: vi.fn(),
    });
    renderPage();
    await screen.findByText("首页设定");

    await userEvent.click(screen.getByRole("button", { name: "加载更多" }));
    await userEvent.type(screen.getByLabelText("搜索名称或摘要"), "新");
    await userEvent.click(screen.getByRole("button", { name: "搜索" }));
    expect(await screen.findByText("筛选后设定")).toBeInTheDocument();

    await act(async () => resolveMore(formalResponse("过期分页设定")));
    await waitFor(() => expect(screen.queryByText("过期分页设定")).not.toBeInTheDocument());
  });

  it("keeps a detail failure local and retries the selected item", async () => {
    const successfulDetail = {
      ...formalResponse().items[0],
      payload: { appearance: "黑发" },
      field_states: { appearance: "provided" },
      field_definitions: [{ key: "appearance", label: "外貌", order: 10 }],
      sources: [],
      version_count: 1,
      read_only: true,
    };
    const getDetail = vi.fn()
      .mockRejectedValueOnce(new Error("详情网络中断"))
      .mockResolvedValueOnce(successfulDetail);
    vi.spyOn(apiModule, "api", "get").mockReturnValue({
      ...apiModule.api,
      getLoreOverview: vi.fn().mockResolvedValue(overview),
      listLoreElements: vi.fn().mockResolvedValue(formalResponse()),
      getLoreElement: getDetail,
      listLoreCandidates: vi.fn(),
    });
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: /林渊/ }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("详情网络中断");
    expect(screen.getByText("林渊")).toBeInTheDocument();
    await userEvent.click(within(alert).getByRole("button", { name: "重试" }));
    expect(await screen.findByText("黑发")).toBeInTheDocument();
    expect(getDetail).toHaveBeenCalledTimes(2);
  });

  it("recovers an invalid cursor from page one and keeps the explanation visible", async () => {
    const first = { ...formalResponse("首页设定"), next_cursor: "stale", has_more: true };
    const list = vi.fn().mockImplementation((_projectId: string, filters: { cursor?: string }) => {
      if (filters.cursor) {
        return Promise.reject(new apiModule.ApiError(409, {
          detail: "列表游标已失效。",
          code: "LORE_CURSOR_STALE",
          reload_required: true,
        }));
      }
      return Promise.resolve(first);
    });
    vi.spyOn(apiModule, "api", "get").mockReturnValue({
      ...apiModule.api,
      getLoreOverview: vi.fn().mockResolvedValue(overview),
      listLoreElements: list,
      getLoreElement: vi.fn(),
      listLoreCandidates: vi.fn(),
    });
    renderPage();
    await screen.findByText("首页设定");
    await userEvent.click(screen.getByRole("button", { name: "加载更多" }));

    expect(await screen.findByRole("status")).toHaveTextContent("已从第一页重新加载");
    expect(await screen.findByText("首页设定")).toBeInTheDocument();
    expect(list).toHaveBeenCalledTimes(3);
  });

  it("moves focus to the selected detail on a 390px viewport", async () => {
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      value: vi.fn().mockImplementation(() => ({ matches: true })),
    });
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: /林渊/ }));

    const detailRegion = screen.getByRole("complementary", { name: "设定详情" });
    await waitFor(() => expect(detailRegion).toHaveFocus());
    expect(screen.getByRole("button", { name: /返回设定列表/ })).toBeInTheDocument();
  });

  it("does not restore a late detail response after returning to the list", async () => {
    let resolveDetail: (value: object) => void = () => {};
    const pendingDetail = new Promise<object>((resolve) => { resolveDetail = resolve; });
    vi.spyOn(apiModule, "api", "get").mockReturnValue({
      ...apiModule.api,
      getLoreOverview: vi.fn().mockResolvedValue(overview),
      listLoreElements: vi.fn().mockResolvedValue(formalResponse()),
      getLoreElement: vi.fn().mockReturnValue(pendingDetail),
      listLoreCandidates: vi.fn(),
    });
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: /林渊/ }));
    await userEvent.click(screen.getByRole("button", { name: /返回设定列表/ }));

    await act(async () => resolveDetail({
      ...formalResponse().items[0],
      payload: { appearance: "过期详情" },
      field_states: { appearance: "provided" },
      field_definitions: [{ key: "appearance", label: "外貌", order: 10 }],
      sources: [],
      version_count: 1,
      read_only: true,
    }));
    await waitFor(() => expect(screen.queryByText("过期详情")).not.toBeInTheDocument());
    expect(screen.getByText("选择一项查看详情")).toBeInTheDocument();
  });

  it("does not show a late detail error after returning to the list", async () => {
    let rejectDetail: (reason: Error) => void = () => {};
    const pendingDetail = new Promise<object>((_resolve, reject) => { rejectDetail = reject; });
    vi.spyOn(apiModule, "api", "get").mockReturnValue({
      ...apiModule.api,
      getLoreOverview: vi.fn().mockResolvedValue(overview),
      listLoreElements: vi.fn().mockResolvedValue(formalResponse()),
      getLoreElement: vi.fn().mockReturnValue(pendingDetail),
      listLoreCandidates: vi.fn(),
    });
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: /林渊/ }));
    await userEvent.click(screen.getByRole("button", { name: /返回设定列表/ }));

    await act(async () => rejectDetail(new Error("过期详情错误")));
    await waitFor(() => expect(screen.queryByText("过期详情错误")).not.toBeInTheDocument());
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
