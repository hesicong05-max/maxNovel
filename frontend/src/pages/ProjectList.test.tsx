import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import * as apiModule from "@/services/api";
import ProjectList from "./ProjectList";

const id = (seed: string) => seed.padEnd(32, seed).slice(0, 32);
const missing = { schema_version: 1, fixture_version: 1, mode: "technical_demo_fixture", environment: "non_production", state: "missing", can_bootstrap: true, preserved: false, project_id: null, plan_id: null, part_id: null, chapter_id: null, element_id: null, assignment_id: null, second_chapter_id: null, foreshadow_element_id: null, foreshadow_lifecycle_id: null, counts: null, next_path: null, recommended_action: "bootstrap_fixture" } as const;
const counts = { setting_type_count: 6, element_count: 7, source_count: 7, relation_count: 3, part_count: 1, chapter_count: 2, assignment_count: 7, foreshadow_lifecycle_count: 1, foreshadow_plan_count: 2, foreshadow_fact_count: 0 } as const;
const ready = { ...missing, state: "ready", can_bootstrap: false, project_id: id("project"), plan_id: id("plan"), part_id: id("part"), chapter_id: id("chapter"), element_id: id("element"), assignment_id: id("assignment"), second_chapter_id: id("second"), foreshadow_element_id: id("foreshadow"), foreshadow_lifecycle_id: id("lifecycle"), counts, next_path: `/project/${id("project")}/lore`, recommended_action: "open_fixture" } as const;
const ordinaryProject = { id: id("ordinary"), title: "普通长篇项目", genre: "科幻", status: "draft", total_chapters: 20, chapter_word_count: 2000, style_intensity: "standard", created_at: "2026-08-13T08:00:00Z", updated_at: "2026-08-13T08:00:00Z", has_worldview: true, has_outline: false, chapter_count: 0 } as const;
const secondProject = { ...ordinaryProject, id: id("second-project"), title: "第二个创作项目" } as const;

function Location() { return <output>{useLocation().pathname}</output>; }
function renderPage() {
  render(<MemoryRouter initialEntries={["/"]}><Routes><Route path="*" element={<><ProjectList /><Location /></>} /></Routes></MemoryRouter>);
}

