import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";
import ProjectWorkspaceShell from "./ProjectWorkspaceShell";

function renderShell(entry: string) {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <Routes>
        <Route path="/project/:id" element={<ProjectWorkspaceShell />}>
          <Route index element={<h1>项目页面</h1>} />
          <Route path="lore" element={<h1>设定页面</h1>} />
          <Route path="plan/chapters" element={<h1>章节页面</h1>} />
          <Route path="plan/foreshadows" element={<h1>伏笔页面</h1>} />
        </Route>
      </Routes>
    </MemoryRouter>
  );
}

describe("ProjectWorkspaceShell", () => {
  it("keeps project routes stable and exposes desktop and mobile navigation", () => {
    renderShell("/project/project-1/lore?scope=review");

    expect(screen.getByRole("heading", { name: "设定页面" })).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "项目工作区" })).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "移动项目导航" })).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: /设定/ })).toHaveLength(2);
    expect(screen.getAllByRole("link", { name: /设定/ })[0]).toHaveAttribute(
      "href",
      "/project/project-1/lore"
    );
    expect(screen.getAllByRole("link", { name: /设定/ })[0]).toHaveAttribute("aria-current", "page");
    expect(screen.getByText("设定仓库", { selector: ".project-workspace-shell__current" })).toBeInTheDocument();
    expect(screen.queryByText("project-1")).not.toBeInTheDocument();
  });

  it("marks the project overview exactly without matching deeper routes", () => {
    renderShell("/project/project-1");

    const overviewLinks = screen.getAllByRole("link", { name: /总览/ });
    expect(overviewLinks).toHaveLength(2);
    expect(overviewLinks.every((link) => link.getAttribute("aria-current") === "page")).toBe(true);
    expect(screen.getByRole("heading", { name: "项目页面" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "跳到工作区内容" })).toHaveAttribute(
      "href",
      "#project-workspace-content"
    );
  });
});
