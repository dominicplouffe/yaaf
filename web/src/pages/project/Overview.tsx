import { ReactNode, useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  AgendaView,
  ChannelRow,
  Dashboard,
  get,
  HealthCheck,
  MessageRow,
  post,
} from "../../api";
import Agenda from "../../agenda";
import { itemsFor, needsYou, useDesk, WARN_VERDICTS } from "../../desk";
import { fmtWhen, usePoll } from "../../hooks";
import Markdown from "../../Markdown";
import { firstName, ProjectCtx } from "../../projectCtx";
import {
  AsOf,
  budgetOf,
  clip,
  clipMarkdown,
  earlyRunSentence,
  fmtSoon,
  fmtUnit,
  fmtWhenShort,
  NotedButton,
  paceDate,
  runHealth,
} from "../../ui";
import { Avatar, Badge, Btn, Term, Verdict } from "../../ui/kit";
import { Icon } from "../../ui/Icon";
import { chairmanDecides, deliveryVerdict, cycleOverdue, flagPlain, fmtDate, fmtRoll, fullyAutonomous } from "../../ui/project";
import { statusBadge } from "../Projects";
import { InboxView } from "../Inbox";

/** A project's home is its Inbox: what it needs from you and what you
    should know, in about five seconds.

      is it on track?            one line — delivery, done, results, money, runs
      does anything need me?     this project's rows of the one Inbox, with their buttons
      what should I know?        the CEO's last word, its clocks, the calendar, health

    The team used to be drawn here too, as an org chart with the decisions in
    the chairman's box. At thirteen teams the chart was three screens wide and
    the decisions scrolled away with it, so the team is a page of its own and
    this one keeps what you open a project to find out. Everything here links
    to where its detail lives. */

/** An obligation to speak, opened by the platform from arithmetic or a failed
    check — never by the CEO deciding the news was worth your time. */
interface FlagRow {
  id: string;
  kind: string;
  status: string;
  summary: string;
  arithmetic: Record<string, unknown> | null;
  opened_at: string | null;
  raised_at: string | null;
  proposal: string | null;
  /** Raised is news he was given; acknowledged is news he answered. */
  acknowledged_at: string | null;
  acknowledgement: string | null;
}
interface FlagsView { unraised: number; flags: FlagRow[] }

/** How many deliverables have been published — the files index's own count. */
interface ShelfView { matched: number }

/** Who holds which verb, in one word for the header. */
function autonomyWord(dash: Dashboard): { word: string; means: string } {
  if (fullyAutonomous(dash)) {
    return { word: "Autonomous", means: "Your CEO accepts its own plan, signs dates and judges unverifiable work, and tells you after. Money, legal questions and credentials are always yours." };
  }
  const all = (["plan_authority", "scope_authority", "commitment_authority", "verdict_policy", "capability_authority"] as const)
    .every((r) => chairmanDecides(dash, r));
  return all
    ? { word: "Supervised", means: "You accept scope, sign the delivery date and judge work no script can check. Your CEO proposes." }
    : { word: "Mixed autonomy", means: "Some decisions are yours and some are your CEO's — Settings → How it runs says which." };
}

/** The team's scoreboard, one line under the chart: is it on track, how
    much of "done" is true, what it has delivered, and what it has cost. Each
    fact links to where its detail lives. */
