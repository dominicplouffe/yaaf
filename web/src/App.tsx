import { Link, Navigate, NavLink, Route, Routes, useLocation } from "react-router-dom";
import { DeskView, get, getToken } from "./api";
import { usePoll } from "./hooks";
import Login from "./pages/Login";
import Desk, { buildItems, needsYou } from "./pages/Desk";
import Projects from "./pages/Projects";
import Founding from "./pages/Founding";
import NewProject from "./pages/NewProject";
import StartProject from "./pages/StartProject";
import ProjectPage from "./pages/Project";
import Roles from "./pages/Roles";
import Settings from "./pages/Settings";
import { Icon } from "./ui/Icon";
import { ThemeToggle } from "./ui/kit";

/** The count beside Desk in the sidebar. Only the rows a decision is actually
    waiting on (Desk's `needsYou`, not its tier) — because a badge that counts
    work the chairman cannot do teaches the chairman to ignore the badge. A
    drought used to sit in here: nothing to approve, no verb the chairman holds,
    and a 1 beside "Desk" every time it climbed.

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
  const n = buildItems(data.projects, () => {}).filter(needsYou).length;
  return n > 0 ? <span className="n">{n}</span> : null;
}

const NAV = [
  { to: "/desk", label: "Your desk", icon: "inbox" as const, badge: true },
  { to: "/projects", label: "Projects", icon: "folder" as const, badge: false },
  // The role library is where you go to see who you can put on a project.
  // Calling it a library described our filing system; this describes the job.
  { to: "/roles", label: "People you can hire", icon: "users" as const, badge: false },
  { to: "/settings", label: "Settings", icon: "sliders" as const, badge: false },
];

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div className="shell">
      <nav className="sidebar">
        <div className="brand" title={`Build ${__BUILD_TIME__}`}>
          y<span>aa</span>f
        </div>
        {/* The one create affordance, and the only verb in a list of places.
            It used to be two of them — a conversation and a form, named after
            our two implementations — sitting among the four destinations and
            pushing them down. Which of the two you want is a question with a
            screen of its own now; this is the door to it. */}
        <Link to="/projects/new" className="navnew">
          <Icon name="plus" size={15} strokeWidth={2.3} />
          New project
        </Link>
        {NAV.map((n) => (
          <NavLink
            key={n.to}
            to={n.to}
            className={({ isActive }) => "navlink" + (isActive ? " active" : "")}
          >
            <Icon name={n.icon} size={17} />
            <span style={{ flex: 1 }}>{n.label}</span>
            {n.badge ? <DeskCount /> : null}
          </NavLink>
        ))}
        <div className="foot">
          <ThemeToggle />
          {/* What used to sit here: the UI's build timestamp and a link to the
              API docs. Neither is something the person running a project has
              any use for, and both told them they were looking at somebody's
              internal tool. The build stamp still exists, on the brand's
              tooltip, where support can ask for it. */}
          <button
            className="linkish"
            style={{ marginTop: "var(--s3)", padding: 0, border: 0, background: "none", minHeight: 0 }}
            onClick={() => {
              localStorage.removeItem("yaaf_admin_token");
              window.location.hash = "#/login";
            }}
          >
            <Icon name="logout" size={14} />
            Sign out
          </button>
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
      {/* One door, then the fork. Every "create a project" affordance in the
          app points at /projects/new, which is now the question rather than
          one of its two answers; the answers are the two routes under it, and
          each is linkable on its own for anyone who already knows. */}
      <Route
        path="/projects/new"
        element={
          <Shell>
            <StartProject />
          </Shell>
        }
      />
      <Route
        path="/projects/new/describe"
        element={
          <Shell>
            <Founding />
          </Shell>
        }
      />
      <Route
        path="/projects/new/form"
        element={
          <Shell>
            <NewProject />
          </Shell>
        }
      />
      {/* The conversation's old address, kept working for bookmarks and for
          the docs that name it. */}
      <Route path="/projects/found" element={<Navigate to="/projects/new/describe" replace />} />
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
      <Route
        path="/settings"
        element={
          <Shell>
            <Settings />
          </Shell>
        }
      />
      <Route path="*" element={<Navigate to="/desk" replace />} />
    </Routes>
  );
}
