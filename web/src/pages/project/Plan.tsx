import { useMemo, useState } from "react";
import { get, post } from "../../api";
import { usePoll } from "../../hooks";
import { AsOf, ConfirmModal, NoteAction, Skeleton } from "../../ui";
import type { ProjectCtx } from "../Project";

/** The visual plan the chairman asked for — twice — before it was twice
    deferred and dropped. Two views over one dataset, so they cannot disagree:
    the stage board answers "when will I have it", the dependency graph answers
    "what is blocking what". The critical path arrives computed from the
    server, from the same arithmetic the refusals quote. */

interface PlanTask {
  id: string;
  title: string;
  status: string;
  holder: string | null;
  failed_checks: number;
}

interface PlanRequirement {
  id: string;
  key: string;
  statement: string;
  status: string;
  stage: number;
  test_kind: string | null;
  acceptance_test: { type?: string; command?: string; target?: string };
  est_runs: number | null;
  spent: { runs: number; usd: number };
  failure_load: number;
  depends_on: string[];
  tasks: PlanTask[];
  met_at: string | null;
  cut_reason: string | null;
  check_output: string | null;
  chairman_verdict: string | null;
}

interface PlanCommitment {
  id: string;
  status: string;
  deliver_by: string;
  projected_done_at: string | null;
  projection_basis: string | null;
  accepted_at: string | null;
  constitution_version: number | null;
  scope_snapshot: string[];
  status_reason: string | null;
  note: string | null;
  met_at: string | null;
}

interface PlanBurnup {
  scope_total: number;
  met_points: { at: string; count: number }[];
}

interface PlanView {
  mode: string;
  planless: boolean;
  commitment?: PlanCommitment | null;
  burnup?: PlanBurnup | null;
  plan?: {
    status: string | null;
    holds: boolean;
    outstanding: string[];
    arithmetic: {
      requirements: number;
      estimated_runs: number;
      multiplier: number;
      adjusted_runs: number;
      days_at_the_daily_cap: number | null;
      affordable: boolean;
      budget: { known: boolean; runs?: number; usd_remaining?: number;
        usd_per_run?: number; why?: string };
    };
    planning_spend: { runs: number; usd: number } | null;
  };
  open_stage?: number;
  critical_path?: { keys: string[]; adjusted_runs: number; multiplier?: number };
  velocity?: {
    met_3w: number;
    per_week_3w: number;
    met_12w: number;
    per_week_12w: number;
    remaining: number;
    weeks_left_at_pace: number | null;
    runs_3w: { total: number; dead: number; failure_share: number;
      attributed_share: number; usd: number;
      work: number; inbox: number; noop: number;
      inbox_share: number; noop_share: number };
    worst_failure: { key: string; failure_load: number; statement: string } | null;
  };
  /** What an Operate project has instead of a burndown: a burndown of work
      that recurs is a flat line, and the shape that carries the information is
      which obligation slipped, and in which turn. */
  standing?: {
    key: string;
    statement: string;
    met: number;
    judged: number;
    status_now: string;
  }[];
  requirements?: PlanRequirement[];
}

function statusBadge(status: string) {
  const cls =
    status === "met" ? "good"
    : status === "cut" ? "critical"
    : status === "checking" ? "warn"
    : status === "in_progress" ? "accent"
    : status === "proposed" ? "serious"
    : "";
  const label = status === "in_progress" ? "in progress" : status;
  return <span className={`badge ${cls}`}>{label}</span>;
}

const SETTLED = new Set(["met", "cut"]);

/** Saying no, at the length saying no actually takes.

    The other levers here are one-line notes and should stay that way — accept,
    cut and "it holds" are decisions whose reason fits in a sentence. A
    rejection is not one of those: it is the entire brief for the rebuild that
    follows, the CEO plans from it and nothing else, and the single-line input
    this used to share with the rest quietly asked a chairman with six defects
    to find to summarise them in a tweet. */
