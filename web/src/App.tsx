import { Navigate, Route, Routes, useLocation, useParams } from "react-router-dom";
import { MeProvider, useMe } from "./me";
import { DeskProvider } from "./desk";
import { ProjectProvider } from "./projectCtx";
import { Shell } from "./shell";
import Login from "./pages/Login";
import Setup from "./pages/Setup";
import Home from "./pages/Home";
import Inbox from "./pages/Inbox";
import Projects from "./pages/Projects";
import Founding from "./pages/Founding";
import NewProject from "./pages/NewProject";
import StartProject from "./pages/StartProject";
import ProjectPage from "./pages/Project";
import Roles from "./pages/Roles";
import Settings from "./pages/Settings";
import { Btn } from "./ui/kit";

/** A project's sidebar and its page read the same polls, so the provider sits
    above both. Keyed by the id: moving from one project to another is a new
    frame, not the old one's numbers relabelled until the next tick lands. */
function ProjectRoute() {
  const { projectId = "" } = useParams();
  return (
    <ProjectProvider key={projectId} projectId={projectId}>
      <Shell>
        <ProjectPage />
      </Shell>
    </ProjectProvider>
  );
}

/** The moment between opening the app and the server saying who you are. */
function Waiting({ error, retry }: { error: string | null; retry: () => void }) {
  return (
    <div className="center-page">
      {error ? (
        <div className="card login-card">
          <h2 style={{ marginTop: 0 }}>The server is not answering</h2>
          <p className="help">
            yaaf could not ask it who you are, so it cannot show you anything yet. This is the
            box, not your password — nothing about your sign-in has changed.
          </p>
          <div className="error">{error}</div>
          <div className="actions">
            <Btn kind="primary" onClick={retry}>Try again</Btn>
          </div>
        </div>
      ) : (
        <p className="subtitle">Loading…</p>
      )}
    </div>
  );
}

/** Sign-in and setup are the two screens that exist for somebody with no
    session — a setup link is opened by a person who has never had one. Every
    other route waits for the server to say who is reading. */
function Gate() {
  const { me, error, refresh } = useMe();
  const location = useLocation();
  const open = location.pathname.startsWith("/login") || location.pathname.startsWith("/setup");
  if (open) {
    return (
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/setup" element={<Setup />} />
      </Routes>
    );
  }
  if (me === undefined) return <Waiting error={error} retry={() => void refresh()} />;
  if (me === null) return <Navigate to="/login" replace />;
  // Everything past the door shares one answer to "what needs me?" — and only
  // a signed-in reader asks it, so the poll never runs against the sign-in form.
  return (
    <DeskProvider>
      <Routes>
        <Route path="/home" element={<Shell><Home /></Shell>} />
        <Route path="/inbox" element={<Shell><Inbox /></Shell>} />
        {/* The Desk became the Inbox. Its address keeps working for the emails,
            bookmarks and docs that name it. */}
        <Route path="/desk" element={<Navigate to="/inbox" replace />} />
        <Route path="/projects" element={<Shell><Projects /></Shell>} />
        {/* One door, then the fork. Every "create a project" affordance points
            at /projects/new, which is the question rather than one of its two
            answers; each answer is linkable on its own for anyone who knows. */}
        <Route path="/projects/new" element={<Shell><StartProject /></Shell>} />
        <Route path="/projects/new/describe" element={<Shell><Founding /></Shell>} />
        <Route path="/projects/new/form" element={<Shell><NewProject /></Shell>} />
        <Route path="/projects/found" element={<Navigate to="/projects/new/describe" replace />} />
        <Route path="/projects/:projectId/*" element={<ProjectRoute />} />
        <Route path="/roles" element={<Shell><Roles /></Shell>} />
        <Route path="/settings" element={<Shell><Settings /></Shell>} />
        <Route path="*" element={<Navigate to="/home" replace />} />
      </Routes>
    </DeskProvider>
  );
}

export default function App() {
  return (
    <MeProvider>
      <Gate />
    </MeProvider>
  );
}
