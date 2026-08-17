import { useState } from "react";
import { AmendmentRow, ApprovalRow, get, post } from "../../api";
import { fmtUsd, fmtWhen, usePoll } from "../../hooks";
import { clockClass, downloadCsv, ExportCsv, fmtLeft } from "../../ui";
import type { ProjectCtx } from "../Project";

/** Which constitutional rule put this in front of the human, in one sentence. */
function whyYours(kind: string): string {
  switch (kind) {
    case "spend":
      return "It exceeds the approval threshold in the constitution — money above that line is never an agent's call.";
    case "legal":
      return "Anything with legal weight is a chairman decision by rule, regardless of amount.";
    case "owner_decision":
      return "The CEO judged this a direction change, not an execution detail.";
    case "tool_request":
      return "An agent is asking for a capability it does not have. Only you can grant one.";
    default:
      return "The constitution routes this kind of decision to the chairman.";
  }
}

function outcomeBadge(a: ApprovalRow) {
  if (a.status === "approved") return <span className="badge good">approved</span>;
  if (a.status === "denied") return <span className="badge critical">denied</span>;
  if (a.status === "expired") return <span className="badge critical">expired → denied</span>;
  return <span className="badge warn">{a.status}</span>;
}

function amendmentBadge(status: AmendmentRow["status"]) {
  const cls = status === "applied" ? "good" : status === "cancelled" ? "critical" : "warn";
  return <span className={`badge ${cls}`}>{status}</span>;
}

/** One decision, with the whole case laid out: what was asked, why it is
    yours, what each answer costs. Silence is an answer here, so it says so. */
