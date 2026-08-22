import { useEffect, useState } from "react";
import { Navigate, NavLink, Route, Routes, useParams } from "react-router-dom";
import {
  ApprovalRow,
  Dashboard,
  get,
  HealthCheck,
  HypothesisRow,
  OrgView,
  post,
  ProjectHealth,
} from "../api";
import { usePoll } from "../hooks";
import { useProjectLive } from "../ws";
import { statusBadge } from "./Projects";
import {
  budgetOf,
  clip,
  clockClass,
  DecideButtons,
  fmtLeft,
  fmtUnit,
  statusBrief,
} from "../ui";
import Overview from "./project/Overview";
import Plan from "./project/Plan";
import Boardroom from "./project/Boardroom";
import Work from "./project/Work";
import Deliverables from "./project/Deliverables";
import Records from "./project/Records";
import Review from "./project/Review";
import Settings from "./project/Settings";

// Two groups, not one list of ten. Left is the daily job in the order it gets
// done — triage, talk, the work, the output. Right is what you open when
// something looks wrong or needs configuring, and it reads quieter because it is.
const TABS = [
  ["", "Overview"],
  ["boardroom", "Boardroom"],
  ["plan", "Plan"],
  ["work", "Work"],
  ["review", "Review"],
  ["deliverables", "Deliverables"],
] as const;

const UTILITY_TABS = [
  ["records", "Records"],
  ["settings", "Settings"],
] as const;

/** Everything a project tab needs, polled once in the shell. Before this, four
    tabs each polled /org on their own schedule and disagreed with each other. */
export interface ProjectCtx {
  projectId: string;
  dash: Dashboard;
  approvals: ApprovalRow[];
  hypotheses: HypothesisRow[];
  org: OrgView | null;
  health: ProjectHealth | null;
  reload: () => void;
  reloadApprovals: () => void;
  reloadOrg: () => void;
  reloadHealth: () => void;
}

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

/** The first sentence of the goal, as an identity line. The full brief used to
    render in full above every tab — 400-odd pixels of prose the reader had
    already read, repainted on every navigation. It is one click away now. */
function goalLead(goal: string): string {
  const firstSentence = goal.split(/(?<=\.)\s/)[0] ?? goal;
  return clip(firstSentence.replace(/\s+/g, " ").trim(), 120);
}

// A warning is not a failure, and the setup badge must not say it is. It used
// to count every check that was not "ok" and paint the total in critical red:
// a project whose five warnings were all "a newer role version exists, adopt
// it when ready" read as "5 to fix", identical to five hard outages, with
// nothing in the badge to tell them apart and nothing actually to fix. The API
// already draws the line — `ok` is false only on a real failure — so the badge
// respects it, and names the thing rather than counting it. "5 role updates"
// tells you what you are looking at; "5 to fix" tells you that you are late.
const WARN_LABELS: Record<string, [one: string, many: string]> = {
  "role-drift": ["role update", "role updates"],
  "data-source": ["data source to check", "data sources to check"],
  connectors: ["connector to finish", "connectors to finish"],
  activity: ["activity notice", "activity notices"],
  chat: ["chat notice", "chat notices"],
  slack: ["Slack notice", "Slack notices"],
  billing: ["billing notice", "billing notices"],
};

function warnLabel(warned: HealthCheck[]): string {
  // Every check key is either "kind" or "kind:instance"; a single kind can be
  // named, a mixture cannot honestly be.
  const kinds = new Set(warned.map((c) => c.key.split(":")[0]));
  const named = kinds.size === 1 ? WARN_LABELS[[...kinds][0]] : undefined;
  if (!named) return "to review";
  return warned.length === 1 ? named[0] : named[1];
}

/** The header's verdict on setup: red only when something is actually broken,
    and always a link to the list that explains it. */
function setupBadge(dash: Dashboard, health: ProjectHealth | null) {
  if (!health) return <span className="help">checking…</span>;
  const failed = health.checks.filter((c) => c.status === "fail");
  const warned = health.checks.filter((c) => c.status === "warn");
  if (!failed.length && !warned.length) {
    return <span className="badge good">healthy</span>;
  }
  const critical = failed.length > 0;
  const shown = critical ? failed : warned;
  return (
    <NavLink
      to={`/projects/${dash.id}`}
      className={`badge ${critical ? "critical" : "warn"}`}
      title={shown.map((c) => c.detail).join("\n")}
    >
      {shown.length} {critical ? "to fix" : warnLabel(warned)}
    </NavLink>
  );
}

