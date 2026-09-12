import { useState } from "react";
import { Link } from "react-router-dom";
import {
  ActivitySeat,
  ActivityView,
  get,
  HealthCheck,
  OrgView,
  post,
  RunRow,
  SpendBucket,
  SpendReport,
  TaskRow,
} from "../../api";
import { fmtWhen, usePoll } from "../../hooks";
import {
  AsOf,
  budgetOf,
  clip,
  clockClass,
  DecideButtons,
  earlyRunSentence,
  fmtLeft,
  fmtUnit,
  fmtWhenShort,
  hoursSince,
  idleSentence,
  NoteAction,
  paceDate,
  runHealth,
  Skeleton,
  spentOf,
} from "../../ui";
import Agenda from "../../agenda";
import { PlanMeter } from "../../llmCredentials";
import { HirePanel, gapsFor, needsOf, useRoleLibrary } from "../../roles";
import type { ProjectCtx } from "../Project";

interface ArtifactFile {
  key: string;
  run_id: string | null;
  name: string;
  size: number;
  modified: string | null;
}
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
interface FlagsView {
  unraised: number;
  flags: FlagRow[];
}

interface FilesView {
  workspace: { path: string; size: number; modified: string }[];
  artifacts: ArtifactFile[];
  // The receipts: one platform-written row per published version, with the
  // hash and the publish time. `artifacts` is the store listing, which is a
  // different thing — a key with no receipt behind it was never published.
  published?: { key: string }[];
}

function fmtSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** Setup & health. A healthy project gets one quiet affirmative line — silence
    is ambiguous with a broken health check, and the ambiguity costs more than
    the pixels. Failing rows keep their plain-language fixes. */
function HealthLine({ ctx }: { ctx: ProjectCtx }) {
  const { projectId, health: data, reloadHealth, reloadOrg } = ctx;
  const [show, setShow] = useState(false);
  if (!data) return null;

  if (data.all_ok && !show) {
    return (
      <div style={{ display: "flex", alignItems: "center", gap: 8, margin: "14px 0", fontSize: 13 }}>
        <span className="badge good">healthy</span>
        <span className="help">All {data.checks.length} health checks passing.</span>
        <button className="small" onClick={() => setShow(true)}>show</button>
      </div>
    );
  }
  const badge = (status: HealthCheck["status"]) => (
    <span className={`badge ${status === "fail" ? "critical" : status === "warn" ? "warn" : "good"}`}>
      {status}
    </span>
  );
  return (
    <div className="card" style={{ margin: "14px 0" }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 10 }}>
        <strong>Setup &amp; health</strong>
        <div className="spacer" />
        {data.all_ok && <button className="small" onClick={() => setShow(false)}>hide</button>}
      </div>
      {!data.all_ok && (
        <p className="subtitle" style={{ margin: "4px 0 8px" }}>
          The project tells you what it needs, instead of failing quietly at 9am. Fix the rows
          below and this collapses back to one green line.
        </p>
      )}
      {data.checks.map((check) => (
        <div
          key={check.key}
          style={{
            display: "flex",
            gap: 10,
            alignItems: "baseline",
            padding: "4px 0",
            ...(check.status === "ok" ? { opacity: 0.5 } : {}),
          }}
        >
          {badge(check.status)}
          <span style={{ flex: 1, fontSize: 13 }}>{check.detail}</span>
          {check.action?.kind === "repin" ? (
            <button
              className="small"
              onClick={async () => {
                await post(`/admin/teams/${check.action!.team_id}/repin`, {});
                reloadHealth();
                // The team card below names the pinned version; adopting from
                // here has to move it too, or the two rows disagree.
                reloadOrg();
              }}
            >
              adopt latest role version
            </button>
          ) : check.status !== "ok" && WHERE_TO[check.where ?? ""] ? (
            <Link to={`/projects/${projectId}/${check.where}`}>
              {WHERE_TO[check.where!]} →
            </Link>
          ) : null}
        </div>
      ))}
    </div>
  );
}

/** Where a health check says the fix lives. A check that names a destination
    the reader cannot click is a check that gets read once and then skipped:
    before this, only `settings` was linked, so drift reported against the plan
    or the roster arrived as a sentence with nowhere to go. */
const WHERE_TO: Record<string, string> = {
  settings: "fix in Settings",
  plan: "open the Plan",
  boardroom: "open the boardroom",
};

/** Budget meter with pace: position is a fact, the projected cap date is the
    decision aid — a fuel gauge vs a range estimate. */
function BudgetMeter({ ctx }: { ctx: ProjectCtx }) {
  const { dash } = ctx;
  const { usdMode, unit, spent, cap, warnRatio, frac } = budgetOf(dash);
  const capped = Math.min(frac, 1);
  const cls = frac >= 1 ? "over" : frac >= warnRatio ? "warn" : "";
  const pace = paceDate(spent, cap);
  const state =
    frac >= 1
      ? "✖ exhausted — auto-paused"
      : frac >= warnRatio
        ? "⚠ past the warning threshold"
        : "✓ within budget";
  return (
    <div className="tile" style={{ border: "none", padding: 0, background: "none" }}>
      <div className="label">Monthly budget ({usdMode ? "USD" : "tokens"})</div>
      <div className="value">
        {fmtUnit(spent, unit)}{" "}
        <span style={{ fontSize: 14, color: "var(--muted)" }}>/ {fmtUnit(cap, unit)}</span>
      </div>
      <div className="meter">
        <div className="track">
          <div className={`fill ${cls}`} style={{ width: `${capped * 100}%` }} />
          <div className="tick" style={{ left: `${warnRatio * 100}%` }} />
        </div>
        <div className="legend">
          <span>
            {state}
            {pace && frac < 1 && (
              <> · on pace to hit the cap {fmtWhenShort(pace.toISOString())} — before the month ends</>
            )}
          </span>
          <span>warn at {Math.round(warnRatio * 100)}%</span>
        </div>
        <div className="legend" style={{ marginTop: 2 }}>
          <span>
            {dash.billing_secret_name
              ? `paying with: ${dash.billing_secret_name} (${usdMode ? "API key" : "Claude subscription"})`
              : "⚠ no payment credential connected — real runs will fail (Settings)"}
          </span>
        </div>
        <PlanMeter credentialId={dash.billing_secret_id} mode={dash.billing_mode} />
      </div>
    </div>
  );
}