function DecisionCard({
  approval,
  dash,
  onDecided,
}: {
  approval: ApprovalRow;
  dash: ProjectCtx["dash"];
  onDecided: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState("");

  const usdMode = dash.billing_mode === "api_key";
  const spent = dash.month_to_date.usd;
  const cap = dash.caps.monthly_usd_cap;
  const after = spent + (approval.amount_usd ?? 0);
  const overruns = usdMode && approval.amount_usd !== null && after > cap;

  async function decide(approved: boolean) {
    if (!approved && note.trim().length === 0) {
      setError("A denial needs a reason — the agent reads it and re-plans from it.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await post(`/admin/approvals/${approval.id}/decide`, {
        approve: approved,
        note: note.trim(),
      });
      onDecided();
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }

  return (
    <div className="card" style={{ marginBottom: 12 }}>
      <div className="pill-row" style={{ margin: 0 }}>
        <span className="chip accent">{approval.kind.replaceAll("_", " ")}</span>
        {approval.amount_usd !== null && approval.amount_usd > 0 && (
          <span className="chip">{fmtUsd(approval.amount_usd)}</span>
        )}
        <div className="spacer" />
        <span className={`clock ${clockClass(approval.expires_at)}`}>
          {fmtLeft(approval.expires_at)} → auto-deny
        </span>
      </div>
      <h3 style={{ margin: "10px 0 8px", fontSize: 15 }}>{approval.title}</h3>
      <dl className="kv">
        <dt>The CEO's case</dt>
        <dd style={{ whiteSpace: "pre-wrap" }}>{approval.body}</dd>
        <dt>Why it's yours</dt>
        <dd>{whyYours(approval.kind)}</dd>
        {usdMode && approval.amount_usd !== null && approval.amount_usd > 0 && (
          <>
            <dt>If you approve</dt>
            <dd>
              {fmtUsd(spent)} spent this month becomes {fmtUsd(after)} of {fmtUsd(cap)}.
              {overruns && (
                <strong style={{ color: "var(--critical)" }}>
                  {" "}That is over the cap — the project pauses itself when it lands.
                </strong>
              )}
            </dd>
          </>
        )}
        <dt>If you deny or stay silent</dt>
        <dd>
          The request is refused and the CEO re-plans around it. Silence counts as a denial when
          the clock runs out — a vacationing chairman degrades the project, never deadlocks it.
        </dd>
      </dl>
      <input
        style={{ marginTop: 12 }}
        value={note}
        placeholder="Your note — the agent reads this (required to deny)"
        onChange={(e) => setNote(e.target.value)}
      />
      {error && <div className="error">{error}</div>}
      <div className="actions">
        <button className="primary" disabled={busy} onClick={() => decide(true)}>
          ✓ Approve
        </button>
        <button className="danger" disabled={busy} onClick={() => decide(false)}>
          ✕ Deny
        </button>
        <span className="help">No undo — the decision is recorded and disclosed.</span>
      </div>
    </div>
  );
}

/** Everything you decided, and everything that decided itself while you were
    away. Approvals used to be a tab that stood empty most days; the live ones
    now travel with you in the strip, and this is where they come to rest. */
export default function Decisions({ ctx }: { ctx: ProjectCtx }) {
  const { projectId, dash, approvals, reloadApprovals } = ctx;
  const { data: amendments } = usePoll(
    () => get<AmendmentRow[]>(`/admin/projects/${projectId}/amendments`),
    15000,
  );

  const pending = approvals
    .filter((a) => a.status === "pending")
    .sort((a, b) => a.expires_at.localeCompare(b.expires_at));
  const history = approvals
    .filter((a) => a.status !== "pending")
    .sort((a, b) => (b.decided_at ?? "").localeCompare(a.decided_at ?? ""));
  const rows = amendments ?? [];

  return (
    <>
      <p className="subtitle">
        Approvals expire to <strong>DENY</strong>. That is the whole design: a chairman who never
        logs in slows the company down, but can never wedge it.
      </p>

      {pending.length > 0 && (
        <>
          <h2 className="zone hot">
            Waiting on you <span className="cnt">{pending.length}</span>
          </h2>
          {pending.map((a) => (
            <DecisionCard
              key={a.id}
              approval={a}
              dash={dash}
              onDecided={() => {
                reloadApprovals();
                ctx.reload();
              }}
            />
          ))}
        </>
      )}

      <h2 className="zone">
        Approvals — the record <span className="cnt">{history.length}</span>
      </h2>
      {history.length === 0 ? (
        <p className="help">Nothing decided yet.</p>
      ) : (
        <>
          <div className="pills">
            <ExportCsv
              onClick={() =>
                downloadCsv(
                  "decisions.csv",
                  ["decided", "title", "kind", "amount_usd", "outcome", "note"],
                  history.map((a) => [
                    a.decided_at,
                    a.title,
                    a.kind,
                    a.amount_usd,
                    a.status,
                    a.decision_note,
                  ]),
                )
              }
            />
          </div>
          <table>
            <thead>
              <tr>
                <th>Decided</th>
                <th style={{ width: "34%" }}>What was asked</th>
                <th>Kind</th>
                <th className="num">Amount</th>
                <th>Outcome</th>
                <th style={{ width: "26%" }}>Your note</th>
              </tr>
            </thead>
            <tbody>
              {history.map((a) => (
                <tr key={a.id}>
                  <td className="mono">{a.decided_at ? fmtWhen(a.decided_at) : "—"}</td>
                  <td>{a.title}</td>
                  <td className="mono">{a.kind.replaceAll("_", " ")}</td>
                  <td className="num">{a.amount_usd ? fmtUsd(a.amount_usd) : "—"}</td>
                  <td>{outcomeBadge(a)}</td>
                  <td>{a.decision_note || <span className="help">no note</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      <h2 className="zone">
        Rule changes <span className="cnt">{rows.length}</span>
      </h2>
      <p className="subtitle" style={{ marginBottom: 8 }}>
        Every rule change is in force on the next agent call, disclosed to the CEO with the
        reason you gave, and kept here — so a limit never moves without a record of who moved
        it and why.
      </p>
      {rows.length === 0 ? (
        <p className="help">
          The constitution stands as it was frozen at creation. Propose a change in{" "}
          Settings → Rules.
        </p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Status</th>
              <th style={{ width: "30%" }}>Reason you gave</th>
              <th style={{ width: "28%" }}>Changes</th>
              <th>Proposed</th>
              <th>Effective</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((a) => (
              <tr key={a.id}>
                <td>
                  {amendmentBadge(a.status)}
                  {a.disclosed && <div className="help">disclosed to CEO</div>}
                </td>
                <td>{a.reason}</td>
                <td>
                  <pre className="block">{JSON.stringify(a.changes, null, 1)}</pre>
                </td>
                <td className="mono">{fmtWhen(a.proposed_at)}</td>
                <td className="mono">{fmtWhen(a.effective_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}
