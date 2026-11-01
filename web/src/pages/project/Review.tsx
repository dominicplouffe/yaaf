import { type ReactNode, useState } from "react";
import {
  Decision, get, PlanMark, post, requirementName, RequirementMeta, ReviewNumbers, ReviewRecord,
  ReviewReport, ReviewRow, ReviewsView,
} from "../../api";
import { fmtWhen, usePoll } from "../../hooks";
import { AsOf, ExportCsv, NoteAction, Skeleton, clip, downloadCsv } from "../../ui";
import { fmtDate, fullyAutonomous } from "../../ui/project";
import type { ProjectCtx } from "../Project";


interface RequirementOption {
  id: string; key: string; statement: string; meta: RequirementMeta | null; status: string;
}

const LEVER_LABELS: Record<string, string> = {
  cut: "Cut scope",
  add: "Add scope",
  reword: "Reword",
  move_date: "Move the date",
  raise_budget: "Raise the budget",
  accept_slip: "Accept the slip",
};

/** A commitment's state in the words the rest of the product uses for it.
    `deliveryVerdict` is the authority on this wording; these are the same
    phrases so the meeting and the strip above it cannot appear to describe
    two different problems. */
const COMMITMENT_PLAIN: Record<string, string> = {
  proposed: "Unsigned",
  accepted: "On track",
  at_risk: "At risk",
  breached: "Date passed",
  met: "Delivered",
  void: "Withdrawn",
};

function field(style: object = {}) {
  return { font: "inherit", fontSize: "var(--t-small)", padding: "6px 10px",
    background: "var(--surface)", color: "var(--ink)",
    border: "1px solid var(--border-strong)", borderRadius: "var(--radius-sm)", ...style };
}


/** The standing status meeting. The janitor opened it with zero tokens and
    froze the numbers; the CEO filed (or owes) its variance narrative; the six
    levers below act through the same mechanisms they would anywhere else and
    are recorded here with the chairman's reason — shown at the next review
    beside what they bought. */



/** One lever, one small form. Every form ends in the same two things — a
    reason and a button — because the reason IS the record. */
