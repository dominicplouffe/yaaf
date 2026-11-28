import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Dashboard, get, post, put } from "../../api";
import { fmtWhen, usePoll } from "../../hooks";
import { ProjectCtx } from "../../projectCtx";
import { Drawer, Skeleton } from "../../ui";

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
          <Link to={`/projects/${projectId}/settings?s=factory`}>Finish it in Settings → Code Factory</Link>.
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
        <p className="help">No production environment yet. The team sets its deploy command and health URL,
          or you can in Settings → Code Factory → Environments. Until then the factory ships a repo,
          not a running service.</p>
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

type Env = { deploy: string; health_url: string; set_by?: string | null };
const ENVS: { key: "production" | "staging" | "preview"; label: string; required: boolean; deploy: string;
  health: string; note: string }[] = [
  { key: "production", label: "Production", required: true, deploy: "fly deploy -a myapp --strategy canary",
    health: "https://myapp.example/healthz",
    note: "Deployed by the platform with secrets granted to deploy alone." },
  { key: "staging", label: "Staging", required: false, deploy: "fly deploy -a myapp-staging",
    health: "https://staging.myapp.example/healthz",
    note: "Every release runs here first; your approval, when the dial asks, comes after." },
  { key: "preview", label: "Preview per change", required: false,
    deploy: "fly deploy -a myapp-pr-$YAAF_CHANGE_NUMBER",
    health: "https://pr-{number}.myapp.example/healthz",
    note: "Builds unsigned code, so it gets only secrets granted to preview, never production's." },
];

/** Where a Code Factory's software runs, one row per environment. The
    platform deploys with these commands, at the right commit, with secrets
    granted to the reserved scopes "deploy" or "preview" — which no seat holds
    — and reads each health URL. Set here or by a seat with environment_set.
    Unless releases are autonomous, a production or staging command a seat
    wrote runs only after the chairman approves a release that shows it. */
