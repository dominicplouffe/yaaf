import { useState } from "react";
import { Dashboard, get, post, put } from "../../api";
import { fmtWhen, usePoll } from "../../hooks";
import { ProjectCtx } from "../../projectCtx";
import { Skeleton } from "../../ui";

/** A Code Factory's two record pages: its changes, every gate on each and who
    checked it; and its releases and incidents. Read from the platform's own
    rows — nothing here is an agent's account of itself. The one button is the
    chairman's own yes on a change touching a path he said needs a person. */

type Gate = { ok: boolean; detail: string };
type ChangeRow = {
  id: string; number: number | null; title: string; summary: string; branch: string;
  status: string; status_reason: string | null; lines: number; files_changed: number;
  head_sha: string | null; gates: Record<string, Gate>; checks: Record<string, string>;
  review: { by: string | null; verdict: string | null; note: string | null; returns: number };
  merged_sha: string | null; merged_at: string | null; created_at: string | null;
};
type ChangesView = {
  changes: ChangeRow[];
  merges_frozen: { flag_id: string; summary: string } | null;
  branch_protection: { ok: boolean; detail: string } | null;
};

const GATE_ORDER = ["serves", "size", "secrets", "tests", "packages", "protected", "checks",
  "scenarios", "review"];
const GATE_NAME: Record<string, string> = {
  serves: "Serves accepted scope", size: "Size", secrets: "No secrets",
  tests: "Tests not weakened", packages: "New packages checked",
  protected: "Paths that need you", checks: "CI on this commit",
  scenarios: "Hidden scenarios", review: "Signed by a reader",
};
const STATUS_TONE: Record<string, string> = {
  merged: "good", ready: "accent", merging: "accent", open: "", refused: "serious",
  closed: "", live: "good", watching: "accent", deploying: "accent", approved: "accent",
  proposed: "warn", rolled_back: "serious", failed: "serious",
};

function Badge({ status }: { status: string }) {
  return <span className={`badge ${STATUS_TONE[status] ?? ""}`}>{status.replace("_", " ")}</span>;
}

