/** "What they did": the run behind a message or a result, opened in place.

    A CEO message says what the CEO concluded; a result says what was made.
    Neither says what was done to get there, and "how did you arrive at that?"
    used to be a trip to Activity › Runs and a raw log. This is the run's own
    record — the platform's rows, in order — one click under the thing it
    explains, and closed until asked for. */
import { useState } from "react";
import { Link } from "react-router-dom";
import { get, RunDid } from "./api";
import { fmtUsd } from "./hooks";
import { fmtAgo } from "./ui";
import { Icon } from "./ui/Icon";

export function WhatTheyDid({ runId, projectId, label = "What they did" }: {
  runId: string; projectId: string; label?: string;
}) {
  const [open, setOpen] = useState(false);
  const [did, setDid] = useState<RunDid | null>(null);
  const [error, setError] = useState<string | null>(null);
  async function toggle() {
    if (!open && did === null) {
      try {
        setDid(await get<RunDid>(`/observe/runs/${runId}/did`));
      } catch (e) {
        setError((e as Error).message);
      }
    }
    setOpen(!open);
  }
  return (
    <div className="did">
      <button type="button" className="linkish did-toggle" onClick={toggle} aria-expanded={open}>
        <Icon name={open ? "down" : "right"} size={12} /> {label}
      </button>
      {open && (
        <div className="did-body">
          {error && <div className="help" style={{ color: "var(--critical)" }}>{error}</div>}
          {!did && !error && <div className="help">Loading…</div>}
          {did && (
            <>
              <div className="help did-meta">
                {did.started_at ? `Started ${fmtAgo(did.started_at)}` : "Queued"}
                {did.model ? ` · ${did.model}` : ""}
                {did.cost_usd !== null ? ` · ${fmtUsd(did.cost_usd)}` : ""}
                {did.status !== "done" && did.status !== "running" ? ` · ${did.status.replaceAll("_", " ")}` : ""}
              </div>
              {did.steps.length === 0 ? (
                <p className="help">It changed nothing on the platform — it read, thought, and wrote what you see.</p>
              ) : (
                <ol className="did-steps">
                  {did.steps.map((s, i) => (
                    <li key={i} className={`did-${s.kind}`}>
                      <span className="mono help">{new Date(s.at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span>
                      <span>{s.text}</span>
                    </li>
                  ))}
                  {did.more_steps > 0 && <li className="help">and {did.more_steps} more</li>}
                </ol>
              )}
              {did.tools.length > 0 && (
                <p className="help did-tools">
                  Reached for: {did.tools.map((t) => `${t.tool}${t.times > 1 ? ` ×${t.times}` : ""}`).join(", ")}
                </p>
              )}
              <Link className="help" to={`/projects/${projectId}/details/activity/runs?run=${encodeURIComponent(did.run_id)}`}>
                View the run →
              </Link>
            </>
          )}
        </div>
      )}
    </div>
  );
}
