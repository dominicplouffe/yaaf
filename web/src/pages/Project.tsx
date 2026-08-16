import { useState } from "react";
import { Navigate, NavLink, Route, Routes, useParams } from "react-router-dom";
import { Dashboard, get, post } from "../api";
import { usePoll } from "../hooks";
import { useProjectLive } from "../ws";
import { statusBadge } from "./Projects";
import Today from "./project/Today";
import Boardroom from "./project/Boardroom";
import Tasks from "./project/Tasks";
import Channels from "./project/Channels";
import Files from "./project/Files";
import Memory from "./project/Memory";
import Runs from "./project/Runs";
import Approvals from "./project/Approvals";
import EvidenceTab from "./project/Evidence";
import Settings from "./project/Settings";

// Ordered by how often the chairman needs them: triage, the conversation, the
// work, the decisions, the proof — then the records, then configuration.
const TABS = [
  ["", "Today"],
  ["boardroom", "Boardroom"],
  ["tasks", "Tasks"],
  ["approvals", "Approvals"],
  ["evidence", "Evidence"],
  ["runs", "Runs"],
  ["files", "Files"],
  ["channels", "Channels"],
  ["memory", "Memory"],
  ["settings", "Settings"],
] as const;

/** Pause wants a recorded reason; stop is terminal and requires typing the
    project's name. In-context confirmation, never a browser dialog. */
function LifecycleConfirm({
  action,
  projectName,
  onConfirm,
  onCancel,
}: {
  action: "pause" | "stop";
  projectName: string;
  onConfirm: (reason: string) => Promise<void>;
  onCancel: () => void;
}) {
  const [reason, setReason] = useState("");
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const armed = action === "pause" || typed.trim() === projectName;

  return (
    <div className="card" style={{ margin: "12px 0" }}>
      <strong>{action === "pause" ? "Pause this project?" : "Stop this project — permanently?"}</strong>
      <p className="help" style={{ margin: "4px 0 8px" }}>
        {action === "pause"
          ? "Running work finishes; nothing new starts until you resume. The reason is recorded and disclosed to the CEO."
          : "Stop is TERMINAL — the project can never run again. The record stays readable."}
      </p>
      <input
        autoFocus
        value={reason}
        placeholder={`Reason to ${action} — recorded, the CEO reads it`}
        onChange={(e) => setReason(e.target.value)}
      />
      {action === "stop" && (
        <input
          style={{ marginTop: 8 }}
          value={typed}
          placeholder={`Type the project's name to confirm: ${projectName}`}
          onChange={(e) => setTyped(e.target.value)}
        />
      )}
      {error && <div className="error">{error}</div>}
      <div className="actions">
        <button
          className={action === "stop" ? "danger" : "primary"}
          disabled={busy || !armed}
          onClick={async () => {
            setBusy(true);
            setError(null);
            try {
              await onConfirm(reason.trim());
            } catch (e) {
              setError((e as Error).message);
              setBusy(false);
            }
          }}
        >
          {action === "pause" ? "⏸ Pause project" : "■ Stop forever"}
        </button>
        <button onClick={onCancel}>Cancel</button>
      </div>
    </div>
  );
}

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
  const [confirming, setConfirming] = useState<"pause" | "stop" | null>(null);

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
          <button
            className="small primary"
            onClick={async () => {
              await post(`/admin/projects/${projectId}/start`);
              reload();
            }}
          >
            ▶ start
          </button>
        )}
        {dash.status === "running" && (
          <button className="small" onClick={() => setConfirming("pause")}>
            ⏸ pause
          </button>
        )}
        {dash.status !== "stopped" && (
          <button className="small danger" onClick={() => setConfirming("stop")}>
            ■ stop
          </button>
        )}
      </div>
      <p className="subtitle">{dash.goal}</p>
      {confirming && (
        <LifecycleConfirm
          action={confirming}
          projectName={dash.name}
          onCancel={() => setConfirming(null)}
          onConfirm={async (reason) => {
            await post(`/admin/projects/${projectId}/${confirming}`, { reason });
            setConfirming(null);
            reload();
          }}
        />
      )}
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
        <Route index element={<Today projectId={projectId} dash={dash} />} />
        <Route path="boardroom" element={<Boardroom projectId={projectId} dash={dash} />} />
        <Route path="tasks" element={<Tasks projectId={projectId} />} />
        {/* Old bookmarks: this tab was "Board" before the rename. */}
        <Route path="board" element={<Navigate to={`/projects/${projectId}/tasks`} replace />} />
        <Route path="channels" element={<Channels projectId={projectId} />} />
        <Route path="memory" element={<Memory projectId={projectId} dash={dash} />} />
        <Route path="files" element={<Files projectId={projectId} />} />
        <Route path="runs" element={<Runs projectId={projectId} dash={dash} />} />
        <Route path="approvals" element={<Approvals projectId={projectId} dash={dash} />} />
        <Route path="evidence" element={<EvidenceTab projectId={projectId} />} />
        <Route path="settings" element={<Settings projectId={projectId} dash={dash} reload={reload} />} />
      </Routes>
    </>
  );
}
