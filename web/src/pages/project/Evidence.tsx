import { useState } from "react";
import { Link } from "react-router-dom";
import { EvidenceRow, get } from "../../api";
import { fmtWhen, usePoll } from "../../hooks";
import { downloadCsv, ExportCsv, Skeleton } from "../../ui";

/** The record of what this project has actually proved.

    This table used to sit at the bottom of the Work tab with a "Your audit"
    column on the end, and above it a queue that dealt every claim as a card
    with pass/fail buttons and scored the chairman on how many he had got
    through. That framing was wrong twice over. It made a *record* look like a
    backlog — 63 unread items and a 0% — for a job the product elsewhere calls
    optional and never finishing. And it asked him to re-judge rows the
    platform had already fetched, hashed and decided, writing his verdict into
    the same column the verifier had written, so afterwards nothing could tell
    "the fetcher confirmed this" from "a person believed it".

    What is left is what a record owes a reader: everything, newest first, with
    what the platform could and could not check, and a way to open one claim
    and see all of it. Nothing here has a verdict button. The things that
    genuinely need him — approvals, asks, the platform's own sampled audit —
    already ask, chase, and expire on their own clocks. */

const PROOF_FILTERS = [
  { key: "all", label: "All", match: (_r: EvidenceRow) => true },
  {
    key: "attested",
    label: "Platform-attested",
    match: (r: EvidenceRow) => r.provenance === "platform_attested",
  },
  {
    key: "claims",
    label: "Verified claims",
    match: (r: EvidenceRow) => r.provenance !== "platform_attested" && r.verification === "verified",
  },
  { key: "pending", label: "Pending", match: (r: EvidenceRow) => r.verification === "pending" },
  { key: "failed", label: "Failed checks", match: (r: EvidenceRow) => r.verification === "failed" },
] as const;

function verifyBadge(verification: string) {
  const cls =
    verification === "verified" || verification === "not_applicable"
      ? "good"
      : verification === "pending"
        ? "warn"
        : "critical";
  const label = verification === "not_applicable" ? "attested/internal" : verification;
  return <span className={`badge ${cls}`}>{label}</span>;
}

/** The sentence the old screen made you assemble by clicking five filter pills
    and reading their counts. It is the question a chairman actually has about
    proof — how much of what this project claims can be checked at all — and it
    is arithmetic, so it can be stated rather than assigned as homework. */
function Coverage({ rows }: { rows: EvidenceRow[] }) {
  if (rows.length === 0) return null;
  const verified = rows.filter((r) => r.verification === "verified").length;
  const failed = rows.filter((r) => r.verification === "failed").length;
  const pending = rows.filter((r) => r.verification === "pending").length;
  const uncheckable = rows.length - verified - failed - pending;
  const bits = [`${verified} the platform fetched and verified`];
  if (failed) bits.push(`${failed} it fetched and refused`);
  if (pending) bits.push(`${pending} not checked yet`);
  if (uncheckable) bits.push(`${uncheckable} nothing could check`);
  return (
    <p className="subtitle" style={{ marginBottom: 8 }}>
      <b>{rows.length} claim{rows.length === 1 ? "" : "s"}</b> · {bits.join(" · ")}.
    </p>
  );
}

const PAGE = 50;

