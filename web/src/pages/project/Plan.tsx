import { useMemo, useState, type ReactNode } from "react";
import {
  get,
  post,
  PlanBurnup,
  PlanCommitment,
  PlanRequirement,
  PlanView,
  requirementName,
} from "../../api";
import { usePoll } from "../../hooks";
import { AsOf, ConfirmModal, ExportCsv, NoteAction, Skeleton, clip, downloadCsv } from "../../ui";
import { ChartHead, plainStatus } from "../../ui/kit";
import { chairmanDecides } from "../../ui/project";
import type { ProjectCtx } from "../Project";
import "./plan.css";

/** The visual plan the chairman asked for — twice — before it was twice
    deferred and dropped. Two views over one dataset, so they cannot disagree:
    the stage board answers "when will I have it", the dependency graph answers
    "what is blocking what". The critical path arrives computed from the
    server, from the same arithmetic the refusals quote.

    The top of it is one sentence, one chart and one quiet line. It used to be
    eight tiles, most reading 0 or a dash with a sentence under each explaining
    the zero, and then a second card that printed the promised date three
    times. Those numbers are all still here, under "More numbers" at the foot. */

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
      title={`Not done: ${clip(requirementName(r), 60)}`}
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
    losing: "var(--critical)",
    gaining: "var(--good)",
    met: "var(--good)",
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
    <div className={`card req-card${SETTLED.has(r.status) ? " settled" : ""}`} style={{
      padding: "10px 14px", marginBottom: 8,
      borderLeft: onCritical ? "3px solid var(--accent)" : undefined,
    }}>
      <div style={{ display: "flex", gap: 10, alignItems: "baseline", flexWrap: "wrap" }}>
        <span className="chip" title={`Requirement ${r.key}`}
          style={{ color: "var(--muted)", fontWeight: 500 }}>{r.key}</span>
        <button className="linklike" style={{ flex: 1, minWidth: 220, textAlign: "left",
          background: "none", border: "none", padding: 0, font: "inherit",
          color: SETTLED.has(r.status) ? "var(--ink-2)" : "var(--ink)", cursor: "pointer" }}
          onClick={() => setOpen(!open)}>
          {requirementName(r)}
          {r.meta?.description && (
            <span className="help" style={{ display: "block" }}>{r.meta.description}</span>
          )}
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
          {requirementName(r) !== r.statement && (
            <div style={{ marginBottom: 6 }}>
              <span className="help">what has to be true: </span>{r.statement}
            </div>
          )}
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

const DAY_MS = 24 * 60 * 60 * 1000;
const WEEK_MS = 7 * DAY_MS;

/** Mirrors `velocity.MIN_MET_FOR_PACE`. Below this many settled in a window
    the server will not call what it measured a pace, and neither does this
    page. */
const MIN_MET_FOR_PACE = 2;

/** The requirement states the server's stage gate counts as open work
    (`policy.OPEN_REQUIREMENT_STATUSES`). Proposed is not in force yet. */
const OPEN = new Set(["pending", "in_progress", "checking"]);

