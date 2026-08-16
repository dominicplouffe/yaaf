import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import type { ProjectCtx } from "../Project";
import Runs from "./Runs";
import Channels from "./Channels";
import Memory from "./Memory";
import Decisions from "./Decisions";

// Four ways of asking the same question — what happened? — kept together and
// out of the daily path. Each was a top-level tab competing with the work.
const VIEWS = [
  ["runs", "Runs", "every model call, why it woke, what it cost"],
  ["messages", "Messages", "every channel you can read but not speak in"],
  ["memory", "Memory", "what the company chose to remember"],
  ["decisions", "Decisions", "everything you decided, and everything that decided itself"],
] as const;

export default function Records({ ctx }: { ctx: ProjectCtx }) {
  const { projectId, dash } = ctx;
  return (
    <>
      <div className="pills" style={{ marginTop: 4 }}>
        {VIEWS.map(([path, label]) => (
          <NavLink
            key={path}
            to={`/projects/${projectId}/records/${path}`}
            className={({ isActive }) => (isActive ? "active" : "")}
          >
            {label}
          </NavLink>
        ))}
      </div>

      <Routes>
        <Route index element={<Navigate to="runs" replace />} />
        <Route path="runs" element={<Runs projectId={projectId} dash={dash} />} />
        <Route path="messages" element={<Channels projectId={projectId} />} />
        <Route path="memory" element={<Memory projectId={projectId} ctx={ctx} />} />
        <Route path="decisions" element={<Decisions ctx={ctx} />} />
      </Routes>
    </>
  );
}
