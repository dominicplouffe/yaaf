import { useState } from "react";
import { ApprovalRow, Dashboard, get, post } from "../../api";
import { fmtUsd, fmtWhen, usePoll } from "../../hooks";
import { clockClass, fmtLeft, fmtUnit } from "../../ui";

/** Why this approval reached a human: every card names the constitutional rule
    that routed it here — things reach you because a rule says so. */
function whyYours(approval: ApprovalRow, threshold: number): string {
  switch (approval.kind) {
    case "spend":
      return `Spend over your $${threshold.toLocaleString()} threshold — constitution, approvals`;
    case "legal":
      return "Legal ambiguity — the constitution routes it to you, not to an agent's judgment";
    case "owner_decision":
      return "Owner-only decision — the kind of call the constitution reserves for you";
    case "tool_request":
      return "New capability request — only you grant agents new tools";
    default:
      return "Routed to you by the constitution";
  }
}

/** One pending approval as a full decision card: the ask, the CEO's case, the
    rule that routed it here, the clock, what happens either way — and the note
    as a real field, because the agent reads it. Never a window.prompt(). */
function DecisionCard({
  approval,
  dash,
  onDecided,
}: {
  approval: ApprovalRow;
  dash: Dashboard;
  onDecided: () => void;
}) {
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const threshold = Number(dash.constitution["approval_threshold_usd"] ?? 250);
  const usdMode = dash.billing_mode === "api_key";
  const remaining = usdMode
    ? Math.max(0, dash.caps.monthly_usd_cap - dash.month_to_date.usd)
    : null;
  const overBudget =
    approval.amount_usd !== null && remaining !== null && approval.amount_usd > remaining;

  async function decide(approve: boolean) {
    if (!approve && !note.trim()) {
      setError("A denial needs a reason — the CEO reads it and replans.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await post(`/admin/approvals/${approval.id}/decide`, { approve, note: note.trim() });
      onDecided();
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }

  return (
    <div className="card" style={{ marginBottom: 10 }}>
      <div style={{ display: "flex", gap: 10, alignItems: "baseline", flexWrap: "wrap" }}>
        <strong style={{ fontSize: 15 }}>{approval.title}</strong>
        <span className="chip accent">{approval.kind.replaceAll("_", " ")}</span>
        {approval.amount_usd !== null && (
          <span className="mono" style={{ fontWeight: 650 }}>{fmtUsd(approval.amount_usd)}</span>
        )}
        <div className="spacer" />
        <span className={`clock ${clockClass(approval.expires_at)}`}>
          expires {fmtWhen(approval.expires_at)} → auto-deny · {fmtLeft(approval.expires_at)}
        </span>
      </div>

      <dl className="kv" style={{ margin: "12px 0" }}>
        <dt>The CEO's case</dt>
        <dd style={{ whiteSpace: "pre-wrap" }}>{approval.body || "—"}</dd>
        <dt>Why it's yours</dt>
        <dd>{whyYours(approval, threshold)}</dd>
        <dt>If you approve</dt>
        <dd>
          {approval.kind === "spend" && approval.amount_usd !== null ? (
            <>
              The CEO may spend up to {fmtUsd(approval.amount_usd)} on this, once. It lands in the
              ledger and counts against the monthly cap
              {overBudget && remaining !== null && (
                <strong style={{ color: "var(--warning)" }}>
                  {" "}— note: this exceeds the {fmtUnit(remaining, "usd")} left this month; the
                  project pauses itself at 100%
                </strong>
              )}
              .
            </>
          ) : (
            "The agent proceeds, and your note travels with the go-ahead."
          )}
        </dd>
        <dt>If you deny / stay silent</dt>
        <dd>
          Denied — the CEO reads your note and replans. Silence denies automatically at the
          deadline.
        </dd>
      </dl>

      <input
        value={note}
        placeholder='Your note — the CEO reads this. e.g. "Approved, but wait until the fix ships"'
        onChange={(e) => setNote(e.target.value)}
        style={{ marginBottom: 10 }}
      />
      {error && <div className="error">{error}</div>}
      <div className="actions" style={{ marginTop: 0 }}>
        <button className="primary" disabled={busy} onClick={() => decide(true)}>
          ✓ Approve{approval.amount_usd !== null ? ` ${fmtUsd(approval.amount_usd)}` : ""}
        </button>
        <button className="danger" disabled={busy} onClick={() => decide(false)}>
          ✕ Deny
        </button>
        <span className="help">decisions are recorded and disclosed — there is no undo</span>
      </div>
    </div>
  );
}

export default function Approvals({ projectId, dash }: { projectId: string; dash: Dashboard }) {
  const { data: approvals, error, reload } = usePoll(
    () => get<ApprovalRow[]>(`/observe/projects/${projectId}/approvals`),
    6000,
  );
  if (error) return <div className="error">{error}</div>;

  const pending = (approvals ?? []).filter((a) => a.status === "pending");
  const decided = (approvals ?? []).filter((a) => a.status !== "pending");

  const statusBadge = (status: string) => {
    const cls = status === "approved" ? "good" : status === "pending" ? "warn" : "critical";
    const label = status === "expired" ? "expired → denied" : status;
    return <span className={`badge ${cls}`}>{label}</span>;
  };

  return (
    <>
      <p className="subtitle">
        The things that legitimately reach you: big spends, legal ambiguity, owner-only decisions —
        framed as recommendations. Undecided approvals expire to DENY, so silence never deadlocks
        the project.
      </p>
      {pending.length > 0 && (
        <h2 className="zone hot" style={{ marginTop: 6 }}>
          Waiting on you <span className="cnt">{pending.length}</span>
        </h2>
      )}
      {pending.map((approval) => (
        <DecisionCard key={approval.id} approval={approval} dash={dash} onDecided={reload} />
      ))}

      <h2>History</h2>
      <table>
        <thead>
          <tr>
            <th>Decided</th>
            <th>Title</th>
            <th>Kind</th>
            <th className="num">Amount</th>
            <th>Outcome</th>
            <th style={{ width: "30%" }}>Your note</th>
          </tr>
        </thead>
        <tbody>
          {decided.map((approval) => (
            <tr key={approval.id}>
              <td className="mono">{fmtWhen(approval.decided_at)}</td>
              <td><strong>{approval.title}</strong></td>
              <td className="mono">{approval.kind}</td>
              <td className="num">{fmtUsd(approval.amount_usd)}</td>
              <td>{statusBadge(approval.status)}</td>
              <td>
                {approval.decision_note ? (
                  `“${approval.decision_note}”`
                ) : approval.status === "expired" ? (
                  <span className="help mono">no decision before the deadline</span>
                ) : (
                  <span className="help">—</span>
                )}
              </td>
            </tr>
          ))}
          {decided.length === 0 && pending.length === 0 && (
            <tr>
              <td colSpan={6} className="help">
                Nothing yet — agents file approvals only for what the constitution says is yours.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </>
  );
}
