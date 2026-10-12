import { useMemo, useState } from "react";
import {
  get,
  post,
  PlanBurnup,
  PlanCommitment,
  PlanRequirement,
} from "../../api";
import { usePoll } from "../../hooks";
import { AsOf, ConfirmModal, ExportCsv, NoteAction, Skeleton, clip, downloadCsv } from "../../ui";
import { Badge, ChartHead, EmptyState, plainStatus } from "../../ui/kit";
import type { ProjectCtx } from "../Project";

/** The visual plan the chairman asked for — twice — before it was twice
    deferred and dropped. Two views over one dataset, so they cannot disagree:
    the stage board answers "when will I have it", the dependency graph answers
    "what is blocking what". The critical path arrives computed from the
    server, from the same arithmetic the refusals quote. */

function statusBadge(status: string) {
  const cls =
    status === "met" ? "good"
    : status === "cut" ? "critical"
    : status === "checking" ? "warn"
    : status === "in_progress" ? "accent"
    : status === "proposed" ? "serious"
    : "";
  return <span className={`badge ${cls}`}>{plainStatus(status)}</span>;
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
          label="Drop it"
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
        label="Drop it"
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
  if (test.type === "metric") {
    const m = r.measurement;
    const aim = `${test.direction === "decreasing" ? "below" : "at or above"} ${m?.target ?? "?"}`;
    if (!m || m.value === null) return `measured — no reading yet; target ${aim}`;
    return `${m.value} — target ${aim}`;
  }
  return "structural — the platform checks the plan itself";
}

/** A measured requirement wears its verdict, because the number IS the status.
    "in progress" on a rank target says nothing a chairman can act on; "losing,
    6,698,328 against 500,000" says the whole thing. */
function MetricVerdict({ m }: { m: NonNullable<PlanRequirement["measurement"]> }) {
  const tone: Record<string, string> = {
    losing: "var(--bad, var(--critical))",
    gaining: "var(--good, var(--good))",
    met: "var(--good, var(--good))",
  };
  return (
    <div style={{ marginTop: 6, fontSize: 12 }}>
      <span style={{ color: tone[m.verdict] ?? "var(--ink-2)", fontWeight: 600 }}>
        {m.verdict}
      </span>
      {m.trend.length > 1 && (
        <span className="help"> · {m.trend.slice(-4).join(" → ")}</span>
      )}
      {m.error && <span className="help"> · last read failed: {m.error}</span>}
      {m.measured_at && !m.error && (
        <span className="help"> · read {new Date(m.measured_at).toLocaleString()}</span>
      )}
    </div>
  );
}

/** One requirement, in full: the statement is the headline, the test is one
    click away, and the numbers are the same ones the refusals quote. */
