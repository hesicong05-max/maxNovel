import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import DemoGuide from "./DemoGuide";

describe("DemoGuide", () => {
  it("marks one current step and carries all server-authoritative anchors", () => {
    render(<MemoryRouter><DemoGuide projectId="project-id" current={2} chapterId="chapter/id" elementId="element/id" foreshadowLifecycleId="lifecycle/id" /></MemoryRouter>);
    expect(screen.getByRole("navigation", { name: "技术演示五步导览" })).toBeInTheDocument();
    expect(screen.getByText("固定样例 · 不调用 AI · 不产生模型费用")).toBeVisible();
    expect(screen.getAllByRole("link")).toHaveLength(5);
    expect(screen.getByRole("link", { name: /2\s+设定仓库/ })).toHaveAttribute("aria-current", "step");
    expect(screen.getByRole("link", { name: /1\s+项目总览/ })).not.toHaveAttribute("aria-current");
    expect(screen.getByRole("link", { name: /2\s+设定仓库/ })).toHaveAttribute("href", "/project/project-id/lore?element=element%2Fid#demo-lore");
    expect(screen.getByRole("link", { name: /3\s+章节规划/ })).toHaveAttribute("href", "/project/project-id/plan/chapters?scope=chapter&target=chapter%2Fid#demo-planning");
    expect(screen.getByRole("link", { name: /4\s+伏笔计划/ })).toHaveAttribute("href", "/project/project-id/plan/foreshadows?lifecycle=lifecycle%2Fid#demo-foreshadow");
    expect(screen.getByRole("link", { name: /5\s+技术模拟/ })).toHaveAttribute("href", "/project/project-id/plan/chapters?scope=chapter&target=chapter%2Fid#demo-technical-generation");
  });

  it.each([1, 2, 3, 4, 5] as const)("exposes exactly one current step when current is %s", (current) => {
    render(<MemoryRouter><DemoGuide projectId="project-id" current={current} /></MemoryRouter>);
    const links = screen.getAllByRole("link");
    expect(links.filter((link) => link.getAttribute("aria-current") === "step")).toHaveLength(1);
    expect(links[current - 1]).toHaveAttribute("aria-current", "step");
  });

  it("uses safe base routes when optional server identities are unavailable", () => {
    render(<MemoryRouter><DemoGuide projectId="project/id" current={1} /></MemoryRouter>);
    expect(screen.getByRole("link", { name: /1\s+项目总览/ })).toHaveAttribute("href", "/project/project/id");
    expect(screen.getByRole("link", { name: /2\s+设定仓库/ })).toHaveAttribute("href", "/project/project/id/lore#demo-lore");
    expect(screen.getByRole("link", { name: /3\s+章节规划/ })).toHaveAttribute("href", "/project/project/id/plan/chapters#demo-planning");
    expect(screen.getByRole("link", { name: /4\s+伏笔计划/ })).toHaveAttribute("href", "/project/project/id/plan/foreshadows#demo-foreshadow");
    expect(screen.getByRole("link", { name: /5\s+技术模拟/ })).toHaveAttribute("href", "/project/project/id/plan/chapters#demo-technical-generation");
  });
});