describe("ProjectList technical demo bootstrap recovery", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("GETs authoritative current after an uncertain POST and navigates only when ready", async () => {
    const getDemoFixture = vi.fn().mockResolvedValueOnce(missing).mockResolvedValueOnce(ready);
    const bootstrapDemoFixture = vi.fn().mockRejectedValue(new Error("network lost"));
    vi.spyOn(apiModule, "api", "get").mockReturnValue({ ...apiModule.api, listProjects: vi.fn().mockResolvedValue([]), getDemoFixture, bootstrapDemoFixture });
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: "建立技术演示样例" }));
    await waitFor(() => expect(screen.getByText(`/project/${id("project")}`)).toBeInTheDocument());
    expect(bootstrapDemoFixture).toHaveBeenCalledTimes(1);
    expect(getDemoFixture).toHaveBeenCalledTimes(2);
  });

  it("allows another explicit click only after GET confirms missing", async () => {
    const getDemoFixture = vi.fn().mockResolvedValue(missing);
    const bootstrapDemoFixture = vi.fn().mockRejectedValue(new Error("network lost"));
    vi.spyOn(apiModule, "api", "get").mockReturnValue({ ...apiModule.api, listProjects: vi.fn().mockResolvedValue([]), getDemoFixture, bootstrapDemoFixture });
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: "建立技术演示样例" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("明确再次建立");
    expect(screen.getByRole("button", { name: "建立技术演示样例" })).toBeEnabled();
    expect(bootstrapDemoFixture).toHaveBeenCalledTimes(1);
  });

  it("hides the POST entry when the authoritative GET cannot verify state", async () => {
    const getDemoFixture = vi.fn().mockResolvedValueOnce(missing).mockRejectedValueOnce(new Error("still offline"));
    const bootstrapDemoFixture = vi.fn().mockRejectedValue(new Error("network lost"));
    vi.spyOn(apiModule, "api", "get").mockReturnValue({ ...apiModule.api, listProjects: vi.fn().mockResolvedValue([]), getDemoFixture, bootstrapDemoFixture });
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: "建立技术演示样例" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("无法核对服务端状态");
    expect(screen.queryByRole("button", { name: "建立技术演示样例" })).not.toBeInTheDocument();
    expect(bootstrapDemoFixture).toHaveBeenCalledTimes(1);
  });

  it("keeps ordinary projects visible when the independent demo GET fails", async () => {
    vi.spyOn(apiModule, "api", "get").mockReturnValue({ ...apiModule.api, listProjects: vi.fn().mockResolvedValue([ordinaryProject]), getDemoFixture: vi.fn().mockRejectedValue(new Error("demo offline")) });
    renderPage();
    expect(await screen.findByText(ordinaryProject.title)).toBeInTheDocument();
    expect(screen.queryByText("还没有项目")).not.toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("普通项目仍可继续使用");
  });

  it("shows a retryable project-list error instead of a false empty state", async () => {
    const listProjects = vi.fn()
      .mockRejectedValueOnce(new Error("项目服务暂时不可用"))
      .mockResolvedValueOnce([ordinaryProject]);
    vi.spyOn(apiModule, "api", "get").mockReturnValue({
      ...apiModule.api,
      listProjects,
      getDemoFixture: vi.fn().mockResolvedValue(missing),
    });
    renderPage();

    expect(await screen.findByRole("heading", { name: "项目列表暂时无法加载" })).toBeInTheDocument();
    expect(screen.queryByText("第一部故事，正等你命名")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "重新加载项目" }));

    expect(await screen.findByRole("link", { name: `打开项目《${ordinaryProject.title}》` })).toHaveAttribute(
      "href",
      `/project/${ordinaryProject.id}`
    );
    expect(listProjects).toHaveBeenCalledTimes(2);
  });

  it("keeps project navigation and deletion as independent actions", async () => {
    const deleteProject = vi.fn().mockResolvedValue(undefined);
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    vi.spyOn(apiModule, "api", "get").mockReturnValue({
      ...apiModule.api,
      listProjects: vi.fn().mockResolvedValue([ordinaryProject, secondProject]),
      getDemoFixture: vi.fn().mockResolvedValue(missing),
      deleteProject,
    });
    renderPage();

    const projectLink = await screen.findByRole("link", { name: `打开项目《${ordinaryProject.title}》` });
    expect(projectLink).toHaveAttribute("href", `/project/${ordinaryProject.id}`);
    expect(screen.getAllByRole("link", { name: `打开项目《${ordinaryProject.title}》` })).toHaveLength(1);
    expect(screen.queryByRole("main")).not.toBeInTheDocument();
    const deleteButton = screen.getByRole("button", { name: `删除项目《${ordinaryProject.title}》` });

    await userEvent.click(deleteButton);
    expect(deleteProject).not.toHaveBeenCalled();
    expect(document.querySelector("output")).toHaveTextContent("/");

    confirmSpy.mockReturnValue(true);
    await userEvent.click(deleteButton);
    await waitFor(() => expect(deleteProject).toHaveBeenCalledTimes(1));
    expect(deleteProject).toHaveBeenCalledWith(ordinaryProject.id);
    expect(screen.queryByRole("link", { name: `打开项目《${ordinaryProject.title}》` })).not.toBeInTheDocument();
    expect(screen.getByText(`已删除项目《${ordinaryProject.title}》。`)).toHaveAttribute("role", "status");
    await waitFor(() => expect(screen.getByRole("link", { name: `打开项目《${secondProject.title}》` })).toHaveFocus());
    expect(document.querySelector("output")).toHaveTextContent("/");
  });

  it("enters authoritative project overview after successful bootstrap and from a ready CTA", async () => {
    const bootstrapDemoFixture = vi.fn().mockResolvedValue({ schema_version: 1, fixture_version: 1, mode: "technical_demo_fixture", environment: "non_production", state: "ready", replayed: false, project_id: ready.project_id, plan_id: ready.plan_id, part_id: ready.part_id, chapter_id: ready.chapter_id, element_id: ready.element_id, assignment_id: ready.assignment_id, next_path: ready.next_path });
    vi.spyOn(apiModule, "api", "get").mockReturnValue({ ...apiModule.api, listProjects: vi.fn().mockResolvedValue([]), getDemoFixture: vi.fn().mockResolvedValue(missing), bootstrapDemoFixture });
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: "建立技术演示样例" }));
    expect(await screen.findByText(`/project/${ready.project_id}`)).toBeInTheDocument();

    vi.restoreAllMocks();
    vi.spyOn(apiModule, "api", "get").mockReturnValue({ ...apiModule.api, listProjects: vi.fn().mockResolvedValue([]), getDemoFixture: vi.fn().mockResolvedValue(ready) });
    renderPage();
    expect(await screen.findByRole("link", { name: "打开五步技术演示" })).toHaveAttribute("href", `/project/${ready.project_id}`);
  });
});