function LeverForm({ review, kind, requirements, hasCommitment, unit, onDone }: {
  review: ReviewRow; kind: string; requirements: RequirementOption[];
  hasCommitment: boolean; unit: string; onDone: () => void;
}) {
  const [reason, setReason] = useState("");
  const [requirementId, setRequirementId] = useState("");
  const [statement, setStatement] = useState("");
  const [testKind, setTestKind] = useState<"command" | "observable">("command");
  const [testText, setTestText] = useState("");
  const [stage, setStage] = useState(1);
  const [estRuns, setEstRuns] = useState(5);
  const [date, setDate] = useState("");
  const [amount, setAmount] = useState("");
  const [error, setError] = useState<string | null>(null);

  const openReqs = requirements.filter((r) => !["met", "cut"].includes(r.status));

  async function pull() {
    setError(null);
    const body: Record<string, unknown> = { kind, reason };
    if (kind === "cut" || kind === "reword") body.requirement_id = requirementId;
    if (kind === "reword" || kind === "add") body.statement = statement;
    if (kind === "add") {
      body.acceptance_test = testKind === "command"
        ? { type: "command", command: testText }
        : { type: "observable", target: testText };
      body.stage = stage;
      body.est_runs = estRuns;
    }
    if (kind === "move_date") body.deliver_by = `${date}T23:59:59Z`;
    if (kind === "raise_budget") body.amount = Number(amount);
    try {
      await post(`/admin/reviews/${review.id}/lever`, body);
      onDone();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  const needsRequirement = kind === "cut" || kind === "reword";
  const ready =
    reason.trim().length >= 5 &&
    (!needsRequirement || requirementId) &&
    (kind !== "reword" || statement.trim().length >= 10) &&
    (kind !== "add" || (statement.trim().length >= 10 && testText.trim().length >= 15)) &&
    (kind !== "move_date" || date) &&
    (kind !== "raise_budget" || Number(amount) > 0);

  return (
    <div style={{ marginTop: 10, display: "flex", flexDirection: "column", gap: 8,
      maxWidth: 640 }}>
      {kind === "move_date" && !hasCommitment && (
        <div className="help">No live commitment — there is no date to move.</div>
      )}
      {needsRequirement && (
        <select value={requirementId} onChange={(e) => setRequirementId(e.target.value)}
          style={field()}>
          <option value="">— pick the requirement —</option>
          {openReqs.map((r) => (
            <option key={r.id} value={r.id}>
              {r.key} · {clip(requirementName(r), 80)}
            </option>
          ))}
        </select>
      )}
      {(kind === "add" || kind === "reword") && (
        <input style={field()} value={statement} placeholder={
          kind === "add" ? "What must be TRUE at the end — an outcome, not a task"
                         : "The new wording"}
          onChange={(e) => setStatement(e.target.value)} />
      )}
      {kind === "add" && (
        <>
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <select value={testKind} style={field()}
              onChange={(e) => setTestKind(e.target.value as "command" | "observable")}>
              <option value="command">command — the platform runs it</option>
              <option value="observable">observable — you confirm it</option>
            </select>
            <label className="help">stage</label>
            <input type="number" min={1} max={50} value={stage} style={field({ width: 64 })}
              onChange={(e) => setStage(Number(e.target.value))} />
            <label className="help">est. runs</label>
            <input type="number" min={1} value={estRuns} style={field({ width: 72 })}
              onChange={(e) => setEstRuns(Number(e.target.value))} />
          </div>
          <input style={field()} value={testText}
            placeholder={testKind === "command"
              ? "the exact command, e.g. curl -sf https://example.com/health"
              : "the checkable artifact/URL/metric you will look at"}
            onChange={(e) => setTestText(e.target.value)} />
        </>
      )}
      {kind === "move_date" && (
        <input type="date" value={date} style={field({ width: 180 })}
          onChange={(e) => setDate(e.target.value)} />
      )}
      {kind === "raise_budget" && (
        <input type="number" min={1} value={amount} style={field({ width: 180 })}
          placeholder={`new monthly cap (${unit})`}
          onChange={(e) => setAmount(e.target.value)} />
      )}
      <input style={field()} value={reason}
        placeholder="Your reason — recorded, disclosed to the CEO, shown next review"
        onChange={(e) => setReason(e.target.value)} />
      {error && <div className="error">{error}</div>}
      <div>
        <button className="small primary" disabled={!ready} onClick={pull}>
          {LEVER_LABELS[kind]}
        </button>
      </div>
    </div>
  );
}

function DecisionList({ decisions }: { decisions: Decision[] }) {
  if (decisions.length === 0) return null;
  return (
    <table style={{ marginTop: 8 }}>
      <tbody>
        {decisions.map((d, i) => (
          <tr key={i}>
            {/* The same words the button that does it wears. `raise_budget`
                is the wire's name for the lever, not the reader's. */}
            <td style={{ whiteSpace: "nowrap" }}>
              <span className="chip">{LEVER_LABELS[d.kind] ?? d.kind.replaceAll("_", " ")}</span>
            </td>
            <td>{d.detail}</td>
            <td className="help">{d.reason}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** A short, plain word for who the lines in a section came from. The record
    is the platform's at zero tokens and cannot be argued with; the CEO's
    words are the CEO's. A reader should never have to guess which. */
function Source({ kind }: { kind: "record" | "ceo" }) {
  const record = kind === "record";
  return (
    <span className={"chip " + (record ? "ok" : "accent")} title={record
      ? "Composed by the platform from its own events when the meeting opened. No agent wrote it."
      : "Filed by the CEO on a reserved governance run."}>
      {record ? "from the record" : "CEO"}
    </span>
  );
}

function KeyedList({ rows, how }: {
  rows: { key: string; statement: string; how?: string; reason?: string | null }[];
  how?: boolean;
}) {
  return (
    <ul style={{ listStyle: "none", margin: "6px 0 0", padding: 0, fontSize: 13 }}>
      {rows.map((r) => (
        <li key={r.key} style={{ display: "flex", gap: 8, minWidth: 0 }}>
          <span className="help" style={{ flex: "0 0 auto", fontFamily: "var(--mono)" }}>
            {r.key}
          </span>
          <span style={{ minWidth: 0 }}>
            {clip(r.statement, 110)}
            {r.reason != null && <span className="help"> — {clip(r.reason, 90)}</span>}
          </span>
          {how && r.how && (
            <span className="help" style={{ marginLeft: "auto", whiteSpace: "nowrap" }}>
              {r.how}
            </span>
          )}
        </li>
      ))}
    </ul>
  );
}

/** What was done in the window, by name, before any number. Mode-shaped:
    a Deliver project did requirements; an Operate project did a cycle; an
    Explore project ran bets and collected evidence. */
function RecordSection({ record, mode }: { record: ReviewRecord | null; mode: string }) {
  if (!record) {
    return (
      <div>
        <div style={{ display: "flex", gap: 8, alignItems: "baseline" }}>
          <b style={{ fontSize: 13 }}>What was done</b><Source kind="record" />
        </div>
        <p className="help" style={{ marginTop: 4 }}>
          This meeting opened before the platform kept a record of the period; only the
          numbers below were frozen.
        </p>
      </div>
    );
  }
  const cell = (n: string, label: string, body: ReactNode) => (
    <div style={{ background: "var(--sunken)", borderRadius: "var(--radius-sm)",
      padding: "10px 12px", minWidth: 0 }}>
      <div style={{ fontSize: 20, fontWeight: 600, lineHeight: 1.1,
        fontVariantNumeric: "tabular-nums" }}>{n}</div>
      <div className="help">{label}</div>
      {body}
    </div>
  );
  const cells: ReactNode[] = [];
  if (mode === "explore") {
    const b = record.bets;
    cells.push(cell(
      `${b.alive.length} alive`,
      `bets — ${b.killed.length} killed, ${b.expired.length} expired this period`,
      <KeyedList rows={[
        ...b.alive.map((h) => ({ key: "alive", statement: h.statement,
          how: `kill ${fmtDate(h.kill_date)}` })),
        ...b.killed.map((h) => ({ key: "killed", statement: h.statement })),
        ...b.expired.map((h) => ({ key: "expired", statement: h.statement })),
      ]} how />));
    cells.push(cell(
      `${record.evidence.verified} / ${record.evidence.failed}`,
      "evidence verified / refused by the platform",
      record.evidence.pending > 0
        ? <p className="help" style={{ marginTop: 6 }}>{record.evidence.pending} still waiting on the verifier</p>
        : null));
  } else {
    if (mode === "operate" && record.cycle) {
      const c = record.cycle;
      cells.push(cell(
        `${c.met ?? 0} of ${c.accepted ?? 0}`,
        `cycle ${c.ordinal} scope met · closed ${c.status}`,
        (c.carried_keys?.length ?? 0) > 0
          ? <p className="help" style={{ marginTop: 6 }}>carried: {c.carried_keys!.join(", ")}</p>
          : null));
    }
    cells.push(cell(
      String(record.requirements_met.length),
      "requirements made true",
      record.requirements_met.length > 0
        ? <KeyedList rows={record.requirements_met} how />
        : <p className="help" style={{ marginTop: 6 }}>none this period</p>));
    if (record.standing_honoured.length > 0) {
      cells.push(cell(
        String(record.standing_honoured.length),
        "standing obligations honoured",
        <KeyedList rows={record.standing_honoured} />));
    }
    if (record.requirements_cut.length > 0) {
      cells.push(cell(
        String(record.requirements_cut.length),
        "requirements cut, with reasons",
        <KeyedList rows={record.requirements_cut} />));
    }
  }
  cells.push(cell(
    String(record.deliverables.length),
    "deliverables published",
    record.deliverables.length > 0 ? (
      <ul style={{ listStyle: "none", margin: "6px 0 0", padding: 0, fontSize: 13 }}>
        {record.deliverables.map((d) => (
          <li key={d.name} style={{ display: "flex", gap: 8, minWidth: 0 }}>
            <span className="help" style={{ fontFamily: "var(--mono)" }}>v{d.version}</span>
            <span style={{ minWidth: 0 }}>{clip(d.name, 60)}
              {d.requirements.length > 0 && (
                <span className="help"> · claims {d.requirements.join(", ")}</span>)}
            </span>
          </li>))}
      </ul>
    ) : <p className="help" style={{ marginTop: 6 }}>nothing published</p>));
  const s = record.spot_checks;
  cells.push(cell(
    `${s.drawn} of ${s.considered}`,
    "closes spot-checked",
    <p className="help" style={{ marginTop: 6 }}>
      {s.failed > 0 ? `${s.failed} failed · ` : ""}
      {s.expired > 0 ? `${s.expired} expired unanswered · ` : ""}
      {record.runs.total} runs, {record.runs.dead} dead
      {record.tasks_closed > 0 ? ` · ${record.tasks_closed} tasks closed` : ""}
    </p>));
  return (
    <div>
      <div style={{ display: "flex", gap: 8, alignItems: "baseline" }}>
        <b style={{ fontSize: 13 }}>What was done since the last meeting</b>
        <Source kind="record" />
        <span className="help">{fmtDate(record.since)} → {fmtDate(record.until)}</span>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))",
        gap: 10, marginTop: 8 }}>
        {cells}
      </div>
    </div>
  );
}

/** The CEO's status report, in the order a meeting is held: what the period
    bought, what did not go to plan, what is next, the ask. The list of what
    was done is the platform's and sits above this; the CEO explains it. */
function ReportSections({ report }: { report: ReviewReport }) {
  const head = (title: string) => (
    <div style={{ display: "flex", gap: 8, alignItems: "baseline" }}>
      <b style={{ fontSize: 13 }}>{title}</b><Source kind="ceo" />
    </div>
  );
  const ask = report.ask;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14, marginBottom: 12 }}>
      <div>
        {head("What it bought us")}
        <p style={{ marginTop: 6, whiteSpace: "pre-wrap", maxWidth: "70ch" }}>{report.done}</p>
      </div>
      <div>
        {head("What did not go to plan, and why")}
        <p style={{ marginTop: 6, whiteSpace: "pre-wrap", maxWidth: "70ch" }}>
          {report.variance.what}
        </p>
        <p className="help" style={{ marginTop: 4, maxWidth: "70ch" }}>
          Largest cause: {report.variance.largest_cause}
        </p>
      </div>
      <div>
        {head("Next period — what it will do")}
        <ul style={{ listStyle: "none", margin: "6px 0 0", padding: 0, display: "flex",
          flexDirection: "column", gap: 6 }}>
          {report.next.map((item, i) => (
            <li key={i} style={{ display: "flex", gap: 10, alignItems: "baseline",
              flexWrap: "wrap", padding: "6px 10px", background: "var(--sunken)",
              borderRadius: "var(--radius-sm)" }}>
              <span style={{ flex: 1, minWidth: 200 }}>{item.what}</span>
              <span className="help" style={{ fontFamily: "var(--mono)" }}>
                {item.serves}
              </span>
              {item.by && <span className="help">by {fmtDate(item.by)}</span>}
            </li>
          ))}
        </ul>
        <p className="help" style={{ margin: "8px 0 0", borderLeft: "3px solid var(--accent)",
          padding: "2px 10px", maxWidth: "70ch" }}>
          How this moves the goal: {report.goal_effect}
        </p>
      </div>
      <div>
        {head(ask.kind === "proceed" ? "The ask: proceed" : "The ask: change something")}
        {ask.kind === "proceed" ? (
          <p className="help" style={{ marginTop: 6 }}>
            Nothing needs you. Closing with no changes records that.
          </p>
        ) : (
          <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>
            {ask.levers.map((l, i) => (
              <li key={i}><b>{LEVER_LABELS[l.kind] ?? l.kind}</b> — {l.detail}</li>
            ))}
          </ul>
        )}
        {ask.why && <p className="help" style={{ marginTop: 4, maxWidth: "70ch" }}>{ask.why}</p>}
      </div>
    </div>
  );
}

