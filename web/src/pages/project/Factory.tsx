import { useState } from "react";
import { Link } from "react-router-dom";
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
  findings?: { preview?: { sha?: string; screenshot_uri?: string; url?: string } };
};
type ChangesView = {
  changes: ChangeRow[];
  merges_frozen: { flag_id: string; summary: string } | null;
  branch_protection: { ok: boolean; detail: string } | null;
};

const GATE_ORDER = ["serves", "size", "secrets", "tests", "packages", "protected", "ci_config",
  "scan", "checks", "coverage", "preview", "scenarios", "review"];
const GATE_NAME: Record<string, string> = {
  serves: "Serves accepted scope", size: "Size", secrets: "No secrets",
  tests: "Tests not weakened", packages: "New packages checked",
  protected: "Paths that need you", ci_config: "CI left alone", scan: "Platform scan",
  coverage: "Changed lines tested", preview: "Preview is up", checks: "CI on this commit",
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
  const { data: setup } = usePoll(
    () => get<FactorySetup>(`/admin/projects/${projectId}/factory/setup`), 30000, [projectId]);
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
      {data.changes.length === 0 && (setup && (!setup.repo || !setup.push_token.set
        || setup.push_token.missing.length > 0) ? (
        <div className="card warn-card">
          <b>Nothing can be built yet.</b>{" "}
          {!setup.repo ? "This factory has no repository to push to."
            : "The seats that build have no GitHub key to push with."}{" "}
          <Link to={`/projects/${projectId}/settings?s=integrations`}>Finish it in Settings → Integrations</Link>.
          The crew can still write and agree the spec meanwhile.
        </div>
      ) : (
        <p className="help">No changes yet. A seat opens one for the task it holds; the platform merges it once every gate is green.</p>
      ))}
      {data.changes.map((c) => {
        const waitsOnYou = (g: Gate | undefined) => !!g && !g.ok && g.detail.includes("chairman");
        const needsYou = (waitsOnYou(c.gates.protected) || waitsOnYou(c.gates.ci_config))
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
            {c.findings?.preview?.screenshot_uri && c.findings.preview.sha === c.head_sha && (
              <figure className="preview-shot">
                <img src={`/observe/changes/${c.id}/preview.png`}
                  alt={`The preview of #${c.number} as the platform saw it`} loading="lazy" />
                <figcaption className="help">The preview of this commit, photographed by the platform.</figcaption>
              </figure>
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
  created_at: string | null; environment: string; sbom: boolean;
  provenance_sig: string | null;
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
          <p className="help mono">{r.environment} · commit {r.sha.slice(0, 12)}
            {r.provenance_sig ? " · provenance signed" : ""}{r.sbom ? " · SBOM kept" : ""}
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
const ENVS: { key: "production" | "staging" | "preview"; label: string; deploy: string;
  health: string; note: string }[] = [
  { key: "production", label: "Production", deploy: "fly deploy -a myapp --strategy canary",
    health: "https://myapp.example/healthz",
    note: "Deployed by the platform with secrets granted to deploy alone." },
  { key: "staging", label: "Staging (optional)", deploy: "fly deploy -a myapp-staging",
    health: "https://staging.myapp.example/healthz",
    note: "Every release runs here first; your approval, when the dial asks, comes after." },
  { key: "preview", label: "Preview per change (optional)",
    deploy: "fly deploy -a myapp-pr-$YAAF_CHANGE_NUMBER",
    health: "https://pr-{number}.myapp.example/healthz",
    note: "Builds unsigned code, so it gets only secrets granted to preview — never production's." },
];

/** Where a Code Factory's software runs. The platform deploys with these
    commands, at the right commit, with secrets granted to the reserved scopes
    "deploy" or "preview" — which no seat holds — and reads each health URL.
    Set here, by a person, and never by a model. */
export function EnvironmentsCard({ projectId, dash }: { projectId: string; dash: Dashboard }) {
  const saved = ((dash.integrations ?? {}) as { environments?: Record<string, Env> })
    .environments ?? {};
  const [envs, setEnvs] = useState<Record<string, Env>>(() => Object.fromEntries(
    ENVS.map((e) => [e.key, saved[e.key] ?? { deploy: "", health_url: "" }])));
  const [said, setSaid] = useState<string | null>(null);
  const set = (key: string, field: keyof Env, value: string) =>
    setEnvs((was) => ({ ...was, [key]: { ...was[key], [field]: value } }));
  async function save() {
    setSaid(null);
    const body = Object.fromEntries(Object.entries(envs)
      .filter(([, e]) => e.deploy.trim() && e.health_url.trim())
      .map(([k, e]) => [k, { deploy: e.deploy.trim(), health_url: e.health_url.trim() }]));
    try {
      await put(`/admin/projects/${projectId}/environments`, body);
      setSaid("Saved. The next deploy uses these.");
    } catch (e) { setSaid(e instanceof Error ? e.message : String(e)); }
  }
  return (
    <div className="card">
      {ENVS.map((e) => (
        <div key={e.key} style={{ marginBottom: "var(--s4)" }}>
          <h3>{e.label}</h3>
          <label className="field" htmlFor={`env-${e.key}-deploy`}>Deploy command</label>
          <input id={`env-${e.key}-deploy`} className="mono" value={envs[e.key].deploy}
            placeholder={e.deploy} onChange={(ev) => set(e.key, "deploy", ev.target.value)} />
          <label className="field" htmlFor={`env-${e.key}-health`}>Health URL</label>
          <input id={`env-${e.key}-health`} value={envs[e.key].health_url} placeholder={e.health}
            onChange={(ev) => set(e.key, "health_url", ev.target.value)} />
          <p className="help">{e.note}</p>
        </div>
      ))}
      <button className="primary" onClick={save}
        disabled={!envs.production.deploy.trim() || !envs.production.health_url.trim()}>Save</button>
      {said && <p className="help">{said}</p>}
    </div>
  );
}

type FactorySetup = {
  platform_token: boolean;
  repo: string | null;
  repo_wanted: string | null;
  repo_wanted_error: string | null;
  branch_protection: { ok: boolean; detail: string } | null;
  push_token: { set: boolean; granted: string[]; missing: string[] };
  notifications: { hook: { ok: boolean; detail: string } | null;
    last_delivery_at: string | null; polled_every_s: number };
  environments: string[];
};

/** Everything a Code Factory needs from GitHub, on one card, in the order it
    is needed. Each of these used to be found missing by an agent's failed
    run: the platform's own key, the repo, the key the seats push with, and
    whether GitHub can reach this install at all. */
export function FactoryGithub({ projectId }: { projectId: string }) {
  const { data, reload } = usePoll(
    () => get<FactorySetup>(`/admin/projects/${projectId}/factory/setup`), 30000, [projectId]);
  const [platformKey, setPlatformKey] = useState("");
  const [repo, setRepo] = useState<string | null>(null);
  const [pushKey, setPushKey] = useState("");
  const [said, setSaid] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  if (!data) return <Skeleton />;
  // The name chosen when the project was made, until somebody types another.
  const repoName = repo ?? data.repo_wanted ?? "";

  async function act(what: () => Promise<unknown>, done: string, clear?: () => void) {
    setBusy(true); setSaid(null);
    try { await what(); setSaid({ ok: true, text: done }); clear?.(); reload(); }
    catch (e) { setSaid({ ok: false, text: e instanceof Error ? e.message : String(e) }); }
    finally { setBusy(false); }
  }
  const mark = (ok: boolean) => <span className={`badge ${ok ? "good" : "warn"}`}>{ok ? "done" : "to do"}</span>;
  const hook = data.notifications.hook;

  return (
    <div className="card factory-github">
      <h3>{mark(data.platform_token)} 1. The platform's GitHub key</h3>
      {data.platform_token ? (
        <p className="help">Connected. The platform creates the repo, opens pull requests and
          merges with it. No agent ever holds it.</p>
      ) : <>
        <p className="help">A GitHub token for the account the platform acts as: it creates the
          repo, opens pull requests, reads CI and merges. Fine-grained: Contents, Pull requests,
          Administration (to protect main) and Webhooks, read and write. Used for every project
          on this install.</p>
        <input type="password" value={platformKey} placeholder="github_pat_…"
          onChange={(e) => setPlatformKey(e.target.value)} aria-label="Platform GitHub key" />
        <button className="primary" disabled={busy || platformKey.trim().length < 8}
          onClick={() => act(() => put("/admin/settings/github", { token: platformKey.trim() }),
          data.repo_wanted && !data.repo
            ? `Saved. Making ${data.repo_wanted} now.` : "Saved.", () => setPlatformKey(""))}>
          Check and save</button>
      </>}

      <h3>{mark(!!data.repo)} 2. The repository</h3>
      {data.repo ? (
        <p className="help">Builds in <code>{data.repo}</code>.
          {data.branch_protection && !data.branch_protection.ok &&
            ` GitHub could not be told to protect main (${data.branch_protection.detail}); the platform still freezes merging if main moves without it.`}</p>
      ) : <>
        <p className="help">
          {data.repo_wanted && !data.platform_token
            ? `It will be made as ${data.repo_wanted} the moment the key above is saved. `
            : ""}
          A bare name creates a new private repo; owner/name uses one you made (it should be
          empty for a new build).</p>
        {data.repo_wanted_error && (
          <p className="help serious">GitHub would not make {data.repo_wanted}: {data.repo_wanted_error}.
            Change the name, or the key's access, and connect again.</p>
        )}
        <input value={repoName} placeholder="my-app or owner/my-app" disabled={!data.platform_token}
          onChange={(e) => setRepo(e.target.value)} aria-label="Repository" />
        <button className="primary" disabled={busy || !data.platform_token || !repoName.trim()}
          onClick={() => act(() => post(`/admin/projects/${projectId}/github`, { repo: repoName.trim() }),
            "Connected, and main is protected.")}>Connect</button>
      </>}

      <h3>{mark(data.push_token.set && data.push_token.missing.length === 0)} 3. The agents' GitHub key</h3>
      <p className="help">
        {data.push_token.set && data.push_token.missing.length === 0
          ? `Held for ${data.push_token.granted.join(", ")}. They push branches with it; only the platform merges.`
          : "A second token, for the seats that build: they push branches with it and never merge. "
            + "Fine-grained, this repo only: Contents and Workflows, read and write. "
            + "A separate account is best, so GitHub shows who wrote what."}
      </p>
      {!(data.push_token.set && data.push_token.missing.length === 0) && <>
        <input type="password" value={pushKey} placeholder="github_pat_…" disabled={!data.repo}
          onChange={(e) => setPushKey(e.target.value)} aria-label="Agents' GitHub key" />
        <button className="primary" disabled={busy || !data.repo || pushKey.trim().length < 8}
          onClick={() => act(() => put(`/admin/projects/${projectId}/factory/push-token`,
            { token: pushKey.trim() }), "Saved: it can push to the repo.", () => setPushKey(""))}>
          Check and save</button>
      </>}

      <h3>{mark(true)} 4. Hearing from GitHub</h3>
      <p className="help">
        {hook?.ok
          ? "GitHub tells this install the moment CI finishes or a branch moves."
          : `The platform checks GitHub every ${Math.round(data.notifications.polled_every_s / 60) || 1} minute` +
            `${data.notifications.polled_every_s >= 120 ? "s" : ""} for CI results and new pushes. ` +
            "Nothing to set up. Give this install a public https address (YAAF_PUBLIC_BASE_URL) " +
            "and reconnect the repo to have GitHub notify it instantly."}
        {data.notifications.last_delivery_at &&
          ` Last notification: ${fmtWhen(data.notifications.last_delivery_at)}.`}
      </p>
      {said && <p className={`help${said.ok ? "" : " serious"}`}>{said.text}</p>}
    </div>
  );
}
