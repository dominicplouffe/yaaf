import { useEffect, useState } from "react";
import { Navigate, NavLink, Route, Routes, useLocation, useParams } from "react-router-dom";
import { Dashboard, post } from "../api";
import { InboxExplainer, RaiseInboxReads } from "../inbox";
import { useProjectFrame } from "../projectCtx";
import { clip, statusBrief } from "../ui";
import { Btn } from "../ui/kit";
import { Icon } from "../ui/Icon";
import Overview from "./project/Overview";
import Boardroom from "./project/Boardroom";
import Details from "./project/Details";
import Member from "./project/Member";
import Results from "./project/Results";
import TeamPage from "./project/Team";
import { SettingsPage } from "./project/Settings";
import { PinSuggestion } from "../shortcuts";

export type { ProjectCtx } from "../projectCtx";

// Five pages: Inbox, Team, Chat, Results, Details, and Settings under the
// project's gear. Ten sidebar pages, before that, were learnable but flat —
// they put the Roadmap, the Spend and the Settings on the same footing as the
// team doing the work, and the team was the sixth line. A project is a team
// at work, so what it needs from you and the team come first; what it made is
// next; how it works inside is one page with tabs. The pages a person opens
// every day sit one level down, so each person can pin theirs (shortcuts.tsx).
// Every old address still lands somewhere — see the redirects at the foot of
// the routes.

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
      <strong>{action === "pause" ? "Pause the team?" : `Stop ${projectName} for good?`}</strong>
      <p className="help" style={{ margin: "4px 0 8px" }}>
        {action === "pause"
          ? "Your team finishes what it's doing and starts nothing new until you resume. Nothing is lost, and you can resume any time."
          : "Your team stops working right away, and this project can't be restarted. Your results, files and history stay here to read."}
      </p>
      <input
        autoFocus
        value={reason}
        aria-label={action === "pause" ? "Why are you pausing?" : "Why are you stopping it?"}
        placeholder={action === "pause" ? "Why are you pausing? Your CEO will see this." : "Why are you stopping it? Your CEO will see this."}
        onChange={(e) => setReason(e.target.value)}
      />
      {action === "stop" && (
        <input
          style={{ marginTop: 8 }}
          value={typed}
          aria-label={`Type ${projectName} to confirm`}
          placeholder={`Type ${projectName} to confirm`}
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
          {action === "pause" ? "Pause team" : "Stop project"}
        </button>
        <button onClick={onCancel}>{action === "pause" ? "Cancel" : "Keep working"}</button>
      </div>
    </div>
  );
}

/** The first sentence of the goal, as an identity line. The full brief used to
    render in full above every tab — 400-odd pixels of prose the reader had
    already read, repainted on every navigation. It is one click away now. */
function goalLead(goal: string): string {
  const firstSentence = goal.split(/(?<=\.)\s/)[0] ?? goal;
  return clip(firstSentence.replace(/\s+/g, " ").trim(), 120);
}

/** Said once, under the header, to anybody who may read this project but not
    decide on it. The buttons stay where they are: the server refuses a verb
    the reader does not hold, in a sentence that names who does, and the
    screens already show that sentence where the button was pressed. Hiding
    every control instead would make an observer's view a different product
    from the one the chairman sees — and the point of observing is to see the
    same thing. */
function StandingNotice({ dash }: { dash: Dashboard }) {
  if (dash.can_steer !== false) return null;
  const said =
    dash.my_role === "observer"
      ? "You're an observer here: you can read everything; decisions are for the chairman and co-chairs."
      : dash.my_role === null
        ? "You're reading this as the instance admin. Decisions belong to the people who chair it."
        : null;
  if (!said) return null;
  return (
    <div className="notice-box" style={{ display: "flex", gap: "var(--s2)", alignItems: "baseline", flexWrap: "wrap" }}>
      <span style={{ flex: 1, minWidth: 0 }}>{said}</span>
      <NavLink to={`/projects/${dash.id}/settings?s=people`}>Who chairs it</NavLink>
    </div>
  );
}

/** The "why is nothing happening?" brief, with a way to put it down.

    Two of these briefs are waits on a clock — the daily run cap and the hourly
    wake ceiling — and both clear themselves. That was already true and it did
    not help: the banner had no falsifiable time in it, so a chairman who had
    already dealt with the throttle had no way to tell a live warning from a
    stuck one, and no way to say "I know". Dismissing now hides the brief only
    until the moment the platform said it would clear. If the wait outlives its
    own promise the brief comes back on its own, because at that point it has
    become news again. Paused and stopped are not waits and have no dismiss.

    Kept in sessionStorage, per project and per condition: a dismissal is a
    reading of right now, not a preference worth surviving a browser restart. */
