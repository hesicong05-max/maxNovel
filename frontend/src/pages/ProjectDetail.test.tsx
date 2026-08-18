import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as apiModule from "@/services/api";
import type { Project } from "@/types";
import ProjectDetail from "./ProjectDetail";

vi.mock("@/components/WorldviewEditor", () => ({
  default: ({ onComplete, migrationTarget, migrationRequestInvalid }: {
    onComplete: () => void;
    migrationTarget?: { category: string; index: number } | null;
    migrationRequestInvalid?: boolean;
  }) => (
    <section aria-label="世界观编辑器">
      {migrationTarget && <span>修正目标 {migrationTarget.category}:{migrationTarget.index}</span>}
      {migrationRequestInvalid && <span>修正链接无效</span>}
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

function renderPage(project: Project, path = `/project/${project.id}`) {
  const getProject = vi.fn().mockResolvedValue(project);
  vi.spyOn(apiModule, "api", "get").mockReturnValue({
    ...apiModule.api,
    getProject,
  });

  render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/project/:id" element={<ProjectDetail />} />
        <Route path="/project/:id/lore" element={<div>设定仓库页面</div>} />
        <Route path="/project/:id/plan/chapters" element={<div>章节规划页面</div>} />
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

    expect(await screen.findByRole("heading", { name: "建立篇章与章节结构" })).toBeInTheDocument();
    expect(screen.getByLabelText("世界观编辑器")).toBeInTheDocument();
    expect(screen.queryByLabelText("章节写作器")).not.toBeInTheDocument();
    expect(screen.queryByText("大纲")).not.toBeInTheDocument();
    expect(screen.getByText(/可以自行创建篇章、章节并安全调整顺序/)).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: "打开章节规划" })[0]).toHaveAttribute(
      "href",
      "/project/project-1/plan/chapters"
    );

    await userEvent.click(screen.getByText("测试打开设定仓库"));
    expect(await screen.findByText("设定仓库页面")).toBeInTheDocument();
  });

  it("presents project identity and workspace destinations as semantic navigation", async () => {
    const getProject = renderPage(baseProject);

    expect(await screen.findByRole("heading", { level: 1, name: baseProject.title })).toBeInTheDocument();
    expect(document.querySelector(".project-overview")).toBeInTheDocument();
    expect(screen.queryByRole("main")).not.toBeInTheDocument();
    const workspaceNav = screen.getByRole("navigation", { name: "项目工作区入口" });
    expect(workspaceNav).toContainElement(screen.getByRole("link", { name: "打开设定仓库" }));
    expect(screen.getByRole("link", { name: "打开设定仓库" })).toHaveAttribute("href", "/project/project-1/lore");
    expect(screen.getAllByRole("link", { name: "打开章节规划" })[0]).toHaveAttribute("href", "/project/project-1/plan/chapters");
    expect(screen.getByRole("link", { name: "← 返回全部项目" })).toHaveAttribute("href", "/");
    expect(getProject).toHaveBeenCalledTimes(1);
  });

  it("provides a safe exit when the project no longer exists", async () => {
    vi.spyOn(apiModule, "api", "get").mockReturnValue({
      ...apiModule.api,
      getProject: vi.fn().mockRejectedValue(new apiModule.ApiError(404, {
        detail: "项目不存在",
        code: "PROJECT_NOT_FOUND",
        retryable: false,
      })),
    });

    render(
      <MemoryRouter initialEntries={["/project/project-1"]}>
        <Routes><Route path="/project/:id" element={<ProjectDetail />} /></Routes>
      </MemoryRouter>
    );

    expect(await screen.findByRole("heading", { name: "没有找到这个项目" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "返回全部项目" })).toHaveAttribute("href", "/");
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
    expect(screen.getByRole("status")).toHaveTextContent(
      "已保留历史章节安排，可继续生成、编辑和导出章节"
    );
    expect(screen.getByRole("button", { name: /章节写作/ })).toHaveAttribute("aria-current", "step");
    expect(screen.queryByText("大纲")).not.toBeInTheDocument();

    await userEvent.click(screen.getByText("测试返回世界观"));
    expect(await screen.findByLabelText("世界观编辑器")).toBeInTheDocument();
    expect(screen.queryByLabelText("章节写作器")).not.toBeInTheDocument();
  });

  it("forces a historical outline project into the requested worldview fix", async () => {
    renderPage(
      { ...baseProject, has_outline: true, chapter_count: 3 },
      `/project/project-1?migration_fix=${encodeURIComponent(
        `characters:0:${"c".repeat(64)}:${"a".repeat(64)}`
      )}`
    );

    expect(await screen.findByLabelText("世界观编辑器")).toBeInTheDocument();
    expect(screen.getByText("修正目标 characters:0")).toBeInTheDocument();
    expect(screen.queryByLabelText("章节写作器")).not.toBeInTheDocument();
  });

  it("keeps an invalid migration fix request in a safe worldview exit state", async () => {
    renderPage(
      { ...baseProject, has_outline: true, chapter_count: 3 },
      "/project/project-1?migration_fix=characters%3A0"
    );

    expect(await screen.findByLabelText("世界观编辑器")).toBeInTheDocument();
    expect(screen.getByText("修正链接无效")).toBeInTheDocument();
    expect(screen.queryByLabelText("章节写作器")).not.toBeInTheDocument();
  });

  it("shows a retryable load error instead of reporting a missing project", async () => {
    let resolveRetry!: (project: Project) => void;
    const retryResult = new Promise<Project>((resolve) => {
      resolveRetry = resolve;
    });
    const getProject = vi.fn()
      .mockRejectedValueOnce(new Error("网络暂时不可用"))
      .mockReturnValueOnce(retryResult);
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
    expect(screen.getByRole("status")).toHaveTextContent("正在打开项目工作台…");
    expect(screen.queryByRole("heading", { name: "没有找到这个项目" })).not.toBeInTheDocument();

    resolveRetry(baseProject);
    expect(await screen.findByText("测试小说")).toBeInTheDocument();
  });
});
