import { useEffect, useState } from "react";
import { Navigate, NavLink, Route, Routes, useLocation, useParams } from "react-router-dom";
import {
  ApprovalRow,
  AskRow,
  Dashboard,
  get,
  HealthCheck,
  HypothesisRow,
  OrgView,
  PlanView,
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
import { Badge, Btn, IconBtn, PageHead, TeamStrip, Term, Vital, VitalsBar } from "../ui/kit";
import { deliveryVerdict, roster, waitingOnYou, working } from "../ui/project";
import Overview from "./project/Overview";
import Progress from "./project/Progress";
import Boardroom from "./project/Boardroom";
import Deliverables from "./project/Deliverables";
import Records from "./project/Records";
import Settings from "./project/Settings";

// Five, where there were eight. Plan, Work and Review each answered the same
// question — will this land, and is any of it real — and a reader had to hold
// the join between them in their head: a requirement here, the task serving it
// there, the evidence settling it somewhere else. They are one tab with three
// lenses now. Review folded in with them because a review is a moment on the
// plan rather than a place, and a tab that is empty most of the month teaches
// people to stop opening it. Settings left the row entirely: configuration is
// something you go and do, not somewhere you live.
const TABS = [
  ["", "Overview"],
  ["boardroom", "Boardroom"],
  ["progress", "Progress"],
  ["deliverables", "Deliverables"],
] as const;

const UTILITY_TABS = [["records", "Records"]] as const;

/** Everything a project tab needs, polled once in the shell. Before this, four
    tabs each polled /org on their own schedule and disagreed with each other. */
export interface ProjectCtx {
  projectId: string;
  dash: Dashboard;
  /** Polled by the shell, not by the tabs. The header needs the commitment to
      say whether delivery is at risk, and Progress needs all of it — two polls
      on two clocks is two answers to one question. */
  plan: PlanView | null;
  approvals: ApprovalRow[];
  asks: AskRow[];
  hypotheses: HypothesisRow[];
  org: OrgView | null;
  health: ProjectHealth | null;
  reload: () => void;
  reloadApprovals: () => void;
  reloadOrg: () => void;
  reloadHealth: () => void;
  reloadPlan: () => void;
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

/** The frame every tab of a project sits in.

    Three things, in this order, and they do not move between tabs: who this
    project is, the four numbers that say whether it is well, and the people
    doing the work. The strip it replaces crammed four unaligned micro-columns
    into a header and ended in two unlabelled glyph buttons, one of which was a
    bare red square that stops a project for good. */
function ProjectFrame({
  dash,
  health,
  plan,
  org,
  approvals,
  asks,
  onPause,
  onStop,
  onStart,
}: {
  dash: Dashboard;
  health: ProjectHealth | null;
  plan: PlanView | null;
  org: OrgView | null;
  approvals: ApprovalRow[];
  asks: AskRow[];
  onPause: () => void;
  onStop: () => void;
  onStart: () => void;
}) {
  const [brief, setBrief] = useState(false);
  const { unit, spent, cap, warnRatio, frac } = budgetOf(dash);
  const verdict = deliveryVerdict(dash, plan);
  const need = waitingOnYou(dash, approvals, asks, plan);
  const people = roster(org);
  const awake = working(people);

  const vitals: Vital[] = [
    { label: "Delivery", value: verdict.label, sub: verdict.sub, tone: verdict.tone },
    {
      label: "Spent this month",
      value: fmtUnit(spent, unit),
      sub: `of the ${fmtUnit(cap, unit)} you capped it at`,
      tone: frac >= 1 ? "critical" : frac >= warnRatio ? "warn" : undefined,
    },
    {
      label: "Waiting on you",
      value: need.count,
      sub: need.count === 0
        ? "nothing needs a decision from you"
        : need.soonest
          ? `the soonest decides itself ${fmtLeft(need.soonest).replace(" left", "")} from now`
          : "nothing else will decide these",
      tone: need.count === 0 ? undefined : "serious",
    },
    {
      label: "Working right now",
      value: awake,
      sub: awake === 0
        ? dash.runs.queued > 0
          ? `nobody yet — ${dash.runs.queued} queued to start`
          : "nobody is working on this at the moment"
        : `of ${people.length} ${people.length === 1 ? "person" : "people"}${dash.runs.queued > 0 ? ` · ${dash.runs.queued} more queued` : ""}`,
    },
  ];

  return (
    <>
      <PageHead
        crumbs={
          <>
            <NavLink to="/projects">Projects</NavLink>
            <span>/</span>
            <span>{dash.name}</span>
          </>
        }
        title={dash.name}
        badge={
          <>
            {statusBadge(dash.status)}
            {dash.drought_active && (
              <Badge tone="serious">
                <Term means="Nothing outside this project has verified any of its claims for longer than your rules allow. Work carries on; the clock is a warning that none of it has been checked by anything but the team that did it.">
                  no outside proof lately
                </Term>
              </Badge>
            )}
            {setupBadge(dash, health)}
          </>
        }
        lede={
          <>
            {goalLead(dash.goal)}{" "}
            <button
              className="linkish"
              onClick={() => setBrief(!brief)}
              aria-expanded={brief}
              style={{ minHeight: 0, padding: 0, border: 0, background: "none", font: "inherit" }}
            >
              {brief ? "hide the brief" : "read the brief"}
            </button>
          </>
        }
        actions={
          <>
            {dash.status !== "running" && dash.status !== "stopped" && (
              <Btn kind="primary" icon="play" onClick={onStart}>Start</Btn>
            )}
            {dash.status === "running" && (
              <Btn icon="pause" onClick={onPause}>Pause</Btn>
            )}
            {dash.status !== "stopped" && (
              <Btn kind="danger" icon="stop" onClick={onStop}>Stop</Btn>
            )}
            <NavLink to={`/projects/${dash.id}/settings`} aria-label="Project settings" title="Project settings">
              <IconBtn icon="gear" label="Project settings" />
            </NavLink>
          </>
        }
      />

      {brief && (
        <div className="briefbox">
          <strong style={{ color: "var(--ink)" }}>The brief every one of them works from</strong>
          <p style={{ whiteSpace: "pre-wrap", margin: "6px 0 0" }}>{dash.goal}</p>
        </div>
      )}

      <VitalsBar vitals={vitals} />

      {people.length > 0 && (
        <div style={{ display: "flex", alignItems: "center", gap: "var(--s3)", margin: "var(--s4) 0 0", flexWrap: "wrap" }}>
          <span style={{ fontSize: "var(--t-micro)", color: "var(--muted)" }}>Your team</span>
          <TeamStrip
            people={people.slice(0, 8)}
            note={people.length > 8 ? `and ${people.length - 8} more` : undefined}
          />
          <NavLink to={`/projects/${dash.id}`} style={{ fontSize: "var(--t-small)", fontWeight: 600 }}>
            See the org chart
          </NavLink>
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
      return "A capability the project does not have — you hold the values.";
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
  // about who is holding the project up.
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
              <span> — ${a.amount_usd.toLocaleString()}</span>
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
  const location = useLocation();
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
  const { data: asks } = usePoll(
    () => get<AskRow[]>(`/observe/projects/${projectId}/asks`),
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
  const { data: plan, reload: reloadPlan } = usePoll(
    () => get<PlanView>(`/observe/projects/${projectId}/plan`),
    8000,
  );

  const [confirming, setConfirming] = useState<"pause" | "stop" | null>(null);
  const onOverview = location.pathname.replace(/\/$/, "") === `/projects/${projectId}`;

  // A failed poll must not blank the page. The last good numbers stay on screen
  // with their timestamp; the banner says they've stopped refreshing.
  if (!dash) {
    return error ? <div className="error">{error}</div> : <p className="subtitle">Loading…</p>;
  }

  const ctx: ProjectCtx = {
    projectId,
    dash,
    approvals: approvals ?? [],
    asks: asks ?? [],
    hypotheses: hypotheses ?? [],
    org,
    health,
    plan,
    reload,
    reloadApprovals,
    reloadOrg,
    reloadHealth,
    reloadPlan,
  };

  const tabClass = ({ isActive }: { isActive: boolean }) => "tab" + (isActive ? " active" : "");
  const utilClass = ({ isActive }: { isActive: boolean }) =>
    "tab util" + (isActive ? " active" : "");

  return (
    <>
      <ProjectFrame
        dash={dash}
        health={health}
        plan={plan}
        org={org}
        approvals={ctx.approvals}
        asks={ctx.asks}
        onStart={async () => {
          await post(`/admin/projects/${projectId}/start`);
          reload();
        }}
        onPause={() => setConfirming("pause")}
        onStop={() => setConfirming("stop")}
      />

      {/* Everywhere except Overview, which lists these in full a few hundred
          pixels below. The strip exists so a decision that expires on its own
          is visible from the tab you happen to be on — not so it is visible
          twice on the one tab that already leads with it. */}
      {!onOverview && (
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
      )}

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
          to Overview to learn the project ran out of runs at 01:22. */}
      <StatusBriefBox dash={dash} projectId={projectId} />
      {error && (
        <div className="error">
          Not refreshing — {error}. The numbers below are the last that landed.
        </div>
      )}

      <nav className="tabs">
        {/* Explore keeps Progress. It has no requirements by design, but it
            very much has work and proof, and those are two of the three lenses
            — the tab drops the one that does not apply rather than vanishing. */}
        {TABS.map(([path, label]) => (
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
        <Route path="boardroom" element={<Boardroom projectId={projectId} dash={dash} org={org} />} />
        <Route path="progress/*" element={<Progress ctx={ctx} />} />
        <Route path="deliverables" element={<Deliverables projectId={projectId} />} />
        <Route path="records/*" element={<Records ctx={ctx} />} />
        <Route path="settings" element={<Settings projectId={projectId} dash={dash} org={org} reload={reload} />} />

        {/* Old bookmarks and links from outside the app. Every tab that moved
            keeps working; nobody has to relearn a URL they had written down. */}
        <Route path="plan" element={<Navigate to={`/projects/${projectId}/progress`} replace />} />
        <Route path="work" element={<Navigate to={`/projects/${projectId}/progress/board`} replace />} />
        <Route path="review" element={<Navigate to={`/projects/${projectId}/progress/review`} replace />} />
        <Route path="board" element={<Navigate to={`/projects/${projectId}/progress/board`} replace />} />
        <Route path="tasks" element={<Navigate to={`/projects/${projectId}/progress/board`} replace />} />
        <Route path="evidence" element={<Navigate to={`/projects/${projectId}/progress/proof`} replace />} />
        <Route path="files" element={<Navigate to={`/projects/${projectId}/deliverables`} replace />} />
        <Route path="approvals" element={<Navigate to={`/projects/${projectId}/records/decisions`} replace />} />
        <Route path="runs" element={<Navigate to={`/projects/${projectId}/records/runs`} replace />} />
        <Route path="channels" element={<Navigate to={`/projects/${projectId}/records/messages`} replace />} />
        <Route path="memory" element={<Navigate to={`/projects/${projectId}/records/memory`} replace />} />
      </Routes>
    </>
  );
}
