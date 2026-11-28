// Settings → Integrations: Slack for the chairman, signed webhooks for
// evidence no agent can forge, and a shared workspace repo for projects that
// are not a Code Factory. Each is a connection row and a table of what it
// reaches; the rare overrides open in a drawer.

import { useCallback, useEffect, useState } from "react";
import { Dashboard, get, OrgView, patch, post, SecretRow, SlackProvision } from "../../../api";
import { ConfirmModal, CopyField, Drawer, SecretInput, StoredSecret } from "../../../ui";
import { Group, PendingBar, SectionHead, Toggle } from "../../../settings/kit";

interface SlackConfig {
  /** Every field optional: an absent one is inherited from the app-wide
      connection, and `enabled: false` switches Slack off for this project. */
  enabled?: boolean;
  bot_token_secret?: string;
  signing_secret?: string;
  app_token_secret?: string;
  chairman_slack_user?: string;
  channel_prefix?: string;
  channel_visibility?: string;
  channel_map: Record<string, string>;
}

/** What the project's Slack wiring resolves to, and where each field came
    from: an empty box means nothing on its own — "not configured" and
    "inherited from the connection you set up once" look identical. */
interface SlackView {
  enabled: boolean;
  global_configured: boolean;
  can_mirror: boolean;
  can_receive: boolean;
  sources: Record<string, string>;
  chairman_slack_user: string;
  channel_prefix: string;
  channel_visibility: string;
  overrides: Record<string, string | null>;
  rooms: { key: string; label: string; expected_name: string; channel_id: string | null }[];
  events_url: string;
}

type Note = (text: string) => void;

export function IntegrationsSection({ projectId, dash, secrets, onSaved, onError }: {
  projectId: string;
  dash: Dashboard;
  secrets: SecretRow[];
  onSaved: Note;
  onError: Note;
}) {
  return (
    <>
      <SectionHead title="Integrations"
        lede="Where this project meets the outside: Slack for you, and signed webhooks for evidence no agent can forge." />
      <SlackGroup projectId={projectId} dash={dash} secrets={secrets} onSaved={onSaved} onError={onError} />
      <WebhookGroup projectId={projectId} dash={dash} />
      <GithubGroup projectId={projectId} dash={dash} onSaved={onSaved} onError={onError} />
    </>
  );
}

// ── Slack ────────────────────────────────────────────────────────────────────

