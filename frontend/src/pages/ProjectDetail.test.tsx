import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as apiModule from "@/services/api";
import type { Project } from "@/types";
import ProjectDetail from "./ProjectDetail";

vi.mock("@/components/WorldviewEditor", () => ({
  default: ({ onComplete }: { onComplete: () => void }) => (
    <section aria-label="世界观编辑器">
      <button onClick={onComplete}>测试打开设定仓库</button>
    </section>
  ),
}));

vi.mock("@/components/ChapterWriter", () => ({
  default: ({ onBack }: { onBack: () => void }) => (
    <section aria-label="章节写作器">
      <button onClick={onBack}>测试返回世界观</button>
    </section>
  ),
}));

vi.mock("@/components/ProgressPanel", () => ({
  default: () => <div>章节进度</div>,
}));

const baseProject: Project = {
  id: "project-1",
  title: "测试小说",
  genre: "玄幻",
  status: "worldview_set",
  total_chapters: 12,
  chapter_word_count: 2000,
  style_intensity: "standard",
  created_at: "2026-08-07T00:00:00Z",
  updated_at: "2026-08-07T00:00:00Z",
  has_worldview: true,
  has_outline: false,
  chapter_count: 0,
};

function renderPage(project: Project) {
  const getProject = vi.fn().mockResolvedValue(project);
  vi.spyOn(apiModule, "api", "get").mockReturnValue({
    ...apiModule.api,
    getProject,
  });

  render(
    <MemoryRouter initialEntries={[`/project/${project.id}`]}>
      <Routes>
        <Route path="/project/:id" element={<ProjectDetail />} />
        <Route path="/project/:id/lore" element={<div>设定仓库页面</div>} />
      </Routes>
    </MemoryRouter>
  );
  return getProject;
}

describe("ProjectDetail outline retirement", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("routes a project without legacy outline to worldview and lore repository", async () => {
    renderPage(baseProject);

    expect(await screen.findByRole("heading", { name: "先完善设定仓库" })).toBeInTheDocument();
    expect(screen.getByLabelText("世界观编辑器")).toBeInTheDocument();
    expect(screen.queryByLabelText("章节写作器")).not.toBeInTheDocument();
    expect(screen.queryByText("大纲")).not.toBeInTheDocument();
    expect(screen.getByText(/章节规划将在第二阶段开放/)).toBeInTheDocument();

    await userEvent.click(screen.getByText("测试打开设定仓库"));
    expect(await screen.findByText("设定仓库页面")).toBeInTheDocument();
  });

  it("opens historical outline projects directly in compatible chapter writing", async () => {
    renderPage({
      ...baseProject,
      status: "outline_pending",
      has_outline: true,
      chapter_count: 3,
    });

    expect(await screen.findByLabelText("章节写作器")).toBeInTheDocument();
    expect(screen.getByText("章节进度")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /章节写作/ })).toHaveAttribute("aria-current", "step");
    expect(screen.queryByText("大纲")).not.toBeInTheDocument();

    await userEvent.click(screen.getByText("测试返回世界观"));
    expect(await screen.findByLabelText("世界观编辑器")).toBeInTheDocument();
    expect(screen.queryByLabelText("章节写作器")).not.toBeInTheDocument();
  });

  it("shows a retryable load error instead of reporting a missing project", async () => {
    const getProject = vi.fn()
      .mockRejectedValueOnce(new Error("网络暂时不可用"))
      .mockResolvedValueOnce(baseProject);
    vi.spyOn(apiModule, "api", "get").mockReturnValue({
      ...apiModule.api,
      getProject,
    });

    render(
      <MemoryRouter initialEntries={["/project/project-1"]}>
        <Routes>
          <Route path="/project/:id" element={<ProjectDetail />} />
        </Routes>
      </MemoryRouter>
    );

    expect(await screen.findByRole("heading", { name: "项目暂时无法加载" })).toBeInTheDocument();
    expect(screen.getByText("网络暂时不可用")).toBeInTheDocument();
    expect(screen.queryByText("项目不存在")).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "重新加载" }));
    await waitFor(() => expect(getProject).toHaveBeenCalledTimes(2));
    expect(await screen.findByText("测试小说")).toBeInTheDocument();
  });
});
