import { Navigate, NavLink, Route, Routes, useLocation } from "react-router-dom";
import { getToken } from "./api";
import Login from "./pages/Login";
import Projects from "./pages/Projects";
import NewProject from "./pages/NewProject";
import ProjectPage from "./pages/Project";
import Roles from "./pages/Roles";

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div className="shell">
      <nav className="sidebar">
        <div className="brand">
          y<span>aa</span>f
        </div>
        <NavLink to="/projects" className={({ isActive }) => "navlink" + (isActive ? " active" : "")}>
          Projects
        </NavLink>
        <NavLink to="/roles" className={({ isActive }) => "navlink" + (isActive ? " active" : "")}>
          Role library
        </NavLink>
        <NavLink to="/projects/new" className={({ isActive }) => "navlink" + (isActive ? " active" : "")}>
          + New project
        </NavLink>
        <div className="foot">
          governance-as-refusals
          <br />
          <a href="/docs" target="_blank" rel="noreferrer">
            API docs ↗
          </a>{" "}
          ·{" "}
          <a
            href="#/login"
            onClick={() => localStorage.removeItem("yaaf_admin_token")}
          >
            log out
          </a>
        </div>
      </nav>
      <main className="main">{children}</main>
    </div>
  );
}

export default function App() {
  const location = useLocation();
  if (!getToken() && !location.pathname.startsWith("/login")) {
    return <Navigate to="/login" replace />;
  }
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route
        path="/projects"
        element={
          <Shell>
            <Projects />
          </Shell>
        }
      />
      <Route
        path="/projects/new"
        element={
          <Shell>
            <NewProject />
          </Shell>
        }
      />
      <Route
        path="/projects/:projectId/*"
        element={
          <Shell>
            <ProjectPage />
          </Shell>
        }
      />
      <Route
        path="/roles"
        element={
          <Shell>
            <Roles />
          </Shell>
        }
      />
      <Route path="*" element={<Navigate to="/projects" replace />} />
    </Routes>
  );
}