/** Who is spending it. One series, named rows, sorted — the question is
    "which team costs me money", and a 14-day time series cannot answer it.

    These rows are the meter above them, broken out: same ledger, same
    month-to-date window, same unit, so they add up to it. They are worth
    nothing otherwise — a reader who catches a card contradicting itself is
    right to stop believing the rest of the page. */
function SpendByTeam({ projectId, usdMode }: { projectId: string; usdMode: boolean }) {
  const { data } = usePoll(
    () => get<SpendReport>(`/observe/projects/${projectId}/spend?group_by=team&window=month`),
    30000,
  );
  const unit = usdMode ? ("usd" as const) : ("tokens" as const);
  const spent = (r: SpendBucket) => spentOf(r, usdMode);
  const rows = [...(data?.rows ?? [])].sort((a, b) => spent(b) - spent(a));
  if (rows.length === 0) return null;
  const max = Math.max(...rows.map(spent), 1e-9);
  const total = rows.reduce((sum, r) => sum + spent(r), 0);
  const top = rows[0];
  const rest = total - spent(top);

  return (
    <div style={{ marginTop: 22 }}>
      <div className="fs-h">Who is spending it — this month</div>
      <div className="hbars">
        {rows.map((row, i) => (
          <div className="hbar" key={row.key}>
            <span>{row.label}</span>
            <span className="track">
              <i className={i === 0 ? "hi" : ""} style={{ width: `${Math.max((spent(row) / max) * 100, 0.8)}%` }} />
            </span>
            <span className="v">
              {fmtUnit(spent(row), unit)} · {row.runs} run
              {row.runs === 1 ? "" : "s"}
            </span>
          </div>
        ))}
      </div>
      {rows.length > 1 && spent(top) > rest && (
        <p className="help" style={{ margin: "10px 0 0" }}>
          {top.label} costs more than everyone else combined — worth knowing before you raise the cap.
        </p>
      )}
    </div>
  );
}

/** The first tab answers the daily question in order: needs you → is it
    working → what came out → what to watch → the money → the org. */
/** The mode-shaped fact, one per mode: Explore counts its window down,
    Deliver offers the completion gate the moment everything is true, Operate
    names the turn of the rhythm it is on. */
function ModeStrip({ dash, reload }: { dash: import("../../api").Dashboard;
  reload: () => void }) {
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (dash.mode === "deliver" && dash.delivery_outstanding !== null
      && dash.delivery_outstanding.length === 0) {
    return (
      <div className="strip" style={{ marginTop: 12, borderLeftColor: "var(--good)" }}>
        <div className="grow">
          <b>Everything is true. This delivery is ready for your confirmation.</b>
          <div className="sb">
            Completing writes the receipt — including which requirements failed before
            they passed — and closes the project down cleanly. Until you do, it keeps
            running and spending.
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
            }}>✓ Yes, complete it</button>
            <button className="small" onClick={() => setConfirming(false)}>not yet</button>
          </>
        ) : (
          <button className="small primary" onClick={() => setConfirming(true)}>
            Complete the project…
          </button>
        )}
      </div>
    );
  }
  if (dash.mode === "explore" && dash.deadline) {
    const daysLeft = Math.ceil(
      (new Date(dash.deadline).getTime() - Date.now()) / 86_400_000);
    return (
      <div className={`strip${daysLeft <= 0 ? " serious" : ""}`} style={{ marginTop: 12 }}>
        <div className="grow">
          <b>{daysLeft > 0
            ? `${daysLeft} day${daysLeft === 1 ? "" : "s"} left in the window.`
            : "The window has closed."}</b>
          <div className="sb">
            Open scope, bounded time — {daysLeft > 0
              ? "what exists by then is what this project is judged on."
              : "the judgment is yours: stop it, set a new date in Settings, or " +
                "convert what worked into a Deliver project."}
          </div>
        </div>
      </div>
    );
  }
  if (dash.mode === "operate" && dash.cycle) {
    const c = dash.cycle;
    // With a date in play the honest question is not "when did this open" but
    // "does the work reach it". That answer is arithmetic — the same the
    // dispatcher gates on — so it is stated here rather than left to be asked
    // for in the boardroom and answered from memory.
    if (c.due_at && c.capacity) {
      const cap = c.capacity;
      return (
        <div className={`strip${cap.feasible ? "" : " serious"}`} style={{ marginTop: 12 }}>
          <div className="grow">
            <b>
              Cycle {c.ordinal} is due {fmtWhen(c.due_at)} —{" "}
              {cap.feasible ? "the work fits." : "the work does not fit."}
            </b>
            <div className="sb">
              {c.goal ? <>{clip(c.goal)}<br /></> : null}
              {cap.why}. {cap.basis}.
              {cap.retry_reserve > 0 && (
                <> Holding back {cap.retry_reserve} run(s) for retries.</>
              )}
              {c.due_source?.kind === "data" && c.due_source.url && (
                <> Date read from <span className="mono">{c.due_source.url}</span>.</>
              )}
            </div>
          </div>
        </div>
      );
    }
    return (
      <p className="help" style={{ margin: "12px 0 0" }}>
        Cycle {c.ordinal}, open since {fmtWhen(c.opened_at)}
        {c.goal ? <> — {clip(c.goal)}</> : <> — each cycle plans fresh, and the CEO opens
        it by reading the last three cycles' narratives.</>}
      </p>
    );
  }
  return null;
}