function StatusBriefBox({ dash, projectId, reload }: {
  dash: Dashboard; projectId: string; reload: () => void;
}) {
  const brief = statusBrief(dash);
  const key = brief?.sig ? `yaaf.brief.${projectId}.${brief.sig}` : null;
  const [, tick] = useState(0);
  // The inbox page carries the same button and explainer at its head; the
  // banner over it says the sentence and leaves the rest to the page.
  const onInbox = useLocation().pathname.endsWith("/activity/inbox");

  // The hide expires on a wall clock, so the box has to re-check itself even
  // when no poll lands — otherwise a brief could stay hidden past its moment.
  useEffect(() => {
    const timer = setInterval(() => tick((n) => n + 1), 20_000);
    return () => clearInterval(timer);
  }, []);

  if (!brief) return null;
  if (key) {
    const until = sessionStorage.getItem(key);
    if (until && Date.now() < Number(until)) return null;
  }

  return (
    <div className="notice-box">
      <div className="brief-head">
        <div>
          <span className={`chip ${brief.tone}`}>{brief.chip}</span>{" "}
          <b>{brief.headline}</b>
        </div>
        {key && (
          <button
            className="small"
            title={
              brief.clearsAt
                ? `Hidden until ${new Date(brief.clearsAt).toLocaleTimeString()} — it returns if the wait outlives that.`
                : "Hidden for an hour, then shown again if it is still true."
            }
            onClick={() => {
              const until = brief.clearsAt
                ? new Date(brief.clearsAt).getTime()
                : Date.now() + 3_600_000;
              // A clearsAt already in the past would hide nothing at all; give
              // the dismissal a floor so the click always does something.
              sessionStorage.setItem(key, String(Math.max(until, Date.now() + 60_000)));
              tick((n) => n + 1);
            }}
          >
            dismiss
          </button>
        )}
      </div>
      <div className="help" style={{ marginTop: 4 }}>
        {brief.detail}
      </div>
      {/* The one brief whose cure is a setting the reader may not know exists,
          and whose subject — the messages — had nowhere to be read. */}
      {dash.status === "running" && dash.activity.blocked_by === "inbox_runs" && !onInbox && (
        <>
          <div className="brief-actions">
            {dash.can_steer && (
              <RaiseInboxReads projectId={projectId} reading={dash.activity.inbox_runs} onDone={reload} />
            )}
            <NavLink to={`/projects/${projectId}/details/activity/inbox`}>
              <Btn small icon="inbox">See the unread messages</Btn>
            </NavLink>
          </div>
          <InboxExplainer projectId={projectId} reading={dash.activity.inbox_runs} />
        </>
      )}
    </div>
  );
}

/** A review opens on a schedule, costs nothing to run, and is the one meeting
    the platform holds whether or not the board has anything on it — so when
    one is open it is said on the pages it is about, not only on its own. */
function ReviewBanner({ projectId, number }: { projectId: string; number: number }) {
  return (
    <div className="strip" style={{ marginTop: 0, marginBottom: "var(--s4)" }}>
      <Icon name="meeting" size={18} />
      <div className="grow">
        <strong>Your status meeting with your CEO is open.</strong>
        <div className="sb">
          What got done since the last one, where the project stands, what happens next, and what your CEO needs from you.</div>
      </div>
      <NavLink to={`/projects/${projectId}/details/reviews`}>
        <Btn kind="primary">Open meeting #{number}</Btn>
      </NavLink>
    </div>
  );
}

/** An old address, kept working: everything after the old prefix lands in
    the same place under the new one. */
function Moved({ to, deep }: {
  to: string;
  /** The old route ends in `/*`, so what follows it is carried along. */
  deep?: boolean;
}) {
  // `*` is only this route's own when the route has a splat. Without one,
  // useParams hands back the *parent's* — `/projects/:id/*` — whose value is
  // the old page's own name, so `/deliverables?open=…` went to
  // `details/deliverables/deliverables`, which Details does not have, and it
  // fell back to the Roadmap. Every "Deliverable published" link in the chat
  // and in the daily letter landed there instead of on the file.
  const { "*": splat = "" } = useParams();
  const { search } = useLocation();
  const rest = deep ? splat : "";
  return <Navigate to={`${rest ? `${to}/${rest}` : to}${search}`} replace />;
}

