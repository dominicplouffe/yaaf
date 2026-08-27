import { Navigate, NavLink, Route, Routes, useLocation } from "react-router-dom";
import { DeskView, get, getToken } from "./api";
import { usePoll } from "./hooks";
import Login from "./pages/Login";
import Desk, { buildItems } from "./pages/Desk";
import Projects from "./pages/Projects";
import NewProject from "./pages/NewProject";
import ProjectPage from "./pages/Project";
import Roles from "./pages/Roles";

/** The count beside Desk in the sidebar. Only tier 1 and 2 — things with a
    consequence if ignored — because a badge that counts optional work teaches
    the chairman to ignore the badge.

    It counts the Desk's own items rather than re-deriving them from the same
    fields: a second copy of "what counts as needing you" would drift from the
    page within a release, and a badge that disagrees with the list under it is
    worse than no badge. Building a few unused nodes twice a minute is the
    cheaper half of that trade.

    Its own poll, at a third of the Desk's rate — the sidebar is on every page
    and the number does not need to be fresher than that. */
function DeskCount() {
  const { data } = usePoll(() => get<DeskView>("/observe/desk?live=true"), 30000);
  if (!data) return null;
  const n = buildItems(data.projects, () => {}).filter((i) => i.tier <= 2).length;
  return n > 0 ? <span className="n">{n}</span> : null;
}

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div className="shell">
      <nav className="sidebar">
        <div className="brand">
          y<span>aa</span>f
        </div>
        <NavLink to="/desk" className={({ isActive }) => "navlink" + (isActive ? " active" : "")}>
          Desk
          <DeskCount />
        </NavLink>
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
          ui build {__BUILD_TIME__}
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
        path="/desk"
        element={
          <Shell>
            <Desk />
          </Shell>
        }
      />
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
      <Route path="*" element={<Navigate to="/desk" replace />} />
    </Routes>
  );
}