function RequirementCard({ r, onCritical, onDone, names }: {
  r: PlanRequirement; onCritical: boolean; onDone: () => void;
  /** Key → statement, so a dependency can be named rather than referenced. */
  names: Map<string, string>;
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
        <span className="chip" title={`Requirement ${r.key}`}
          style={{ color: "var(--muted)", fontWeight: 500 }}>{r.key}</span>
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
        <span
          className="chip"
          title={
            r.est_runs != null
              ? `${r.spent.runs} of the ${r.est_runs} jobs estimated for this`
              : `${r.spent.runs} jobs so far; nobody estimated this one`
          }
          style={over ? { color: "var(--serious)" } : undefined}
        >
          {r.spent.runs}{r.est_runs != null ? ` of ${r.est_runs}` : ""} jobs
        </span>
        {r.spent.usd > 0 && <span className="chip">${r.spent.usd.toFixed(2)}</span>}
        <Levers r={r} onDone={onDone} />
      </div>
      {r.depends_on.length > 0 && !SETTLED.has(r.status) && (
        <div style={{ marginTop: 4, fontSize: 12, color: "var(--muted)" }}>
          Waits for{" "}
          {r.depends_on
            .map((k, i) => (
              <span key={k} title={`Requirement ${k}`}>
                {i > 0 ? ", " : ""}
                {clip(names.get(k) ?? k, 60)}
              </span>
            ))}
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
          {r.measurement && <MetricVerdict m={r.measurement} />}
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
                    <td><span className="badge">{plainStatus(t.status)}</span></td>
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
/** Is the work going to be finished by the date you signed?
 *
 * The chart that was here drew two lines and told you nothing you could read
 * off it: no y-axis, so a point had no value; no gridlines, so no point had a
 * neighbour; and three labels placed at their own x with no regard for each
 * other or for the edge of the picture — which is how a shipped screenshot
 * came to say "projected Oc", clipped mid-word at the frame.
 *
 * It states its question, its window and its unit; every mark is inside the
 * frame; and where two dates land close enough to collide they are drawn as
 * one label rather than two on top of each other. */

const CW = 760, CH = 268, CL = 46, CR = 18, CT = 20, CB = 56;

/** The value axis stops short of the frame. When "all N settled" WAS the top
    of the picture, a project that had settled everything drew its last and
    longest run — the days between the final requirement and today — along the
    border, where a 2.5px accent line is indistinguishable from the frame
    itself. The one mark encoding elapsed time was invisible. */
const YTOP = CT + 18;

/** Padding is a fraction of the window drawn, not of the promise. The window
    used to start exactly at the first mark, so the first point was painted on
    the left wall by construction — which is why every small project looked
    like a single vertical line at the edge — and to end 8% of the whole
    promise past the last, so a ninety-day commitment squeezed three weeks of
    real work into the first inch of the frame. */
const PAD_LEFT = 0.07, PAD_RIGHT = 0.11;

/** A week is the narrowest honest window: this chart's unit is requirements
    per week, and an axis of hours stretched over 700px is noise at full width. */
const MIN_SPAN_MS = 7 * 24 * 60 * 60 * 1000;

/** A projection years past the promise is honest arithmetic and a useless
    picture: one dashed line to 2031 flattens everything that happened into a
    smudge at the origin. Past this the line runs to the wall and says so. */
const HORIZON = 1.6;

/** Wide enough for the longest bottom label plus air. Three dates share one
    ruler, so collision is a question about every pair — not just about the
    promise and the projection, which is all the old test asked. */
const LABEL_W = 96;

/** Below any of these there is no shape to draw, so the card states the
    numbers instead. A line through two points is the number above it said
    twice, with a slope nobody can check; a two-requirement scale makes every
    step half the plot height; and a set of marks written inside one morning
    is a moment, not a window. */
const MIN_POINTS = 3, MIN_SCOPE = 3, MIN_SPREAD_MS = 24 * 60 * 60 * 1000;

/** Keeps a label inside the frame and points it away from the wall it is
    nearest, which is the whole of the fix for the clipped axis label. */
function labelAt(px: number): { x: number; anchor: "start" | "middle" | "end" } {
  if (px < CL + 34) return { x: CL, anchor: "start" };
  if (px > CW - CR - 34) return { x: CW - CR, anchor: "end" };
  return { x: px, anchor: "middle" };
}

/** "in 12 days" / "11 days ago". A date a reader has to do arithmetic on to
    place against today is a date that has not finished being written. */
function dayGap(t: number, now: number): string {
  const days = Math.round((t - now) / 86400000);
  if (days === 0) return "today";
  return days > 0
    ? `in ${days} day${days === 1 ? "" : "s"}`
    : `${-days} day${days === -1 ? "" : "s"} ago`;
}

interface Mark {
  at: number; top: string; bottom: string;
  colour: string; weight: number; dashed: boolean;
}

/** Two dates a label's width apart on screen are one label, whatever they are
    a fortnight apart in the data. Folding over all three — today, the promise,
    the projection — rather than testing the promise against the projection
    alone is what stops "today" and "now expected" being painted on top of each
    other every time the scope settles and the projection becomes today. */
function mergeMarks(marks: Mark[], x: (t: number) => number): Mark[] {
  const out: Mark[] = [];
  for (const m of [...marks].sort((a, b) => a.at - b.at)) {
    const prev = out[out.length - 1];
    const collides = prev !== undefined && (
      Math.abs(x(m.at) - x(prev.at)) < LABEL_W ||
      fmtDay(new Date(prev.at).toISOString()) === fmtDay(new Date(m.at).toISOString())
    );
    if (!collides) { out.push({ ...m }); continue; }
    // The rule stands at the later of the two: of "promised the 20th" and
    // "expected the 21st", the one a reader must not be told is early is the
    // later one.
    //
    // But the label keeps BOTH dates. Collapsing "promised Oct 2" and
    // "expected Oct 4" into a single "Oct 4 — you promised · now expected"
    // moves the promise three days to the right and puts the chart at odds
    // with the tile above it, which still reads Oct 2. Two dates close enough
    // to share a label are not the same date.
    const day = fmtDay(new Date(m.at).toISOString());
    if (!prev.top.split(" → ").includes(day)) prev.top = `${prev.top} → ${day}`;
    prev.at = Math.max(prev.at, m.at);
    prev.bottom = `${prev.bottom} → ${m.bottom}`;
    if (m.weight >= prev.weight) prev.colour = m.colour;
    prev.weight = Math.max(prev.weight, m.weight);
    prev.dashed = prev.dashed || m.dashed;
  }
  return out;
}

function yTicks(scope: number): number[] {
  if (scope <= 6) return Array.from({ length: scope + 1 }, (_, i) => i);
  const step = Math.ceil(scope / 4);
  const out: number[] = [];
  for (let v = 0; v <= scope; v += step) out.push(v);
  if (out[out.length - 1] !== scope) out.push(scope);
  return out;
}

interface BurnupFacts {
  scope: number;
  settled: number;
  open: number | null;
  points: { t: number; n: number }[];
  deliverBy: number;
  projected: number | null;
  late: boolean;
  daysLate: number | null;
  drawable: boolean;
  /** Empty when drawable. Why not, in the words the card prints. */
  why: string;
}

/** The numbers both renderings read, computed once so the picture and the
    tiles can never say different things about the same promise. */
function burnupFacts(c: PlanCommitment, burnup: PlanBurnup, open: number | null): BurnupFacts {
  const scope = Math.max(burnup.scope_total, 1);
  // The server builds the line from the same set it counts the ceiling from,
  // so this clamp is a belt: if that ever drifts again the chart flattens at
  // its own top instead of walking out through the frame.
  const points = burnup.met_points.map((p) => ({
    t: new Date(p.at).getTime(), n: Math.min(p.count, scope),
  }));
  const deliverBy = new Date(c.deliver_by).getTime();
  const projected = c.projected_done_at ? new Date(c.projected_done_at).getTime() : null;
  const late = projected !== null && projected > deliverBy;
  const spread = points.length ? points[points.length - 1].t - points[0].t : 0;

  const why =
    points.length === 0 ? "nothing has settled yet"
    : points.length < MIN_POINTS
      ? `only ${points.length} thing${points.length === 1 ? " has" : "s have"} settled so far`
    : scope < MIN_SCOPE ? `a plan of ${scope} has no shape to show`
    : spread < MIN_SPREAD_MS ? "everything so far settled inside one day"
    : "";

  return {
    scope, settled: Math.min(burnup.settled, scope), open, points, deliverBy, projected, late,
    daysLate: late ? Math.ceil((projected! - deliverBy) / 86400000) : null,
    drawable: why === "", why,
  };
}

/** Will the work be finished by the date you signed? The card either draws the
    shape or states the numbers — never a line through one or two points, which
    is the number said twice and a slope that cannot be read. */
function Burnup({ c, burnup, open }: {
  c: PlanCommitment; burnup: PlanBurnup; open: number | null;
}) {
  const f = burnupFacts(c, burnup, open);
  return f.drawable ? <BurnupChart c={c} f={f} /> : <BurnupSummary c={c} f={f} />;
}

function BurnupSummary({ c, f }: { c: PlanCommitment; f: BurnupFacts }) {
  const now = Date.now();
  return (
    <div className="card" style={{ marginBottom: "var(--s4)" }}>
      <ChartHead
        question="Will the work be finished by the date you signed?"
        meta={`Requirements settled, against all ${f.scope} in the plan · as at ${fmtDay(new Date(now).toISOString())}`}
      />
      <div className="tiles" style={{ marginTop: 0 }}>
        <div className="tile">
          <div className="label">Settled</div>
          <div className="value">{f.settled} of {f.scope}</div>
          <div className="sub">
            {f.open === null ? "requirements proven true"
              : f.open === 0 ? "nothing still open" : `${f.open} still open`}
          </div>
        </div>
        <div className="tile">
          <div className="label">You promised</div>
          <div className="value">{fmtDay(c.deliver_by)}</div>
          <div className="sub">{dayGap(f.deliverBy, now)}</div>
        </div>
        {f.projected !== null && (
          <div className={`tile${c.status === "breached" ? " alert" : ""}`}>
            <div className="label">{f.late ? "Now expected" : "On course for"}</div>
            <div className="value">{fmtDay(new Date(f.projected).toISOString())}</div>
            <div className="sub">
              <Badge tone={f.late ? "serious" : "good"}>
                {f.late
                  ? `${f.daysLate} day${f.daysLate === 1 ? "" : "s"} past the promise`
                  : "inside the promise"}
              </Badge>
            </div>
          </div>
        )}
      </div>

      {f.points.length === 0 ? (
        <EmptyState
          icon="target"
          title="Nothing has settled yet"
          foot={c.projection_basis
            ? `Any date above is arithmetic, not a promise anybody made — ${c.projection_basis}`
            : undefined}
        >
          The promise stands and the work is under way. The moment requirements
          start being proven true there is a line worth drawing here.
        </EmptyState>
      ) : (
        <p className="help" style={{ margin: "var(--s3) 0 0" }}>
          {/* Said rather than drawn. The chart that used to be here drew a line
              through one point, which is how it came to be a vertical bar at
              the left edge and nothing else. */}
          Too little has happened to draw a line worth reading — {f.why}.
          {c.projection_basis
            ? ` The date is arithmetic, not a promise anybody made — ${c.projection_basis}.`
            : ""}
        </p>
      )}
    </div>
  );
}

function BurnupChart({ c, f }: { c: PlanCommitment; f: BurnupFacts }) {
  const now = Date.now();
  const { scope, points, settled, deliverBy, projected, late } = f;
  const projColour = late ? "var(--serious)" : "var(--good)";

  // The window is the data's, padded on both sides, floored to a week, and
  // capped so a runaway projection cannot squeeze the work into the origin.
  const opened = c.accepted_at ? new Date(c.accepted_at).getTime() : points[0].t;
  const lo0 = Math.min(opened, points[0].t, now);
  const cap = deliverBy + Math.max(deliverBy - lo0, MIN_SPAN_MS) * HORIZON;
  const offFrame = projected !== null && projected > cap;
  const hi0 = Math.max(now, deliverBy, offFrame ? cap : (projected ?? 0));
  const span = Math.max(hi0 - lo0, MIN_SPAN_MS);
  const lo = lo0 - span * PAD_LEFT;
  const hi = lo0 + span * (1 + PAD_RIGHT);

  const x = (t: number) => CL + ((t - lo) / (hi - lo)) * (CW - CL - CR);
  const y = (n: number) => CH - CB - (n / scope) * (CH - CB - YTOP);

  let line = `M ${x(lo0)} ${y(0)}`;
  let lastN = 0;
  for (const p of points) {
    line += ` L ${x(p.t)} ${y(lastN)} L ${x(p.t)} ${y(p.n)}`;
    lastN = p.n;
  }
  line += ` L ${x(Math.min(now, hi))} ${y(lastN)}`;

  const marks = mergeMarks([
    { at: now, top: fmtDay(new Date(now).toISOString()), bottom: "today",
      colour: "var(--muted)", weight: 1, dashed: false },
    { at: deliverBy, top: fmtDay(c.deliver_by), bottom: "you promised",
      colour: c.status === "breached" ? "var(--critical)" : "var(--ink-2)",
      weight: 1.5, dashed: false },
    ...(projected === null ? [] : [{
      at: offFrame ? cap : projected,
      top: fmtDay(new Date(projected).toISOString()),
      bottom: offFrame ? "expected beyond this window" : late ? "now expected" : "on course for",
      colour: projColour, weight: 1.5, dashed: true,
    }]),
  ], x);

  return (
    <div className="card" style={{ marginBottom: "var(--s4)" }}>
      <ChartHead
        question="Will the work be finished by the date you signed?"
        meta={`Requirements settled, against all ${scope} in the plan · ${fmtDay(new Date(lo0).toISOString())} to ${fmtDay(new Date(hi0).toISOString())}`}
        legend={
          <>
            <span><i />Settled so far</span>
            {projected !== null && lastN < scope && (
              <span><i className="dash" style={{ borderTopColor: projColour }} />If the pace holds</span>
            )}
          </>
        }
      />
      <svg
        viewBox={`0 0 ${CW} ${CH}`}
        style={{ display: "block", width: "100%" }}
        role="img"
        aria-label={`${settled} of ${scope} requirements are settled. You promised ${fmtDay(c.deliver_by)}${projected !== null ? `; the platform projects ${fmtDay(new Date(projected).toISOString())}` : ""}.`}
      >
        {yTicks(scope).map((v) => (
          <g key={v}>
            <line x1={CL} y1={y(v)} x2={CW - CR} y2={y(v)}
              stroke={v === 0 || v === scope ? "var(--border-strong)" : "var(--border)"}
              strokeWidth={1} />
            <text x={CL - 10} y={y(v) + 4} fontSize={12} textAnchor="end" fill="var(--muted)">{v}</text>
          </g>
        ))}
        {/* Names the top of the axis. "all N settled" beside a forecast saying
            work remains read as a claim that everything was done, so it only
            says that once it is true. */}
        <text x={CL} y={y(scope) - 8} fontSize={12} fill="var(--muted)">
          {lastN >= scope ? `all ${scope} settled` : `all ${scope} in the plan`}
        </text>

        {marks.map((m) => {
          const px = Math.max(CL, Math.min(x(m.at), CW - CR));
          const lbl = labelAt(px);
          return (
            <g key={m.bottom}>
              <line x1={px} y1={CT} x2={px} y2={CH - CB} stroke={m.colour}
                strokeWidth={m.weight} strokeDasharray={m.dashed ? "5 4" : undefined} />
              <text x={lbl.x} y={CH - CB + 20} fontSize={12} fontWeight={600}
                textAnchor={lbl.anchor} fill={m.colour}>{m.top}</text>
              <text x={lbl.x} y={CH - CB + 36} fontSize={12}
                textAnchor={lbl.anchor} fill="var(--muted)">{m.bottom}</text>
            </g>
          );
        })}

        {projected !== null && lastN < scope && (
          <line x1={x(Math.min(now, hi))} y1={y(lastN)}
            x2={Math.min(x(offFrame ? cap : projected), CW - CR)} y2={y(scope)}
            stroke={projColour} strokeDasharray="6 5" strokeWidth={2} strokeLinecap="round" />
        )}
        <path d={line} fill="none" stroke="var(--accent)" strokeWidth={2.5}
          strokeLinecap="round" strokeLinejoin="round" />
        {points.map((p) => (
          <circle key={p.t} cx={x(p.t)} cy={y(p.n)} r={4.5}
            fill="var(--accent)" stroke="var(--surface)" strokeWidth={2} />
        ))}
      </svg>
      {c.projection_basis && (
        <p className="help" style={{ margin: 0, borderTop: "1px solid var(--border)", paddingTop: "var(--s3)" }}>
          The dashed line is arithmetic, not a promise anybody made — {c.projection_basis}
        </p>
      )}
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
  // The frame's read: the delivery tile, the sidebar's count and this page
  // were drawn from the same answer, so they cannot disagree about the date.
  const { projectId, plan: data, asOf: at, error, reload } = ctx;
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
  const reqNames = new Map(reqs.map((r) => [r.key, r.statement]));
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
            <div className="label">Still to do</div>
            <div className="value">{sums.requirements}</div>
            {/* `plan_arithmetic.requirements` counts the LIVE ones — settled
                and cut are out, which is what makes the runs beside it mean
                "work left". Labelling it "requirements in the plan" had it
                disagreeing with the card below, which counts the whole plan. */}
            <div className="sub">
              requirement{sums.requirements === 1 ? "" : "s"} still open
              {data.burnup && data.burnup.scope_total > 0
                ? ` of ${data.burnup.scope_total} in the plan`
                : ""}
            </div>
          </div>
          <div className="tile">
            <div className="label">Work it should take</div>
            <div className="value">{sums.adjusted_runs}</div>
            <div className="sub">
              jobs{sums.multiplier > 1 ? ` — padded ×${sums.multiplier}, because this CEO has been running over` : ""}
            </div>
          </div>
          {sums.days_at_the_daily_cap != null && (
            <div className="tile">
              <div className="label">If nothing went wrong</div>
              <div className="value">{sums.days_at_the_daily_cap}<small> days</small></div>
              <div className="sub">days, at the most work you allow in one day</div>
            </div>
          )}
          {data.velocity && (
            <div className="tile">
              <div className="label">Requirements settled</div>
              {/* A rate, or a dash. Printing `0 a week` for a project that has
                  not been alive a week says its team has achieved nothing,
                  which is a different claim from "there is nothing to measure
                  yet" and one the arithmetic cannot support. */}
              <div className="value">
                {data.velocity.pace_known
                  ? <>{data.velocity.per_week_3w}<small> a week</small></>
                  : "—"}
              </div>
              <div className="sub">
                {data.velocity.pace_known
                  ? (data.velocity.weeks_left_at_pace != null
                      ? `${data.velocity.remaining} still open — about ${data.velocity.weeks_left_at_pace} weeks at this rate`
                      : `${data.velocity.remaining} still open`)
                  : `${data.velocity.remaining} still open — too little has settled to call this a pace`}
              </div>
            </div>
          )}
          {data.velocity && data.velocity.runs_3w.total > 0 && (
            <div className="tile">
              <div className="label">Spend that bought nothing</div>
              <div className="value">
                {Math.round(data.velocity.runs_3w.failure_share * 100)}<small>%</small>
              </div>
              <div className="sub">
                {data.velocity.runs_3w.dead} of {data.velocity.runs_3w.total} jobs ended with
                nothing to show
                {data.velocity.worst_failure
                  ? ` — worst on "${clip(data.velocity.worst_failure.statement, 44)}"`
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
              <div className="label">What the rest of the budget buys</div>
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
          {/* Said out loud because a burn-up used to sit below this. Work that
              comes round again has no line to burn up; what matters is which
              obligation slipped, and in which turn. */}
          <p className="help" style={{ margin: "0 0 10px" }}>
            This work comes round again every cycle, so there is no line to burn
            up — what counts is which one slipped, and when.
          </p>
          {data.standing.map((r) => {
            const missed = r.judged > 0 && r.met < r.judged;
            return (
              <div className="wrow" key={r.key}>
                <span className={`badge ${missed ? "warn" : "good"}`}>
                  {r.met}/{r.judged}
                </span>
                <div className="grow">
                  <b title={`Requirement ${r.key}`}>{clip(r.statement, 90)}</b>
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

      {/* Deliver only. Work that comes round again does not burn up — the
          shape that carries the information for an Operate project is the
          record above, and drawing both put a chart that cannot be right
          underneath the one that is. */}
      {data.mode === "deliver" && commitment && commitment.status !== "proposed"
        && data.burnup && (
        <Burnup c={commitment} burnup={data.burnup}
          open={data.velocity?.remaining ?? null} />
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
            The longest chain runs {critical.join(" → ")} — about{" "}
            {data.critical_path!.adjusted_runs} jobs, and nothing shortens the project until
            it does.
          </span>
        )}
        <span className="sp" style={{ flex: 1 }} />
        {reqs.length > 0 && (
          <ExportCsv
            onClick={() =>
              downloadCsv(
                "the-plan.csv",
                ["Order", "What has to be true", "State", "Stage", "Jobs used",
                 "Jobs estimated", "Spent (USD)", "Failed checks", "Waits for", "Settled"],
                reqs.map((r) => [
                  r.key,
                  r.statement,
                  plainStatus(r.status),
                  r.stage,
                  r.spent.runs,
                  r.est_runs ?? "",
                  r.spent.usd.toFixed(2),
                  r.failure_load,
                  r.depends_on.join(" "),
                  r.met_at ?? "",
                ]),
              )
            }
          />
        )}
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
                <RequirementCard key={r.id} r={r} names={reqNames}
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