export default function ProjectPage() {
  const frame = useProjectFrame();
  const location = useLocation();
  const [confirming, setConfirming] = useState<"pause" | "stop" | null>(null);

  const ctx = frame?.ctx ?? null;

  if (!ctx) {
    return frame?.error ? <div className="error">{frame.error}</div> : <p className="subtitle">Loading…</p>;
  }
  const { projectId, dash } = ctx;
  const base = `/projects/${projectId}`;
  const sub = location.pathname.slice(base.length).replace(/^\//, "").split("/")[0];
  const onOverview = sub === "";
  const onRoadmap = location.pathname.startsWith(`${base}/details/roadmap`);

  return (
    <>

      <StandingNotice dash={dash} />

      {confirming && (
        <LifecycleConfirm
          action={confirming}
          projectName={dash.name}
          onCancel={() => setConfirming(null)}
          onConfirm={async (reason) => {
            await post(`/admin/projects/${projectId}/${confirming}`, { reason });
            setConfirming(null);
            ctx.reload();
          }}
        />
      )}
      {/* One answer to "why is nothing happening?", on every page — a chairman
          who opens Tasks to see four of them sitting still should not have to
          go to the Overview to learn the project ran out of runs at 01:22. */}
      {/* The chat says this itself, in its presence and the one strip above
          the conversation — a second box about the same wait was noise. */}
      {sub !== "chat" && <StatusBriefBox dash={dash} projectId={projectId} reload={ctx.reload} />}
      <PinSuggestion projectId={projectId} />
      {ctx.error && (
        <div className="error">
          Not refreshing — {ctx.error}. The numbers below are the last that landed.
        </div>
      )}
      {ctx.reviews?.open && (onOverview || onRoadmap) && (
        <ReviewBanner projectId={projectId} number={ctx.reviews.open.number} />
      )}

      <Routes>
        <Route index element={
          <Overview ctx={ctx} onPause={() => setConfirming("pause")} onStop={() => setConfirming("stop")}
            goalLead={goalLead(dash.goal)} />
        } />
        <Route path="team" element={<TeamPage ctx={ctx} />} />
        <Route path="team/:agentId" element={<Member ctx={ctx} />} />
        <Route path="chat" element={<Boardroom projectId={projectId} dash={dash} org={ctx.org} />} />
        <Route path="results" element={<Results ctx={ctx} />} />
        <Route path="details/*" element={<Details ctx={ctx} />} />
        <Route path="settings" element={<SettingsPage ctx={ctx} />} />

        {/* Old bookmarks, emails and links from outside the app. Every page
            that moved keeps working, query and all; nobody relearns a URL
            they wrote down. Ten sidebar pages became five: Overview and Team
            are the Inbox and Team pages, Settings hangs off the project, and
            the rest are tabs of Details. */}
        <Route path="roadmap" element={<Moved to={`${base}/details/roadmap`} />} />
        <Route path="tasks" element={<Moved to={`${base}/details/tasks`} />} />
        <Route path="deliverables" element={<Moved to={`${base}/details/deliverables`} />} />
        <Route path="spend" element={<Moved to={`${base}/details/spend`} />} />
        <Route path="reviews" element={<Moved to={`${base}/details/reviews`} />} />
        <Route path="activity/*" element={<Moved to={`${base}/details/activity`} deep />} />
        <Route path="boardroom" element={<Navigate to={`${base}/chat`} replace />} />
        <Route path="progress" element={<Navigate to={`${base}/details/roadmap`} replace />} />
        <Route path="progress/board" element={<Navigate to={`${base}/details/tasks`} replace />} />
        <Route path="progress/proof" element={<Navigate to={`${base}/details/activity/evidence`} replace />} />
        <Route path="progress/review" element={<Navigate to={`${base}/details/reviews`} replace />} />
        <Route path="records/*" element={<Moved to={`${base}/details/activity`} deep />} />
        <Route path="plan" element={<Navigate to={`${base}/details/roadmap`} replace />} />
        <Route path="work" element={<Navigate to={`${base}/details/tasks`} replace />} />
        <Route path="board" element={<Navigate to={`${base}/details/tasks`} replace />} />
        <Route path="review" element={<Navigate to={`${base}/details/reviews`} replace />} />
        <Route path="evidence" element={<Navigate to={`${base}/details/activity/evidence`} replace />} />
        <Route path="files" element={<Navigate to={`${base}/details/deliverables`} replace />} />
        <Route path="approvals" element={<Navigate to={`${base}/details/activity/decisions`} replace />} />
        <Route path="runs" element={<Navigate to={`${base}/details/activity/runs`} replace />} />
        <Route path="channels" element={<Navigate to={`${base}/details/activity/messages`} replace />} />
        <Route path="inbox" element={<Navigate to={`${base}/details/activity/inbox`} replace />} />
        <Route path="memory" element={<Navigate to={`${base}/details/activity/memory`} replace />} />
        <Route path="*" element={<Navigate to={base} replace />} />
      </Routes>

    </>
  );
}