export default function Overview({
  ctx,
  asOf,
  error,
}: {
  ctx: ProjectCtx;
  asOf: Date | null;
  error: string | null;
}) {
  const { projectId, dash, approvals, hypotheses, org, reload, reloadApprovals, reloadOrg } = ctx;
  const { data: tasks } = usePoll(
    () => get<TaskRow[]>(`/observe/projects/${projectId}/board`),
    10000,
  );
  const { data: runs } = usePoll(
    () => get<RunRow[]>(`/observe/projects/${projectId}/runs?limit=200`),
    8000,
  );
  const { data: files } = usePoll(
    () => get<FilesView>(`/observe/projects/${projectId}/files`),
    20000,
  );
  const { data: flagsView, reload: reloadFlags } = usePoll(
    () => get<FlagsView>(`/observe/projects/${projectId}/flags`),
    8000,
  );
  // Today, in the project's own timezone — not a rolling 24 hours. The roster
  // card named the people and said nothing about what any of them had done,
  // which is the question anyone reading a roster is actually asking.
  const { data: activity } = usePoll(
    () => get<ActivityView>(`/observe/projects/${projectId}/activity?since=today`),
    15000,
  );
  const today = new Map((activity?.seats ?? []).map((s) => [s.agent_id, s]));
  const [repinMsg, setRepinMsg] = useState<string | null>(null);

  const pending = approvals.filter((a) => a.status === "pending");
  const expiredBets = hypotheses.filter((h) => h.status === "expired" && !h.extended_once);
  const blocked = (tasks ?? []).filter((t) => t.status === "blocked");
  const killSoon = hypotheses.filter(
    (h) => h.status === "active" && hoursSince(h.kill_date) > -7 * 24 && hoursSince(h.kill_date) < 0,
  );

  const health = runs ? runHealth(runs) : null;
  const usdMode = dash.billing_mode === "api_key";

  // Newest artifact per name — the same deliverable republished by a later run
  // is a version of one thing, not two unrelated files.
  const byName = (() => {
    const seen = new Map<string, ArtifactFile>();
    for (const a of files?.artifacts ?? []) {
      const held = seen.get(a.name);
      if (!held || (a.modified ?? "") > (held.modified ?? "")) seen.set(a.name, a);
    }
    return seen;
  })();
  const latestArtifacts = [...byName.values()]
    .sort((a, b) => (b.modified ?? "").localeCompare(a.modified ?? ""))
    .slice(0, 3);
  // Deduped, because the Deliverables tab counts deduped and the two numbers
  // sat side by side disagreeing: this card counted every version of every
  // file and called it "published files", so three deliverables republished
  // fifty times read as 156 of them.
  const deliverableCount = byName.size;
  // Undefined means the field did not arrive, not that nothing was published:
  // a missing receipts array must not turn every row into an accusation.
  const receiptKeys = files?.published && new Set(files.published.map((r) => r.key));

  // ── needs you ──
  const needs: React.ReactNode[] = [];
  for (const a of pending) {
    needs.push(
      <div className="arow" key={a.id}>
        <span className="chip accent">{a.kind.replaceAll("_", " ")}</span>
        <div className="grow">
          <div className="tt">
            {a.title}
            {a.amount_usd !== null && <span className="mono"> — ${a.amount_usd.toLocaleString()}</span>}
          </div>
          <div className="sb">the CEO's full case is one click away</div>
        </div>
        <span className={`clock ${clockClass(a.expires_at)}`}>
          {fmtLeft(a.expires_at).replace(" left", "")} → auto-deny
        </span>
        <DecideButtons approvalId={a.id} onDone={reloadApprovals} />
        <Link to={`/projects/${projectId}/records/decisions`}>context →</Link>
      </div>,
    );
  }
  // A RAISED flag is the CEO having brought you a problem with a proposal —
  // "here is what broke, here are the numbers, here is what I would do, say
  // the word". That is a decision waiting on you, so it sits with the others —
  // and until there was a button here it was the one row on this desk you
  // could not answer. Pressing "read it" took you to the boardroom and left
  // the card exactly where it was, forever on the kinds nothing resolves.
  const rawFlags = flagsView?.flags ?? [];
  for (const f of rawFlags.filter((f) => f.status === "raised" && !f.acknowledged_at)) {
    needs.push(
      <div className="arow" key={f.id}>
        <span className="chip serious">{f.kind.replaceAll("_", " ")}</span>
        <div className="grow">
          <div className="tt">{clip(f.summary)}</div>
          <div className="sb">
            {f.proposal ? `proposes: ${clip(f.proposal)}` : "raised without a proposal"}
          </div>
          {/* The third thing every row on this desk owes its reader, and the
              one this card never said: what happens if you do nothing. For a
              flag the honest answer is "the CEO carries on" — it does not
              block and it does not expire, which is exactly why it needs an
              answer rather than a deadline. */}
          <div className="sb help">
            The platform opened this from arithmetic and made the CEO bring it to you. Nothing
            here blocks or expires — it proceeds on its proposal unless you say otherwise.
          </div>
        </div>
        <NoteAction
          label="Noted"
          className="small primary"
          placeholder="Anything to say back? — the CEO reads this"
          confirmLabel="✓ noted"
          onSubmit={async (note) => {
            await post(`/admin/flags/${f.id}/ack`, { note });
            reloadFlags();
          }}
        />
        <Link to={`/projects/${projectId}/boardroom`}>the CEO's case →</Link>
      </div>,
    );
  }
  for (const h of expiredBets) {
    needs.push(
      <div className="arow" key={h.id}>
        <span className="chip serious">bet expired</span>
        <div className="grow">
          <div className="tt">“{clip(h.statement)}” hit its kill date {fmtWhenShort(h.kill_date)}</div>
          <div className="sb">you hold the one extension — or let it stand and the CEO pivots</div>
        </div>
        <Link to={`/projects/${projectId}/work`}>decide →</Link>
      </div>,
    );
  }

  // ── watch ──
  // Auditing lives here and not under "Needs you" on purpose. Nothing expires,
  // nothing breaks, and no agent is waiting on it — so putting it beside an
  // approval that auto-denies in 6 hours teaches the chairman that the
  // needs-you count is padded, and then he stops trusting the count that
  // matters. It is the most valuable optional thing on the page; it is still
  // optional.
  const watch: React.ReactNode[] = [];
  // An UNRAISED flag is different in kind from everything else on this page:
  // nothing is waiting on you, and there is nothing to decide. It is the one
  // signal that your CEO has news it has not brought you — which is the whole
  // reason flags exist. The failure this was built for was a CEO that had
  // written "run ration is the binding constraint" in its own notes and then
  // told its chairman "solid progress". Drawn here, cheerfulness in the
  // boardroom is self-refuting rather than something you have to catch.
  const unraised = rawFlags.filter((f) => f.status === "open");
  for (const f of unraised) {
    watch.push(
      <div className="wrow" key={f.id}>
        <span className="badge serious">not yet raised</span>
        <div className="grow">
          {clip(f.summary)}{" "}
          <span className="help">
            — the platform opened this{f.opened_at ? ` ${fmtWhenShort(f.opened_at)}` : ""}; your
            CEO is required to bring it to the boardroom with a proposal, and the reporting
            cadence does not hold it back.
          </span>
        </div>
        <Link to={`/projects/${projectId}/boardroom`}>ask about it →</Link>
      </div>,
    );
  }
  // Acknowledged, and still true. A moment closes when you answer it — a
  // missed cycle cannot un-miss, so your word is the end of it. A state cannot:
  // the forecast is still negative, and saying so does not turn it. Rather than
  // let the card vanish as if answering had fixed it, it drops here under your
  // own note until the arithmetic turns and the platform resolves it.
  for (const f of rawFlags.filter((f) => f.status === "raised" && f.acknowledged_at)) {
    watch.push(
      <div className="wrow" key={f.id}>
        <span className="badge">noted</span>
        <div className="grow">
          {clip(f.summary)}{" "}
          <span className="help">
            — still true. You noted this
            {f.acknowledged_at ? ` ${fmtWhenShort(f.acknowledged_at)}` : ""}
            {f.acknowledgement ? `: “${clip(f.acknowledgement, 100)}”` : ""}; it clears when the
            numbers do, not when you answer.
          </span>
        </div>
        <Link to={`/projects/${projectId}/boardroom`}>take it up →</Link>
      </div>,
    );
  }
  {
    const { unit, spent, cap, warnRatio, frac } = budgetOf(dash);
    const pace = usdMode ? paceDate(spent, cap) : null;
    if (frac < 1 && (frac >= warnRatio || pace)) {
      watch.push(
        <div className="wrow" key="budget">
          <span className="badge warn">budget {Math.round(frac * 100)}%</span>
          <div className="grow">
            {fmtUnit(spent, unit)} of {fmtUnit(cap, unit)}
            {pace && (
              <>
                {" "}— on pace to hit the cap <strong>{fmtWhenShort(pace.toISOString())}</strong>. The
                platform pauses the project at 100%.
              </>
            )}
          </div>
          <Link to={`/projects/${projectId}/settings`}>amend budget →</Link>
        </div>,
      );
    }
    const windowH = Number(dash.constitution["drought_hours"] ?? 24);
    const applies = hoursSince(dash.created_at) >= windowH;
    // Tracked rather than inferred, because the row below suppresses itself
    // when a drought is already speaking for this project — and "did the
    // drought draw?" is two branches, not one boolean on the dashboard.
    let droughtShown = false;
    if (dash.drought_active) {
      droughtShown = true;
      watch.push(
        <div className="wrow" key="drought">
          <span className="badge serious">drought</span>
          <div className="grow">
            No verified external evidence in {windowH}h — the platform is refusing new internal
            work until proof lands.
          </div>
          <Link to={`/projects/${projectId}/work`}>see the proof →</Link>
        </div>,
      );
    } else if (applies && dash.last_countable_evidence_at) {
      const elapsed = hoursSince(dash.last_countable_evidence_at);
      if (elapsed >= windowH * 0.5) {
        droughtShown = true;
        watch.push(
          <div className="wrow" key="drought">
            <span className="badge warn">drought {Math.floor(elapsed)}h / {windowH}h</span>
            <div className="grow">
              No verified external evidence for {Math.floor(elapsed)} hours — at {windowH}h, new
              internal work gets refused until proof lands.
            </div>
            <Link to={`/projects/${projectId}/work`}>see the proof →</Link>
          </div>,
        );
      }
    }
    // Money moved and nothing outside the project confirmed anything. Distinct
    // from the drought, which counts hours rather than dollars, so it is
    // suppressed while a drought row is already saying the same thing louder.
    // USD only: a zero here in token billing means "not priced in dollars",
    // not "spent nothing".
    if (usdMode && !droughtShown && dash.spend_24h >= 1 && dash.evidence_24h === 0) {
      watch.push(
        <div className="wrow" key="spend-noproof">
          <span className="badge">no proof</span>
          <div className="grow">
            <b>
              ${dash.spend_24h.toLocaleString(undefined, { maximumFractionDigits: 2 })} spent in
              the last 24h
            </b>{" "}
            and nothing verified by anyone outside the project. The work may be real — but
            only the agents say so.
          </div>
          <Link to={`/projects/${projectId}/work`}>see what it bought →</Link>
        </div>,
      );
    }
    for (const h of killSoon) {
      watch.push(
        <div className="wrow" key={h.id}>
          <span className="badge warn">kill date {fmtLeft(h.kill_date).replace(" left", "")}</span>
          <div className="grow">“{clip(h.statement)}” — {fmtWhenShort(h.kill_date)}</div>
          <Link to={`/projects/${projectId}/work`}>see the bet →</Link>
        </div>,
      );
    }
    // A full memory is an outage that looks like nothing: writes are refused,
    // runs die on the refusal cap, and the only symptom is a spend chart that
    // keeps climbing. It earns a row before it gets there.
    for (const m of dash.memory?.scopes ?? []) {
      if (m.live < m.cap * (dash.memory?.warn_ratio ?? 0.85)) continue;
      const full = m.live >= m.cap;
      watch.push(
        <div className="wrow" key={`memory:${m.scope}:${m.team_id ?? ""}`}>
          <span className={`badge ${full ? "serious" : "warn"}`}>
            memory {m.live}/{m.cap}
          </span>
          <div className="grow">
            {m.name} {full ? "is full" : "is nearly full"} —{" "}
            {full
              ? "new entries there are being refused, and only "
              : "at the cap, new entries get refused until "}
            {m.scope === "project" ? "the CEO" : (m.curator ?? "a lead nobody has named")} can
            fold entries to make room.
          </div>
          <Link to={`/projects/${projectId}/records/memory`}>see what it keeps →</Link>
        </div>,
      );
    }
  }

  const runningRuns = (runs ?? []).filter((r) => r.status === "running");
  const soonestApproval = pending.length > 0 ? pending.reduce((a, b) => (a.expires_at < b.expires_at ? a : b)) : null;

  return (
    <>
      {needs.length > 0 ? (
        <>
          <h2 className="zone hot" style={{ marginTop: 6 }}>Needs you <span className="cnt">{needs.length}</span></h2>
          <div className="arows">{needs}</div>
        </>
      ) : (
        <p className="help" style={{ margin: "10px 0 0" }}>Nothing waiting on you here.</p>
      )}

      <ModeStrip dash={dash} reload={reload} />

      {/* Is it working? The question a chairman asks first and the product
          answered nowhere: runs that ended early were a grey aside on a tab
          three clicks away, in the same weight as everything else. */}
      <h2 className="zone">Is it working?</h2>
      {!health ? (
        <Skeleton rows={2} />
      ) : (
        <>
          <div className="tiles" style={{ margin: 0 }}>
            {/* Same source as the project bar. Two live counters polled on
                different clocks will disagree, and a reader who spots two
                numbers for one fact stops trusting both. */}
            <div className="tile">
              <div className="label">Working now</div>
              <div className="value">{dash.runs.running}</div>
              <div className="sub">
                {dash.runs.running === 0
                  ? dash.runs.queued > 0
                    ? `${dash.runs.queued} queued to start`
                    : // A paused or stopped project is not idle, it is off. The
                      // brief above the tabs carries the reason; repeating a
                      // cap here would explain the wrong wall.
                      dash.status !== "running"
                      ? `project is ${dash.status}`
                      : idleSentence(dash.activity)
                  : runningRuns.length === dash.runs.running
                    ? runningRuns.map((r) => r.agent_name ?? "—").join(" · ")
                    : // The roster comes from a slower poll than the count. Rather
                      // than print a list that contradicts the number beside it,
                      // say nothing until the two agree — one tick at most.
                      "…"}
              </div>
            </div>
            {/* The denominator is the whole point: 55 means nothing on its own,
                and showing it without "of 55" is exactly how a project that had
                spent its entire day's allowance read as a busy one.

                Three separate gates count runs — the day, the trailing hour,
                and the slice reserved for governance — and any of them can be
                the reason this tile is the answer. */}
            <div
              className={`tile${
                ["daily_runs", "pacing", "governance_reserve"].includes(
                  dash.activity.blocked_by ?? "",
                )
                  ? " alert"
                  : ""
              }`}
            >
              <div className="label">Runs today</div>
              <div className="value">
                {dash.activity.daily_runs.used}
                <span className="mono" style={{ fontSize: "0.6em", opacity: 0.7 }}>
                  {" "}/ {dash.activity.daily_runs.cap}
                </span>
              </div>
              <div className="sub">
                {dash.activity.blocked_by === "daily_runs"
                  ? `capped — resets at ${dash.activity.daily_runs.resets_at.slice(11, 16)} UTC`
                  : dash.activity.blocked_by === "pacing"
                    ? `pacing — ${dash.activity.pacing.used} of ${dash.activity.pacing.cap} this hour`
                    : dash.activity.blocked_by === "governance_reserve"
                      ? `ordinary work done — last ${dash.activity.governance_reserve.reserved} reserved`
                      : `${health.clean} finished cleanly`}
                {/* Never let a cap read as a total. */}
                {(runs ?? []).length >= 200 && " · newest 200 runs"}
              </div>
            </div>
            <div className={`tile${health.ratio >= 0.2 ? " alert" : ""}`}>
              <div className="label">Ended early</div>
              <div className="value">{health.early.length}</div>
              <div className="sub">
                {health.todays.length === 0
                  ? "—"
                  : health.faults.length === 0
                    ? "none faulted — stopped on purpose"
                    : `${Math.round(health.ratio * 100)}% of what you paid for`}
              </div>
            </div>
            <div className="tile">
              <div className="label">Spent today</div>
              <div className="value">
                {usdMode ? `$${health.usd.toFixed(2)}` : fmtUnit(health.tokens, "tokens")}
              </div>
              <div className="sub">
                {(() => {
                  const { cap } = budgetOf(dash);
                  const share = cap > 0 ? (usdMode ? health.usd : health.tokens) / cap : 0;
                  return `${Math.round(share * 100)}% of the monthly cap`;
                })()}
              </div>
            </div>
          </div>

          {health.early.length > 0 && (
            <div className="card" style={{ marginTop: 12 }}>
              <div style={{ fontSize: 13.5, lineHeight: 1.6 }}>
                <strong>{earlyRunSentence(health)}</strong>{" "}
                {health.faults.length === 0
                  ? "Every one of them was stopped on purpose, so nothing here points at a fault — though their tokens were still spent."
                  : health.ratio >= 0.2
                    ? "Under one in five is normal wear. This much means something is standing in the way — a dead tool mount, a wrong credential, or work an agent cannot actually do."
                    : "That is within normal wear for unattended work."}
              </div>
              <div className="actions" style={{ marginTop: 10 }}>
                <Link to={`/projects/${projectId}/records/runs`}>see what happened →</Link>
              </div>
            </div>
          )}
        </>
      )}

      {/* What came out. The point of the project, previously seven tabs away. */}
      {/* The schedule the platform will actually run — the wakes, the standing
          meeting, the rhythm, the clocks that decide themselves, and the
          promise with the projection beside it. An autonomous project that
          will not show you its calendar is a haunted one. */}
      <h2 className="zone">What happens next</h2>
      <Agenda projectId={projectId} />

      <h2 className="zone">Your deliverable</h2>
      <div className="wbox">
        {latestArtifacts.length === 0 ? (
          <div className="wrow">
            <span className="badge">none yet</span>
            <div className="grow help">
              Nothing published. Deliverables appear here the moment a team publishes one — drafts
              included, so you can read the work as it takes shape.
            </div>
          </div>
        ) : (
          <>
            {latestArtifacts.map((a) => (
              <div className="wrow" key={a.key}>
                {receiptKeys && !receiptKeys.has(a.key) ? (
                  <span className="chip warn">no receipt</span>
                ) : (
                  <span className="chip ok">published</span>
                )}
                <div className="grow">
                  <div className="tt mono">{a.name}</div>
                  <div className="sb">
                    {fmtSize(a.size)} · {a.modified ? fmtWhen(a.modified) : "—"}
                  </div>
                </div>
                <Link
                  to={`/projects/${projectId}/deliverables?open=${encodeURIComponent(a.key)}`}
                >
                  open →
                </Link>
              </div>
            ))}
            <div className="wrow">
              <div className="grow help">
                {deliverableCount} deliverable{deliverableCount === 1 ? "" : "s"} in total.
              </div>
              <Link to={`/projects/${projectId}/deliverables`}>all deliverables →</Link>
            </div>
          </>
        )}
      </div>

      {watch.length > 0 && (
        <>
          <h2 className="zone">Watch <span className="cnt">{watch.length}</span></h2>
          <div className="wbox">{watch}</div>
        </>
      )}

      <h2 className="zone">The plan</h2>
      <div className="wbox">
        <div className="wrow">
          <span className="badge accent">{hypotheses.filter((h) => h.status === "active").length} live bets</span>
          <div className="grow">
            {dash.open_tasks} open task{dash.open_tasks === 1 ? "" : "s"} of {dash.queue_ceiling}{" "}
            ceiling
            {blocked.length > 0 && <> · <span className="chip serious">{blocked.length} blocked</span></>}
            {" · "}
            {dash.external_evidence_in_window} verified external proof
            {dash.external_evidence_in_window === 1 ? "" : "s"} in the last{" "}
            {String(dash.constitution["drought_hours"] ?? 24)}h
          </div>
          <Link to={`/projects/${projectId}/work`}>the work →</Link>
        </div>
        <div className="wrow">
          <span className="badge">
            CEO judgment{" "}
            {dash.ceo_judgment.conversion === null
              ? "—"
              : `${Math.round(dash.ceo_judgment.conversion * 100)}%`}
          </span>
          <div className="grow help">
            {dash.ceo_judgment.tasks_with_verified_external} of {dash.ceo_judgment.tasks_created}{" "}
            bets the CEO placed ever produced real-world proof.
          </div>
          {soonestApproval && (
            <span className={`clock ${clockClass(soonestApproval.expires_at)}`}>
              next decision expires {fmtLeft(soonestApproval.expires_at)}
            </span>
          )}
        </div>
      </div>

      <h2 className="zone">The money</h2>
      <div className="card">
        <BudgetMeter ctx={ctx} />
        <SpendByTeam projectId={projectId} usdMode={usdMode} />
      </div>

      <HealthLine ctx={ctx} />

      <h2 className="zone">The project</h2>
      {repinMsg && <div className="notice-box">{repinMsg}</div>}
      <div className="list-grid">
        <div className="card">
          <div style={{ display: "flex", alignItems: "baseline", gap: 8, flexWrap: "wrap" }}>
            <strong>CEO</strong>
            <div className="spacer" />
            {org?.ceo?.role_name && (
              <span className="chip" title={org.ceo.role_slug ?? undefined}>
                {org.ceo.role_name}
              </span>
            )}
          </div>
          <div style={{ marginTop: 6 }}>
            {org?.ceo ? (
              <span className="badge accent">{org.ceo.name}</span>
            ) : (
              <span className="help">—</span>
            )}
          </div>
          {org?.ceo && <SeatDay seat={today.get(org.ceo.id)} />}
          <p className="help" style={{ marginBottom: 0 }}>
            The only agent that speaks to you (boardroom) and the only one that creates tasks.
          </p>
        </div>
        {(org?.teams ?? []).map((team) => {
          const teamTasks = (tasks ?? []).filter((t) => t.team_id === team.id);
          const open = teamTasks.filter(
            (t) => ["ready", "claimed", "checking", "review"].includes(t.status)).length;
          const stuck = teamTasks.filter((t) => t.status === "blocked").length;
          const behind = team.role_version !== null
            && team.role_latest_version !== null
            && team.role_version < team.role_latest_version;
          return (
            <div className="card" key={team.id}>
              <div style={{ display: "flex", alignItems: "baseline", gap: 8, flexWrap: "wrap" }}>
                <strong>{team.name}</strong>
                <div className="spacer" />
                {/* Which charter these people work from. A team is a role plus
                    its people, and the people were the only half on the card. */}
                {team.role_name && (
                  <span className="chip" title={team.role_slug ?? undefined}>
                    {team.role_name} v{team.role_version}
                  </span>
                )}
                {behind && <span className="chip warn">behind</span>}
                {!team.running && <span className="chip warn">stood down</span>}
              </div>
              {team.agents.map((agent) => (
                <div key={agent.id} style={{ marginTop: 6 }}>
                  <span className={`badge ${agent.status === "active" ? "good" : "warn"}`}>
                    {agent.name}
                    {team.lead_agent_id === agent.id ? " (lead)" : ""}
                    {agent.status !== "active" ? ` · ${agent.status}` : ""}
                  </span>
                  <SeatDay seat={today.get(agent.id)} />
                </div>
              ))}
              <p className="help" style={{ margin: "8px 0 0" }}>
                {teamTasks.length === 0
                  ? "no work on its board"
                  : `${open} open${stuck > 0 ? `, ${stuck} blocked` : ""}`}
                {team.stand_down_when && (
                  <> · stands down when {conditionWords(team.stand_down_when)}</>
                )}
              </p>
              <StaffingActions team={team} onDone={reloadOrg} />
              {/* Teams pin their role version at creation; edited roles apply
                  only when the chairman explicitly adopts them. The button is
                  here only when there is something to adopt — five cards each
                  offering to move a team to the version it already runs is
                  five buttons that teach the reader to ignore the row. */}
              {behind && (
                <div className="actions" style={{ marginTop: 8 }}>
                  <button
                    className="small"
                    onClick={async () => {
                      const result = await post<{ changed: boolean; version: number }>(
                        `/admin/teams/${team.id}/repin`, {});
                      setRepinMsg(result.changed
                        ? `${team.name} now runs ${team.role_name ?? team.role_slug} v${result.version}; effective on the next run.`
                        : `${team.name} is already on the latest role version (v${result.version}).`);
                      reloadOrg();
                    }}
                  >
                    adopt v{team.role_latest_version}
                  </button>
                </div>
              )}
            </div>
          );
        })}
        <AddTeam projectId={projectId} org={org} onAdded={reloadOrg} />
      </div>

      <AsOf at={asOf} error={error} />
    </>
  );
}

