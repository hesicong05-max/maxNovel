import { Routes, Route, NavLink } from "react-router-dom";
import ErrorBoundary from "@/components/ErrorBoundary";
import ProjectList from "@/pages/ProjectList";
import NewProject from "@/pages/NewProject";
import ProjectDetail from "@/pages/ProjectDetail";
import Settings from "@/pages/Settings";
import Community from "@/pages/Community";
import CommunityNovelDetail from "@/pages/CommunityNovelDetail";
import CommunityEdit from "@/pages/CommunityEdit";

function App() {
  return (
    <ErrorBoundary>
      <div className="app-layout">
        <aside className="sidebar">
        <div className="sidebar-brand">
          <div className="sidebar-brand-icon">✦</div>
          <div className="sidebar-brand-title">满分小说</div>
          <div className="sidebar-brand-sub">世界观续写 Agent</div>
        </div>
        <nav>
          <ul className="sidebar-nav">
            <li>
              <NavLink to="/" end>
                <span className="sidebar-nav-icon">◈</span>
                我的项目
              </NavLink>
            </li>
            <li>
              <NavLink to="/new">
                <span className="sidebar-nav-icon">+</span>
                新建项目
              </NavLink>
            </li>
            <li>
              <NavLink to="/community">
                <span className="sidebar-nav-icon">◆</span>
                社区
              </NavLink>
            </li>
            <li>
              <NavLink to="/settings">
                <span className="sidebar-nav-icon">⚙</span>
                API 设置
              </NavLink>
            </li>
          </ul>
        </nav>
        <div className="sidebar-footer">
          <NavLink to="/settings" className="sidebar-settings-link">
            <span className="sidebar-nav-icon">🔑</span>
            API Key 配置
          </NavLink>
        </div>
      </aside>
      <main className="main-content">
        <Routes>
          <Route path="/" element={<ProjectList />} />
          <Route path="/new" element={<NewProject />} />
          <Route path="/project/:id" element={<ProjectDetail />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="/community" element={<Community />} />
          <Route path="/community/novel/:id" element={<CommunityNovelDetail />} />
          <Route path="/community/edit/:id" element={<CommunityEdit />} />
          <Route path="/community/upload" element={<CommunityEdit />} />
        </Routes>
      </main>
    </div>
    </ErrorBoundary>
  );
}

export default App;
