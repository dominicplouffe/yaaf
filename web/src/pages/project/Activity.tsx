import { useState } from "react";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { get, post } from "../../api";
import { ProjectBrief, BriefBody } from "../../briefing";
import { usePoll } from "../../hooks";
import { Skeleton } from "../../ui";
import { Seg } from "../../ui/kit";
import type { ProjectCtx } from "../../projectCtx";
import Runs from "./Runs";
import AgentInbox from "./AgentInbox";
import Channels from "./Channels";
import Memory from "./Memory";
import Decisions from "./Decisions";
import Evidence from "./Evidence";

// Every way of asking "what happened?", on one page. The five record views
// were a utility tab; they lead with the narrative now, because "what did I
// miss?" is the question this page is opened with, and the five tables are
// where that sentence's evidence lives.
const VIEWS = [
  ["summary", "What happened", "the briefing: what moved, what is worth your eye"],
  ["runs", "Runs", "every model call, why it woke, what it cost"],
  ["decisions", "Decisions", "everything you decided, and everything that decided itself"],
  // Evidence is a record and belongs with the records. On the Work tab it was
  // drawn as a queue with a verdict button on every row and a percentage of it
  // done, which is a demand rather than an account.
  ["evidence", "Proof", "every claim the agents made, and what the platform could check"],
  // Before Messages: the inbox answers "who has not read what", which is the
  // question the inbox banner raises and the channel log cannot answer.
  ["inbox", "Agent inbox", "each agent's unread messages"],
  ["messages", "Messages", "every channel you can read but not speak in"],
  ["memory", "Memory", "what the project chose to remember"],
] as const;

type Window = "mark" | "24" | "168";

/** The briefing, one window at a time. "Since you last looked" is the default
    because it is the question; the fixed windows are for a reader who wants to
    compare. Marking it read is an explicit press, never a side effect of the
    page polling — a mark that moved on every poll would make "since you last
    looked" permanently empty. */
function Summary({ ctx }: { ctx: ProjectCtx }) {
  const { projectId } = ctx;
  const [win, setWin] = useState<Window>("mark");
  const [marked, setMarked] = useState<string | null>(null);
  const { data, error, reload } = usePoll(
    () => get<ProjectBrief>(
      `/observe/projects/${projectId}/briefing${win === "mark" ? "" : `?hours=${win}`}`),
    30000, [win, projectId],
  );
  return (
    <div className="card" style={{ marginTop: "var(--s3)" }}>
      <div className="bf-bar">
        <Seg<Window>
          value={win}
          onPick={setWin}
          options={[
            { key: "mark", label: "Since you last looked" },
            { key: "24", label: "24 hours" },
            { key: "168", label: "7 days" },
          ]}
        />
        <span className="grow" />
        {win === "mark" && data && !data.quiet && (
          <button className="small" onClick={async () => {
            await post(`/admin/projects/${projectId}/briefing/seen`, { until: data.until });
            setMarked(data.until);
            reload();
          }}>
            Mark as read
          </button>
        )}
      </div>
      {error && <div className="error">{error}</div>}
      {marked && win === "mark" && data?.quiet && (
        <p className="help">Marked as read. New things land here as they happen.</p>
      )}
      {!data ? <Skeleton rows={4} /> : <BriefBody brief={data} />}
    </div>
  );
}

export default function Activity({ ctx }: { ctx: ProjectCtx }) {
  const { projectId, dash } = ctx;
  return (
    <>
      <div className="pills" style={{ marginTop: 4 }}>
        {VIEWS.map(([path, label, means]) => (
          <NavLink
            key={path}
            to={`/projects/${projectId}/activity/${path}`}
            title={means}
            className={({ isActive }) => (isActive ? "active" : "")}
          >
            {label}
            {/* Where the inbox banner's number lives now: on the door to it,
                not over every page of a project that is resting as designed. */}
            {path === "inbox" && dash.activity.waiting.unread > 0 && (
              <span className="pill-n">{dash.activity.waiting.unread}</span>
            )}
          </NavLink>
        ))}
      </div>

      <Routes>
        <Route index element={<Navigate to="summary" replace />} />
        <Route path="summary" element={<Summary ctx={ctx} />} />
        <Route path="runs" element={<Runs projectId={projectId} dash={dash} />} />
        <Route path="inbox" element={<AgentInbox ctx={ctx} />} />
        <Route path="messages" element={<Channels projectId={projectId} />} />
        <Route path="memory" element={<Memory projectId={projectId} ctx={ctx} />} />
        <Route path="decisions" element={<Decisions ctx={ctx} />} />
        <Route path="evidence" element={<Evidence projectId={projectId} />} />
      </Routes>
    </>
  );
}