function TeamFoot({ ctx, published }: { ctx: ProjectCtx; published: number | null }) {
  const { dash, plan, projectId, hypotheses } = ctx;
  const verdict = deliveryVerdict(dash, plan);
  const { unit, spent, cap, frac } = budgetOf(dash);
  const pace = paceDate(spent, cap);
  const reqs = (plan?.requirements ?? []).filter((r) => r.status !== "cut");
  const explore = dash.mode === "explore";
  const bets = hypotheses.filter((h) => h.status === "active");
  const base = `/projects/${projectId}`;
  const tone = verdict.tone === "good" ? "good" : verdict.tone;
  return (
    <div className="team-foot" aria-label="Is it on track?">
      <Link to={explore ? `${base}/details/tasks` : `${base}/details/roadmap`} title={verdict.sub}>
        <b className={`tf-${tone}`}>{verdict.label}</b>
      </Link>
      {explore ? (
        <Link to={`${base}/details/tasks`}>
          <b>{bets.length}</b> {bets.length === 1 ? "bet" : "bets"} in play ·{" "}
          {dash.ceo_judgment.tasks_with_verified_external} proven outside
        </Link>
      ) : (
        <Link to={`${base}/details/roadmap`}>
          <b>{plan?.burnup?.settled ?? reqs.filter((r) => r.status === "met").length} of{" "}
            {plan?.burnup?.scope_total ?? reqs.length}</b>{" "}
          <Term means="A requirement is a testable outcome; together they define done. Only the platform can mark one met.">
            must-be-true things
          </Term>{" "}true
        </Link>
      )}
      <Link to={`${base}/results`}>
        <b>{published ?? "—"}</b> result{published === 1 ? "" : "s"} delivered · see results
      </Link>
      <Link to={`${base}/details/spend`} title={pace ? `on pace to reach the cap ${fmtWhenShort(pace.toISOString())}` : "on pace to finish the month under the cap"}>
        <b>{fmtUnit(spent, unit)}</b> this month ·{" "}
        {frac >= 1 ? "the cap is reached — the project is paused" : `pauses at ${fmtUnit(cap, unit)}`}
      </Link>
      <span>
        <b>{dash.activity.daily_runs.used} of {dash.activity.daily_runs.cap}</b> runs today
        {dash.activity.waiting.unread > 0 &&
          ` · ${dash.activity.waiting.unread} unread message${dash.activity.waiting.unread === 1 ? "" : "s"}`}
        {dash.status === "running" && !dash.activity.brief.banner && dash.activity.brief.clears_at && (
          <span title={`${dash.activity.brief.headline} ${dash.activity.brief.detail}`}>
            {` · resumes ${fmtSoon(dash.activity.brief.clears_at)}`}
          </span>
        )}
      </span>
    </div>
  );
}

/** The mode-shaped fact, one per mode: Explore counts its window down,
    Deliver offers the completion gate the moment everything is true, Operate
    names the turn of the rhythm it is on. */