/** One strip of facts that are true on every tab: is it alive, is anyone
    working, what has it cost, is it configured — plus the two lifecycle acts. */
function ProjectBar({
  dash,
  health,
  onPause,
  onStop,
  onStart,
}: {
  dash: Dashboard;
  health: ProjectHealth | null;
  onPause: () => void;
  onStop: () => void;
  onStart: () => void;
}) {
  const [brief, setBrief] = useState(false);
  const { unit, spent, cap, warnRatio, frac } = budgetOf(dash);
  const meterCls = frac >= 1 ? "over" : frac >= warnRatio ? "warn" : "";

  return (
    <>
      <div className="pbar">
        <div className="id">
          <div className="t">
            <h1>{dash.name}</h1>
            {statusBadge(dash.status)}
            {dash.drought_active && <span className="badge serious">☂ evidence drought</span>}
          </div>
          <div className="kicker">
            {goalLead(dash.goal)}{" "}
            <button onClick={() => setBrief(!brief)} aria-expanded={brief}>
              {brief ? "hide the brief" : "the full brief ▾"}
            </button>
          </div>
        </div>

        <div className="vitals">
          <div className="vital">
            <span className="l">Working</span>
            <span className="v">
              {dash.runs.running > 0 ? (
                <>
                  <span className="working-dot" aria-hidden="true" />
                  {dash.runs.running} agent{dash.runs.running === 1 ? "" : "s"}
                </>
              ) : (
                <span className="help">idle{dash.runs.queued > 0 ? ` · ${dash.runs.queued} queued` : ""}</span>
              )}
            </span>
          </div>

          <div className="vital">
            <span className="l">This month</span>
            <span className="v">
              {fmtUnit(spent, unit)} <span className="help">/ {fmtUnit(cap, unit)}</span>
              <span className={`mini-meter ${meterCls}`}>
                <i style={{ width: `${Math.min(frac, 1) * 100}%` }} />
              </span>
            </span>
          </div>

          <div className="vital">
            <span className="l">Setup</span>
            <span className="v">{setupBadge(dash, health)}</span>
          </div>

          <div className="vital">
            <span className="l">&nbsp;</span>
            <span className="v">
              {dash.status !== "running" && dash.status !== "stopped" && (
                <button className="small primary" onClick={onStart}>▶ start</button>
              )}
              {dash.status === "running" && (
                <button className="small" onClick={onPause} title="Pause this project">⏸</button>
              )}
              {dash.status !== "stopped" && (
                <button className="small danger" onClick={onStop} title="Stop this project">■</button>
              )}
            </span>
          </div>
        </div>
      </div>

      {brief && (
        <div className="briefbox">
          <strong style={{ color: "var(--ink)" }}>The brief every agent reads</strong>
          <p style={{ whiteSpace: "pre-wrap", margin: "6px 0 0" }}>{dash.goal}</p>
        </div>
      )}
    </>
  );
}

/** Why this one is on the chairman's desk, in a clause.

    It used to read "Nobody else can decide this" for every kind — a claim
    about the constitution, made without reading it. On a project whose
    authority dial hands the CEO the plan, the scope and the date, that
    sentence was false, and false in the most expensive direction: it told a
    chairman who had deliberately delegated that he was still the bottleneck.
    Each clause below is true at every dial setting, because each names
    something the dial does not delegate. */
function whyItIsYours(kind: string): string {
  switch (kind) {
    case "spend":
      return "Above your approval threshold, so it is never an agent's call.";
    case "legal":
      return "Anything with legal weight is yours by rule, at any autonomy setting.";
    case "tool_request":
      return "A capability the company does not have — you hold the values.";
    case "assist":
      return "A step only a human can take. The platform hands these over, never defeats them.";
    case "owner_decision":
      return "The CEO judged this yours rather than its own.";
    default:
      return "The constitution routes this kind of decision to you.";
  }
}

/** What a tab cannot do: be seen from the other five. Approvals expire to DENY,
    so the one thing that must never depend on the chairman opening the right
    tab is the decision that runs out of time on its own. */
