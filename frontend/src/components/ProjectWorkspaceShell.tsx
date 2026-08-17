import { NavLink, Outlet, useLocation, useParams } from "react-router-dom";

type WorkspaceDestination = {
  label: string;
  shortLabel: string;
  path: (projectId: string) => string;
  end?: boolean;
};

const destinations: WorkspaceDestination[] = [
  {
    label: "项目总览",
    shortLabel: "总览",
    path: (projectId) => `/project/${projectId}`,
    end: true,
  },
  {
    label: "设定仓库",
    shortLabel: "设定",
    path: (projectId) => `/project/${projectId}/lore`,
  },
  {
    label: "章节规划",
    shortLabel: "规划",
    path: (projectId) => `/project/${projectId}/plan/chapters`,
  },
  {
    label: "伏笔计划",
    shortLabel: "伏笔",
    path: (projectId) => `/project/${projectId}/plan/foreshadows`,
  },
];

function activeDestination(pathname: string) {
  if (pathname.endsWith("/lore")) return destinations[1];
  if (pathname.endsWith("/plan/chapters")) return destinations[2];
  if (pathname.endsWith("/plan/foreshadows")) return destinations[3];
  return destinations[0];
}

function WorkspaceLinks({ projectId, mobile = false }: { projectId: string; mobile?: boolean }) {
  return destinations.map((destination, index) => (
    <NavLink
      key={destination.label}
      to={destination.path(projectId)}
      end={destination.end}
      className={({ isActive }) =>
        ["project-workspace-nav__link", isActive ? "is-active" : ""].filter(Boolean).join(" ")
      }
    >
      <span className="project-workspace-nav__index" aria-hidden="true">
        {String(index + 1).padStart(2, "0")}
      </span>
      <span>{mobile ? destination.shortLabel : destination.label}</span>
    </NavLink>
  ));
}

export default function ProjectWorkspaceShell() {
  const { id } = useParams<{ id: string }>();
  const { pathname } = useLocation();

  if (!id) return <Outlet />;

  const projectId = encodeURIComponent(id);
  const currentDestination = activeDestination(pathname);

  return (
    <div className="project-workspace-shell">
      <a className="project-workspace-shell__skip" href="#project-workspace-content">
        跳到工作区内容
      </a>

      <header className="project-workspace-shell__context">
        <div className="project-workspace-shell__identity">
          <span className="project-workspace-shell__eyebrow">Novel workspace</span>
          <strong>项目工作台</strong>
          <span className="project-workspace-shell__current" aria-live="polite">
            {currentDestination.label}
          </span>
        </div>

        <nav className="project-workspace-nav project-workspace-nav--desktop" aria-label="项目工作区">
          <WorkspaceLinks projectId={projectId} />
        </nav>
      </header>

      <div id="project-workspace-content" className="project-workspace-shell__content" tabIndex={-1}>
        <Outlet />
      </div>

      <nav className="project-workspace-nav project-workspace-nav--mobile" aria-label="移动项目导航">
        <WorkspaceLinks projectId={projectId} mobile />
      </nav>
    </div>
  );
}
