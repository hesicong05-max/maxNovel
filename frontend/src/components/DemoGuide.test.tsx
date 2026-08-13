import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import DemoGuide from "./DemoGuide";

describe("DemoGuide", () => {
  it("marks one current step and carries all server-authoritative anchors", () => {
    render(<MemoryRouter><DemoGuide projectId="project-id" current={2} chapterId="chapter/id" elementId="element/id" foreshadowLifecycleId="lifecycle/id" /></MemoryRouter>);
    expect(screen.getByRole("link", { name: /2\s+设定仓库/ })).toHaveAttribute("aria-current", "step");
    expect(screen.getByRole("link", { name: /1\s+项目总览/ })).not.toHaveAttribute("aria-current");
    expect(screen.getByRole("link", { name: /2\s+设定仓库/ })).toHaveAttribute("href", "/project/project-id/lore?element=element%2Fid#demo-lore");
    expect(screen.getByRole("link", { name: /3\s+章节规划/ })).toHaveAttribute("href", "/project/project-id/plan/chapters?scope=chapter&target=chapter%2Fid#demo-planning");
    expect(screen.getByRole("link", { name: /4\s+伏笔计划/ })).toHaveAttribute("href", "/project/project-id/plan/foreshadows?lifecycle=lifecycle%2Fid#demo-foreshadow");
    expect(screen.getByRole("link", { name: /5\s+技术模拟/ })).toHaveAttribute("href", "/project/project-id/plan/chapters?scope=chapter&target=chapter%2Fid#demo-technical-generation");
  });
});