export function Changes({ ctx }: { ctx: ProjectCtx }) {
  const { projectId, seats } = ctx;
  const { data, reload } = usePoll(() => get<ChangesView>(`/observe/projects/${projectId}/changes`),
    10000, [projectId]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  if (!data) return <Skeleton />;
  const name = (id: string | null) => seats.find((s) => s.id === id)?.name ?? "a seat";

  async function approve(id: string) {
    setBusy(id); setError(null);
    try { await post(`/admin/changes/${id}/approve`); reload(); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(null); }
  }

  return (
    <div className="factory">
      {data.merges_frozen && (
        <div className="card warn-card" role="alert">
          <b>Merging is frozen.</b> {data.merges_frozen.summary}
        </div>
      )}
      {data.branch_protection && !data.branch_protection.ok && (
        <p className="help">GitHub could not be told to protect main ({data.branch_protection.detail}).
          The platform still refuses to count anything it did not merge, and freezes merging if main moves without it.</p>
      )}
      {error && <p className="help serious">{error}</p>}
      {data.changes.length === 0 && (
        <p className="help">No changes yet. A seat opens one for the task it holds; the platform merges it once every gate is green.</p>
      )}
      {data.changes.map((c) => {
        const needsYou = c.gates.protected && !c.gates.protected.ok
          && !c.gates.protected.detail.startsWith("the diff")
          && (c.status === "open" || c.status === "ready");
        return (
          <section className="card" key={c.id}>
            <div className="row-between">
              <h3><span className="muted mono">#{c.number}</span> {c.title}</h3>
              <Badge status={c.status} />
            </div>
            <p className="help mono">{c.branch} · {c.lines} lines · {c.files_changed} files
              {c.merged_sha ? ` · merged ${c.merged_sha.slice(0, 12)} ${fmtWhen(c.merged_at)}` : ""}</p>
            {c.status_reason && <p className="help">{c.status_reason}</p>}
            <table className="gates">
              <tbody>
                {GATE_ORDER.filter((g) => c.gates[g]).map((g) => (
                  <tr key={g}>
                    <td aria-label={c.gates[g].ok ? "passed" : "not yet"}>{c.gates[g].ok ? "✓" : "·"}</td>
                    <td>{GATE_NAME[g] ?? g}</td>
                    <td className="muted">{c.gates[g].detail}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {c.review.verdict && (
              <p className="help">{c.review.verdict === "signed" ? "Signed" : "Returned"} by {name(c.review.by)}
                {c.review.returns ? ` · returned ${c.review.returns}×` : ""}: {c.review.note}</p>
            )}
            {needsYou && (
              <button className="primary" disabled={busy === c.id} onClick={() => approve(c.id)}>
                Approve this commit ({c.head_sha?.slice(0, 7)})
              </button>
            )}
          </section>
        );
      })}
    </div>
  );
}

type ReleaseRow = {
  id: string; version: string; title: string; sha: string; notes: string; status: string;
  status_reason: string | null; probes: { ok?: number; failed?: number };
  deployed_at: string | null; live_at: string | null; rolled_back_at: string | null;
  created_at: string | null;
};
type IncidentRow = {
  id: string; number: number; title: string; status: string; opened_by: string;
  timeline: { at: string; what: string }[]; postmortem: string | null; opened_at: string | null;
};
type ReleasesView = { releases: ReleaseRow[]; incidents: IncidentRow[]; production: boolean };

export function Releases({ ctx }: { ctx: ProjectCtx }) {
  const { projectId } = ctx;
  const { data } = usePoll(() => get<ReleasesView>(`/observe/projects/${projectId}/releases`),
    15000, [projectId]);
  if (!data) return <Skeleton />;
  return (
    <div className="factory">
      {!data.production && (
        <p className="help">No production environment yet: set its deploy command and health URL in
          Settings → Environments. Until then the factory ships a repo, not a running service.</p>
      )}
      {data.incidents.length > 0 && <h3>Incidents</h3>}
      {data.incidents.map((i) => (
        <section className="card" key={i.id}>
          <div className="row-between">
            <h3>Incident {i.number} · {i.title}</h3>
            <span className={`badge ${i.status === "open" ? "serious" : "good"}`}>{i.status}</span>
          </div>
          <ol className="timeline">
            {i.timeline.map((e, n) => <li key={n}><span className="mono muted">{fmtWhen(e.at)}</span> {e.what}</li>)}
          </ol>
          {i.postmortem && <details><summary>Postmortem</summary><p className="prewrap">{i.postmortem}</p></details>}
        </section>
      ))}
      <h3>Releases</h3>
      {data.releases.length === 0 && <p className="help">Nothing released yet.</p>}
      {data.releases.map((r) => (
        <section className="card" key={r.id}>
          <div className="row-between">
            <h3>v{r.version} · {r.title}</h3>
            <Badge status={r.status} />
          </div>
          <p className="help mono">commit {r.sha.slice(0, 12)}
            {r.deployed_at ? ` · deployed ${fmtWhen(r.deployed_at)}` : ""}
            {r.status === "watching" ? ` · health ${r.probes.ok ?? 0} ok, ${r.probes.failed ?? 0} failed` : ""}
            {r.live_at ? ` · live ${fmtWhen(r.live_at)}` : ""}</p>
          {r.status_reason && <p className="help">{r.status_reason}</p>}
          {r.status === "proposed" && <p className="help">Waiting for your yes — it is in your Inbox.</p>}
          <details><summary>What ships</summary><p className="prewrap">{r.notes}</p></details>
        </section>
      ))}
    </div>
  );
}

type Env = { deploy: string; health_url: string };

/** Where a Code Factory's software runs. The platform deploys with this
    command, at a release's commit, with the secrets granted to the reserved
    scope "deploy" — which no seat holds — and reads the health URL for the
    canary watch. Set here, by a person, and never by a model. */
export function EnvironmentsCard({ projectId, dash }: { projectId: string; dash: Dashboard }) {
  const saved = ((dash.integrations ?? {}) as { environments?: { production?: Env } })
    .environments?.production;
  const [deploy, setDeploy] = useState(saved?.deploy ?? "");
  const [health, setHealth] = useState(saved?.health_url ?? "");
  const [said, setSaid] = useState<string | null>(null);
  async function save() {
    setSaid(null);
    try {
      await put(`/admin/projects/${projectId}/environments`,
        { production: { deploy: deploy.trim(), health_url: health.trim() } });
      setSaid("Saved. The next release deploys with this.");
    } catch (e) { setSaid(e instanceof Error ? e.message : String(e)); }
  }
  return (
    <div className="card">
      <label className="field" htmlFor="env-deploy">Production deploy command</label>
      <input id="env-deploy" className="mono" value={deploy} placeholder="fly deploy -a myapp --strategy canary"
        onChange={(e) => setDeploy(e.target.value)} />
      <label className="field" htmlFor="env-health">Health URL the canary guard reads</label>
      <input id="env-health" value={health} placeholder="https://myapp.example/healthz"
        onChange={(e) => setHealth(e.target.value)} />
      <p className="help">The credentials the command needs go in Secrets, granted to <code>deploy</code> alone.
        Agents never receive them; a bad deploy rolls back on its own.</p>
      <button className="primary" onClick={save} disabled={!deploy.trim() || !health.trim()}>Save</button>
      {said && <p className="help">{said}</p>}
    </div>
  );
}