export default function Evidence({ projectId }: { projectId: string }) {
  const { data, error } = usePoll(
    () => get<EvidenceRow[]>(`/observe/projects/${projectId}/evidence?limit=500`),
    15000,
  );
  const [filter, setFilter] = useState<(typeof PROOF_FILTERS)[number]["key"]>("all");
  const [showAll, setShowAll] = useState(false);
  const [open, setOpen] = useState<string | null>(null);

  if (error && !data) return <div className="error">{error}</div>;
  if (!data) return <Skeleton rows={4} />;

  const rows = data;
  const failedCount = rows.filter((r) => r.verification === "failed").length;
  const matching = rows.filter(PROOF_FILTERS.find((f) => f.key === filter)!.match);
  const shown = showAll ? matching : matching.slice(0, PAGE);

  /** The one thing to do about a claim, and it is a conversation rather than a
      verdict: the chairman's own channel is the boardroom, so a question about
      proof goes there with the claim already quoted. It appears only on a row
      he has opened — a link on every line is a call to action on every line,
      which is the queue this screen stopped being. */
  function askAbout(row: EvidenceRow): string {
    return (
      `About the evidence recorded ${fmtWhen(row.at)}`
      + (row.task_title ? ` for "${row.task_title}"` : "")
      + `:\n\n> ${row.description}\n\n`
      + `The platform's verdict on it is "${row.verification}". `
    );
  }

  return (
    <>
      <p className="subtitle">
        Every claim the agents made and what the platform could check about it, newest first.
        Only verified or platform-attested external evidence counts toward the drought and the
        external floor — agent claims are fetched, hashed and checked by the platform, never
        taken at their word. Read-only: nothing here is waiting on you, and the platform's own
        sampled audit is on the <Link to={`/projects/${projectId}/details/tasks`}>Tasks page</Link>.
      </p>
      <Coverage rows={rows} />
      {/* Failed proof is not a footnote: the platform fetched what an agent
          cited and did not find it. That work counted for nothing. */}
      {failedCount > 0 && failedCount >= rows.length / 4 && (
        <div className="error">
          <strong>{failedCount} of {rows.length} pieces of evidence failed verification.</strong>{" "}
          The platform fetched the source an agent cited and got a 404 or a mismatch. Failed proof
          counts for nothing — which is why the drought clock keeps running while work looks busy.
        </div>
      )}
      <div className="pills">
        {PROOF_FILTERS.map((f) => {
          const count = rows.filter(f.match).length;
          if (f.key !== "all" && count === 0) return null;
          return (
            <button
              key={f.key}
              className={filter === f.key ? "active" : ""}
              onClick={() => setFilter(f.key)}
            >
              {f.label} {count}
            </button>
          );
        })}
        <ExportCsv
          onClick={() =>
            downloadCsv(
              "evidence.csv",
              ["when", "kind", "provenance", "verification", "proves", "description", "source_url"],
              rows.map((r) => [
                r.at, r.kind, r.provenance, r.verification, r.task_title ?? "",
                r.description, r.source_url,
              ]),
            )
          }
        />
      </div>
      <table>
        <thead>
          <tr>
            <th>When</th>
            <th>Proof</th>
            <th style={{ width: "34%" }}>What happened</th>
            <th>What it proves</th>
            <th>Source</th>
          </tr>
        </thead>
        <tbody>
          {shown.map((row) => (
            <tr key={row.id}>
              <td>{fmtWhen(row.at)}</td>
              <td>
                {verifyBadge(row.verification)}
                <div className="help" style={{ marginTop: 2 }}>
                  {row.provenance.replaceAll("_", " ")} · {row.kind}
                </div>
              </td>
              <td>
                {open === row.id ? row.description : <div className="clamp">{row.description}</div>}
                <button
                  className="linkish"
                  onClick={() => setOpen(open === row.id ? null : row.id)}
                >
                  {open === row.id ? "less" : "everything behind this claim"}
                </button>
                {open === row.id && (
                  <dl className="kv" style={{ margin: "8px 0 0" }}>
                    {row.verifier_notes && (
                      <>
                        <dt>what the verifier found</dt>
                        <dd><pre className="block">{row.verifier_notes}</pre></dd>
                      </>
                    )}
                    {row.has_snapshot && (
                      <>
                        <dt>snapshot</dt>
                        <dd>
                          <a
                            href={`/observe/evidence/${row.id}/snapshot`}
                            target="_blank"
                            rel="noreferrer"
                          >
                            what the platform fetched, as it fetched it ↗
                          </a>
                        </dd>
                      </>
                    )}
                    <dt>ask about it</dt>
                    <dd>
                      <Link
                        to={`/projects/${projectId}/chat`}
                        state={{ draft: askAbout(row) }}
                      >
                        take this to Chat →
                      </Link>
                    </dd>
                  </dl>
                )}
              </td>
              <td>
                {row.task_title
                  ? <span className="help">{row.task_title}</span>
                  : <span className="help">standing work</span>}
              </td>
              <td>
                {row.source_url ? (
                  <a href={row.source_url} target="_blank" rel="noreferrer">link ↗</a>
                ) : (
                  "—"
                )}
              </td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td colSpan={5} className="help">
                Nothing recorded yet. Internal work never appears here — only the outside world
                acting.
              </td>
            </tr>
          )}
          {rows.length > 0 && shown.length === 0 && (
            <tr>
              <td colSpan={5} className="help">Nothing matches this filter.</td>
            </tr>
          )}
        </tbody>
      </table>
      {matching.length > shown.length && (
        <div style={{ marginTop: 8 }}>
          <button className="small" onClick={() => setShowAll(true)}>
            show all {matching.length} rows
          </button>
        </div>
      )}
    </>
  );
}