function plural(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`;
}

/** Two dates as one span, "Nov 17 – 19", or one date when they share a day. */
function fmtSpan(lo: number, hi: number): string {
  const a = fmtDay(new Date(lo).toISOString());
  const b = fmtDay(new Date(hi).toISOString());
  if (a === b) return b;
  return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" })
    .formatRange(new Date(lo), new Date(hi));
}

function spansDays(lo: number, hi: number): boolean {
  return fmtDay(new Date(lo).toISOString()) !== fmtDay(new Date(hi).toISOString());
}

type Velocity = NonNullable<PlanView["velocity"]>;

interface Pace { weeks: 3 | 12; perWeek: number; met: number }

/** The windows `commitment_projection` divides by, in the order it tries
    them: the trailing three weeks, then the trailing twelve. A window with
    too few settled in it, or too short to hold a week, is not a pace. */
function measuredPaces(v: Velocity | undefined): Pace[] {
  if (!v) return [];
  const out: Pace[] = [];
  if (v.per_week_3w > 0 && v.met_3w >= MIN_MET_FOR_PACE) {
    out.push({ weeks: 3, perWeek: v.per_week_3w, met: v.met_3w });
  }
  if (v.per_week_12w > 0 && v.met_12w >= MIN_MET_FOR_PACE) {
    out.push({ weeks: 12, perWeek: v.per_week_12w, met: v.met_12w });
  }
  return out;
}

/** "the pace of the last 12 days (2 settled)". A young project's three-week
    window is only as long as the project, and says so. */
function paceWords(p: Pace, v: Velocity): string {
  if (p.weeks === 12) return `the pace of the last 12 weeks (${p.met} settled)`;
  const days = v.measured_over_days;
  const window = days >= 20.5 ? "3 weeks" : plural(Math.max(1, Math.round(days)), "day");
  return `the pace of the last ${window} (${p.met} settled)`;
}

/** Why there is no date, in a sentence. Only reached when neither window
    holds a pace, so each branch is one of the server's own refusals to
    divide. */
function whyNoPace(v: Velocity | undefined): string {
  if (!v) return "There is no pace to measure yet.";
  const young = v.measured_over_days < 20.5;
  const met = Math.max(v.met_3w, v.met_12w);
  if (met === 0) {
    return young ? "Nothing has settled yet." : "Nothing has settled in the last 12 weeks.";
  }
  if (met < MIN_MET_FOR_PACE) {
    return `Only ${met} has settled ${young ? "so far" : "in the last 12 weeks"}; a pace needs at least ${MIN_MET_FOR_PACE}.`;
  }
  return `${plural(met, "requirement")} settled in ${plural(Math.max(1, Math.round(v.measured_over_days)), "day")}; a pace needs at least a week.`;
}

/** Where the open work lands, as a span. One end is the server's own date:
    the pace it chose, the one the janitor stamps and the refusals quote. The
    other end is the same division by the other window's pace, when that
    window holds one too, so the width of the span is how far the two measured
    paces disagree. Nothing goes into it that the server did not measure.

    Null whenever the server's date did not come from a measured pace. Its
    fallback is the plan's own arithmetic at the daily cap, a best case rather
    than a forecast, and this page does not print a best case as a date. */
interface Projection {
  lo: number;
  hi: number;
  /** What the span was divided from, as a noun phrase. */
  from: string;
}

function projectionOf(c: PlanCommitment, v: Velocity | undefined, now: number): Projection | null {
  const paces = measuredPaces(v);
  if (!v || v.remaining === 0 || !c.projected_done_at || paces.length === 0) return null;
  const own = new Date(c.projected_done_at).getTime();
  const [first, second] = paces;
  const other = second && second.perWeek !== first.perWeek
    ? now + (v.remaining / second.perWeek) * WEEK_MS
    : own;
  const lo = Math.min(own, other);
  const hi = Math.max(own, other);
  const from = second && spansDays(lo, hi)
    ? `the pace of the last 3 weeks and of the last 12 (${first.met} and ${second.met} settled)`
    : paceWords(first, v);
  return { lo, hi, from };
}

/** What the page counts, once, so the sentence, the quiet line under the
    chart and the stage labels are all read off the same tally. */
interface Counts {
  /** The burn-up's own pair when it has one, so this line and the chart agree. */
  settled: number;
  scope: number;
  /** Accepted and not yet settled — the server's open statuses. */
  open: number;
  /** Of those, the ones a number settles rather than work. */
  openMeasured: number;
  inWork: number;
  notStarted: number;
  /** Levers only you can pull: accept or cut, a verdict, or a proposed promise. */
  waiting: number;
  /** Everything past proposed, settled or not. */
  accepted: number;
}

/** Which decisions the dial left with the chairman. On an autonomous
    project the CEO accepts its own scope and judges its own observables, so
    neither is waiting on him. */
interface Yours { scope: boolean; verdict: boolean }

function waitsOnYou(r: PlanRequirement, yours: Yours): boolean {
  return (r.status === "proposed" && yours.scope)
    || (r.status === "checking" && r.test_kind === "observable" && yours.verdict);
}

function countsOf(
  reqs: PlanRequirement[], burnup: PlanBurnup | null | undefined,
  /** A proposed commitment is a decision of yours too; "nothing waiting on
      you" under its Accept button would be the page contradicting itself. */
  promiseProposed: boolean,
  yours: Yours,
): Counts {
  const live = reqs.filter((r) => r.status !== "proposed" && r.status !== "cut");
  const useBurnup = !!burnup && burnup.scope_total > 0;
  return {
    settled: useBurnup ? burnup!.settled : live.filter((r) => r.status === "met").length,
    scope: useBurnup ? burnup!.scope_total : live.length,
    open: reqs.filter((r) => OPEN.has(r.status)).length,
    openMeasured: reqs.filter((r) => OPEN.has(r.status) && r.test_kind === "metric").length,
    inWork: reqs.filter((r) =>
      (r.status === "in_progress" || r.status === "checking") && !waitsOnYou(r, yours)).length,
    notStarted: reqs.filter((r) => r.status === "pending").length,
    waiting: reqs.filter((r) => waitsOnYou(r, yours)).length + (promiseProposed ? 1 : 0),
    accepted: reqs.filter((r) => r.status !== "proposed").length,
  };
}

type Tone = "good" | "plain" | "ask" | "warn" | "critical";

/** The one sentence at the top of the Roadmap: a bold claim and its basis. */
interface Status { tone: Tone; lead: string; basis: string }

const FROZEN = "New scope is frozen until the promise holds again: cut requirements below, or move the date.";

/** Will the work be finished by the date you signed? Answered once, in words.

    This replaces eight tiles and a second card that printed the promised date
    three times. Every case reads the commitment's status (the janitor's,
    which owns the transitions) for the verb, and the measured pace for the
    date, and says "too early to project" rather than print a date the pace
    cannot support. */
function deliveryStatus(
  c: PlanCommitment | null, v: Velocity | undefined, k: Counts, now: number,
): Status {
  const allSettled = k.open === 0 && k.accepted > 0;

  if (c === null) {
    if (allSettled) {
      return { tone: "good", lead: "Everything in the plan is settled.", basis: "No delivery date was promised." };
    }
    const pace = measuredPaces(v)[0];
    if (pace && v && v.remaining > 0) {
      const weeks = Math.round((v.remaining / pace.perWeek) * 10) / 10;
      return {
        tone: "plain",
        lead: "No delivery date promised yet.",
        basis: `At ${paceWords(pace, v)}, the open work takes about ${plural(weeks, "week")}.`,
      };
    }
    const why = whyNoPace(v);
    return {
      tone: "plain",
      lead: "No delivery date promised yet.",
      basis: `Too early to project: ${why.charAt(0).toLowerCase()}${why.slice(1)}`,
    };
  }

  const promised = fmtDay(c.deliver_by);
  const deliverBy = new Date(c.deliver_by).getTime();
  const proj = projectionOf(c, v, now);
  const span = proj ? fmtSpan(proj.lo, proj.hi) : "";
  const daysLate = proj ? Math.ceil((proj.hi - deliverBy) / DAY_MS) : 0;

  if (c.status === "proposed") {
    const what = c.scope_snapshot.length > 0
      ? plural(c.scope_snapshot.length, "requirement") : "the accepted scope";
    const date = proj
      ? `The work lands about ${span}, projected from ${proj.from}.`
      : v && v.remaining === 0
        ? ""
        : "Too early to project from a pace, so the date is the plan's best case: "
          + "the most work you allow in a day, with nothing going wrong.";
    return {
      tone: "ask",
      lead: `Proposed: ${what} by ${promised}.`,
      basis: `Nothing binds until you accept.${date ? ` ${date}` : ""}`,
    };
  }

  if (c.status === "met") {
    const on = c.met_at ? fmtDay(c.met_at) : null;
    return {
      tone: "good",
      lead: on === null ? `Delivered, promised ${promised}.`
        : on === promised ? `Delivered ${on}, the day promised.`
        : `Delivered ${on}, promised ${promised}.`,
      basis: "Every requirement in the promise is proven true.",
    };
  }

  if (c.status === "breached") {
    return {
      tone: "critical",
      lead: `The promise of ${promised} passed ${dayGap(deliverBy, now)} with ${k.open} still open.`,
      basis: (proj
        ? `The rest lands about ${span}, projected from ${proj.from}.`
        : "Too early to project when the rest lands.") + ` ${FROZEN}`,
    };
  }

  if (c.status === "at_risk") {
    if (!proj) {
      return {
        tone: "warn",
        lead: `At risk: even the best case misses the promise of ${promised}.`,
        basis: "Too early to project a date, but at the most work you allow in a day "
          + `the open work still runs past it. ${FROZEN}`,
      };
    }
    return {
      tone: "warn",
      lead: !spansDays(proj.lo, proj.hi) && daysLate > 0
        ? `At risk: now expected ${span}, ${plural(daysLate, "day")} past the promise of ${promised}.`
        : `At risk: now expected ${span}, promised ${promised}.`,
      basis: `Projection from ${proj.from}. ${FROZEN}`,
    };
  }

  // Accepted: the promise holds, as far as the janitor last looked.
  if (allSettled) {
    return {
      tone: "good",
      lead: now <= deliverBy
        ? `Everything is settled, inside the promise of ${promised}.`
        : `Everything is settled, promised ${promised}.`,
      basis: "",
    };
  }
  if (v && v.remaining === 0 && k.openMeasured > 0) {
    return {
      tone: "plain",
      lead: `The work is settled, promised ${promised}.`,
      basis: `${plural(k.openMeasured, "measured target")} still open; `
        + `${k.openMeasured === 1 ? "it settles" : "they settle"} when the number does.`,
    };
  }
  if (proj) {
    const late = proj.lo > deliverBy;
    return {
      tone: late ? "warn" : "good",
      lead: late
        ? `Now expected ${span}, promised ${promised}.`
        : `On course for ${span}, promised ${promised}.`,
      basis: `Projection from ${proj.from}; it firms up as more settle.`
        + (!late && daysLate > 0
          ? ` The late end runs ${plural(daysLate, "day")} past the promise.` : ""),
    };
  }
  return { tone: "plain", lead: `Too early to project, promised ${promised}.`, basis: whyNoPace(v) };
}

/** The sentence, and under it whatever levers the sentence hands you. A
    promise that needs you is boxed; one that does not is just said. */
function PlanStatus({ s, children }: { s: Status; children?: ReactNode }) {
  const box = s.tone === "critical" ? " strip critical"
    : s.tone === "warn" ? " strip serious"
    : s.tone === "ask" ? " strip"
    : "";
  return (
    <div className={`plan-status${box}`}>
      <div className="grow">
        <p className="plan-lede">
          <b>{s.lead}</b>{s.basis ? ` ${s.basis}` : ""}
        </p>
        {children}
      </div>
    </div>
  );
}

/** The CEO asked to promise a date it did not get to pick. The chairman takes
    it, replaces it with his own, or declines — nothing binds until he acts.
    The proposal itself is said by the sentence above; these are its levers. */
function ProposedLevers({ c, onDone }: { c: PlanCommitment; onDone: () => void }) {
  const [date, setDate] = useState(isoDay(c.deliver_by));
  return (
    <>
      {c.note && <p className="plan-note">The CEO adds: “{c.note}”</p>}
      <div className="plan-acts">
        <label className="help" htmlFor="cmt-date">deliver by</label>
        <input id="cmt-date" type="date" value={date} min={isoDay(new Date().toISOString())}
          onChange={(e) => setDate(e.target.value)} />
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
    </>
  );
}

/** A promise that stopped holding. New scope is already frozen server-side —
    the sentence above says so and shows the arithmetic; this is the lever. */
function MoveDate({ c, onDone }: { c: PlanCommitment; onDone: () => void }) {
  const [date, setDate] = useState("");
  const [reason, setReason] = useState("");
  return (
    <div className="plan-acts">
      <input type="date" aria-label="New delivery date" value={date}
        onChange={(e) => setDate(e.target.value)} />
      <input type="text" className="plan-reason" value={reason}
        placeholder="Why the date moves — the CEO reads it"
        onChange={(e) => setReason(e.target.value)} />
      <button className="small" disabled={!date || reason.trim().length < 5}
        onClick={async () => {
          await post(`/admin/commitments/${c.id}/move`,
            { deliver_by: `${date}T23:59:59Z`, reason });
          onDone();
        }}>Move the date</button>
    </div>
  );
}

/** Three facts at most, in one quiet line under the chart. A zero is left
    out rather than printed, except the one that is good news. */
function PlanFacts({ k }: { k: Counts }) {
  const facts: string[] = [];
  if (k.scope > 0) facts.push(`${k.settled} of ${k.scope} settled`);
  if (k.inWork > 0) facts.push(`${k.inWork} in work`);
  else if (k.notStarted > 0) facts.push(`${k.notStarted} not started`);
  facts.push(k.waiting > 0 ? `${k.waiting} waiting on you` : "nothing waiting on you");
  return (
    <p className="plan-facts">
      {facts.map((f) => <span key={f}>{f}</span>)}
    </p>
  );
}

function MoreRow({ label, value, children }: {
  label: string; value?: string; children: ReactNode;
}) {
  return (
    <div className="pm-row">
      <span className="pm-l">{label}</span>
      <b className="pm-v">{value ?? ""}</b>
      <span className="pm-why">{children}</span>
    </div>
  );
}

/** The numbers that used to be tiles at the top. All of them still exist and
    are still right; most of them answer a question nobody asks first, so they
    wait one click down instead of each explaining its own zero up top. */
function MoreNumbers({ data, c }: { data: PlanView; c: PlanCommitment | null }) {
  const sums = data.plan!.arithmetic;
  const v = data.velocity;
  const runs = v?.runs_3w;
  return (
    <details className="plan-more">
      <summary>More numbers</summary>
      <div className="pm-list">
        {/* `plan_arithmetic.requirements` counts the LIVE ones — settled and
            cut are out, which is what makes the jobs beside it mean "work
            left". */}
        <MoreRow label="Work it should take" value={plural(sums.adjusted_runs, "job")}>
          for the {plural(sums.requirements, "requirement")} still open
          {data.burnup && data.burnup.scope_total > 0 ? ` of ${data.burnup.scope_total} in the plan` : ""}
          {sums.multiplier > 1 ? `, padded ×${sums.multiplier} because this CEO has been running over` : ""}
        </MoreRow>
        {sums.days_at_the_daily_cap != null && (
          <MoreRow label="If nothing went wrong" value={plural(sums.days_at_the_daily_cap, "day")}>
            at the most work you allow in one day
          </MoreRow>
        )}
        {v && (
          /* A rate, or a dash. Printing `0 a week` for a project that has
             not been alive a week says its team has achieved nothing, which
             is a different claim from "there is nothing to measure yet". */
          <MoreRow label="Requirements settled" value={v.pace_known ? `${v.per_week_3w} a week` : "—"}>
            {v.pace_known
              ? (v.weeks_left_at_pace != null
                  ? `${v.remaining} still open, about ${plural(v.weeks_left_at_pace, "week")} at this rate`
                  : `${v.remaining} still open`)
              : `${v.remaining} still open; too little has settled to call this a pace`}
          </MoreRow>
        )}
        {v && runs && runs.total > 0 && (
          <MoreRow label="Spend that bought nothing" value={`${Math.round(runs.failure_share * 100)}%`}>
            {runs.dead} of {runs.total} jobs ended with nothing to show
            {v.worst_failure ? `, worst on “${clip(v.worst_failure.statement, 44)}”` : ""}
          </MoreRow>
        )}
        {runs && runs.total > 0 && runs.inbox + runs.noop > 0 && (
          /* What the project spent talking to itself. Its own broadcasts
             generate this traffic, so it is the one cost that can be priced
             before it is paid. */
          <MoreRow label="Overhead"
            value={`${Math.round(((runs.inbox + runs.noop) / runs.total) * 100)}%`}>
            {runs.inbox} reading the inbox, {runs.noop} that changed nothing, of {runs.total} runs
          </MoreRow>
        )}
        {sums.budget.known ? (
          <MoreRow label="Affordable" value={plural(sums.budget.runs ?? 0, "run")}>
            ${sums.budget.usd_remaining} left, at ${sums.budget.usd_per_run} a run
          </MoreRow>
        ) : (
          <MoreRow label="What the rest of the budget buys" value="—">
            {sums.budget.why}
          </MoreRow>
        )}
        {c?.projection_basis && (
          <MoreRow label="How the platform works out the date">{c.projection_basis}</MoreRow>
        )}
      </div>
    </details>
  );
}

// ── the burn-up ─────────────────────────────────────────────────────────────────

/** The burn-up: what has become true, against the promise and the projection.
    Server data only — the marks are the commitment (his) and the projection
    (the platform's), which is the whole conversation.
 *
 * The chart that was here drew two lines and told you nothing you could read
 * off it: no y-axis, so a point had no value; no gridlines, so no point had a
 * neighbour; and three labels placed at their own x with no regard for each
 * other or for the edge of the picture — which is how a shipped screenshot
 * came to say "projected Oc", clipped mid-word at the frame.
 *
 * Every mark is inside the frame, and where two dates land close enough to
 * collide they are drawn as one label rather than two on top of each other.
 * The question it answers is asked and answered by the sentence above it. */

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

/** Below any of these there is no shape to draw, so the card says so instead.
    A line through two points is the number above it said twice, with a slope
    nobody can check; a two-requirement scale makes every step half the plot
    height; and a set of marks written inside one morning is a moment, not a
    window. */
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
    // with the sentence above it, which still reads Oct 2. Two dates close
    // enough to share a label are not the same date.
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
  points: { t: number; n: number }[];
  deliverBy: number;
  /** The span the sentence above states, or null when it states none. */
  range: { lo: number; hi: number } | null;
  late: boolean;
  drawable: boolean;
  /** Empty when drawable. Why not, in the words the card prints. */
  why: string;
}

/** The numbers the picture reads, computed once, from the same projection the
    sentence above it quotes — so the two can never say different things
    about the same promise. */
function burnupFacts(
  c: PlanCommitment, burnup: PlanBurnup, range: BurnupFacts["range"],
): BurnupFacts {
  const scope = Math.max(burnup.scope_total, 1);
  // The server builds the line from the same set it counts the ceiling from,
  // so this clamp is a belt: if that ever drifts again the chart flattens at
  // its own top instead of walking out through the frame.
  const points = burnup.met_points.map((p) => ({
    t: new Date(p.at).getTime(), n: Math.min(p.count, scope),
  }));
  const deliverBy = new Date(c.deliver_by).getTime();
  const spread = points.length ? points[points.length - 1].t - points[0].t : 0;

  const why =
    scope < MIN_SCOPE ? `A plan of ${scope} has no shape to draw.`
    : points.length < MIN_POINTS ? `The line is drawn once ${MIN_POINTS} have settled.`
    : spread < MIN_SPREAD_MS ? "Everything so far settled inside one day; the line is drawn once the work spans more."
    : "";

  return {
    scope, settled: Math.min(burnup.settled, scope), points, deliverBy, range,
    late: c.status === "at_risk" || c.status === "breached"
      || (range !== null && range.lo > deliverBy),
    drawable: why === "", why,
  };
}

/** Requirements settled over time, under the sentence that says what they
    add up to. It either draws the shape or says why there is none yet —
    never a line through one or two points, which is the number said twice
    and a slope that cannot be read. */
function Burnup({ c, burnup, range }: {
  c: PlanCommitment; burnup: PlanBurnup; range: BurnupFacts["range"];
}) {
  const f = burnupFacts(c, burnup, range);
  return f.drawable ? <BurnupChart c={c} f={f} /> : <BurnupSummary f={f} />;
}

function BurnupSummary({ f }: { f: BurnupFacts }) {
  return (
    <div className="card plan-chart">
      <ChartHead question="Requirements settled over time" meta={f.why} />
    </div>
  );
}

function BurnupChart({ c, f }: { c: PlanCommitment; f: BurnupFacts }) {
  const now = Date.now();
  const { scope, points, settled, deliverBy, range, late } = f;
  const projColour = late ? "var(--serious)" : "var(--good)";
  const span = range !== null && spansDays(range.lo, range.hi);

  // The window is the data's, padded on both sides, floored to a week, and
  // capped so a runaway projection cannot squeeze the work into the origin.
  const opened = c.accepted_at ? new Date(c.accepted_at).getTime() : points[0].t;
  const lo0 = Math.min(opened, points[0].t, now);
  const cap = deliverBy + Math.max(deliverBy - lo0, MIN_SPAN_MS) * HORIZON;
  const offFrame = range !== null && range.hi > cap;
  const hi0 = Math.max(now, deliverBy, offFrame ? cap : (range?.hi ?? 0));
  const width = Math.max(hi0 - lo0, MIN_SPAN_MS);
  const lo = lo0 - width * PAD_LEFT;
  const hi = lo0 + width * (1 + PAD_RIGHT);

  const x = (t: number) => CL + ((t - lo) / (hi - lo)) * (CW - CL - CR);
  const y = (n: number) => CH - CB - (n / scope) * (CH - CB - YTOP);
  /** Where a projection meets the top of the plan, held inside the frame. */
  const reach = (t: number) => Math.min(x(Math.min(t, cap)), CW - CR);

  let line = `M ${x(lo0)} ${y(0)}`;
  let lastN = 0;
  for (const p of points) {
    line += ` L ${x(p.t)} ${y(lastN)} L ${x(p.t)} ${y(p.n)}`;
    lastN = p.n;
  }
  line += ` L ${x(Math.min(now, hi))} ${y(lastN)}`;
  const tipX = x(Math.min(now, hi));
  const tipY = y(lastN);

  const marks = mergeMarks([
    { at: now, top: fmtDay(new Date(now).toISOString()), bottom: "today",
      colour: "var(--muted)", weight: 1, dashed: false },
    { at: deliverBy, top: fmtDay(c.deliver_by), bottom: "you promised",
      colour: c.status === "breached" ? "var(--critical)" : "var(--ink-2)",
      weight: 1.5, dashed: false },
    ...(range === null ? [] : [{
      at: offFrame ? cap : range.hi,
      top: fmtSpan(range.lo, range.hi),
      bottom: offFrame ? "expected beyond this window" : late ? "now expected" : "on course for",
      colour: projColour, weight: 1.5, dashed: true,
    }]),
  ], x);

  return (
    <div className="card plan-chart">
      <ChartHead
        question="Requirements settled over time"
        meta={`${fmtDay(new Date(lo0).toISOString())} to ${fmtDay(new Date(hi0).toISOString())}`}
        legend={
          <>
            <span><i />Settled so far</span>
            {range !== null && lastN < scope && (
              <span>
                <i className="dash" style={{ borderTopColor: projColour }} />
                {span ? "Projection range" : "If the pace holds"}
              </span>
            )}
          </>
        }
      />
      <svg
        viewBox={`0 0 ${CW} ${CH}`}
        style={{ display: "block", width: "100%" }}
        role="img"
        aria-label={`${settled} of ${scope} requirements are settled. You promised ${fmtDay(c.deliver_by)}${range !== null ? `; the platform projects ${fmtSpan(range.lo, range.hi)}` : ""}.`}
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

        {/* The projection, from today's tip to the top of the plan: one dashed
            line when the two measured paces land on the same day, and the
            span between them shaded when they do not. */}
        {range !== null && lastN < scope && (
          <g>
            {span && (
              <path
                d={`M ${tipX} ${tipY} L ${reach(range.lo)} ${y(scope)} L ${reach(range.hi)} ${y(scope)} Z`}
                fill={projColour} fillOpacity={0.14} stroke="none" />
            )}
            {span && (
              <line x1={tipX} y1={tipY} x2={reach(range.lo)} y2={y(scope)}
                stroke={projColour} strokeDasharray="6 5" strokeWidth={2} strokeLinecap="round" />
            )}
            <line x1={tipX} y1={tipY} x2={reach(range.hi)} y2={y(scope)}
              stroke={projColour} strokeDasharray="6 5" strokeWidth={2} strokeLinecap="round" />
          </g>
        )}
        <path d={line} fill="none" stroke="var(--accent)" strokeWidth={2.5}
          strokeLinecap="round" strokeLinejoin="round" />
        {points.map((p) => (
          <circle key={p.t} cx={x(p.t)} cy={y(p.n)} r={4.5}
            fill="var(--accent)" stroke="var(--surface)" strokeWidth={2} />
        ))}
      </svg>
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
                  {"  "}{clip(requirementName(r), 15)}
                </tspan>
              </text>
              <text x={p.x + 12} y={p.y + 35} fontSize={12} fill={
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
              {fmtDay(c.opened_at)} → {c.closed_at ? fmtDay(c.closed_at) : "—"}
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

/** What a stage heading says, read off its own rows. A stage is settled when
    everything in force in it is met or cut — which is what "Stage 1 · open"
    sitting above a requirement marked "Proven true" got wrong: the label came
    from the server's open stage, which reads 1 when nothing is open at all. */
function stageState(rows: PlanRequirement[]): { label: string; tone: string } {
  const inForce = rows.filter((r) => r.status !== "proposed");
  if (inForce.length === 0) return { label: "proposed", tone: "serious" };
  if (inForce.every((r) => SETTLED.has(r.status))) return { label: "settled", tone: "good" };
  if (inForce.some((r) => r.status === "in_progress" || r.status === "checking" || r.status === "met")) {
    return { label: "in work", tone: "accent" };
  }
  return { label: "not started", tone: "" };
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
  const reqNames = new Map(reqs.map((r) => [r.key, requirementName(r)]));
  const stages = [...new Set(reqs.map((r) => r.stage))].sort((a, b) => a - b);
  // A void commitment binds nothing (an Operate project's leftover), so it
  // is no promise to talk about.
  const commitment = data.commitment && data.commitment.status !== "void"
    ? data.commitment : null;
  const now = Date.now();
  const counts = countsOf(reqs, data.burnup, commitment?.status === "proposed", {
    scope: chairmanDecides(ctx.dash, "plan_authority"),
    verdict: chairmanDecides(ctx.dash, "verdict_policy"),
  });
  const projection = commitment ? projectionOf(commitment, data.velocity, now) : null;
  // The stage the server's gate holds work behind: the lowest one with open
  // work in it. None when nothing is open, rather than stage 1 by default.
  const openStages = reqs.filter((r) => OPEN.has(r.status)).map((r) => r.stage);
  const firstOpen = openStages.length > 0 ? Math.min(...openStages) : null;
  const status = data.mode === "deliver" && (commitment !== null || (plan.holds && reqs.length > 0))
    ? deliveryStatus(commitment, data.velocity, counts, now)
    : null;

  return (
    <div className="plan">
      {ctx.dash.mode === "operate" && ctx.dash.cycle && (
        <PlanStatus s={{
          tone: "plain",
          lead: `Cycle ${ctx.dash.cycle.ordinal} — this board is this cycle's plan.`,
          basis: "Each cycle plans fresh; unfinished scope carries forward when the rhythm rolls.",
        }} />
      )}
      {/* R-00, translated. When the plan does not hold the reasons are the
          platform's own words — the same list the CEO reads in its refusal. */}
      {!plan.holds && (
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
      )}
      {status && (
        <PlanStatus s={status}>
          {commitment?.status === "proposed" && (
            <ProposedLevers c={commitment} onDone={reload} />
          )}
          {commitment && (commitment.status === "at_risk" || commitment.status === "breached") && (
            <MoveDate c={commitment} onDone={reload} />
          )}
        </PlanStatus>
      )}

      {/* Deliver only. Work that comes round again does not burn up — the
          shape that carries the information for an Operate project is the
          record below, and drawing both put a chart that cannot be right
          beside the one that is. */}
      {data.mode === "deliver" && commitment && commitment.status !== "proposed"
        && data.burnup && (
        <Burnup c={commitment} burnup={data.burnup} range={projection} />
      )}

      {reqs.length > 0 && <PlanFacts k={counts} />}

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

      <div style={{ display: "flex", gap: 10, alignItems: "center", marginBottom: 12, flexWrap: "wrap" }}>
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
                ["Order", "Name", "What has to be true", "State", "Stage", "Jobs used",
                 "Jobs estimated", "Spent (USD)", "Failed checks", "Waits for", "Settled"],
                reqs.map((r) => [
                  r.key,
                  requirementName(r),
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
        stages.map((stage) => {
          const rows = reqs.filter((r) => r.stage === stage);
          const st = stageState(rows);
          // Refused only behind an EARLIER stage with open work, and only
          // said where there is something left here to refuse.
          const behind = firstOpen !== null && firstOpen < stage && st.label !== "settled"
            ? firstOpen : null;
          return (
            <section key={stage} className="plan-stage">
              <h2 className="plan-stage-h">
                Stage {stage}
                <span className={`badge ${st.tone}`}>{st.label}</span>
                {behind !== null && (
                  <span className="help">work here is refused until stage {behind} settles</span>
                )}
              </h2>
              {rows.map((r) => (
                <RequirementCard key={r.id} r={r} names={reqNames}
                  onCritical={critical.includes(r.key)} onDone={reload} />
              ))}
            </section>
          );
        })
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

      {plan.holds && <MoreNumbers data={data} c={commitment} />}
    </div>
  );
}