function ActionStrip({
  projectId,
  dash,
  approvals,
  hypotheses,
  onDecided,
}: {
  projectId: string;
  dash: Dashboard;
  approvals: ApprovalRow[];
  hypotheses: HypothesisRow[];
  onDecided: () => void;
}) {
  const pending = approvals
    .filter((a) => a.status === "pending")
    .sort((a, b) => a.expires_at.localeCompare(b.expires_at));
  const expiredBets = hypotheses.filter((h) => h.status === "expired" && !h.extended_once);
  // Proposed scope is the CEO parked on the chairman: its report can say
  // "waiting on R-01 acceptance" without saying where accepting happens, so
  // this strip says it from every tab. Explore is excluded — it has no Plan
  // tab to send anyone to, and proposals are refused there at the door.
  // …unless the dial gave acceptance to the CEO, in which case a proposal is
  // the CEO's own inbox and saying "awaits your acceptance" would be a lie
  // about who is holding the company up.
  const ceoAcceptsScope = dash.constitution["plan_authority"] === "ceo";
  const proposedScope = dash.mode !== "explore" && dash.requirements_proposed > 0
    ? dash.requirements_proposed : 0;

  const scopeNeedsYou = proposedScope > 0 && !ceoAcceptsScope;

  if (pending.length === 0 && expiredBets.length === 0 && !scopeNeedsYou) return null;

  const rest = pending.length - 1 + expiredBets.length + (scopeNeedsYou ? 1 : 0);

  if (pending.length > 0) {
    const a = pending[0];
    return (
      <div className="strip">
        <span className="chip warn">{a.kind.replaceAll("_", " ")}</span>
        <div className="grow">
          <div>
            <strong>{clip(a.title, 90)}</strong>
            {a.amount_usd !== null && a.amount_usd > 0 && (
              <span className="mono"> — ${a.amount_usd.toLocaleString()}</span>
            )}
          </div>
          <div className="sb">
            {whyItIsYours(a.kind)} It auto-denies in{" "}
            <span className={`clock ${clockClass(a.expires_at)}`}>
              {fmtLeft(a.expires_at).replace(" left", "")}
            </span>
            {rest > 0 && ` · ${rest} more waiting on you`}
          </div>
        </div>
        <DecideButtons approvalId={a.id} onDone={onDecided} />
        <NavLink to={`/projects/${projectId}/records/decisions`}>the full case →</NavLink>
      </div>
    );
  }

  if (scopeNeedsYou) {
    return (
      <div className="strip">
        <span className="chip warn">scope proposed</span>
        <div className="grow">
          <div>
            <strong>
              {proposedScope === 1
                ? "A requirement awaits your acceptance"
                : `${proposedScope} requirements await your acceptance`}
            </strong>
          </div>
          <div className="sb">
            The CEO proposed what “done” should mean and can create no work for it
            until you accept or cut each one
            {expiredBets.length > 0 && ` · ${expiredBets.length} more waiting on you`}
          </div>
        </div>
        <NavLink to={`/projects/${projectId}/plan`}>decide on the Plan tab →</NavLink>
      </div>
    );
  }

  const h = expiredBets[0];
  return (
    <div className="strip serious">
      <span className="chip serious">bet expired</span>
      <div className="grow">
        <div>
          <strong>“{clip(h.statement, 90)}”</strong> reached its kill date
        </div>
        <div className="sb">
          You hold the one extension — or let it stand and the CEO pivots
          {rest > 0 && ` · ${rest} more waiting on you`}
        </div>
      </div>
      <NavLink to={`/projects/${projectId}/work`}>decide →</NavLink>
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
function StatusBriefBox({ dash, projectId }: { dash: Dashboard; projectId: string }) {
  const brief = statusBrief(dash);
  const key = brief?.sig ? `yaaf.brief.${projectId}.${brief.sig}` : null;
  const [, tick] = useState(0);

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
    </div>
  );
}

export default function ProjectPage() {
  const { projectId = "" } = useParams();
  const { data: dash, error, at, reload } = usePoll(
    () => get<Dashboard>(`/observe/projects/${projectId}`),
    6000,
  );
  // Latency hint over the 6s poll above: a change stamp on the socket pulls
  // the same reload forward. The poll stays — it is the source of truth, and
  // the UI works identically (just staler) if the socket never connects.
  useProjectLive(projectId, reload);

  // Polled once here, handed down. The strip needs approvals and bets on every
  // tab anyway, so the tabs stop fetching them a second time on their own clock.
  const { data: approvals, reload: reloadApprovals } = usePoll(
    () => get<ApprovalRow[]>(`/observe/projects/${projectId}/approvals`),
    6000,
  );
  const { data: hypotheses } = usePoll(
    () => get<HypothesisRow[]>(`/observe/projects/${projectId}/hypotheses`),
    15000,
  );
  const { data: org, reload: reloadOrg } = usePoll(
    () => get<OrgView>(`/observe/projects/${projectId}/org`),
    15000,
  );
  const { data: health, reload: reloadHealth } = usePoll(
    () => get<ProjectHealth>(`/observe/projects/${projectId}/health`),
    10000,
  );

  const [confirming, setConfirming] = useState<"pause" | "stop" | null>(null);

  // A failed poll must not blank the page. The last good numbers stay on screen
  // with their timestamp; the banner says they've stopped refreshing.
  if (!dash) {
    return error ? <div className="error">{error}</div> : <p className="subtitle">Loading…</p>;
  }

  const ctx: ProjectCtx = {
    projectId,
    dash,
    approvals: approvals ?? [],
    hypotheses: hypotheses ?? [],
    org,
    health,
    reload,
    reloadApprovals,
    reloadOrg,
    reloadHealth,
  };

  const tabClass = ({ isActive }: { isActive: boolean }) => "tab" + (isActive ? " active" : "");
  const utilClass = ({ isActive }: { isActive: boolean }) =>
    "tab util" + (isActive ? " active" : "");

  return (
    <>
      <ProjectBar
        dash={dash}
        health={health}
        onStart={async () => {
          await post(`/admin/projects/${projectId}/start`);
          reload();
        }}
        onPause={() => setConfirming("pause")}
        onStop={() => setConfirming("stop")}
      />

      <ActionStrip
        projectId={projectId}
        dash={dash}
        approvals={ctx.approvals}
        hypotheses={ctx.hypotheses}
        onDecided={() => {
          reloadApprovals();
          reload();
        }}
      />

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
      {/* One answer to "why is nothing happening?", on every tab — a chairman
          who opens Work to see four tasks sitting still should not have to go
          to Overview to learn the company ran out of runs at 01:22. */}
      <StatusBriefBox dash={dash} projectId={projectId} />
      {error && (
        <div className="error">
          Not refreshing — {error}. The numbers below are the last that landed.
        </div>
      )}

      <nav className="tabs">
        {/* Explore has no plan by design, so it gets no Plan tab — a tab that
            only ever explains its own absence is clutter, not honesty. */}
        {TABS.filter(([path]) => path !== "plan" || dash.mode !== "explore")
          .map(([path, label]) => (
          <NavLink
            key={path}
            to={path === "" ? `/projects/${projectId}` : `/projects/${projectId}/${path}`}
            end={path === ""}
            className={tabClass}
          >
            {label}
          </NavLink>
        ))}
        <span className="sp" />
        <span className="div" />
        {UTILITY_TABS.map(([path, label]) => (
          <NavLink key={path} to={`/projects/${projectId}/${path}`} className={utilClass}>
            {label}
          </NavLink>
        ))}
      </nav>

      <Routes>
        <Route index element={<Overview ctx={ctx} asOf={at} error={error} />} />
        <Route path="boardroom" element={<Boardroom projectId={projectId} dash={dash} />} />
        <Route path="plan" element={<Plan ctx={ctx} />} />
        <Route path="work" element={<Work ctx={ctx} />} />
        <Route path="review" element={<Review ctx={ctx} />} />
        <Route path="deliverables" element={<Deliverables projectId={projectId} />} />
        <Route path="records/*" element={<Records ctx={ctx} />} />
        <Route path="settings" element={<Settings projectId={projectId} dash={dash} reload={reload} />} />

        {/* Old bookmarks and links from outside the app. Every tab that moved
            keeps working; nobody has to relearn a URL they had written down. */}
        <Route path="board" element={<Navigate to={`/projects/${projectId}/work`} replace />} />
        <Route path="tasks" element={<Navigate to={`/projects/${projectId}/work`} replace />} />
        <Route path="evidence" element={<Navigate to={`/projects/${projectId}/work`} replace />} />
        <Route path="files" element={<Navigate to={`/projects/${projectId}/deliverables`} replace />} />
        <Route path="approvals" element={<Navigate to={`/projects/${projectId}/records/decisions`} replace />} />
        <Route path="runs" element={<Navigate to={`/projects/${projectId}/records/runs`} replace />} />
        <Route path="channels" element={<Navigate to={`/projects/${projectId}/records/messages`} replace />} />
        <Route path="memory" element={<Navigate to={`/projects/${projectId}/records/memory`} replace />} />
      </Routes>
    </>
  );
}
