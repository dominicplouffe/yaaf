import { Fragment, useState } from "react";
import { Link, NavLink, Navigate, Route, Routes, useSearchParams } from "react-router-dom";
import { get, post, RecordLine, RecordView } from "../../api";
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
  ["record", "Everything", "every line, filterable by person and kind, each saying on whose authority it happened"],
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

const KIND_LABEL: Record<RecordLine["kind"], string> = {
  message: "Message",
  run: "Run",
  yours: "Your decision",
  ceo: "CEO decided",
  rule: "By a rule",
  proof: "Proof",
};

const FILTERS: { key: "all" | RecordLine["kind"]; label: string }[] = [
  { key: "all", label: "Everything" },
  { key: "message", label: "Messages" },
  { key: "run", label: "Runs" },
  { key: "yours", label: "Your decisions" },
  { key: "ceo", label: "What the CEO decided" },
  { key: "rule", label: "By your rules" },
  { key: "proof", label: "Proof" },
];

/** The record: every line, newest first, filtered by person and kind — and
    each action saying on whose authority it happened. Filters live in the
    address, so "Ada's runs this week" is a link you can send. */
function Record({ ctx }: { ctx: ProjectCtx }) {
  const { projectId } = ctx;
  const [params, setParams] = useSearchParams();
  const kind = (params.get("kind") ?? "all") as "all" | RecordLine["kind"];
  const person = params.get("person") ?? "";
  const days = params.get("days") ?? "7";
  const query = new URLSearchParams({ kind, days, limit: "500" });
  if (person) query.set("agent_id", person);
  const { data, error } = usePoll(
    () => get<RecordView>(`/observe/projects/${projectId}/record?${query}`), 30000,
    [projectId, kind, person, days]);
  const set = (key: string, value: string, fallback: string) => {
    const next = new URLSearchParams(params);
    if (value === fallback) next.delete(key); else next.set(key, value);
    setParams(next, { replace: true });
  };
  const csv = new URLSearchParams(query);
  csv.set("format", "csv");
  csv.set("limit", "2000");

  let day = "";
  return (
    <div className="card record" style={{ marginTop: "var(--s3)" }}>
      <div className="record-bar">
        <div className="seg" role="group" aria-label="Show">
          {FILTERS.map((f) => (
            <button key={f.key} type="button" aria-pressed={kind === f.key}
              className={kind === f.key ? "on" : ""} onClick={() => set("kind", f.key, "all")}>
              {f.label}
            </button>
          ))}
        </div>
        <span className="grow" />
        <label className="record-pick">
          <span className="sr-only">Person</span>
          <select value={person} onChange={(e) => set("person", e.target.value, "")}>
            <option value="">Everyone</option>
            {(data?.people ?? []).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        </label>
        <label className="record-pick">
          <span className="sr-only">When</span>
          <select value={days} onChange={(e) => set("days", e.target.value, "7")}>
            <option value="1">Today</option>
            <option value="7">7 days</option>
            <option value="30">30 days</option>
            <option value="90">90 days</option>
          </select>
        </label>
        <a className="btnlink small" href={`/observe/projects/${projectId}/record?${csv}`} download>
          Export CSV
        </a>
      </div>
      {error && <div className="error">{error}</div>}
      {!data ? <Skeleton rows={6} /> : data.lines.length === 0 ? (
        <p className="help">Nothing of that kind{person ? " by that person" : ""} in this window.</p>
      ) : (
        <div className="record-lines">
          {data.lines.map((line, i) => {
            const label = new Date(line.at).toLocaleDateString([], { weekday: "long", month: "short", day: "numeric" });
            const header = label !== day ? (day = label) : null;
            return (
              <Fragment key={`${line.at}-${i}`}>
                {header && <div className="record-day">{header}</div>}
                <div className={`record-line k-${line.kind}`}>
                  <span className="mono help">{new Date(line.at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}</span>
                  <span className="record-kind">{KIND_LABEL[line.kind]}</span>
                  <span className="record-what">
                    <b>{line.who_id
                      ? <button type="button" className="linkish inline" onClick={() => ctx.openAgent(line.who_id!)}>{line.who}</button>
                      : line.who}</b>{" "}
                    {line.text}
                    {line.authority && <span className="record-why">On whose authority: {line.authority}</span>}
                  </span>
                  {line.run_id && (
                    <Link className="small record-run" to={`/projects/${projectId}/details/activity/runs?run=${encodeURIComponent(line.run_id)}`}>
                      View run
                    </Link>
                  )}
                </div>
              </Fragment>
            );
          })}
          {data.more > 0 && <p className="help">and {data.more} older — narrow the window or export the CSV.</p>}
        </div>
      )}
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
            to={`/projects/${projectId}/details/activity/${path}`}
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
        <Route index element={<Navigate to="record" replace />} />
        <Route path="record" element={<Record ctx={ctx} />} />
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