const OUTCOME_LABELS: Record<string, string> = {
  done: "done", partly: "partly", not_done: "not done", dropped: "dropped",
};

/** What the CEO said it would do last meeting, beside what happened. The
    platform's column first — it cannot be argued with — and the CEO's line
    second, where "done" against an open requirement was refused at filing. */
function PlanVsActual({ plan, outcomes }: {
  plan: PlanMark[]; outcomes: ReviewReport["last_plan"] | undefined;
}) {
  const said = new Map((outcomes ?? []).map((o) => [o.item, o]));
  const tone = (m: PlanMark) =>
    ["true", "completed"].includes(m.mark) ? "var(--good)"
      : ["open", "missed"].includes(m.mark) ? "var(--critical)"
      : "var(--muted)";
  return (
    <div>
      <div style={{ display: "flex", gap: 8, alignItems: "baseline", flexWrap: "wrap" }}>
        <b style={{ fontSize: 13 }}>What it said it would do last meeting, and what happened</b>
        <Source kind="record" /><Source kind="ceo" />
      </div>
      <div style={{ overflowX: "auto" }}>
        <table style={{ marginTop: 8 }}>
          <thead>
            <tr><th>Said it would</th><th>Serves</th><th>Platform says</th><th>CEO says</th></tr>
          </thead>
          <tbody>
            {plan.map((m) => {
              const o = said.get(m.item);
              return (
                <tr key={m.item}>
                  <td>{m.what}</td>
                  <td className="help" style={{ fontFamily: "var(--mono)", whiteSpace: "nowrap" }}>
                    {m.serves}
                  </td>
                  <td style={{ color: tone(m), fontWeight: 600, whiteSpace: "nowrap" }}>
                    {m.says}
                  </td>
                  <td>
                    {o ? (
                      <>
                        <b>{OUTCOME_LABELS[o.outcome] ?? o.outcome}</b>
                        {o.note && <span className="help"> — {o.note}</span>}
                      </>
                    ) : <span className="help">not filed yet</span>}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function NumbersTiles({ n, mode, record, requirements }: {
  n: ReviewNumbers; mode: string; record: ReviewRecord | null;
  requirements: RequirementOption[];
}) {
  // Explore has no requirements and no date: the first three tiles would read
  // "—", "0" and nothing. Its scope is bets, and the bets were frozen onto the
  // numbers all along without a tile to show them.
  if (mode === "explore") {
    const alive = n.hypotheses.filter((h) => h.status === "active");
    const nextKill = alive.map((h) => h.kill_date).sort()[0];
    return (
      <div className="tiles" style={{ margin: "12px 0" }}>
        <div className="tile">
          <div className="label">Bets alive</div>
          <div className="value">{alive.length}</div>
          <div className="sub">{n.hypotheses.length - alive.length} expired, not yet killed</div>
        </div>
        <div className="tile">
          <div className="label">Next kill date</div>
          <div className="value" style={{ fontSize: 18 }}>{nextKill ? fmtDate(nextKill) : "—"}</div>
          <div className="sub">{nextKill ? "a bet dies then unless you extend it" : "no live bet"}</div>
        </div>
        <BudgetTile n={n} />
        <AuditTile n={n} record={record} />
      </div>
    );
  }
  const worst = n.velocity.worst_failure;
  const worstRow = worst ? requirements.find((r) => r.key === worst.key) : null;
  return (
    <div className="tiles" style={{ margin: "12px 0" }}>
      <div className="tile">
        <div className="label">Requirements settled</div>
        {/* Same rule as the Plan tile: a rate is a measurement or it is a
            dash. A review that opens with "0 a week" about a project too new
            to have a pace starts the meeting on a false premise.

            Falling back to the rate itself when the snapshot predates the
            flag, rather than to "unknown": an older meeting that recorded a
            real pace should keep showing it. */}
        <div className="value">
          {(n.velocity.pace_known ?? n.velocity.per_week_3w > 0)
            ? <>{n.velocity.per_week_3w}<small> a week</small></> : "—"}
        </div>
        <div className="sub">{n.velocity.remaining} still open
          {(n.velocity.pace_known ?? n.velocity.per_week_3w > 0)
            ? (n.velocity.weeks_left_at_pace != null
                ? ` — about ${n.velocity.weeks_left_at_pace} weeks at this rate` : "")
            : " — too little has settled to call this a pace"}</div>
      </div>
      <div className="tile">
        <div className="label">Met since last</div>
        <div className="value">{n.scope.met_since_last_review}</div>
        <div className="sub">{n.scope.met} met · {n.scope.cut} cut ·
          {" "}{n.scope.proposed} proposed</div>
      </div>
      {n.commitment && (
        <div className="tile">
          <div className="label">Commitment</div>
          {/* One state, one name. The strip at the top of every tab calls this
              "Date passed"; this tile called the same fact "breached", which
              leaves a reader deciding whether they are two problems. */}
          <div className="value" style={{ fontSize: 18 }}>
            {COMMITMENT_PLAIN[n.commitment.status] ?? n.commitment.status}
          </div>
          <div className="sub">
            {/* The same words for a date as every other screen. `2026-09-13`
                beside "Sep 13" on the strip two inches above is the reader
                doing a format conversion to check they are the same day. */}
            promised {fmtDate(n.commitment.deliver_by)}
            {n.commitment.projected_done_at
              ? ` · now ${fmtDate(n.commitment.projected_done_at)}` : ""}
          </div>
        </div>
      )}
      <BudgetTile n={n} />
      <AuditTile n={n} record={record} />
      {worst && (
        <div className="tile alert">
          <div className="label">Most dead runs</div>
          <div className="value" style={{ fontSize: 18 }}>{worst.failure_load}</div>
          {/* The key alone sent the reader to the Roadmap to find out what
              R-29 was. The statement is on the page already; say it. */}
          <div className="sub">
            on {worst.key}{worstRow ? ` · ${clip(requirementName(worstRow), 70)}` : ""}
          </div>
        </div>
      )}
    </div>
  );
}

function BudgetTile({ n }: { n: ReviewNumbers }) {
  return (
    <div className="tile">
      <div className="label">This month's spend</div>
      <div className="value">
        {Math.round((n.budget.spent_mtd / Math.max(n.budget.cap, 1)) * 100)}<small>% of plan</small>
      </div>
      <div className="sub">
        {n.budget.spent_mtd.toLocaleString()} of {n.budget.cap.toLocaleString()} {n.budget.unit}
      </div>
    </div>
  );
}

function AuditTile({ n, record }: { n: ReviewNumbers; record: ReviewRecord | null }) {
  // "Audit coverage 0" read as a score. It is a count of closes that landed
  // on the chairman's desk, out of the closes that could have; the record
  // knows the denominator, the older snapshot does not.
  const considered = record?.spot_checks.considered;
  return (
    <div className="tile">
      <div className="label">Spot-checked</div>
      <div className="value">
        {n.audit_coverage.sampled}{considered != null && <small> of {considered} closes</small>}
      </div>
      <div className="sub">
        {n.audit_coverage.failed} failed · {n.audit_coverage.expired_unreviewed} expired unanswered
      </div>
    </div>
  );
}

export default function Review({ ctx }: { ctx: ProjectCtx }) {
  const { projectId } = ctx;
  const { data, at, error, reload } = usePoll<ReviewsView>(
    () => get<ReviewsView>(`/observe/projects/${projectId}/reviews`), 10000);
  const { data: requirements } = usePoll<RequirementOption[]>(
    () => get<RequirementOption[]>(`/admin/projects/${projectId}/requirements`), 30000);
  const [lever, setLever] = useState<string | null>(null);
  const [showHistory, setShowHistory] = useState<Record<number, boolean>>({});

  if (!data) return <Skeleton rows={5} />;
  const open = data.open;
  const unit = ctx.dash.billing_mode === "subscription" ? "tokens" : "USD";
  // Whether anything on this screen is the chairman's to do. Under a dial that
  // handed scope, dates and verdicts to the CEO, a page headed "you decide"
  // above six lever buttons is describing somebody else's job.
  const selfRunning = fullyAutonomous(ctx.dash);

  return (
    <div>
      <div style={{ display: "flex", alignItems: "baseline", gap: 10, marginBottom: 8 }}>
        <p className="subtitle" style={{ margin: 0, flex: 1 }}>
          {selfRunning
            ? "Your CEO's standing report. You left the decisions to it, so nothing here is "
              + "waiting on you — read it, say anything you want said back, and close it."
            : "The standing meeting: the platform brings the numbers, the CEO explains the "
              + "variance, you decide. Every lever is recorded with your reason and shown at "
              + "the next review beside what it bought."}
        </p>
        <AsOf at={at} error={error} />
      </div>

      {!open ? (
        <div className="card" style={{ maxWidth: 640 }}>
          <p className="help" style={{ margin: 0 }}>
            No review is open. The platform opens one on the schedule you set
            (Settings → Rules in force → “Days between status reviews”) — with zero
            tokens, whatever the board or the budget looks like — and emails you when
            it does.
          </p>
        </div>
      ) : (
        <div className="card" style={{ padding: "14px 18px", marginBottom: 18 }}>
          <div style={{ display: "flex", gap: 10, alignItems: "baseline", flexWrap: "wrap" }}>
            <h2 style={{ margin: 0 }}>Review #{open.number}</h2>
            <span className="help">opened {fmtWhen(open.opened_at)}</span>
            <span className="sp" style={{ flex: 1 }} />
            <NoteAction label="Close the review" className="small primary"
              placeholder="Closing note (optional) — 'no change' is a decision too"
              confirmLabel="Close"
              onSubmit={async (note) => {
                await post(`/admin/reviews/${open.id}/close`, { note });
                setLever(null);
                reload();
              }} />
          </div>

          {/* One sentence in words before any number: a reader who does not
              live in the project knows where it stands by the second line. */}
          <p style={{ fontFamily: "var(--font-display)", fontSize: 22, lineHeight: 1.3,
            margin: "10px 0 14px", maxWidth: "60ch" }}>
            {open.headline}
          </p>

          <div style={{ marginBottom: 16 }}>
            <RecordSection record={open.record} mode={ctx.dash.mode} />
          </div>

          {open.numbers.prior?.plan && open.numbers.prior.plan.length > 0 && (
            <div style={{ marginBottom: 16 }}>
              <PlanVsActual plan={open.numbers.prior.plan} outcomes={open.report?.last_plan} />
            </div>
          )}

          {open.numbers.prior && open.numbers.prior.decisions.length > 0 && (
            <div style={{ marginBottom: 12 }}>
              <b style={{ fontSize: 13 }}>
                Last review's decisions, beside what they bought
              </b>
              <div className="help">
                {open.numbers.prior.bought.met_since} requirement(s) met since ·
                pace {open.numbers.prior.bought.pace_then ?? "—"} →
                {" "}{open.numbers.prior.bought.pace_now}/wk
              </div>
              <DecisionList decisions={open.numbers.prior.decisions} />
            </div>
          )}

          {/* The recommendation first, the reasoning behind a fold.
              These two arrived the other way round: six paragraphs of a CEO
              explaining itself, with the one line saying what it wanted to do
              in small grey type underneath. A reader looking for "what do you
              want from me" had to read all of it to find out the answer was
              "nothing". */}
          {open.report ? (
            <ReportSections report={open.report} />
          ) : (
          <div style={{ marginBottom: 12 }}>
            <div style={{ display: "flex", gap: 8, alignItems: "baseline" }}>
              <b style={{ fontSize: 13 }}>What your CEO says it will do</b><Source kind="ceo" />
            </div>
            {open.narrative || open.recommended ? (
              <>
                <p style={{ marginTop: 6, whiteSpace: "pre-wrap" }}>
                  {open.recommended || "It filed an explanation but recommended nothing."}
                </p>
                {open.narrative && (
                  <details style={{ marginTop: 6 }}>
                    <summary className="help" style={{ cursor: "pointer" }}>
                      Why, in its own words — {open.narrative.length.toLocaleString()} characters
                    </summary>
                    <p style={{ whiteSpace: "pre-wrap", marginTop: 6 }}>{open.narrative}</p>
                  </details>
                )}
              </>
            ) : (
              <p className="help" style={{ marginTop: 6 }}>
                Not filed yet. The CEO is woken for it on the governance reserve, so it
                lands even on a day the ordinary allowance is spent.
              </p>
            )}
          </div>
          )}

          {open.decisions.length > 0 && (
            <div style={{ marginBottom: 12 }}>
              <b style={{ fontSize: 13 }}>Decided this review</b>
              <DecisionList decisions={open.decisions} />
            </div>
          )}

          {/* Said out loud because the buttons look like a to-do list and are
              not one. Pressing one opens a form; using it files a decision
              into "Decided this review" above and leaves the meeting open,
              because the meeting ends when you close it and not before. A row
              of buttons where nothing visibly resolves is a row of buttons a
              reader concludes is broken. */}
          <b style={{ fontSize: 13 }}>Change something — optional</b>
          <p className="help" style={{ margin: "4px 0 0" }}>
            {selfRunning
              ? "You do not have to use any of these: your CEO already holds these decisions. "
              : "Use one only if you want to change the plan. "}
            Anything you do lands under “Decided this review” above and stays there; the meeting
            itself ends when you press <b>Close the review</b>, and closing with no changes is
            itself a recorded decision.
          </p>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 8 }}>
            {Object.entries(LEVER_LABELS).map(([kind, label]) => (
              <button key={kind}
                className={"small" + (lever === kind ? " primary" : "")}
                onClick={() => setLever(lever === kind ? null : kind)}>
                {label}
              </button>
            ))}
          </div>
          {lever && (
            <LeverForm review={open} kind={lever}
              requirements={requirements ?? []}
              hasCommitment={open.numbers.commitment != null}
              unit={unit}
              onDone={() => { setLever(null); reload(); }} />
          )}

          {/* The forecast's inputs, last. They opened the meeting for a year
              and a reader who did not live in the project could not say
              what "11 a week · 0.4 weeks at this rate" meant for him. */}
          <details style={{ marginTop: 16 }}>
            <summary style={{ fontSize: 13, fontWeight: 600 }}>The numbers behind this</summary>
            <NumbersTiles n={open.numbers} mode={ctx.dash.mode} record={open.record}
              requirements={requirements ?? []} />
          </details>
        </div>
      )}

      {data.history.length > 0 && (
        <>
          <div style={{ display: "flex", alignItems: "baseline", gap: "var(--s3)" }}>
            <h2 style={{ flex: 1 }}>Past reviews</h2>
            {/* What you decided, and what it bought — the one record a
                chairman is most likely to want outside this screen. */}
            <ExportCsv
              onClick={() =>
                downloadCsv(
                  "reviews.csv",
                  ["Review", "Opened", "Closed", "Settled since the last one",
                   "Settling per week", "Still open", "What you decided", "Why"],
                  data.history.flatMap((r) => {
                    const facts = [
                      r.number,
                      r.opened_at,
                      r.closed_at ?? "",
                      r.numbers.scope.met_since_last_review,
                      r.numbers.velocity.per_week_3w,
                      r.numbers.velocity.remaining,
                    ];
                    return r.decisions.length === 0
                      ? [[...facts, "no changes", ""]]
                      : r.decisions.map((d) => [...facts, `${d.kind}: ${d.detail}`, d.reason]);
                  }),
                )
              }
            />
          </div>
          {data.history.map((r) => (
            <div className="card" key={r.id} style={{ padding: "10px 14px", marginBottom: 8 }}>
              <div style={{ display: "flex", gap: 10, alignItems: "baseline", flexWrap: "wrap" }}>
                <b>#{r.number}</b>
                <span className="help">
                  {fmtWhen(r.opened_at)} → {r.closed_at ? fmtWhen(r.closed_at) : "—"}
                </span>
                <span className="chip">
                  {r.decisions.length === 0 ? "no changes"
                    : `${r.decisions.length} decision${r.decisions.length === 1 ? "" : "s"}`}
                </span>
                <span className="sp" style={{ flex: 1 }} />
                <button className="small" onClick={() =>
                  setShowHistory((s) => ({ ...s, [r.number]: !s[r.number] }))}>
                  {showHistory[r.number] ? "less" : "more"}
                </button>
              </div>
              <p className="help" style={{ margin: "4px 0 0" }}>{r.headline}</p>
              <DecisionList decisions={r.decisions} />
              {showHistory[r.number] && r.narrative && (
                <p style={{ whiteSpace: "pre-wrap", fontSize: 13, marginTop: 8 }}>
                  <span className="help">CEO's narrative: </span>{r.narrative}
                </p>
              )}
            </div>
          ))}
        </>
      )}
    </div>
  );
}
