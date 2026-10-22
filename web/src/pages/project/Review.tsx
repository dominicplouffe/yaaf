import { useState } from "react";
import {
  Decision, get, post, requirementName, RequirementMeta, ReviewNumbers, ReviewRow, ReviewsView,
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

function NumbersTiles({ n }: { n: ReviewNumbers }) {
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
      <div className="tile">
        <div className="label">Budget</div>
        <div className="value">
          {Math.round((n.budget.spent_mtd / Math.max(n.budget.cap, 1)) * 100)}<small>%</small>
        </div>
        <div className="sub">
          {n.budget.spent_mtd.toLocaleString()} of {n.budget.cap.toLocaleString()} {n.budget.unit}
        </div>
      </div>
      <div className="tile">
        <div className="label">Audit coverage</div>
        <div className="value">{n.audit_coverage.sampled}</div>
        <div className="sub">
          sampled · {n.audit_coverage.failed} failed ·
          {" "}{n.audit_coverage.expired_unreviewed} expired unreviewed
        </div>
      </div>
      {n.velocity.worst_failure && (
        <div className="tile alert">
          <div className="label">Worst offender</div>
          <div className="value" style={{ fontSize: 18 }}>{n.velocity.worst_failure.key}</div>
          <div className="sub">{n.velocity.worst_failure.failure_load} dead runs</div>
        </div>
      )}
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

          <NumbersTiles n={open.numbers} />

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
          <div style={{ marginBottom: 12 }}>
            <b style={{ fontSize: 13 }}>What your CEO says it will do</b>
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
