import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import App from "./App";

vi.mock("@/components/AuthContext", () => ({
  AuthProvider: ({ children }: { children: React.ReactNode }) => children,
  useAuth: () => ({
    loading: false,
    isAuthenticated: true,
    user: { id: "user-1", username: "测试作者", email: "author@example.com", is_admin: false },
    logout: vi.fn(),
  }),
}));

vi.mock("@/pages/LoginPage", () => ({ default: () => <h1>登录页</h1> }));
vi.mock("@/pages/ProjectList", () => ({ default: () => <h1>项目列表页</h1> }));
vi.mock("@/pages/NewProject", () => ({ default: () => <h1>新建项目页</h1> }));
vi.mock("@/pages/ProjectDetail", () => ({ default: () => <h1>项目总览页</h1> }));
vi.mock("@/pages/LoreRepositoryPage", () => ({ default: () => <h1>设定仓库页</h1> }));
vi.mock("@/pages/ChapterPlanningPage", () => ({ default: () => <h1>章节规划页</h1> }));
vi.mock("@/pages/ForeshadowPlanningPage", () => ({ default: () => <h1>伏笔计划页</h1> }));
vi.mock("@/pages/Settings", () => ({ default: () => <h1>设置页</h1> }));
vi.mock("@/pages/Community", () => ({ default: () => <h1>社区页</h1> }));
vi.mock("@/pages/CommunityNovelDetail", () => ({ default: () => <h1>社区详情页</h1> }));
vi.mock("@/pages/CommunityEdit", () => ({ default: () => <h1>社区编辑页</h1> }));

function renderApp(entry: string) {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <App />
    </MemoryRouter>
  );
}

describe("App project workspace routing", () => {
  it("wraps existing project routes in the project workspace shell", () => {
    renderApp("/project/project-1/plan/chapters?scope=chapter&target=chapter-1");

    expect(screen.getByRole("heading", { name: "章节规划页" })).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "项目工作区" })).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "移动项目导航" })).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: /章节规划|规划/ })[0]).toHaveAttribute(
      "aria-current",
      "page"
    );
  });

  it("does not add the project workspace to global routes", () => {
    renderApp("/community");

    expect(screen.getByRole("heading", { name: "社区页" })).toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "项目工作区" })).not.toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "移动项目导航" })).not.toBeInTheDocument();
  });
});