/** The staffing controls, which lived only behind an admin curl until now.

    Both verbs take a reason. A roster change without one is unreadable six
    weeks later, and "we took the designer off after the template shipped" is a
    sentence somebody has to be able to find. */
function StaffingActions({ team, onDone }: {
  team: OrgView["teams"][number];
  onDone: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const verb = team.running ? "stand_down" : "recall";

  async function go() {
    const reason = window.prompt(
      team.running
        ? `Why is ${team.name}'s job finished? (Its channel, memory and history are kept.)`
        : `Why is ${team.name} coming back?`);
    if (!reason || reason.trim().length < 10) {
      if (reason !== null) setError("Give a reason of at least ten characters.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await post(`/admin/teams/${team.id}/${verb}`, { reason });
      onDone();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="actions" style={{ marginTop: 8 }}>
      <button className="small" disabled={busy} onClick={go}>
        {team.running ? "stand down" : "recall"}
      </button>
      {error && <span className="help warn" style={{ marginLeft: 8 }}>{error}</span>}
    </div>
  );
}

/** The condition in the words the boardroom uses, so the card and the message
    the chairman already read say the same thing. */
function conditionWords(when: NonNullable<OrgView["teams"][number]["stand_down_when"]>) {
  return when.kind === "requirement_met"
    ? `${(when.keys ?? []).join(" and ")} are met`
    : `every requirement at or below stage ${when.stage} has settled`;
}

/** One seat's day, under its name.

    Runs first because runs are the rationed thing and the number people argue
    about; tasks closed second because a seat that spends and closes nothing is
    the shape worth noticing. Silent when the seat has not run — an empty row
    under every name on a quiet morning teaches the reader to skip the line. */
function SeatDay({ seat }: { seat: ActivitySeat | undefined }) {
  if (!seat || seat.runs === 0) return null;
  const tokens = seat.tokens_in + seat.tokens_out + seat.cache_creation + seat.cache_read;
  const parts = [`${seat.runs} run${seat.runs === 1 ? "" : "s"} today`];
  if (seat.tasks_completed) parts.push(`${seat.tasks_completed} closed`);
  if (seat.holding) parts.push(`${seat.holding} in hand`);
  if (tokens) parts.push(`${(tokens / 1e6).toFixed(1)}M tok`);
  if (seat.usd) parts.push(`$${seat.usd.toFixed(2)}`);
  return (
    <div className="help" style={{ fontSize: 12, marginTop: 2 }}>{parts.join(" · ")}</div>
  );
}

/** Hiring onto a project that already exists.

    The other door into the same panel. Here the chairman is not browsing —
    they know the symptom ("nothing gets checked", "nobody writes it up"), so
    this one opens on the gaps its own roster implies before it offers the
    library. */
function AddTeam({ projectId, org, onAdded }: {
  projectId: string;
  org: OrgView | null;
  onAdded: () => void;
}) {
  const { roles, crews, loaded, error: libraryError } = useRoleLibrary();
  const [open, setOpen] = useState(false);
  const [picked, setPicked] = useState<string[]>([]);
  const [teamName, setTeamName] = useState("");
  const [employees, setEmployees] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const roleSlug = picked[0] ?? "";
  const role = roles.find((r) => r.slug === roleSlug);

  // Which roles this project already runs, and under which team name — shown
  // on the row so hiring a second Data Analyst is a choice, not an accident.
  const alreadyOn: Record<string, string> = {};
  for (const team of org?.teams ?? []) {
    if (team.role_slug) alreadyOn[team.role_slug] = team.name;
  }
  const roster = roles.filter((r) => r.slug in alreadyOn);
  const gaps = gapsFor(roster).filter(
    (gap) => gap.suggest.some((slug) => !(slug in alreadyOn)));

  // One role at a time here: a team is a role plus its people, and the names
  // are the part only the chairman can supply.
  function toggle(slug: string) {
    setPicked((prev) => (prev[0] === slug ? [] : [slug]));
    const next = roles.find((r) => r.slug === slug);
    if (next && !teamName.trim()) setTeamName(next.name.split(" / ")[0]);
  }

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      await post(`/admin/projects/${projectId}/teams`, {
        role_slug: roleSlug,
        name: teamName.trim(),
        employees: employees.split(",").map((s) => s.trim()).filter(Boolean),
      });
      setTeamName("");
      setEmployees("");
      setPicked([]);
      onAdded();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card">
      <strong>＋ Add a team</strong>
      {error && <div className="error">{error}</div>}
      {gaps.length > 0 && !roleSlug && (
        <p className="help" style={{ margin: "6px 0 0" }}>
          {gaps[0].sentence}
        </p>
      )}
      <div className="actions" style={{ marginTop: 8 }}>
        <button className="small" onClick={() => setOpen(true)}>
          {role ? `Role: ${role.name} — change` : "Choose a role…"}
        </button>
      </div>
      {role && (
        <>
          <label className="field">Team name</label>
          <input value={teamName} onChange={(e) => setTeamName(e.target.value)}
            placeholder="Content" />
          <label className="field">
            Employees (comma-separated names; first is lead if several)
          </label>
          <input value={employees} onChange={(e) => setEmployees(e.target.value)}
            placeholder="Blogger" />
          {needsOf(role.secret_scopes ?? []).length > 0 && (
            <p className="help" style={{ marginTop: 6 }}>
              {role.name} needs{" "}
              {needsOf(role.secret_scopes ?? []).map((n) => n.words).join(" and ")} —
              add it in Settings → Credentials, scoped to this role, or its runs
              will fail asking for it.
            </p>
          )}
          <div className="actions">
            <button
              className="primary small"
              disabled={busy || !roleSlug || !teamName.trim() || !employees.trim()}
              onClick={submit}
            >
              Add team
            </button>
            <span className="help">
              The CEO sees the new team in its next context and can plan for it.
            </span>
          </div>
        </>
      )}
      {open && (
        <HirePanel
          roles={roles}
          crews={crews}
          loaded={loaded}
          error={libraryError}
          selected={picked}
          onToggle={toggle}
          onClose={() => setOpen(false)}
          alreadyOn={alreadyOn}
          gaps={gaps}
          title="Who's missing?"
        />
      )}
    </div>
  );
}