function ModeStrip({ dash, reload }: { dash: Dashboard; reload: () => void }) {
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const closesAfter = Number(dash.constitution?.["complete_after_hours"] ?? 0);
  if (dash.mode === "deliver" && dash.status === "completed") {
    // Completing is automatic only because it can be taken back.
    return (
      <div className="strip good">
        <div className="grow">
          <b>Delivered.</b>
          <div className="sb">
            The receipt is under Activity → Decisions. If something is missing, reopen it — up
            to 14 days after it closed — and tell the CEO what.
          </div>
          {error && <div className="error">{error}</div>}
        </div>
        {dash.can_steer !== false && (confirming ? (
          <>
            <button className="small primary" onClick={async () => {
              try {
                await post(`/admin/projects/${dash.id}/reopen`);
                reload();
              } catch (e) { setError((e as Error).message); }
            }}>Yes, reopen it</button>
            <button className="small" onClick={() => setConfirming(false)}>Cancel</button>
          </>
        ) : (
          <button className="small" onClick={() => setConfirming(true)}>Reopen…</button>
        ))}
      </div>
    );
  }
  if (dash.mode === "deliver" && dash.delivery_outstanding !== null
      && dash.delivery_outstanding.length === 0) {
    return (
      <div className="strip good">
        <div className="grow">
          <b>Everything is true.{closesAfter > 0
            ? ` It completes itself about ${closesAfter} hours after you were told, unless its scope moves.`
            : " This delivery is ready for your confirmation."}</b>
          <div className="sb">
            Completing writes the receipt — including which requirements failed before they
            passed — and closes the project down. It is not spending while it waits, and you
            can reopen it for 14 days afterwards.
          </div>
          {error && <div className="error">{error}</div>}
        </div>
        {confirming ? (
          <>
            <button className="small primary" onClick={async () => {
              try {
                await post(`/admin/projects/${dash.id}/complete`);
                reload();
              } catch (e) { setError((e as Error).message); }
            }}>Yes, complete it</button>
            <button className="small" onClick={() => setConfirming(false)}>Not yet</button>
          </>
        ) : (
          <button className="small primary" onClick={() => setConfirming(true)}>Complete the project…</button>
        )}
      </div>
    );
  }
  if (dash.mode === "explore" && dash.deadline) {
    const daysLeft = Math.ceil((new Date(dash.deadline).getTime() - Date.now()) / 86_400_000);
    return (
      <div className={`strip${daysLeft <= 0 ? " serious" : ""}`}>
        <div className="grow">
          <b>{daysLeft > 0 ? `${daysLeft} day${daysLeft === 1 ? "" : "s"} left in the window.` : "The window has closed."}</b>
          <div className="sb">
            Open scope, bounded time — {daysLeft > 0
              ? "what exists by then is what this project is judged on."
              : "the judgment is yours: stop it, set a new date in Settings, or convert what worked into a Deliver project."}
          </div>
        </div>
      </div>
    );
  }
  if (dash.mode === "operate" && dash.cycle) {
    const c = dash.cycle;
    // With a date in play the honest question is not "when did this open" but
    // "does the work reach it". That answer is arithmetic — the same the
    // dispatcher gates on — so it is stated rather than left to be asked.
    if (c.due_at && c.capacity) {
      const cap = c.capacity;
      return (
        <div className={`strip${cap.feasible ? "" : " serious"}`}>
          <div className="grow">
            <b>Cycle {c.ordinal} is due {fmtWhen(c.due_at)} — {cap.feasible ? "the work fits." : "the work does not fit."}</b>
            <div className="sb">
              {c.goal ? <>{clip(c.goal)}<br /></> : null}
              {cap.why}. {cap.basis}.
              {cap.retry_reserve > 0 && <> Holding back {cap.retry_reserve} run(s) for retries.</>}
              {c.due_source?.kind === "data" && c.due_source.url && (
                <> Date read from <span className="mono">{c.due_source.url}</span>.</>
              )}
            </div>
          </div>
        </div>
      );
    }
    return (
      <p className="help">
        Cycle {c.ordinal}, open since {fmtWhen(c.opened_at)}
        {c.goal ? <> — {clip(c.goal)}</> : <> — each cycle plans fresh, and the CEO opens it by reading the last three cycles' narratives.</>}
      </p>
    );
  }
  return null;
}

/** The arithmetic behind "at risk", with the levers beside it — only when the
    date is actually in trouble, and only the levers the dial left with you. */
function LateBanner({ ctx }: { ctx: ProjectCtx }) {
  const { dash, plan, projectId } = ctx;
  const verdict = deliveryVerdict(dash, plan);
  if (verdict.daysLate === null) return null;
  const v = plan?.velocity;
  const c = plan?.commitment;
  const scope = chairmanDecides(dash, "scope_authority");
  const date = chairmanDecides(dash, "commitment_authority");
  return (
    <Verdict
      tone={verdict.tone === "good" ? undefined : verdict.tone}
      headline={verdict.headline}
      actions={scope || date ? (
        <>
          {scope && <Link to={`/projects/${projectId}/details/roadmap`}><button className="primary">Drop something from the plan</button></Link>}
          {date && <Link to={`/projects/${projectId}/details/roadmap`}><button>Move the delivery date</button></Link>}
        </>
      ) : (
        <Link to={`/projects/${projectId}/chat`}><button>Ask {firstName(ctx.ceoName)} what it is doing about it</button></Link>
      )}
    >
      <p>
        {v && v.pace_known && v.remaining > 0 && c?.projected_done_at ? (
          <>
            <b>{v.remaining}</b> requirement{v.remaining === 1 ? " is" : "s are"} still open, and at{" "}
            <b>{v.per_week_3w.toFixed(1)} a week</b> {v.remaining === 1 ? "it lands" : "they land"} around{" "}
            <b>{fmtDate(c.projected_done_at)}</b>.{" "}
          </>
        ) : null}
        {scope || date
          ? "Nothing new goes into the plan until you choose, and the work already under way carries on either way."
          : "You left scope and dates to your CEO, so it will cut something or move the date itself and tell you what it did."}
      </p>
    </Verdict>
  );
}

/** The CEO's last word, and anything the platform made it bring you — the
    news, beside the work, one step from answering it. */
function CeoCard({ ctx, news }: { ctx: ProjectCtx; news: ReactNode[] }) {
  const { projectId } = ctx;
  const navigate = useNavigate();
  const [channelId, setChannelId] = useState<string | null>(null);
  useEffect(() => {
    get<ChannelRow[]>(`/observe/projects/${projectId}/channels`)
      .then((cs) => setChannelId(cs.find((c) => c.kind === "boardroom")?.id ?? null))
      .catch(() => setChannelId(null));
  }, [projectId]);
  const { data: tail } = usePoll(
    () => channelId ? get<MessageRow[]>(`/observe/channels/${channelId}/messages?limit=6`) : Promise.resolve([] as MessageRow[]),
    15000, [channelId],
  );
  // The CEO's own words only: a line the platform posted has no sender, and
  // under a "From <CEO>" heading it read as the CEO saying it.
  const last = [...(tail ?? [])].reverse().find((m) => !!m.from_agent);
  const ceo = firstName(ctx.ceoName);
  return (
    <section className="card ov-card">
      <div className="card-h">
        <h3>From {ceo}</h3>
        {last && <span className="help">{fmtWhen(last.at)}</span>}
        <span className="grow" />
        <Link className="card-link" to={`/projects/${projectId}/chat`}>Chat <Icon name="arrow" size={13} /></Link>
      </div>
      {last ? (
        <div className="ceo-last">
          <Avatar name={ctx.ceoName} team="leadership" />
          <div className="ceo-body"><Markdown>{clipMarkdown(last.body, 420)}</Markdown></div>
        </div>
      ) : (
        <p className="help">
          {ceo} hasn't said anything yet. Open with the goal, the constraints, and how you want to work.
        </p>
      )}
      {news.length > 0 && <div className="ov-news">{news}</div>}
      <div className="quick">
        <button className="qchip" onClick={() => navigate(`/projects/${projectId}/chat`)}>
          <Icon name="reply" size={13} /> Reply…
        </button>
        <button className="qchip" onClick={() => navigate(`/projects/${projectId}/chat`, { state: { draft: "What changed since we last spoke, and what is next?" } })}>
          What changed?
        </button>
        <button className="qchip" onClick={() => navigate(`/projects/${projectId}/chat`, { state: { draft: "What is the biggest risk to the date right now?" } })}>
          Biggest risk?
        </button>
      </div>
    </section>
  );
}

/** The calendar, as a list: what happens next, coloured by who resolves it.
    The full strip — rhythms, every wake, the notes that explain its blanks —
    is one press away rather than half a screen of the first page. */
function ComingUp({ projectId }: { projectId: string }) {
  const [full, setFull] = useState(false);
  const { data } = usePoll(() => get<AgendaView>(`/observe/projects/${projectId}/agenda?days=14`), 60000);
  const now = Date.now();
  const events = (data?.events ?? []).filter((e) => new Date(e.at).getTime() >= now - 3_600_000).slice(0, 7);
  return (
    <section className="card ov-card">
      <div className="card-h">
        <h3>Coming up</h3>
        {data && <span className="help">{data.timezone}</span>}
        <span className="grow" />
        <button className="card-link" onClick={() => setFull(!full)}>{full ? "Hide the calendar" : "Full calendar"}</button>
      </div>
      {full ? <Agenda projectId={projectId} /> : events.length ? (
        <ul className="agenda-list">
          {events.map((e, i) => (
            <li key={`${e.at}-${i}`} className={`ag-${e.tone}`}>
              <span className="ag-when">
                <b>{new Date(e.at).toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" })}</b>
                <span>{new Date(e.at).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })}</span>
              </span>
              <i className="ag-dot" />
              <span className="ag-what">{e.label}{e.detail && <span className="help"> — {clip(e.detail, 90)}</span>}</span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="help">{data ? (data.notes[0] ?? "Nothing is scheduled in the next fourteen days.") : "Loading…"}</p>
      )}
      {!full && (
        <div className="ag-key">
          <span className="k-consequence">Decides itself</span>
          <span className="k-deadline">Deadline</span>
          <span className="k-meeting">Meeting</span>
          <span className="k-projection">Projection</span>
        </div>
      )}
    </section>
  );
}

/** Setup & health. A healthy project gets one quiet affirmative line — silence
    is ambiguous with a broken health check, and the ambiguity costs more than
    the pixels. Failing rows keep their plain-language fixes.

    Two kinds of row, said in the header badge's own words: a problem is
    something broken, a suggestion is advice. A suggestion the reader has read
    can be put away, and then it stops lighting the badge — most of what kept
    the badge lit on every project was advice that is true forever and was
    read on the first day. Put-away rows stay on the card, dimmed, with a way
    back; a problem has no such button. `id` is the badge's target. */
function HealthLine({ ctx }: { ctx: ProjectCtx }) {
  const { projectId, health: data, reloadHealth, reloadOrg } = ctx;
  const [show, setShow] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (!data) return null;
  const isOpen = (c: HealthCheck) => c.status === "fail" || (c.status === "warn" && !c.acked_at);
  const open = data.checks.filter(isOpen);
  const putAway = data.checks.filter((c) => c.status === "warn" && c.acked_at);
  if (!open.length && !show) {
    return (
      <div className="health-line" id="setup-health">
        <span className="good"><Icon name="check" size={15} strokeWidth={2.2} /></span>
        <b>Setup is healthy.</b>
        <span className="help">
          {putAway.length
            ? `${putAway.length} suggestion${putAway.length === 1 ? "" : "s"} put away.`
            : `All ${data.checks.length} checks pass.`}
        </span>
        <span className="grow" />
        <button className="small" onClick={() => setShow(true)}>Details</button>
      </div>
    );
  }
  const ack = async (check: HealthCheck, put: boolean) => {
    setError(null);
    try {
      await post(`/admin/projects/${projectId}/health/ack`, { sig: check.sig, ack: put });
      reloadHealth();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };
  // The pace row answers itself: switching is an amendment like any made in
  // Settings, with its reason written for him, so it is on the record and
  // disclosed to the CEO the same way.
  const switchTempo = async (check: HealthCheck) => {
    if (check.action?.kind !== "tempo") return;
    setError(null);
    try {
      await post(`/admin/projects/${projectId}/amendments`, {
        reason: `Pace set to ${check.action.to_label} from Setup & health, to match the project's cycle.`,
        changes: check.action.changes,
      });
      reloadHealth();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };
  // The missing seat, hired the way the Team page hires one: on the role's
  // own model, with its own channel, disclosed in the audit trail.
  const hire = async (check: HealthCheck) => {
    if (check.action?.kind !== "hire") return;
    setError(null);
    try {
      await post(`/admin/projects/${projectId}/teams`, {
        role_slug: check.action.role_slug,
        name: check.action.team_name,
        employees: [check.action.employee],
      });
      reloadHealth();
      reloadOrg();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };
  // Keeping is the project's answer, not one reader's tidying, so it is a
  // decision on the record and the row leaves every chair's card.
  const keepTempo = async () => {
    setError(null);
    try {
      await post(`/admin/projects/${projectId}/tempo/keep`, {});
      reloadHealth();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };
  // Problems, then suggestions, then what passes, then what was put away.
  const rank = (c: HealthCheck) =>
    c.status === "fail" ? 0 : c.status === "warn" ? (c.acked_at ? 3 : 1) : 2;
  const rows = [...data.checks].sort((a, b) => rank(a) - rank(b));
  const badge = (c: HealthCheck) => (
    <span className={`badge ${c.status === "fail" ? "critical" : c.status === "ok" ? "good" : c.acked_at ? "" : "warn"}`}>
      {c.status === "fail" ? "problem" : c.status === "warn" ? (c.acked_at ? "put away" : "suggestion") : "ok"}
    </span>
  );
  return (
    <section className="card ov-card" id="setup-health" style={{ marginTop: "var(--s4)" }}>
      <div className="card-h">
        <h3>Setup &amp; health</h3>
        <span className="grow" />
        {!open.length && <button className="small" onClick={() => setShow(false)}>Hide</button>}
      </div>
      {open.length > 0 && (
        <p className="help" style={{ margin: "0 0 8px" }}>
          A <b>problem</b> means something is broken: fix it. A <b>suggestion</b> is a choice
          for you: act on it, or put it away once you have decided and it stops counting.
        </p>
      )}
      {error && <div className="error">{error}</div>}
      {rows.map((check) => (
        <div key={check.key}
          className={`hrow${check.status === "ok" ? " ok" : ""}${check.acked_at ? " put-away" : ""}`}>
          {badge(check)}
          <span className="grow">{check.detail}</span>
          {check.action?.kind === "repin" && !check.acked_at ? (
            <button className="small" onClick={async () => {
              if (check.action?.kind !== "repin") return;
              await post(`/admin/teams/${check.action.team_id}/repin`, {});
              reloadHealth();
              reloadOrg();
            }}>adopt the latest role version</button>
          ) : check.action?.kind === "hire" && !check.acked_at ? (
            <button className="small" onClick={() => hire(check)}>
              {check.action.label}
            </button>
          ) : check.action?.kind === "tempo" && !check.acked_at ? (
            <button className="small" onClick={() => switchTempo(check)}>
              Switch to “{check.action.to_label}”
            </button>
          ) : isOpen(check) && WHERE_TO[check.where ?? ""] ? (
            <Link to={WHERE_TO[check.where!][2]
              ? WHERE_TO[check.where!][0]
              : `/projects/${projectId}${WHERE_TO[check.where!][0]}`}>{WHERE_TO[check.where!][1]} →</Link>
          ) : null}
          {check.action?.kind === "tempo" && !check.acked_at ? (
            <button className="small" onClick={() => keepTempo()}>
              Keep “{check.action.keep_label}”
            </button>
          ) : check.status === "warn" && (
            <button className="small" onClick={() => ack(check, !check.acked_at)}>
              {check.acked_at ? "Show again" : "Got it"}
            </button>
          )}
        </div>
      ))}
    </section>
  );
}

/** Where a health check says the fix lives — a check that names a place the
    reader cannot click is read once and then skipped. */
const WHERE_TO: Record<string, [string, string, boolean?]> = {
  settings: ["/details/settings", "fix in Settings"],
  data_sources: ["/details/settings?s=sources", "open Data sources"],
  plan: ["/details/roadmap", "open the Roadmap"],
  boardroom: ["/chat", "open the chat"],
  team: ["/team", "see the team"],
  // A price is the install's, not the project's, so it is set where prices
  // live — an app-wide page, not one under the project.
  prices: ["/settings?section=prices", "open Model prices", true],
};

/** Where each problem verdict is fixed: page under the project, and the link. */
const FIX_AT: Record<string, [string, string]> = {
  blocked: ["/details/settings?s=money", "Add a key"],
  failing: ["/details/activity/runs", "See the runs"],
  late: ["/details/roadmap", "Cut scope or move the date"],
  at_risk: ["/details/roadmap", "See the plan"],
  stalled: ["/team", "See who is stuck"],
  spinning: ["/details/activity/runs", "See what the runs did"],
};

export default function Overview({
  ctx, onPause, onStop, setupBadge, goalLead,
}: {
  ctx: ProjectCtx;
  onPause: () => void;
  onStop: () => void;
  setupBadge: ReactNode;
  goalLead: string;
}) {
  const { projectId, dash } = ctx;
  const desk = useDesk();
  const [brief, setBrief] = useState(false);
  const { data: flagsView, reload: reloadFlags } = usePoll(
    () => get<FlagsView>(`/observe/projects/${projectId}/flags`), 15000);
  // A count, asked of the same index the Deliverables page reads.
  const { data: shelf } = usePoll(
    () => get<ShelfView>(`/observe/projects/${projectId}/files/browse/rows?state=published&limit=1&lean=true`),
    30000);

  // The same rows the Inbox and the sidebar badge count, for this project. A
  // paused project is not counted there (pausing is a state, not a request),
  // but its own page still lists what is parked on it.
  const deskProject = desk.data?.projects.find((p) => p.id === projectId);
  const items = useMemo(
    () => (deskProject ? itemsFor(deskProject, desk.reload) : []),
    [deskProject, desk.reload],
  );
  const health = runHealth(ctx.runs ?? []);
  const ceo = firstName(ctx.ceoName);
  const auto = autonomyWord(dash);
  const base = `/projects/${projectId}`;
  const unpaid = !dash.billing_secret_id && dash.integrations?.runtime !== "mock";

  const rawFlags = flagsView?.flags ?? [];

  // ── your CEO is telling you ──
  //
  // A raised flag is the CEO bringing you something with a proposal. It does
  // not block, it does not expire, and it proceeds on that proposal whether or
  // not you answer — so it reads beside the CEO's last word, never under the
  // heading that says a decision is waiting on you.
  const news: React.ReactNode[] = [];
  for (const f of rawFlags.filter((f) => f.status === "raised" && !f.acknowledged_at)) {
    const plain = flagPlain(f.kind);
    news.push(
      <div className="flag" key={f.id}>
        <div className="flag-t"><Icon name="flag" size={14} /> <b>{plain.title}</b></div>
        <div className="help">
          {ceo} proposes: {f.proposal ? clip(f.proposal, 200) : "nothing yet"}. That goes ahead unless
          you say otherwise.
        </div>
        <div className="flag-a">
          <NotedButton
            primary={false}
            onSubmit={async (note) => {
              await post(`/admin/flags/${f.id}/ack`, { note });
              reloadFlags();
            }}
          />
          <Link to={`${base}/chat`}>talk it through →</Link>
        </div>
      </div>,
    );
  }

  // ── watching ──
  // Clocks the CEO resolves: shown so nothing happens silently, never counted
  // as needing you. The project-wide ones come from the same rows the Inbox
  // shows under Watching; the two below exist only here.
  const watch: ReactNode[] = items.filter((i) => !needsYou(i)).map((it) => (
    <div className="wrow" key={it.key}>
      <span className="badge">{it.kind}</span>
      <div className="grow">{it.what} <span className="help">{it.consequence}</span></div>
      <Link to={it.to}>{it.toLabel} →</Link>
    </div>
  ));
  // An UNRAISED flag: your CEO has news it has not yet brought you. Drawn
  // here, cheerfulness in the chat is self-refuting rather than something
  // you have to catch.
  for (const f of rawFlags.filter((f) => f.status === "open")) {
    watch.push(
      <div className="wrow" key={f.id}>
        <span className="badge serious">not yet raised</span>
        <div className="grow">
          <b>{flagPlain(f.kind).title}</b>{" "}
          <span className="help">
            — the platform opened this{f.opened_at ? ` ${fmtWhenShort(f.opened_at)}` : ""}; {ceo} is
            required to bring it to you with a proposal. In its own words: {clip(f.summary)}
          </span>
        </div>
        <Link to={`${base}/chat`}>ask about it →</Link>
      </div>,
    );
  }
  // Acknowledged, and still true: your word ended the conversation, not the
  // condition, so it waits here under your note until the arithmetic turns.
  for (const f of rawFlags.filter((f) => f.status === "raised" && f.acknowledged_at)) {
    watch.push(
      <div className="wrow" key={f.id}>
        <span className="badge">noted</span>
        <div className="grow">
          <b>{flagPlain(f.kind).title}</b>{" "}
          <span className="help">
            — still true. You noted it{f.acknowledged_at ? ` ${fmtWhenShort(f.acknowledged_at)}` : ""}
            {f.acknowledgement ? `: “${clip(f.acknowledgement, 100)}”` : ""}; it clears when the numbers do.
          </span>
        </div>
        <Link to={`${base}/chat`}>take it up →</Link>
      </div>,
    );
  }
  // A full memory is an outage that looks like nothing: writes are refused,
  // runs die on the refusal cap, and the only symptom is a climbing bill.
  for (const m of dash.memory?.scopes ?? []) {
    if (m.live < m.cap * (dash.memory?.warn_ratio ?? 0.85)) continue;
    const full = m.live >= m.cap;
    watch.push(
      <div className="wrow" key={`memory:${m.scope}:${m.team_id ?? ""}`}>
        <span className={`badge ${full ? "serious" : "warn"}`}>memory {m.live}/{m.cap}</span>
        <div className="grow">
          {m.name} {full ? "is full" : "is nearly full"} —{" "}
          {full ? "new entries there are being refused, and only " : "at the cap, new entries get refused until "}
          {m.scope === "project" ? "the CEO" : (m.curator ?? "a lead nobody has named")} can fold entries to make room.
        </div>
        <Link to={`${base}/details/activity/memory`}>see what it keeps →</Link>
      </div>,
    );
  }

  const steer = dash.can_steer !== false;

  return (
    <>
      <header className="ov-head">
        <div className="eyebrow">
          <span className="chip">{dash.mode === "deliver" ? "Deliver" : dash.mode === "operate" ? "Operate" : "Explore"}</span>
          {ctx.plan?.commitment?.deliver_by && <span>Due {fmtDate(ctx.plan.commitment.deliver_by)}</span>}
          {dash.mode === "operate" && dash.cycle && (
            cycleOverdue(dash.cycle)
              ? <span style={{ color: "var(--serious)" }}>Cycle {dash.cycle.ordinal} · overdue to close since {fmtDate(dash.cycle.rolls_at)}</span>
              : <span>Cycle {dash.cycle.ordinal} · {dash.cycle.due_at ? "due" : "closes"} {fmtRoll(dash.cycle.due_at ?? dash.cycle.rolls_at)}</span>
          )}
          {!ctx.plan?.commitment?.deliver_by && dash.deadline && <span>Window closes {fmtDate(dash.deadline)}</span>}
          <span className="term-plain"><Term means={auto.means}>{auto.word}</Term></span>
          {dash.my_role && <span>You are {dash.my_role === "chairman" ? "the chairman" : dash.my_role === "co_chair" ? "a co-chair" : "an observer"}</span>}
        </div>
        <div className="ov-title-row">
          <div className="grow">
            <h1 className="ov-title">
              {dash.name} {statusBadge(dash.status, unpaid)}
              {dash.drought_active && (
                <Badge tone="serious">
                  <Term means="Nothing outside this project has verified any of its claims for longer than your rules allow. Work carries on; new internal-only work is refused until proof lands.">
                    no outside proof lately
                  </Term>
                </Badge>
              )}
              {setupBadge}
            </h1>
            {deskProject?.status_line && (
              // The sentence Home, the CLI and Claude's desk all say about this
              // project, with the one page that changes it.
              <p className={`ov-status${WARN_VERDICTS.includes(deskProject.status_line.verdict) ? " warn" : ""}`}>
                {deskProject.status_line.sentence}
                {FIX_AT[deskProject.status_line.verdict] && (
                  <> <Link to={`${base}${FIX_AT[deskProject.status_line.verdict][0]}`}>
                    {FIX_AT[deskProject.status_line.verdict][1]}</Link></>
                )}
              </p>
            )}
            <p className="ov-goal">
              {goalLead}{" "}
              <button className="linkish inline" onClick={() => setBrief(!brief)} aria-expanded={brief}>
                {brief ? "Hide the brief" : "Read the brief"}
              </button>
            </p>
          </div>
          <div className="ov-acts">
            {steer && dash.status !== "running" && dash.status !== "stopped" && (
              // Starting a project nothing can pay for used to succeed and show
              // "Running" while its first run died: the platform allows it (a
              // keyless project is "not configured yet"), so the button says it.
              unpaid ? (
                <Link to={`${base}/details/settings?s=money`} className="btnlink primary"
                  title="Agents cannot run without a billing credential">
                  <Icon name="play" size={15} />Add a key to start
                </Link>
              ) : (
                <Btn kind="primary" icon="play" onClick={async () => { await post(`/admin/projects/${projectId}/start`); ctx.reload(); }}>Start</Btn>
              )
            )}
            {steer && dash.status === "running" && <Btn icon="pause" onClick={onPause}>Pause the team</Btn>}
            {steer && dash.status !== "stopped" && <Btn kind="danger" icon="stop" onClick={onStop}>Stop</Btn>}
            <Link to={`${base}/chat`} className="btnlink primary"><Icon name="message" size={15} />Message {ceo}</Link>
          </div>
        </div>
        {brief && (
          <div className="briefbox">
            <strong style={{ color: "var(--ink)" }}>The brief every one of them works from</strong>
            <p style={{ whiteSpace: "pre-wrap", margin: "6px 0 0" }}>{dash.goal}</p>
          </div>
        )}
      </header>

      <ModeStrip dash={dash} reload={ctx.reload} />
      <LateBanner ctx={ctx} />

      <TeamFoot ctx={ctx} published={shelf?.matched ?? null} />

      {/* The project's home is what it needs from you: this project's rows
          of the one Inbox, with their buttons, and what you should know. The
          team itself is a page of its own — at eighteen people an org chart
          is a place you go to, not a header you scroll past. */}
      <InboxView project={projectId} />

      <div className="ov-grid">
        <div className="ov-col">
          <CeoCard ctx={ctx} news={news} />
          {watch.length > 0 && (
            <section className="card ov-card">
              <div className="card-h">
                <h3>Watching</h3>
                <span className="count">{watch.length}</span>
                <span className="grow" />
                <span className="help">clocks {ceo} is handling — nothing here waits on you</span>
              </div>
              <div className="wbox">{watch}</div>
            </section>
          )}
        </div>
        <aside className="ov-col">
          <ComingUp projectId={projectId} />
        </aside>
      </div>

      {health.early.length > 0 && (
        <div className={`strip${health.ratio >= 0.2 ? " serious" : ""}`} style={{ marginTop: "var(--s4)" }}>
          <div className="grow">
            <b>{earlyRunSentence(health)}</b>
            <div className="sb">
              {health.faults.length === 0
                ? "Every one of them was stopped on purpose, so nothing points at a fault — though their tokens were still spent."
                : health.ratio >= 0.2
                  ? "Under one in five is normal wear. This much means something is in the way — a dead tool mount, a wrong credential, or work an agent cannot do."
                  : "That is within normal wear for unattended work."}
            </div>
          </div>
          <Link to={`${base}/details/activity/runs`}>see what happened →</Link>
        </div>
      )}

      <HealthLine ctx={ctx} />
      <AsOf at={ctx.asOf} error={ctx.error} />
    </>
  );
}
