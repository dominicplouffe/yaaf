import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  get,
  HealthCheck,
  post,
  RoleSummary,
  RunRow,
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
  paceDate,
  runHealth,
  Skeleton,
} from "../../ui";
import type { ProjectCtx } from "../Project";

interface ArtifactFile {
  key: string;
  run_id: string | null;
  name: string;
  size: number;
  modified: string | null;
}
interface FilesView {
  workspace: { path: string; size: number; modified: string }[];
  artifacts: ArtifactFile[];
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
  const { projectId, health: data, reloadHealth } = ctx;
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
              }}
            >
              adopt latest role version
            </button>
          ) : check.where === "settings" && check.status !== "ok" ? (
            <Link to={`/projects/${projectId}/settings`}>fix in Settings →</Link>
          ) : null}
        </div>
      ))}
    </div>
  );
}

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
      </div>
    </div>
  );
}

/** Who is spending it. One series, named rows, sorted — the question is
    "which team costs me money", and a 14-day time series cannot answer it. */
function SpendByTeam({ projectId, usdMode }: { projectId: string; usdMode: boolean }) {
  const { data } = usePoll(
    () => get<SpendReport>(`/observe/projects/${projectId}/spend?group_by=team&days=30`),
    30000,
  );
  const rows = [...(data?.rows ?? [])].sort((a, b) => b.usd - a.usd);
  if (rows.length === 0) return null;
  const max = Math.max(...rows.map((r) => r.usd), 1e-9);
  const total = rows.reduce((sum, r) => sum + r.usd, 0);
  const top = rows[0];
  const rest = total - top.usd;

  return (
    <div style={{ marginTop: 22 }}>
      <div className="fs-h">Who is spending it — last 30 days</div>
      <div className="hbars">
        {rows.map((row, i) => (
          <div className="hbar" key={row.key}>
            <span>{row.label}</span>
            <span className="track">
              <i className={i === 0 ? "hi" : ""} style={{ width: `${Math.max((row.usd / max) * 100, 0.8)}%` }} />
            </span>
            <span className="v">
              {usdMode ? `$${row.usd.toFixed(2)}` : fmtUnit(row.tokens_out, "tokens")} · {row.runs} run
              {row.runs === 1 ? "" : "s"}
            </span>
          </div>
        ))}
      </div>
      {usdMode && rows.length > 1 && top.usd > rest && (
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
            they passed — and closes the company down cleanly. Until you do, it keeps
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
    return (
      <p className="help" style={{ margin: "12px 0 0" }}>
        Cycle {dash.cycle.ordinal}, open since {fmtWhen(dash.cycle.opened_at)} — each
        cycle plans fresh, and the CEO opens it by reading the last three cycles'
        narratives.
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
  const [repinMsg, setRepinMsg] = useState<string | null>(null);

  const pending = approvals.filter((a) => a.status === "pending");
  const expiredBets = hypotheses.filter((h) => h.status === "expired" && !h.extended_once);
  const completed = (tasks ?? []).filter((t) => t.status === "done");
  const unaudited = completed.filter((t) => !t.audit_status).length;
  const blocked = (tasks ?? []).filter((t) => t.status === "blocked");
  const killSoon = hypotheses.filter(
    (h) => h.status === "active" && hoursSince(h.kill_date) > -7 * 24 && hoursSince(h.kill_date) < 0,
  );

  const health = runs ? runHealth(runs) : null;
  const usdMode = dash.billing_mode === "api_key";

  // Newest artifact per name — the same deliverable republished by a later run
  // is a version of one thing, not two unrelated files.
  const latestArtifacts = (() => {
    const byName = new Map<string, ArtifactFile>();
    for (const a of files?.artifacts ?? []) {
      const seen = byName.get(a.name);
      if (!seen || (a.modified ?? "") > (seen.modified ?? "")) byName.set(a.name, a);
    }
    return [...byName.values()]
      .sort((a, b) => (b.modified ?? "").localeCompare(a.modified ?? ""))
      .slice(0, 3);
  })();

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
  if (unaudited >= 3) {
    watch.push(
      <div className="wrow" key="audit">
        <span className="badge">spot-check</span>
        <div className="grow">
          <b>{unaudited} finished {unaudited === 1 ? "task" : "tasks"} nobody has checked.</b>{" "}
          The agents marked these done and wrote their own evidence for it. Two minutes on five
          of them at random: you see what each task had to prove and what the agent claimed,
          and you say whether you believe it.
        </div>
        <Link to={`/projects/${projectId}/work?audit=1`}>check 5 now →</Link>
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
    if (dash.drought_active) {
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
                and showing it without "of 55" is exactly how a company that had
                spent its entire day's allowance read as a busy one. */}
            <div
              className={`tile${
                dash.activity.blocked_by === "daily_runs" ? " alert" : ""
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
                  : `${health.clean} finished cleanly`}
                {/* Never let a cap read as a total. */}
                {(runs ?? []).length >= 200 && " · newest 200 runs"}
              </div>
            </div>
            <div className={`tile${health.ratio >= 0.2 ? " alert" : ""}`}>
              <div className="label">Ended early</div>
              <div className="value">{health.early.length}</div>
              <div className="sub">
                {health.todays.length > 0
                  ? `${Math.round(health.ratio * 100)}% of what you paid for`
                  : "—"}
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
                {health.ratio >= 0.2
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
                <span className="chip ok">published</span>
                <div className="grow">
                  <div className="tt mono">{a.name}</div>
                  <div className="sb">
                    {fmtSize(a.size)} · {a.modified ? fmtWhen(a.modified) : "—"}
                  </div>
                </div>
                <Link to={`/projects/${projectId}/deliverables`}>open →</Link>
              </div>
            ))}
            <div className="wrow">
              <div className="grow help">
                {(files?.artifacts ?? []).length} published file
                {(files?.artifacts ?? []).length === 1 ? "" : "s"} in total.
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

      <h2 className="zone">The company</h2>
      {repinMsg && <div className="notice-box">{repinMsg}</div>}
      <div className="list-grid">
        <div className="card">
          <strong>CEO</strong>
          <div style={{ marginTop: 6 }}>
            {org?.ceo ? (
              <span className="badge accent">{org.ceo.name}</span>
            ) : (
              <span className="help">—</span>
            )}
          </div>
          <p className="help" style={{ marginBottom: 0 }}>
            The only agent that speaks to you (boardroom) and the only one that creates tasks.
          </p>
        </div>
        {(org?.teams ?? []).map((team) => {
          const teamTasks = (tasks ?? []).filter((t) => t.team_id === team.id);
          const open = teamTasks.filter((t) => ["ready", "claimed", "checking"].includes(t.status)).length;
          const stuck = teamTasks.filter((t) => t.status === "blocked").length;
          return (
            <div className="card" key={team.id}>
              <strong>{team.name}</strong>
              <div className="pill-row" style={{ marginTop: 6 }}>
                {team.agents.map((agent) => (
                  <span key={agent.id} className={`badge ${agent.status === "active" ? "good" : "warn"}`}>
                    {agent.name}
                    {team.lead_agent_id === agent.id ? " (lead)" : ""}
                  </span>
                ))}
              </div>
              <p className="help" style={{ margin: "8px 0 0" }}>
                {open} open{stuck > 0 && `, ${stuck} blocked`}
                {teamTasks.length === 0 && "no work on its board"}
              </p>
              {/* Teams pin their role version at creation; edited roles apply
                  only when the chairman explicitly adopts them. */}
              <div className="actions" style={{ marginTop: 8 }}>
                <button
                  className="small"
                  onClick={async () => {
                    const result = await post<{ changed: boolean; version: number }>(
                      `/admin/teams/${team.id}/repin`, {});
                    setRepinMsg(result.changed
                      ? `${team.name} now runs role version ${result.version}; effective on the next run.`
                      : `${team.name} is already on the latest role version (${result.version}).`);
                  }}
                >
                  adopt latest role version
                </button>
              </div>
            </div>
          );
        })}
        <AddTeam projectId={projectId} onAdded={reloadOrg} />
      </div>

      <AsOf at={asOf} error={error} />
    </>
  );
}

function AddTeam({ projectId, onAdded }: { projectId: string; onAdded: () => void }) {
  const [roles, setRoles] = useState<RoleSummary[]>([]);
  const [roleSlug, setRoleSlug] = useState("");
  const [teamName, setTeamName] = useState("");
  const [employees, setEmployees] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    get<RoleSummary[]>("/admin/roles")
      .then((all) => setRoles(all.filter((r) => !r.is_ceo_role)))
      .catch(() => {});
  }, []);

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
      onAdded();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <details className="card">
      <summary>＋ Add a team</summary>
      {error && <div className="error">{error}</div>}
      <label className="field">Role (from the library)</label>
      <select value={roleSlug} onChange={(e) => setRoleSlug(e.target.value)}>
        <option value="">— pick a role —</option>
        {roles.map((role) => (
          <option key={role.slug} value={role.slug}>
            {role.name} ({role.model ?? "default"})
          </option>
        ))}
      </select>
      <label className="field">Team name</label>
      <input value={teamName} onChange={(e) => setTeamName(e.target.value)} placeholder="Content" />
      <label className="field">Employees (comma-separated names; first is lead if several)</label>
      <input value={employees} onChange={(e) => setEmployees(e.target.value)} placeholder="Blogger" />
      <div className="actions">
        <button
          className="primary small"
          disabled={busy || !roleSlug || !teamName.trim() || !employees.trim()}
          onClick={submit}
        >
          Add team
        </button>
        <span className="help">The CEO sees the new team in its next context and can plan for it.</span>
      </div>
    </details>
  );
}
