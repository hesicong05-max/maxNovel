import { Routes, Route, NavLink, Navigate } from "react-router-dom";
import ErrorBoundary from "@/components/ErrorBoundary";
import { AuthProvider, useAuth } from "@/components/AuthContext";
import LoginPage from "@/pages/LoginPage";
import ProjectList from "@/pages/ProjectList";
import NewProject from "@/pages/NewProject";
import ProjectDetail from "@/pages/ProjectDetail";
import Settings from "@/pages/Settings";
import Community from "@/pages/Community";
import CommunityNovelDetail from "@/pages/CommunityNovelDetail";
import CommunityEdit from "@/pages/CommunityEdit";

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { loading, isAuthenticated } = useAuth();
  if (loading) return <div className="app-loading">加载中...</div>;
  if (!isAuthenticated) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

function Sidebar() {
  const { user, logout } = useAuth();
  return (
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
        {user && (
          <div className="sidebar-user">
            <div className="sidebar-user-info">
              <span className="sidebar-user-name">{user.username}</span>
              <span className="sidebar-user-email">{user.email}</span>
            </div>
            <button className="sidebar-logout" onClick={logout} title="退出登录">
              ⏻
            </button>
          </div>
        )}
      </div>
    </aside>
  );
}

function AppRoutes() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route
        path="*"
        element={
          <ProtectedRoute>
            <div className="app-layout">
              <Sidebar />
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
          </ProtectedRoute>
        }
      />
    </Routes>
  );
}

function App() {
  return (
    <ErrorBoundary>
      <AuthProvider>
        <AppRoutes />
      </AuthProvider>
    </ErrorBoundary>
  );
}

export default App;
