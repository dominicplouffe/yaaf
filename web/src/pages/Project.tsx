import { NavLink, Route, Routes, useParams } from "react-router-dom";
import { Dashboard, get, post } from "../api";
import { usePoll } from "../hooks";
import { useProjectLive } from "../ws";
import { statusBadge } from "./Projects";
import Overview from "./project/Overview";
import Boardroom from "./project/Boardroom";
import Board from "./project/Board";
import Channels from "./project/Channels";
import Memory from "./project/Memory";
import Runs from "./project/Runs";
import Approvals from "./project/Approvals";
import EvidenceTab from "./project/Evidence";
import Settings from "./project/Settings";

const TABS = [
  ["", "Overview"],
  ["boardroom", "Boardroom"],
  ["board", "Board"],
  ["channels", "Channels"],
  ["memory", "Memory"],
  ["runs", "Runs"],
  ["approvals", "Approvals"],
  ["evidence", "Evidence"],
  ["settings", "Settings"],
] as const;

export default function ProjectPage() {
  const { projectId = "" } = useParams();
  const { data: dash, error, reload } = usePoll(
    () => get<Dashboard>(`/observe/projects/${projectId}`),
    6000,
  );
  // Latency hint over the 6s poll above: a change stamp on the socket pulls
  // the same reload forward. The poll stays — it is the source of truth, and
  // the UI works identically (just staler) if the socket never connects.
  useProjectLive(projectId, reload);

  async function lifecycle(action: "start" | "pause" | "stop") {
    const reason =
      action === "start" ? "" : prompt(`Reason to ${action} this project?`) ?? "";
    if (action !== "start" && reason === null) return;
    if (action === "stop" && !confirm("Stop is TERMINAL — the project can never run again. Sure?"))
      return;
    await post(`/admin/projects/${projectId}/${action}`, action === "start" ? undefined : { reason });
    reload();
  }

  if (error) return <div className="error">{error}</div>;
  if (!dash) return <p className="subtitle">Loading…</p>;

  return (
    <>
      <div style={{ display: "flex", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}>
        <h1>{dash.name}</h1>
        {statusBadge(dash.status)}
        {dash.pending_approvals > 0 && (
          <span className="badge warn">⚠ {dash.pending_approvals} approval(s) waiting on you</span>
        )}
        {dash.drought_active && <span className="badge serious">☂ evidence drought</span>}
        <div className="spacer" />
        {dash.status !== "running" && dash.status !== "stopped" && (
          <button className="small primary" onClick={() => lifecycle("start")}>
            ▶ start
          </button>
        )}
        {dash.status === "running" && (
          <button className="small" onClick={() => lifecycle("pause")}>
            ⏸ pause
          </button>
        )}
        {dash.status !== "stopped" && (
          <button className="small danger" onClick={() => lifecycle("stop")}>
            ■ stop
          </button>
        )}
      </div>
      <p className="subtitle">{dash.goal}</p>
      {dash.status_reason && dash.status !== "running" && (
        <div className="notice-box">Status reason: {dash.status_reason}</div>
      )}

      <nav className="tabs">
        {TABS.map(([path, label]) => (
          <NavLink
            key={path}
            to={path === "" ? `/projects/${projectId}` : `/projects/${projectId}/${path}`}
            end={path === ""}
            className={({ isActive }) => "tab" + (isActive ? " active" : "")}
          >
            {label}
          </NavLink>
        ))}
      </nav>

      <Routes>
        <Route index element={<Overview projectId={projectId} dash={dash} />} />
        <Route path="boardroom" element={<Boardroom projectId={projectId} dash={dash} />} />
        <Route path="board" element={<Board projectId={projectId} />} />
        <Route path="channels" element={<Channels projectId={projectId} />} />
        <Route path="memory" element={<Memory projectId={projectId} dash={dash} />} />
        <Route path="runs" element={<Runs projectId={projectId} />} />
        <Route path="approvals" element={<Approvals projectId={projectId} />} />
        <Route path="evidence" element={<EvidenceTab projectId={projectId} />} />
        <Route path="settings" element={<Settings projectId={projectId} dash={dash} reload={reload} />} />
      </Routes>
    </>
  );
}