export function EnvironmentsCard({ projectId, dash, onSaved }: {
  projectId: string; dash: Dashboard; onSaved?: () => void;
}) {
  const saved = ((dash.integrations ?? {}) as { environments?: Record<string, Env> })
    .environments ?? {};
  const [open, setOpen] = useState<(typeof ENVS)[number] | null>(null);
  return (
    <div className="set-card">
      <div className="table-wrap">
        <table className="pset-table">
          <thead><tr><th>Environment</th><th>Deploys with</th><th>Health check</th><th /></tr></thead>
          <tbody>
            {ENVS.map((e) => {
              const env = saved[e.key];
              return (
                <tr key={e.key}>
                  <td><b>{e.label}</b><span className="sub">{e.required ? "Needed before the first release" : "Optional"}</span></td>
                  <td>{env?.deploy ? <code className="pset-code">{env.deploy}</code> : <span className="help">Not set</span>}
                    {env?.set_by && <span className="sub">Written by {env.set_by}</span>}</td>
                  <td>{env?.health_url ? <span className="mono pset-url">{env.health_url}</span>
                    : e.required ? <span className="badge warn">Not set</span> : <span className="help">—</span>}</td>
                  <td style={{ textAlign: "right" }}>
                    <button type="button" className={`small${!env?.deploy && e.required ? " primary" : ""}`}
                      onClick={() => setOpen(e)}>{env?.deploy ? "Edit" : "Set up"}</button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {open && <EnvDrawer projectId={projectId} env={open} saved={saved}
        onClose={() => setOpen(null)} onSaved={() => { setOpen(null); onSaved?.(); }} />}
    </div>
  );
}

function EnvDrawer({ projectId, env, saved, onClose, onSaved }: {
  projectId: string;
  env: (typeof ENVS)[number];
  saved: Record<string, Env>;
  onClose: () => void;
  onSaved: () => void;
}) {
  const was = saved[env.key] ?? { deploy: "", health_url: "" };
  const [deploy, setDeploy] = useState(was.deploy);
  const [health, setHealth] = useState(was.health_url);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const filled = !!deploy.trim() && !!health.trim();
  const cleared = !deploy.trim() && !health.trim();
  async function save() {
    setBusy(true);
    setError(null);
    // The endpoint takes every environment at once; the others are sent as
    // they are saved, this one as edited — or left out, which clears it.
    const body: Record<string, { deploy: string; health_url: string }> = {};
    for (const e of ENVS) {
      const src = e.key === env.key ? { deploy, health_url: health } : saved[e.key];
      if (src?.deploy?.trim() && src.health_url?.trim())
        body[e.key] = { deploy: src.deploy.trim(), health_url: src.health_url.trim() };
    }
    try {
      await put(`/admin/projects/${projectId}/environments`, body);
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <Drawer title={env.label} sub={env.note} onClose={onClose}
      footer={<>
        <span className="grow help">The next deploy uses it.</span>
        <button type="button" onClick={onClose}>Cancel</button>
        <button type="button" className="primary" disabled={busy || (!filled && !(cleared && !env.required))}
          onClick={() => void save()}>{busy ? "Saving…" : cleared && !env.required ? "Clear" : "Save"}</button>
      </>}>
      <label className="field" htmlFor={`env-${env.key}-deploy`}>Deploy command</label>
      <input id={`env-${env.key}-deploy`} className="mono" value={deploy} placeholder={env.deploy}
        onChange={(e) => setDeploy(e.target.value)} />
      <label className="field" htmlFor={`env-${env.key}-health`}>Health URL</label>
      <input id={`env-${env.key}-health`} value={health} placeholder={env.health}
        onChange={(e) => setHealth(e.target.value)} />
      <p className="help">Read after each deploy; failed reads during the canary watch roll the release back.</p>
      {was.set_by && env.key !== "preview" && (
        <p className="help">Written by {was.set_by}. Unless releases are autonomous, it runs once you
          approve the next release, which shows it to you. Saving here approves it now.</p>)}
      {error && <div className="error">{error}</div>}
    </Drawer>
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

/** Everything a Code Factory needs from GitHub, in the order it is needed,
    as steps. Each of these used to be found missing by an agent's failed run:
    the platform's own key, the repo, the key the seats push with, and whether
    GitHub can reach this install at all. A step done is one line; only the
    step to do now is open; the ones after it say what they wait for. */
export function FactoryGithub({ projectId, onCount }: {
  projectId: string;
  /** How many of the four are done, for the section's state line. */
  onCount?: (done: number) => void;
}) {
  const { data, reload } = usePoll(
    () => get<FactorySetup>(`/admin/projects/${projectId}/factory/setup`), 30000, [projectId]);
  const [platformKey, setPlatformKey] = useState("");
  const [repo, setRepo] = useState<string | null>(null);
  const [pushKey, setPushKey] = useState("");
  const [said, setSaid] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [changing, setChanging] = useState<number | null>(null);
  const pushOk = !!data && data.push_token.set && data.push_token.missing.length === 0;
  const done = data ? [data.platform_token, !!data.repo, pushOk, true] : [];
  const count = done.filter(Boolean).length;
  useEffect(() => { if (data) onCount?.(count); }, [count, !!data]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!data) return <Skeleton />;
  // The name chosen when the project was made, until somebody types another.
  const repoName = repo ?? data.repo_wanted ?? "";
  const now = done.findIndex((d) => !d);

  async function act(what: () => Promise<unknown>, text: string, clear?: () => void) {
    setBusy(true); setSaid(null);
    try { await what(); setSaid({ ok: true, text }); clear?.(); setChanging(null); reload(); }
    catch (e) { setSaid({ ok: false, text: e instanceof Error ? e.message : String(e) }); }
    finally { setBusy(false); }
  }
  const hook = data.notifications.hook;
  const every = Math.round(data.notifications.polled_every_s / 60) || 1;
  const openAt = (i: number) => (i === now || changing === i) && i < 3;

  const steps: { title: string; line: React.ReactNode; body?: React.ReactNode; locked?: string }[] = [
    {
      title: "The platform's GitHub key",
      line: data.platform_token
        ? "Connected. The platform creates the repo, opens pull requests and merges with it. No agent ever holds it."
        : "A token for the account the platform acts as: it creates the repo, opens pull requests, reads CI and merges. Used for every project on this install.",
      body: <>
        <div className="pset-perms"><span className="badge">Fine-grained</span><span className="badge">Contents: read &amp; write</span>
          <span className="badge">Pull requests: read &amp; write</span><span className="badge">Administration: read &amp; write</span>
          <span className="badge">Webhooks: read &amp; write</span></div>
        <div className="pset-inline">
          <input type="password" value={platformKey} placeholder="github_pat_…" aria-label="Platform GitHub key"
            onChange={(e) => setPlatformKey(e.target.value)} />
          <button type="button" className="primary" disabled={busy || platformKey.trim().length < 8}
            onClick={() => act(() => put("/admin/settings/github", { token: platformKey.trim() }),
              data.repo_wanted && !data.repo ? `Saved. Making ${data.repo_wanted} now.` : "Saved.",
              () => setPlatformKey(""))}>Check and save</button>
        </div>
      </>,
    },
    {
      title: "The repository",
      line: data.repo
        ? <>Builds in <code>{data.repo}</code>.{data.branch_protection && !data.branch_protection.ok &&
            ` GitHub couldn't be told to protect main (${data.branch_protection.detail}); the platform still freezes merging if main moves without it.`}</>
        : "A bare name creates a new private repo; owner/name uses an empty one you made.",
      locked: "Waits for the platform's key.",
      body: <>
        {data.repo_wanted_error && (
          <p className="help serious">GitHub wouldn&apos;t make {data.repo_wanted}: {data.repo_wanted_error}.
            Change the name, or the key&apos;s access, and connect again.</p>
        )}
        <div className="pset-inline">
          <input value={repoName} placeholder="my-app or owner/my-app" aria-label="Repository"
            onChange={(e) => setRepo(e.target.value)} />
          <button type="button" className="primary" disabled={busy || !repoName.trim()}
            onClick={() => act(() => post(`/admin/projects/${projectId}/github`, { repo: repoName.trim() }),
              "Connected, and main is protected.")}>Connect</button>
        </div>
      </>,
    },
    {
      title: "The agents' GitHub key",
      line: pushOk
        ? `Held by ${data.push_token.granted.join(", ")}. They push branches with it; only the platform merges.`
        : "A second token, for the seats that build. They push branches and never merge. A separate GitHub account is best, so GitHub shows who wrote what.",
      locked: "Waits for the repository.",
      body: <>
        <div className="pset-perms"><span className="badge">Fine-grained</span><span className="badge">This repo only</span>
          <span className="badge">Contents: read &amp; write</span><span className="badge">Workflows: read &amp; write</span></div>
        <div className="pset-inline">
          <input type="password" value={pushKey} placeholder="github_pat_…" aria-label="Agents' GitHub key"
            onChange={(e) => setPushKey(e.target.value)} />
          <button type="button" className="primary" disabled={busy || pushKey.trim().length < 8}
            onClick={() => act(() => put(`/admin/projects/${projectId}/factory/push-token`, { token: pushKey.trim() }),
              "Saved: it can push to the repo.", () => setPushKey(""))}>Check and save</button>
        </div>
      </>,
    },
    {
      title: "Hearing from GitHub",
      line: <>{hook?.ok
        ? "GitHub tells this install the moment CI finishes or a branch moves."
        : `The platform checks GitHub every ${every} minute${every === 1 ? "" : "s"} for CI results and new pushes. Nothing to set up. Give this install a public https address (YAAF_PUBLIC_BASE_URL) and reconnect the repo to be told instantly.`}
        {data.notifications.last_delivery_at && ` Last notification: ${fmtWhen(data.notifications.last_delivery_at)}.`}</>,
    },
  ];

  return (
    <div className="set-card">
      <ol className="pset-steps">
        {steps.map((st, i) => {
          const isDone = done[i];
          const locked = !isDone && now >= 0 && i > now && i < 3;
          return (
            <li key={st.title} className={`${isDone ? "done" : ""}${i === now ? " now" : ""}${locked ? " locked" : ""}`}>
              <span className="n" aria-hidden="true">{isDone ? "✓" : i + 1}</span>
              <div className="t">
                <b>{st.title}<span className="sr-only">{isDone ? " — done" : " — to do"}</span></b>
                <span className="d">{locked && st.locked ? `${st.locked} ${st.line}` : st.line}</span>
              </div>
              <span className="a">
                {isDone && i < 3 && st.body && changing !== i && (
                  <button type="button" className="small" onClick={() => setChanging(i)}>Change</button>)}
                {changing === i && <button type="button" className="small" onClick={() => setChanging(null)}>Cancel</button>}
              </span>
              {openAt(i) && st.body && !locked && <div className="b">{st.body}</div>}
            </li>
          );
        })}
      </ol>
      {said && <div className="pset-foot"><span className={said.ok ? "set-flash" : "error"} role={said.ok ? "status" : "alert"}>{said.text}</span></div>}
    </div>
  );
}