function SlackGroup({ projectId, dash, secrets, onSaved, onError }: {
  projectId: string; dash: Dashboard; secrets: SecretRow[]; onSaved: Note; onError: Note;
}) {
  const existing = (dash.integrations["slack"] as SlackConfig | null | undefined) ?? null;
  const [view, setView] = useState<SlackView | null>(null);
  const [org, setOrg] = useState<OrgView | null>(null);
  const saved = existing?.channel_map ?? {};
  const [channels, setChannels] = useState<Record<string, string>>(saved);
  const [provisioned, setProvisioned] = useState<SlackProvision | null>(null);
  const [drawer, setDrawer] = useState(false);
  const [busy, setBusy] = useState(false);

  const loadView = useCallback(() => {
    get<SlackView>(`/admin/projects/${projectId}/slack`).then(setView).catch(() => {});
  }, [projectId]);
  useEffect(() => {
    get<OrgView>(`/observe/projects/${projectId}/org`).then(setOrg).catch(() => {});
    loadView();
  }, [projectId, loadView]);
  const savedKey = JSON.stringify(saved);
  // A provisioning run rewrites the map; the boxes follow it.
  useEffect(() => { setChannels(saved); }, [savedKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const off = existing?.enabled === false;
  const mapped = (map: Record<string, string>) =>
    Object.fromEntries(Object.entries(map).map(([k, v]) => [k, v.trim()]).filter(([, v]) => v));
  const dirty = Object.keys({ ...saved, ...channels })
    .filter((k) => (saved[k] ?? "").trim() !== (channels[k] ?? "").trim()).length;
  const overriding = ["bot_token", "signing_secret", "app_token", "chairman_slack_user", "channel_prefix"]
    .filter((f) => view?.sources[f] === "project");

  /** The whole slack block, rebuilt. PATCH merges at the level of
      `integrations`, so a partial block would silently drop what it omits —
      this is the one place that decides what the block contains. */
  function block(over: Partial<SlackConfig> = {}): SlackConfig {
    const b: SlackConfig = { channel_map: mapped(channels) };
    if (existing?.bot_token_secret) b.bot_token_secret = existing.bot_token_secret;
    if (existing?.signing_secret) b.signing_secret = existing.signing_secret;
    if (existing?.app_token_secret) b.app_token_secret = existing.app_token_secret;
    if (existing?.chairman_slack_user) b.chairman_slack_user = existing.chairman_slack_user;
    if (existing?.channel_prefix) b.channel_prefix = existing.channel_prefix;
    if (existing?.channel_visibility) b.channel_visibility = existing.channel_visibility;
    if (existing?.enabled === false) b.enabled = false;
    return { ...b, ...over };
  }
  async function run(fn: () => Promise<unknown>, done: string) {
    setBusy(true);
    try {
      await fn();
      loadView();
      onSaved(done);
    } catch (e) {
      onError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const rooms = [
    { key: "boardroom", label: "Boardroom", who: "You and the CEO" },
    { key: "leadership", label: "Leadership", who: "The CEO and the leads" },
    ...(org?.teams ?? []).map((t) => ({ key: `team:${t.id}`, label: `Team: ${t.name}`, who: "The team's seats" })),
  ];
  const expected = Object.fromEntries((view?.rooms ?? []).map((r) => [r.key, r.expected_name]));
  const provRow = (key: string) => provisioned?.channels.find((c) => c.key === key);
  const missing = rooms.filter((r) => !(channels[r.key] ?? "").trim()).length;
  const canProvision = !off && (view?.global_configured || view?.sources.bot_token !== undefined);

  const connection = off ? <>Off for this project. Nothing mirrors or arrives, whatever the workspace connection says; the bus keeps every message.</>
    : view?.can_mirror ? <><b>{overriding.length ? "This project's own tokens" : "The workspace connection"}</b>
        <span className="help"> · {view.can_receive
          ? <>you chat with the CEO from Slack as <code>{view.chairman_slack_user}</code></>
          : "outbound only"}</span></>
    : view?.global_configured ? <><b>The workspace connection</b><span className="help"> · ready; map a room to start mirroring</span></>
    : view ? <><b className="warn-ink">Not connected.</b> Connect Slack once in <a href="#/settings?section=slack">workspace Settings → Slack</a>, or give this project its own tokens.</>
    : <span className="help">Loading…</span>;

  return (
    <Group title="Slack" aside={
      <Toggle id="slack-on" checked={!off} disabled={busy} label="Mirror to Slack"
        onChange={(on) => run(() => patch(`/admin/projects/${projectId}`,
          { integrations: { slack: block({ enabled: on }) } }),
          on ? "Slack is on again for this project." : "Slack is off for this project. The bus keeps every message.")} />
    }>
      <div className="set-card">
        <div className="set-row">
          <span className="set-row-lab">Connection<span className="set-row-hint">Mapped rooms mirror within about 15 seconds</span></span>
          <div className="set-row-val">{connection}</div>
          <div className="set-row-act">
            <button type="button" className="small" onClick={() => setDrawer(true)}>
              {overriding.length ? `Own tokens (${overriding.length})` : "Use a different workspace"}</button>
          </div>
        </div>
        <div className="table-wrap">
          <table className="pset-table">
            <thead><tr><th>Room</th><th>Slack channel ID</th><th>Status</th></tr></thead>
            <tbody>
              {rooms.map((r) => {
                const value = channels[r.key] ?? "";
                const row = provRow(r.key);
                return (
                  <tr key={r.key}>
                    <td><b>{r.label}</b><span className="sub">{r.who}</span></td>
                    <td>
                      <input className="mono pset-chan" value={value} aria-label={`Slack channel ID for ${r.label}`}
                        placeholder={expected[r.key] ? `#${expected[r.key]} when created` : "C…"}
                        onChange={(e) => setChannels((c) => ({ ...c, [r.key]: e.target.value }))} />
                    </td>
                    <td>
                      {row?.error ? <span className="badge critical">{row.error}</span>
                        : row ? <span className="badge good">#{row.name} · {row.already_mapped ? "already mapped"
                            : row.created ? (row.invited ? "created, you're in it" : "created") : "adopted"}</span>
                        : off ? <span className="badge">Paused</span>
                        : value.trim() ? <span className="badge good">Mirroring</span>
                        : <span className="badge warn">Not mirrored</span>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        {canProvision && (
          <div className="pset-foot">
            <button type="button" className="small" disabled={busy || missing === 0} onClick={() => run(async () => {
              const result = await post<SlackProvision>(`/admin/projects/${projectId}/slack/channels`);
              setProvisioned(result);
            }, "Channels made. The bot invited you to each.")}>
              {missing ? `Create ${missing} missing channel${missing === 1 ? "" : "s"}` : "Every room has a channel"}</button>
            <span className="help">{view?.channel_visibility ?? "Private"}, named <code>{view?.channel_prefix ?? dash.slug}-…</code>, with you invited. An existing name is adopted, never duplicated.</span>
          </div>
        )}
      </div>

      <PendingBar count={dirty} what="to the Slack rooms" note="Mirroring follows within seconds." busy={busy}
        onDiscard={() => setChannels(saved)}
        onSave={() => run(() => patch(`/admin/projects/${projectId}`, { integrations: { slack: block() } }),
          "Slack rooms saved. Mirroring follows within seconds.")} />

      {drawer && (
        <SlackTokens projectId={projectId} existing={existing} view={view} secrets={secrets}
          overriding={overriding.length > 0} block={block}
          onClose={() => setDrawer(false)}
          onDone={(text) => { setDrawer(false); loadView(); onSaved(text); }} onError={onError} />
      )}
    </Group>
  );
}

/** The project's own Slack tokens, for a project in a different workspace.
    Blank fields inherit the workspace connection. */
function SlackTokens({ projectId, existing, view, secrets, overriding, block, onClose, onDone, onError }: {
  projectId: string;
  existing: SlackConfig | null;
  view: SlackView | null;
  secrets: SecretRow[];
  overriding: boolean;
  block: (over?: Partial<SlackConfig>) => SlackConfig;
  onClose: () => void;
  onDone: Note;
  onError: Note;
}) {
  const [bot, setBot] = useState("");
  const [signing, setSigning] = useState("");
  const [app, setApp] = useState("");
  const [user, setUser] = useState(existing?.chairman_slack_user ?? "");
  const [prefix, setPrefix] = useState(existing?.channel_prefix ?? "");
  const [busy, setBusy] = useState(false);
  const stored = ([["Bot token", existing?.bot_token_secret], ["Signing secret", existing?.signing_secret],
    ["App token", existing?.app_token_secret]] as [string, string | undefined][])
    .map(([label, name]) => [label, name ? secrets.find((s) => s.name === name) : undefined] as const)
    .filter(([, s]) => !!s);

  async function save() {
    setBusy(true);
    try {
      // Stored as ungranted service secrets: the bridge decrypts them
      // server-side; no agent sandbox ever receives them.
      for (const [name, value] of [["slack-bot", bot], ["slack-signing", signing], ["slack-app", app]]) {
        if (value.trim()) await post("/admin/secrets", { project_id: projectId, name, value: value.trim(),
          role_scopes: [], kind: "service" });
      }
      const over: Partial<SlackConfig> = {};
      if (bot.trim()) over.bot_token_secret = "slack-bot";
      if (signing.trim()) over.signing_secret = "slack-signing";
      if (app.trim()) over.app_token_secret = "slack-app";
      const b = block(over);
      if (user.trim()) b.chairman_slack_user = user.trim(); else delete b.chairman_slack_user;
      if (prefix.trim()) b.channel_prefix = prefix.trim(); else delete b.channel_prefix;
      await patch(`/admin/projects/${projectId}`, { integrations: { slack: b } });
      onDone("Saved. Mirroring follows within seconds.");
    } catch (e) {
      onError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function reset() {
    setBusy(true);
    try {
      await patch(`/admin/projects/${projectId}`, { integrations: { slack: { channel_map: block().channel_map } } });
      onDone("Back to the workspace Slack connection. The rooms are kept.");
    } catch (e) {
      onError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const origin = (f: string) => view?.sources[f] === "project" ? "this project's"
    : view?.sources[f] === "global" ? "inherited" : null;

  return (
    <Drawer title="A different Slack workspace"
      sub="For a project that lives in another workspace. Blank fields inherit the workspace connection."
      onClose={onClose}
      footer={<>
        {overriding && <button type="button" className="quiet-danger" disabled={busy} onClick={reset}>Use the workspace connection</button>}
        <span className="grow" />
        <button type="button" onClick={onClose}>Cancel</button>
        <button type="button" className="primary" disabled={busy} onClick={save}>{busy ? "Saving…" : "Save"}</button>
      </>}>
      {stored.map(([label, s]) => (
        <div key={label}>
          <label className="field">{label} <span className="help">stored</span></label>
          <StoredSecret secretId={s!.id} note="These never enter a sandbox; the bridge decrypts them server-side."
            onRotate={async (value) => {
              await post("/admin/secrets", { project_id: projectId, name: s!.name, value, role_scopes: [] });
            }} />
        </div>
      ))}
      <label className="field">Bot token {origin("bot_token") && <span className="help">{origin("bot_token")}</span>}
        <SecretInput value={bot} placeholder="xoxb-…" onChange={setBot} /></label>
      <label className="field">Signing secret {origin("signing_secret") && <span className="help">{origin("signing_secret")}</span>}
        <SecretInput value={signing} onChange={setSigning} /></label>
      <label className="field">App token {origin("app_token") && <span className="help">{origin("app_token")}</span>}
        <SecretInput value={app} placeholder="xapp-…" onChange={setApp} />
        <span className="help">For Socket Mode, so the install needs no public address.</span></label>
      <label className="field">Your Slack member ID
        <input value={user} placeholder={view?.chairman_slack_user ? `${view.chairman_slack_user} (inherited)` : "U…"}
          onChange={(e) => setUser(e.target.value)} /></label>
      <label className="field">Channel name prefix
        <input value={prefix} placeholder={view?.channel_prefix ?? ""} onChange={(e) => setPrefix(e.target.value)} /></label>
      {view?.events_url && <p className="help">Event Subscriptions request URL, for an install reachable from
        the internet: <code>{view.events_url}</code></p>}
    </Drawer>
  );
}

// ── webhooks ─────────────────────────────────────────────────────────────────

/** The unforgeable-evidence doors. The secrets are shown once — when made, or
    when rotated — because a secret the screen can always re-show is a secret
    every screen-share leaks. */
function WebhookGroup({ projectId, dash }: { projectId: string; dash: Dashboard }) {
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [fresh, setFresh] = useState<{ github: string; generic: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const origin = window.location.origin;
  const made = !!(dash.integrations["webhook_secret"] || dash.integrations["github_webhook_secret"]);
  return (
    <Group title="Evidence webhooks" aside={<span className="help">signed evidence sent here counts as platform-attested</span>}>
      <div className="set-card">
        <div className="set-row">
          <span className="set-row-lab">GitHub<span className="set-row-hint">HMAC, X-Hub-Signature-256</span></span>
          <div className="set-row-val"><CopyField value={`${origin}/webhooks/github/${projectId}`} /></div>
          <div className="set-row-act" />
        </div>
        <div className="set-row">
          <span className="set-row-lab">Any other sender<span className="set-row-hint">X-Yaaf-Signature</span></span>
          <div className="set-row-val"><CopyField value={`${origin}/webhooks/generic/${projectId}`} /></div>
          <div className="set-row-act" />
        </div>
        <div className="set-row">
          <span className="set-row-lab">Signing secrets<span className="set-row-hint">Shown once, when made</span></span>
          <div className="set-row-val">{made
            ? "Made. No run ever holds them, which is what makes this evidence impossible for an agent to forge."
            : "Not made yet."}</div>
          <div className="set-row-act">
            <button type="button" className={`small${made ? " danger" : " primary"}`} onClick={() => made ? setAsking(true) : rotate()}>
              {made ? "Rotate…" : "Make signing secrets"}</button>
          </div>
        </div>
        {error && <div className="pset-foot"><span className="error">{error}</span></div>}
      </div>
      {asking && (
        <ConfirmModal title="Rotate the webhook signing secrets?" confirmLabel="Rotate" danger busy={busy}
          onConfirm={rotate} onCancel={() => setAsking(false)}>
          <p>GitHub and any other sender fail their signature checks until you paste the new secrets into
            them. Evidence they send meanwhile is refused.</p>
        </ConfirmModal>
      )}
      {fresh && (
        <ConfirmModal title="New signing secrets" confirmLabel="I've copied them" cancelLabel="Close"
          onConfirm={() => setFresh(null)} onCancel={() => setFresh(null)}>
          <p>Copy them now. They are not shown again.</p>
          <label className="field">GitHub</label>
          <CopyField value={fresh.github} />
          <label className="field">Any other sender</label>
          <CopyField value={fresh.generic} />
        </ConfirmModal>
      )}
    </Group>
  );

  async function rotate() {
    setBusy(true);
    setError(null);
    try {
      const out = await post<{ github_webhook_secret: string; webhook_secret: string }>(
        `/admin/projects/${projectId}/webhook_secrets`, {});
      setAsking(false);
      setFresh({ github: out.github_webhook_secret, generic: out.webhook_secret });
    } catch (e) {
      setAsking(false);
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
}

// ── GitHub workspace ─────────────────────────────────────────────────────────

function GithubGroup({ projectId, dash, onSaved, onError }: {
  projectId: string; dash: Dashboard; onSaved: Note; onError: Note;
}) {
  const [repo, setRepo] = useState("");
  const [busy, setBusy] = useState(false);
  if (dash.mode === "code_factory") {
    return (
      <Group title="GitHub workspace">
        <div className="set-card pad help">This is a Code Factory, so its repository lives under{" "}
          <a href={`#/projects/${projectId}/settings?s=factory`}>Code Factory</a>.</div>
      </Group>
    );
  }
  return (
    <Group title="GitHub workspace" aside={<span className="help">a private repo the agents share, so work outlives the sandbox</span>}>
      <div className="set-card">
        <div className="set-row">
          <span className="set-row-lab">Workspace repo<span className="set-row-hint">Cloned into each run's sandbox</span></span>
          <div className="set-row-val">
            {dash.github_repo ? <><b>{dash.github_repo}</b> <span className="badge good">Connected</span></>
              : <input value={repo} aria-label="Workspace repo" placeholder="my-project, or owner/my-project"
                  onChange={(e) => setRepo(e.target.value)} />}
          </div>
          <div className="set-row-act">
            {!dash.github_repo && (
              <button type="button" className="small primary" disabled={busy || !repo.trim()} onClick={async () => {
                setBusy(true);
                try {
                  const out = await post<{ github_repo: string }>(`/admin/projects/${projectId}/github`, { repo: repo.trim() });
                  onSaved(`Workspace repo ready: ${out.github_repo}. The runner clones it into each run.`);
                } catch (e) {
                  onError((e as Error).message);
                } finally {
                  setBusy(false);
                }
              }}>Attach</button>
            )}
          </div>
        </div>
        {!dash.github_repo && <div className="pset-foot help">A bare name creates a private repo; owner/name attaches one you have.</div>}
      </div>
    </Group>
  );
}