function RejectVerdict({ r, onDone }: { r: PlanRequirement; onDone: () => void }) {
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function go() {
    if (!note.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await post(`/admin/requirements/${r.id}/verdict`, { ok: false, note: note.trim() });
      setOpen(false);
      setNote("");
      onDone();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (!open) {
    return (
      <button className="small danger" onClick={() => setOpen(true)}>
        It doesn't…
      </button>
    );
  }
  return (
    <ConfirmModal
      title={`${r.key} does not hold`}
      confirmLabel="✗ Not met — send it back"
      danger
      busy={busy || !note.trim()}
      onConfirm={go}
      onCancel={() => { setOpen(false); setError(null); }}
    >
      <p style={{ marginTop: 0 }}>{r.statement}</p>
      <p>
        Write what is wrong and what has to change. The CEO plans the whole rebuild from
        this and from nothing else, so the detail you put here is the detail it works to.
      </p>
      <textarea
        autoFocus
        rows={12}
        value={note}
        onChange={(e) => setNote(e.target.value)}
        placeholder={"What is wrong with the deliverable.\nWhat the correct method or "
          + "number is.\nWhether anything you already accepted has to be redone because "
          + "of it — defects usually surface at the last deliverable and are made "
          + "further upstream."}
      />
      <ul style={{ margin: "10px 0 0", paddingLeft: 18 }}>
        <li>
          <b>It stays rejected.</b> It comes back to you only after new work closes under
          it — you will not be asked about the same unchanged deliverable again.
        </li>
        <li>
          <b>The CEO is woken immediately</b> with these words in full, and decides what
          this requirement rebuilds and whether earlier stages have to be redone.
        </li>
        <li>
          A stage you already accepted cannot be reopened — the CEO proposes a fresh
          requirement at that stage and you accept it, which puts the later stages back
          behind it.
        </li>
      </ul>
      {error && <p className="help" style={{ color: "var(--critical)" }}>{error}</p>}
    </ConfirmModal>
  );
}

/** Chairman levers for one requirement. Under the default authority dial
    accept and cut are his alone — the CEO proposes — and the verdict is how
    an observable acceptance test gets an answer a script cannot give. Settled
    scope gets the undo: reopen returns a met or cut requirement to the plan,
    and its stage reopens with it. */
function Levers({ r, onDone }: { r: PlanRequirement; onDone: () => void }) {
  if (r.status === "proposed") {
    return (
      <>
        <NoteAction
          label="Accept…"
          className="small primary"
          placeholder="Note (optional) — the CEO reads it"
          confirmLabel="✓ Accept into scope"
          onSubmit={async (note) => {
            await post(`/admin/requirements/${r.id}/accept`, { note });
            onDone();
          }}
        />
        <NoteAction
          label="Cut…"
          className="small danger"
          placeholder="Why this is out — the CEO reads it and re-plans"
          confirmLabel="✂ Cut"
          requireNote
          onSubmit={async (note) => {
            await post(`/admin/requirements/${r.id}/cut`, { reason: note });
            onDone();
          }}
        />
      </>
    );
  }
  if (r.status === "checking" && r.test_kind === "observable") {
    return (
      <>
        <NoteAction
          label="It holds…"
          className="small primary"
          placeholder="What you checked (optional)"
          confirmLabel="✓ Met"
          onSubmit={async (note) => {
            await post(`/admin/requirements/${r.id}/verdict`, { ok: true, note });
            onDone();
          }}
        />
        <RejectVerdict r={r} onDone={onDone} />
      </>
    );
  }
  if (!SETTLED.has(r.status)) {
    return (
      <NoteAction
        label="Cut…"
        className="small danger"
        placeholder="Why this is out of scope — the CEO reads it and re-plans"
        confirmLabel="✂ Cut"
        requireNote
        onSubmit={async (note) => {
          await post(`/admin/requirements/${r.id}/cut`, { reason: note });
          onDone();
        }}
      />
    );
  }
  // Settled — met or cut. Reopen is the undo that never used to exist.
  return (
    <NoteAction
      label="Reopen…"
      className="small"
      placeholder={r.status === "met"
        ? "Why this is not actually done — it returns to the plan"
        : "Why this belongs back in scope — it returns to the plan"}
      confirmLabel="↩ Reopen"
      requireNote
      onSubmit={async (note) => {
        await post(`/admin/requirements/${r.id}/reopen`, { reason: note });
        onDone();
      }}
    />
  );
}

function testLine(r: PlanRequirement): string {
  const test = r.acceptance_test || {};
  if (test.type === "command") return test.command ?? "";
  if (test.type === "observable") return test.target ?? "";
  return "structural — the platform checks the plan itself";
}

/** One requirement, in full: the statement is the headline, the test is one
    click away, and the numbers are the same ones the refusals quote. */
function RequirementCard({ r, onCritical, onDone }: {
  r: PlanRequirement; onCritical: boolean; onDone: () => void;
}) {
  const [open, setOpen] = useState(false);
  const over = r.est_runs != null && r.spent.runs > r.est_runs && !SETTLED.has(r.status);
  return (
    <div className="card" style={{
      padding: "10px 14px", marginBottom: 8,
      opacity: SETTLED.has(r.status) ? 0.65 : 1,
      borderLeft: onCritical ? "3px solid var(--accent)" : undefined,
    }}>
      <div style={{ display: "flex", gap: 10, alignItems: "baseline", flexWrap: "wrap" }}>
        <span className="chip">{r.key}</span>
        <button className="linklike" style={{ flex: 1, minWidth: 220, textAlign: "left",
          background: "none", border: "none", padding: 0, font: "inherit",
          color: "var(--ink)", cursor: "pointer" }}
          onClick={() => setOpen(!open)}>
          {r.statement}
        </button>
        {statusBadge(r.status)}
        {r.failure_load > 0 && (
          <span className="badge serious">{r.failure_load} failures</span>
        )}
        <span className="chip" title="runs spent / estimated"
          style={over ? { color: "var(--serious)" } : undefined}>
          {r.spent.runs}{r.est_runs != null ? `/${r.est_runs}` : ""} runs
        </span>
        {r.spent.usd > 0 && <span className="chip">${r.spent.usd.toFixed(2)}</span>}
        <Levers r={r} onDone={onDone} />
      </div>
      {r.depends_on.length > 0 && !SETTLED.has(r.status) && (
        <div style={{ marginTop: 4, fontSize: 12, color: "var(--muted)" }}>
          waits for {r.depends_on.join(", ")}
        </div>
      )}
      {/* Not behind the fold. What you said when you sent it back is the
          reason this requirement is in the state it is in, and it stays on
          the card until the rebuild comes back to you. */}
      {r.chairman_verdict && !SETTLED.has(r.status) && (
        <div className="sb" style={{
          marginTop: 6, paddingLeft: 8, whiteSpace: "pre-wrap",
          borderLeft: "3px solid var(--critical)",
        }}>
          <span className="help">you sent this back: </span>{r.chairman_verdict}
        </div>
      )}
      {open && (
        <div style={{ marginTop: 8, fontSize: 13, color: "var(--ink-2)" }}>
          <div style={{ fontFamily: "var(--mono, monospace)", fontSize: 12 }}>
            {r.test_kind === "command" ? "$ " : ""}{testLine(r)}
          </div>
          {r.check_output && !r.chairman_verdict && (
            <div style={{ marginTop: 6 }}>
              <span className="help">last check: </span>{r.check_output}
            </div>
          )}
          {r.cut_reason && (
            <div style={{ marginTop: 6 }}>
              <span className="help">cut because: </span>{r.cut_reason}
            </div>
          )}
          {r.tasks.length > 0 && (
            <table style={{ marginTop: 8 }}>
              <tbody>
                {r.tasks.map((t) => (
                  <tr key={t.id}>
                    <td style={{ width: "60%" }}>{t.title}</td>
                    <td><span className="badge">{t.status}</span></td>
                    <td>{t.holder ?? "—"}</td>
                    <td>{t.failed_checks > 0 ? `${t.failed_checks} failed checks` : ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </div>
  );
}

// ── the commitment ──────────────────────────────────────────────────────────────

function fmtDay(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function isoDay(iso: string): string {
  return iso.slice(0, 10);
}

/** The CEO asked to promise a date it did not get to pick. The chairman takes
    it, replaces it with his own, or declines — nothing binds until he acts. */
function ProposedCommitment({ c, onDone }: { c: PlanCommitment; onDone: () => void }) {
  const [date, setDate] = useState(isoDay(c.deliver_by));
  return (
    <div className="strip" style={{ marginBottom: 14 }}>
      <div className="grow">
        <b>The CEO proposes to commit: {c.scope_snapshot.length > 0
          ? `${c.scope_snapshot.length} requirements` : "the accepted scope"} by {fmtDay(c.deliver_by)}.</b>
        <div className="sb">
          The date is the platform's own projection — {c.projection_basis ?? "computed from the plan"}.
          {c.note ? <> The CEO adds: “{c.note}”</> : null}
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 8, flexWrap: "wrap" }}>
          <label className="help" htmlFor="cmt-date">deliver by</label>
          <input id="cmt-date" type="date" value={date} min={isoDay(new Date().toISOString())}
            onChange={(e) => setDate(e.target.value)}
            style={{ font: "inherit", fontSize: 13, padding: "3px 6px",
              background: "var(--surface)", color: "var(--ink)",
              border: "1px solid var(--border)", borderRadius: 5 }} />
          <button className="small primary" onClick={async () => {
            const body = date !== isoDay(c.deliver_by)
              ? { deliver_by: `${date}T23:59:59Z` } : {};
            await post(`/admin/commitments/${c.id}/accept`, body);
            onDone();
          }}>✓ Accept the commitment</button>
          <NoteAction
            label="Decline…"
            className="small danger"
            placeholder="Why not — the CEO reads it and keeps working the plan"
            confirmLabel="✗ Decline"
            requireNote
            onSubmit={async (note) => {
              await post(`/admin/commitments/${c.id}/decline`, { reason: note });
              onDone();
            }}
          />
        </div>
      </div>
    </div>
  );
}

/** A promise that stopped holding. New scope is already frozen server-side —
    this shows the arithmetic and hands the chairman his levers. */
function SlippingCommitment({ c, onDone }: { c: PlanCommitment; onDone: () => void }) {
  const [date, setDate] = useState("");
  const [reason, setReason] = useState("");
  const breached = c.status === "breached";
  return (
    <div className={`strip ${breached ? "critical" : "serious"}`} style={{ marginBottom: 14 }}>
      <div className="grow">
        <b>
          {breached
            ? `The commitment is breached: ${fmtDay(c.deliver_by)} has passed.`
            : `The commitment is at risk: promised ${fmtDay(c.deliver_by)}, projected ${c.projected_done_at ? fmtDay(c.projected_done_at) : "unknown"}.`}
        </b>
        <div className="sb">
          {c.status_reason ?? ""} New scope is frozen for the CEO — existing work continues.
          Your levers: cut requirements below, or move the date with a reason.
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 8, flexWrap: "wrap" }}>
          <input type="date" value={date} onChange={(e) => setDate(e.target.value)}
            style={{ font: "inherit", fontSize: 13, padding: "3px 6px",
              background: "var(--surface)", color: "var(--ink)",
              border: "1px solid var(--border)", borderRadius: 5 }} />
          <input type="text" value={reason} placeholder="Why the date moves — the CEO reads it"
            onChange={(e) => setReason(e.target.value)}
            style={{ font: "inherit", fontSize: 13, padding: "3px 8px", minWidth: 280,
              background: "var(--surface)", color: "var(--ink)",
              border: "1px solid var(--border)", borderRadius: 5 }} />
          <button className="small" disabled={!date || reason.trim().length < 5}
            onClick={async () => {
              await post(`/admin/commitments/${c.id}/move`,
                { deliver_by: `${date}T23:59:59Z`, reason });
              onDone();
            }}>Move the date</button>
        </div>
      </div>
    </div>
  );
}

/** The burn-up: what has become true, against the promise and the projection.
    Server data only — the two markers are the commitment (his) and the
    projection (the platform's), which is the whole conversation. */
function Burnup({ c, burnup }: { c: PlanCommitment; burnup: PlanBurnup }) {
  const W = 720, H = 170, L = 36, R = 12, T = 14, B = 26;
  const now = Date.now();
  const points = burnup.met_points.map((p) => ({ t: new Date(p.at).getTime(), n: p.count }));
  const start = Math.min(
    c.accepted_at ? new Date(c.accepted_at).getTime() : now,
    points.length ? points[0].t : now,
  );
  const deliverBy = new Date(c.deliver_by).getTime();
  const projected = c.projected_done_at ? new Date(c.projected_done_at).getTime() : null;
  const end = Math.max(deliverBy, projected ?? 0, now) + (deliverBy - start) * 0.06 + 1;
  const yMax = Math.max(burnup.scope_total, 1);
  const x = (t: number) => L + ((t - start) / (end - start)) * (W - L - R);
  const y = (n: number) => H - B - (n / yMax) * (H - T - B);

  // The line of record: met counts, stepped. Then the platform's dashed reach
  // from where the work stands now to where it is projected to land.
  let line = `M ${x(start)} ${y(0)}`;
  let lastN = 0;
  for (const p of points) {
    line += ` L ${x(p.t)} ${y(lastN)} L ${x(p.t)} ${y(p.n)}`;
    lastN = p.n;
  }
  line += ` L ${x(Math.min(now, end))} ${y(lastN)}`;

  return (
    <div className="card" style={{ padding: "14px 18px", marginBottom: 14 }}>
      <div style={{ display: "flex", gap: 12, alignItems: "baseline", flexWrap: "wrap" }}>
        <b style={{ fontSize: 13 }}>Burn-up</b>
        <span className="help">
          {lastN} of {burnup.scope_total} true · committed {fmtDay(c.deliver_by)}
          {projected ? ` · projected ${fmtDay(new Date(projected).toISOString())}` : ""}
          {c.projection_basis ? ` — ${c.projection_basis}` : ""}
        </span>
      </div>
      <div style={{ overflowX: "auto" }}>
        <svg viewBox={`0 0 ${W} ${H}`} style={{ display: "block", width: "100%", minWidth: 520 }}>
          {/* scope line */}
          <line x1={L} y1={y(yMax)} x2={W - R} y2={y(yMax)}
            stroke="var(--border)" strokeDasharray="2 3" />
          <text x={L} y={y(yMax) - 4} fontSize={10} fill="var(--muted)">
            scope · {burnup.scope_total}
          </text>
          <line x1={L} y1={y(0)} x2={W - R} y2={y(0)} stroke="var(--border)" />
          {/* the two markers: his date, and the platform's */}
          <line x1={x(deliverBy)} y1={T} x2={x(deliverBy)} y2={H - B}
            stroke={c.status === "breached" ? "var(--critical)" : "var(--ink-2)"}
            strokeWidth={1.4} />
          <text x={x(deliverBy)} y={H - B + 14} fontSize={10} textAnchor="middle"
            fill={c.status === "breached" ? "var(--critical)" : "var(--ink-2)"}>
            committed {fmtDay(c.deliver_by)}
          </text>
          {projected && Math.abs(x(projected) - x(deliverBy)) > 40 && (
            <text x={x(projected)} y={T + 9} fontSize={10} textAnchor="middle"
              fill={projected > deliverBy ? "var(--serious)" : "var(--good)"}>
              projected {fmtDay(new Date(projected).toISOString())}
            </text>
          )}
          {projected && (
            <line x1={x(projected)} y1={T} x2={x(projected)} y2={H - B}
              stroke={projected > deliverBy ? "var(--serious)" : "var(--good)"}
              strokeDasharray="4 3" strokeWidth={1.4} />
          )}
          {/* projection reach: from where the work stands to where it lands */}
          {projected && lastN < yMax && (
            <line x1={x(Math.min(now, end))} y1={y(lastN)} x2={x(projected)} y2={y(yMax)}
              stroke={projected > deliverBy ? "var(--serious)" : "var(--good)"}
              strokeDasharray="3 4" strokeWidth={1.2} />
          )}
          {/* the record */}
          <path d={line} fill="none" stroke="var(--accent)" strokeWidth={2} />
          {points.map((p) => (
            <circle key={p.t} cx={x(p.t)} cy={y(p.n)} r={2.6} fill="var(--accent)" />
          ))}
        </svg>
      </div>
    </div>
  );
}

// ── the dependency graph ────────────────────────────────────────────────────────

const NODE_W = 168;
const NODE_H = 46;
const GAP_X = 64;
const GAP_Y = 18;

/** Layered by longest distance from a source, which is the drawing where every
    edge points right and the critical path reads left to right. Layout is the
    browser's; the path itself is the server's. */
function layout(reqs: PlanRequirement[]) {
  const byKey = new Map(reqs.map((r) => [r.key, r]));
  const depth = new Map<string, number>();
  const walk = (key: string, seen: Set<string>): number => {
    if (depth.has(key)) return depth.get(key)!;
    if (seen.has(key)) return 0; // cycles are refused server-side; belt only
    seen.add(key);
    const r = byKey.get(key);
    const d = r && r.depends_on.length
      ? 1 + Math.max(...r.depends_on.map((k) => walk(k, seen)))
      : 0;
    depth.set(key, d);
    return d;
  };
  reqs.forEach((r) => walk(r.key, new Set()));

  const layers = new Map<number, string[]>();
  for (const r of reqs) {
    const d = depth.get(r.key) ?? 0;
    layers.set(d, [...(layers.get(d) ?? []), r.key]);
  }
  const pos = new Map<string, { x: number; y: number }>();
  let maxRows = 1;
  for (const [d, keys] of layers) {
    keys.sort();
    maxRows = Math.max(maxRows, keys.length);
    keys.forEach((key, i) => {
      pos.set(key, { x: d * (NODE_W + GAP_X), y: i * (NODE_H + GAP_Y) });
    });
  }
  const width = (Math.max(...[...layers.keys()], 0) + 1) * (NODE_W + GAP_X) - GAP_X;
  const height = maxRows * (NODE_H + GAP_Y) - GAP_Y;
  return { pos, width, height };
}

function nodeStroke(status: string): string {
  if (status === "met") return "var(--good)";
  if (status === "cut") return "var(--critical)";
  if (status === "checking") return "var(--warning)";
  if (status === "proposed") return "var(--muted)";
  return "var(--border)";
}

function Graph({ reqs, critical }: { reqs: PlanRequirement[]; critical: string[] }) {
  const { pos, width, height } = useMemo(() => layout(reqs), [reqs]);
  const onPath = new Set(critical);
  const pathEdges = new Set(
    critical.slice(1).map((key, i) => `${critical[i]}→${key}`),
  );

  return (
    <div style={{ overflowX: "auto" }}>
      <svg width={width + 4} height={height + 4} style={{ display: "block", minWidth: 480 }}>
        <defs>
          <marker id="dep" viewBox="0 0 10 10" refX="9" refY="5"
            markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M0 0L10 5L0 10z" fill="var(--muted)" />
          </marker>
          <marker id="dep-hot" viewBox="0 0 10 10" refX="9" refY="5"
            markerWidth="8" markerHeight="8" orient="auto-start-reverse">
            <path d="M0 0L10 5L0 10z" fill="var(--accent)" />
          </marker>
        </defs>
        {reqs.flatMap((r) =>
          r.depends_on.map((dep) => {
            const a = pos.get(dep); const b = pos.get(r.key);
            if (!a || !b) return null;
            const hot = pathEdges.has(`${dep}→${r.key}`);
            const x1 = a.x + NODE_W, y1 = a.y + NODE_H / 2;
            const x2 = b.x - 2, y2 = b.y + NODE_H / 2;
            const mid = (x1 + x2) / 2;
            return (
              <path key={`${dep}-${r.key}`}
                d={`M ${x1} ${y1} C ${mid} ${y1}, ${mid} ${y2}, ${x2} ${y2}`}
                fill="none"
                stroke={hot ? "var(--accent)" : "var(--border)"}
                strokeWidth={hot ? 2.2 : 1.3}
                markerEnd={hot ? "url(#dep-hot)" : "url(#dep)"} />
            );
          }),
        )}
        {reqs.map((r) => {
          const p = pos.get(r.key);
          if (!p) return null;
          const hot = onPath.has(r.key);
          const settled = SETTLED.has(r.status);
          return (
            <g key={r.key} opacity={settled && !hot ? 0.55 : 1}>
              <rect x={p.x} y={p.y} width={NODE_W} height={NODE_H} rx={7}
                fill="var(--surface)"
                stroke={hot ? "var(--accent)" : nodeStroke(r.status)}
                strokeWidth={hot ? 2.2 : 1.3} />
              <text x={p.x + 12} y={p.y + 19} fontSize={12.5} fontWeight={650}
                fill="var(--ink)">
                {r.key}
                <tspan fontWeight={400} fill="var(--ink-2)">
                  {"  "}{r.statement.length > 14 ? r.statement.slice(0, 14) + "…" : r.statement}
                </tspan>
              </text>
              <text x={p.x + 12} y={p.y + 35} fontSize={11} fill={
                r.status === "met" ? "var(--good)"
                : r.status === "cut" ? "var(--critical)"
                : r.failure_load > 0 ? "var(--serious)"
                : "var(--muted)"}>
                {r.status === "in_progress" ? "in progress" : r.status}
                {r.failure_load > 0 ? ` · ${r.failure_load} failures` : ""}
                {r.est_runs != null ? ` · ${r.spent.runs}/${r.est_runs}` : ""}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

// ── the page ────────────────────────────────────────────────────────────────────

interface CycleRow {
  ordinal: number;
  status: string;
  opened_at: string;
  closed_at: string | null;
  narrative: string | null;
}

/** Operate only: the rhythm's record. The narratives are shown whole — they
    are exactly what the CEO reads at the top of each cycle, and the chairman
    should never see less than his own CEO does. */
function CycleHistory({ projectId }: { projectId: string }) {
  const { data } = usePoll<{ current: { ordinal: number } | null; history: CycleRow[] }>(
    () => get(`/observe/projects/${projectId}/cycles`), 30000);
  const [openOrdinal, setOpenOrdinal] = useState<number | null>(null);
  if (!data || data.history.length === 0) return null;
  return (
    <section style={{ marginTop: 22 }}>
      <h2>Past cycles</h2>
      {data.history.map((c) => (
        <div className="card" key={c.ordinal} style={{ padding: "8px 14px", marginBottom: 6 }}>
          <div style={{ display: "flex", gap: 10, alignItems: "baseline", flexWrap: "wrap" }}>
            <b>Cycle {c.ordinal}</b>
            <span className={`badge ${c.status === "completed" ? "good"
              : c.status === "skipped" ? "critical" : "serious"}`}>{c.status}</span>
            <span className="help">
              {c.opened_at.slice(0, 10)} → {c.closed_at?.slice(0, 10) ?? "—"}
            </span>
            <span className="sp" style={{ flex: 1 }} />
            {c.narrative && (
              <button className="small" onClick={() =>
                setOpenOrdinal(openOrdinal === c.ordinal ? null : c.ordinal)}>
                {openOrdinal === c.ordinal ? "hide narrative" : "narrative"}
              </button>
            )}
          </div>
          {openOrdinal === c.ordinal && c.narrative && (
            <p style={{ whiteSpace: "pre-wrap", fontSize: 13, marginTop: 8 }}>
              {c.narrative}
            </p>
          )}
        </div>
      ))}
    </section>
  );
}

export default function Plan({ ctx }: { ctx: ProjectCtx }) {
  const { projectId } = ctx;
  const { data, at, error, reload } = usePoll<PlanView>(
    () => get<PlanView>(`/observe/projects/${projectId}/plan`),
    8000,
  );
  const [view, setView] = useState<"stages" | "graph">("stages");

  if (!data) return <Skeleton rows={5} />;

  if (data.planless) {
    return (
      <div className="card" style={{ maxWidth: 640 }}>
        <h2>No plan, on purpose</h2>
        <p className="help" style={{ marginTop: 6 }}>
          This is an Explore project: a goal, bets and evidence, and your call on
          when it has earned its keep. Requirements — and the schedule they make
          possible — belong to Deliver and Operate projects.
        </p>
      </div>
    );
  }

  const plan = data.plan!;
  const reqs = data.requirements ?? [];
  const critical = data.critical_path?.keys ?? [];
  const sums = plan.arithmetic;
  const stages = [...new Set(reqs.map((r) => r.stage))].sort((a, b) => a - b);
  const commitment = data.commitment ?? null;

  return (
    <div>
      {ctx.dash.mode === "operate" && ctx.dash.cycle && (
        <p className="help" style={{ margin: "0 0 10px" }}>
          Cycle {ctx.dash.cycle.ordinal} — this board is this cycle's plan. Each cycle
          plans fresh; unfinished scope carries forward when the rhythm rolls.
        </p>
      )}
      {commitment?.status === "proposed" && (
        <ProposedCommitment c={commitment} onDone={reload} />
      )}
      {commitment && (commitment.status === "at_risk" || commitment.status === "breached") && (
        <SlippingCommitment c={commitment} onDone={reload} />
      )}
      {/* R-00, translated. When the plan does not hold the reasons are the
          platform's own words — the same list the CEO reads in its refusal. */}
      {!plan.holds ? (
        <div className="strip serious" style={{ marginBottom: 14 }}>
          <div className="grow">
            <b>The plan does not hold yet — the board is closed.</b>
            <div className="sb">
              {plan.outstanding.map((line, i) => <div key={i}>· {line}</div>)}
            </div>
            {plan.planning_spend && plan.planning_spend.runs > 0 && (
              <div className="sb">
                Planning so far: {plan.planning_spend.runs} runs
                {plan.planning_spend.usd > 0 ? ` · $${plan.planning_spend.usd.toFixed(2)}` : ""}
              </div>
            )}
          </div>
        </div>
      ) : (
        <div className="tiles" style={{ marginBottom: 14 }}>
          {commitment && !["proposed", "at_risk", "breached"].includes(commitment.status) && (
            <div className="tile">
              <div className="label">Commitment</div>
              <div className="value">{fmtDay(commitment.deliver_by)}</div>
              <div className="sub">
                {commitment.status === "met"
                  ? `delivered ${commitment.met_at ? fmtDay(commitment.met_at) : ""} — every requirement true`
                  : commitment.projected_done_at
                    ? `projected ${fmtDay(commitment.projected_done_at)} — holds`
                    : "accepted — no projection yet"}
              </div>
            </div>
          )}
          <div className="tile">
            <div className="label">Scope</div>
            <div className="value">{sums.requirements}</div>
            <div className="sub">requirements in the plan</div>
          </div>
          <div className="tile">
            <div className="label">Estimated</div>
            <div className="value">{sums.adjusted_runs}</div>
            <div className="sub">
              runs{sums.multiplier > 1 ? ` (×${sums.multiplier} — this CEO runs over)` : ""}
            </div>
          </div>
          {sums.days_at_the_daily_cap != null && (
            <div className="tile">
              <div className="label">At the daily cap</div>
              <div className="value">{sums.days_at_the_daily_cap}<small> days</small></div>
              <div className="sub">if every run went to plan</div>
            </div>
          )}
          {data.velocity && (
            <div className="tile">
              <div className="label">Pace</div>
              <div className="value">{data.velocity.per_week_3w}<small> /wk</small></div>
              <div className="sub">
                {data.velocity.weeks_left_at_pace != null
                  ? `${data.velocity.remaining} left — ~${data.velocity.weeks_left_at_pace} weeks at this pace`
                  : data.velocity.met_3w === 0
                    ? "nothing met in 3 weeks yet"
                    : `${data.velocity.remaining} requirements left`}
              </div>
            </div>
          )}
          {data.velocity && data.velocity.runs_3w.total > 0 && (
            <div className="tile">
              <div className="label">Waste</div>
              <div className="value">
                {Math.round(data.velocity.runs_3w.failure_share * 100)}<small>%</small>
              </div>
              <div className="sub">
                {data.velocity.runs_3w.dead} of {data.velocity.runs_3w.total} runs died
                {data.velocity.worst_failure
                  ? ` — ${data.velocity.worst_failure.key} worst at ${data.velocity.worst_failure.failure_load}`
                  : ""}
              </div>
            </div>
          )}
          {data.velocity && data.velocity.runs_3w.total > 0 &&
           data.velocity.runs_3w.inbox + data.velocity.runs_3w.noop > 0 && (
            /* What the project spent talking to itself. Its own broadcasts
               generate this traffic, so it is the one cost that can be priced
               before it is paid — and until it was counted, 57% of every run
               one project ever spent was an agent reading a message. */
            <div className="tile">
              <div className="label">Overhead</div>
              <div className="value">
                {Math.round(
                  ((data.velocity.runs_3w.inbox + data.velocity.runs_3w.noop) /
                    data.velocity.runs_3w.total) * 100,
                )}
                <small>%</small>
              </div>
              <div className="sub">
                {data.velocity.runs_3w.inbox} reading the inbox,{" "}
                {data.velocity.runs_3w.noop} that changed nothing — of{" "}
                {data.velocity.runs_3w.total} runs
              </div>
            </div>
          )}
          {sums.budget.known ? (
            <div className="tile">
              <div className="label">Affordable</div>
              <div className="value">{sums.budget.runs}</div>
              <div className="sub">
                runs — ${sums.budget.usd_remaining} at ${sums.budget.usd_per_run}/run
              </div>
            </div>
          ) : (
            <div className="tile">
              <div className="label">Budget check</div>
              <div className="value">—</div>
              <div className="sub">{sums.budget.why}</div>
            </div>
          )}
        </div>
      )}

      {data.standing && data.standing.length > 0 && (
        /* The operating record. A standing obligation is owed again every
           cycle, so "met" on its own says nothing — the pattern is the fact,
           and before this it lived in prose where nothing could check it. */
        <div className="card" style={{ marginBottom: 14 }}>
          <div className="label" style={{ marginBottom: 8 }}>Owed every cycle</div>
          {data.standing.map((r) => {
            const missed = r.judged > 0 && r.met < r.judged;
            return (
              <div className="wrow" key={r.key}>
                <span className={`badge ${missed ? "warn" : "good"}`}>
                  {r.met}/{r.judged}
                </span>
                <div className="grow">
                  <b className="mono">{r.key}</b> {r.statement}
                  <div className="help">
                    {r.judged === 0
                      ? "no cycle has closed on it yet"
                      : missed
                        ? `missed ${r.judged - r.met} of the last ${r.judged} cycles`
                        : `met in all ${r.judged} cycles judged so far`}
                    {" — "}
                    {r.status_now === "met" ? "done for this cycle" : "open this cycle"}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {commitment && commitment.status !== "proposed" && data.burnup && (
        <Burnup c={commitment} burnup={data.burnup} />
      )}

      <div style={{ display: "flex", gap: 10, alignItems: "center", marginBottom: 12 }}>
        <div className="seg" style={{ display: "inline-flex", border: "1px solid var(--border)",
          borderRadius: 6, overflow: "hidden" }}>
          {(["stages", "graph"] as const).map((v) => (
            <button key={v} onClick={() => setView(v)} style={{
              font: "inherit", fontSize: 13, padding: "5px 14px", border: "none",
              cursor: "pointer",
              background: view === v ? "var(--surface-2)" : "transparent",
              color: view === v ? "var(--ink)" : "var(--muted)",
            }}>
              {v === "stages" ? "Stages" : "Dependencies"}
            </button>
          ))}
        </div>
        {critical.length > 1 && (
          <span className="help">
            critical path {critical.join(" → ")} · ~{data.critical_path!.adjusted_runs} runs
          </span>
        )}
        <span className="sp" style={{ flex: 1 }} />
        <AsOf at={at} error={error} />
      </div>

      {reqs.length === 0 ? (
        <div className="card">
          <p className="help">
            Nothing proposed yet. The CEO's first task is the plan: it proposes
            requirements, you accept them here, and the moment the plan holds the
            board opens by itself.
          </p>
        </div>
      ) : view === "stages" ? (
        stages.map((stage) => (
          <section key={stage} style={{ marginBottom: 18 }}>
            <h2 style={{ display: "flex", gap: 10, alignItems: "baseline" }}>
              Stage {stage}
              {stage === data.open_stage && <span className="badge accent">open</span>}
              {stage > (data.open_stage ?? 1) && (
                <span className="help">work here is refused until stage {data.open_stage} settles</span>
              )}
            </h2>
            <div style={{ marginTop: 8 }}>
              {reqs.filter((r) => r.stage === stage).map((r) => (
                <RequirementCard key={r.id} r={r}
                  onCritical={critical.includes(r.key)} onDone={reload} />
              ))}
            </div>
          </section>
        ))
      ) : (
        <div className="card" style={{ padding: 18 }}>
          <Graph reqs={reqs} critical={critical} />
          <p className="help" style={{ marginTop: 10 }}>
            Arrows point from what must finish to what it unblocks. The accented
            chain is the critical path — the longest run of unsettled work, at
            this CEO's measured estimate ratio. Anything on it that slips moves
            the whole plan.
          </p>
        </div>
      )}

      {ctx.dash.mode === "operate" && <CycleHistory projectId={projectId} />}
    </div>
  );
}
