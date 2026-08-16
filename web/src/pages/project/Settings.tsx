import { Fragment, useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  AmendmentRow,
  CloneResult,
  Dashboard,
  del,
  get,
  ConstitutionField,
  McpServerEntry,
  OrgView,
  patch,
  post,
  ProjectBundle,
  RoleSummary,
} from "../../api";
import { fmtWhen, usePoll } from "../../hooks";
import { ConfirmModal, SecretInput, StoredSecret } from "../../ui";

/** "amazon-audit-v2" → "amazon-audit-v3"; "Acme" → "Acme (clone)". Twins come
    in numbered runs far more often than they come in ones. */
function nextInSeries(value: string, fallback: string): string {
  const match = value.match(/^(.*?)(\d+)$/);
  return match ? `${match[1]}${Number(match[2]) + 1}` : `${value}${fallback}`;
}

/** The shared after-action helper: run, show a message (fixed, or computed
    from the response when the server's answer is the news), reload the dash. */
type Act = (
  fn: () => Promise<unknown>,
  ok: string | ((result: unknown) => string),
) => Promise<void>;

interface SecretRow {
  id: string;
  name: string;
  project_id: string | null;
  role_scopes: string[];
  kind: "service" | "billing";
}

function envVarName(name: string): string {
  return name.replace(/[^A-Za-z0-9]+/g, "_").replace(/^_+|_+$/g, "").toUpperCase();
}

/** Config addresses a secret by name (`slack.bot_token_secret`, a data source's
    `url_secret`); revealing one needs its id. One lookup, used by every card. */
function secretByName(secrets: SecretRow[], name: string | null | undefined) {
  return name ? secrets.find((s) => s.name === name) : undefined;
}

function amendmentBadge(status: AmendmentRow["status"]) {
  const cls = status === "applied" ? "good" : status === "pending" ? "warn" : "";
  return <span className={`badge ${cls}`}>{status}</span>;
}

function hoursUntil(iso: string): number {
  return Math.max(0, Math.ceil((new Date(iso).getTime() - Date.now()) / 3_600_000));
}

function RoleChecks({
  roles,
  selected,
  onChange,
}: {
  roles: RoleSummary[];
  selected: string[];
  onChange: (slugs: string[]) => void;
}) {
  return (
    <div className="pill-row">
      {roles.map((role) => (
        <label key={role.slug} style={{ display: "flex", gap: 5, alignItems: "center" }}>
          <input
            type="checkbox"
            style={{ width: "auto" }}
            checked={selected.includes(role.slug)}
            onChange={(e) =>
              onChange(
                e.target.checked
                  ? [...selected, role.slug]
                  : selected.filter((slug) => slug !== role.slug),
              )
            }
          />
          {role.name}
        </label>
      ))}
    </div>
  );
}

interface SlackConfig {
  bot_token_secret: string;
  signing_secret: string;
  app_token_secret?: string;
  chairman_slack_user: string;
  channel_map: Record<string, string>;
}

function SlackBridge({
  projectId,
  dash,
  secrets,
  act,
  busy,
}: {
  projectId: string;
  dash: Dashboard;
  secrets: SecretRow[];
  act: (fn: () => Promise<unknown>, ok: string) => Promise<void>;
  busy: boolean;
}) {
  const existing = (dash.config["slack"] as SlackConfig | null | undefined) ?? null;
  const [org, setOrg] = useState<OrgView | null>(null);
  const [botToken, setBotToken] = useState("");
  const [signingSecret, setSigningSecret] = useState("");
  const [appToken, setAppToken] = useState("");
  const [chairmanUser, setChairmanUser] = useState(existing?.chairman_slack_user ?? "");
  const [channels, setChannels] = useState<Record<string, string>>(existing?.channel_map ?? {});

  useEffect(() => {
    get<OrgView>(`/observe/projects/${projectId}/org`).then(setOrg).catch(() => {});
  }, [projectId]);

  function setChannel(key: string, value: string) {
    setChannels((prev) => ({ ...prev, [key]: value }));
  }

  const mapped = Object.entries(channels).filter(([, v]) => v.trim());
  const canSave = mapped.length > 0 && (existing !== null || botToken.trim().length > 0);
  const eventsUrl = `${location.origin}/webhooks/slack/${projectId}`;

  function channelInput(key: string, label: string) {
    return (
      <div key={key}>
        <label className="field">{label}</label>
        <input
          value={channels[key] ?? ""}
          onChange={(e) => setChannel(key, e.target.value)}
          placeholder="C… (blank = not mirrored)"
        />
      </div>
    );
  }

  return (
    <>
      <p className="subtitle">
        The bus in Postgres stays the source of truth; Slack is a mirror plus an inbound path
        for you alone. Mapped channels mirror outward within ~15s. A Slack outage never blocks
        the company — messages queue and retry.
      </p>
      {existing ? (
        <p className="help">
          <span className="badge good">connected</span> mirroring{" "}
          {Object.keys(existing.channel_map).length} channel(s)
          {existing.chairman_slack_user ? "; inbound enabled for your Slack user" : ""}.
        </p>
      ) : (
        <p className="help">Not connected. You need a Slack app with the `chat:write` bot
          scope, installed to your workspace, and the bot invited to each channel you map.</p>
      )}

      {existing && (
        <>
          <label className="field">What is stored now</label>
          <table>
            <tbody>
              {([
                ["Bot token", existing.bot_token_secret],
                ["Signing secret", existing.signing_secret],
                ["App token", existing.app_token_secret],
              ] as [string, string | undefined][]).map(([label, name]) => {
                const secret = secretByName(secrets, name);
                if (!secret) return null;
                return (
                  <tr key={label}>
                    <td style={{ width: "30%" }}>{label}</td>
                    <td>
                      <StoredSecret
                        secretId={secret.id}
                        onRotate={async (value) => {
                          await post("/admin/secrets", {
                            project_id: projectId, name: secret.name, value, role_scopes: [],
                          });
                        }}
                        note="These never enter a sandbox — the bridge decrypts them server-side."
                      />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </>
      )}

      <div className="row">
        <div>
          <label className="field">Bot token (xoxb-…){existing ? " — blank keeps current" : ""}</label>
          <SecretInput value={botToken} placeholder="xoxb-…" onChange={setBotToken} />
        </div>
        <div>
          <label className="field">Signing secret{existing ? " — blank keeps current" : ""}</label>
          <SecretInput value={signingSecret} placeholder="from Basic Information"
                       onChange={setSigningSecret} />
        </div>
        <div>
          <label className="field">
            App token (xapp-…) — chat from Slack, no public URL needed
            {existing?.app_token_secret ? " — blank keeps current" : ""}
          </label>
          <SecretInput value={appToken} placeholder="xapp-… (enables Socket Mode)"
                       onChange={setAppToken} />
        </div>
      </div>
      <div className="row">
        <div>
          <label className="field">
            Your Slack member ID (U…) — only this user's boardroom-channel messages reach the CEO
          </label>
          <input value={chairmanUser} placeholder="U… (blank = outbound mirror only)"
                 onChange={(e) => setChairmanUser(e.target.value)} />
        </div>
      </div>
      <label className="field">Channel map — Slack channel IDs, bot invited to each</label>
      <div className="row">
        {channelInput("boardroom", "Boardroom (chairman ↔ CEO)")}
        {channelInput("leadership", "Leadership (CEO + leads)")}
      </div>
      <div className="row">
        {(org?.teams ?? []).map((team) => channelInput(`team:${team.id}`, `Team room: ${team.name}`))}
      </div>
      <div className="actions">
        <button
          className="primary"
          disabled={busy || !canSave}
          onClick={() =>
            act(async () => {
              // Credentials are stored as ungranted service secrets: the bridge
              // decrypts them server-side; no agent sandbox ever receives them.
              if (botToken.trim()) {
                await post("/admin/secrets", { project_id: projectId, name: "slack-bot",
                  value: botToken.trim(), role_scopes: [], kind: "service" });
              }
              if (signingSecret.trim()) {
                await post("/admin/secrets", { project_id: projectId, name: "slack-signing",
                  value: signingSecret.trim(), role_scopes: [], kind: "service" });
              }
              if (appToken.trim()) {
                await post("/admin/secrets", { project_id: projectId, name: "slack-app",
                  value: appToken.trim(), role_scopes: [], kind: "service" });
              }
              const socketMode = appToken.trim() !== "" || !!existing?.app_token_secret;
              await patch(`/admin/projects/${projectId}`, {
                config: { slack: {
                  bot_token_secret: "slack-bot",
                  signing_secret: "slack-signing",
                  ...(socketMode ? { app_token_secret: "slack-app" } : {}),
                  chairman_slack_user: chairmanUser.trim(),
                  channel_map: Object.fromEntries(mapped.map(([k, v]) => [k, v.trim()])),
                }},
              });
              setBotToken("");
              setSigningSecret("");
              setAppToken("");
            }, "Slack connected. Mirroring starts within seconds.")
          }
        >
          {existing ? "Update bridge" : "Connect Slack"}
        </button>
        {existing && (
          <button
            disabled={busy}
            onClick={() => {
              if (!confirm("Disconnect Slack? The bus keeps everything; mirroring just stops."))
                return;
              act(
                () => patch(`/admin/projects/${projectId}`, { config: { slack: null } }),
                "Slack disconnected. The bus is unaffected.",
              );
            }}
          >
            Disconnect
          </button>
        )}
      </div>
      <p className="help">
        To chat from Slack: in the Slack app, enable <b>Socket Mode</b> (Settings → Socket
        Mode), create an app-level token with the `connections:write` scope, paste it above,
        and subscribe the bot to the `message.channels` event. Your messages in the mapped
        boardroom channel reach the CEO instantly and its replies mirror back — no public
        URL, no tunnel. The Event Subscriptions request URL <code>{eventsUrl}</code> remains
        the alternative for boxes that are publicly reachable.
      </p>
    </>
  );
}

interface DataSourceSaveResult {
  name: string;
  check: { ok: boolean; detail: string };
  note: string;
}

function checkBadge(last: McpServerEntry["last_check"]) {
  if (!last) return <span className="badge warn">never tested</span>;
  return last.ok ? (
    <span className="badge good">reachable</span>
  ) : (
    <span className="badge critical">unreachable</span>
  );
}

type RoleOption = { slug: string; teams: string[] };

/** One checkbox per distinct role in the org, labelled with the team(s) running
    it — grants are per role, so sibling teams share a box. */
function roleOptionsOf(org: OrgView | null): RoleOption[] {
  const options: RoleOption[] = [];
  for (const team of org?.teams ?? []) {
    if (!team.role_slug) continue;
    const existing = options.find((option) => option.slug === team.role_slug);
    if (existing) existing.teams.push(team.name);
    else options.push({ slug: team.role_slug, teams: [team.name] });
  }
  return options;
}

function RoleTicks({ options, selected, onChange }: {
  options: RoleOption[];
  selected: string[];
  onChange: (slugs: string[]) => void;
}) {
  if (options.length === 0)
    return <p className="help">No teams yet — add a team on the Overview tab first.</p>;
  return (
    <div className="pill-row">
      {options.map((option) => (
        <label key={option.slug} style={{ display: "flex", gap: 5, alignItems: "center" }}>
          <input
            type="checkbox"
            style={{ width: "auto" }}
            checked={selected.includes(option.slug)}
            onChange={(e) =>
              onChange(
                e.target.checked
                  ? [...selected, option.slug]
                  : selected.filter((slug) => slug !== option.slug),
              )
            }
          />
          {option.teams.join(", ")} ({option.slug})
        </label>
      ))}
    </div>
  );
}

function savedMessage(result: unknown): string {
  const saved = result as DataSourceSaveResult;
  return (
    `Saved '${saved.name}' — ${saved.check.ok ? "" : "warning: "}` +
    `${saved.check.detail}. ${saved.note}`
  );
}

/** Editing a source in place. The address is fetched and shown outright — an
    address is not a password, and you cannot fix a port you cannot see. The
    token stays masked and blank-keeps-current, so changing only the URL never
    has to touch it. */
function DataSourceEditor({
  projectId, source, secrets, roleOptions, act, busy, onDone,
}: {
  projectId: string;
  source: McpServerEntry;
  secrets: SecretRow[];
  roleOptions: RoleOption[];
  act: Act;
  busy: boolean;
  onDone: () => void;
}) {
  const urlSecretId = secretByName(secrets, source.url_secret)?.id;
  const [url, setUrl] = useState("");
  const [loadError, setLoadError] = useState<string | null>(null);
  const [bearer, setBearer] = useState("");
  const [dropToken, setDropToken] = useState(false);
  const [roles, setRoles] = useState<string[]>(source.roles);

  useEffect(() => {
    setLoadError(null);
    if (!urlSecretId) {
      // Either the list has not arrived yet, or the row really is gone — this
      // effect re-runs the moment an id shows up, so only the latter sticks.
      setLoadError("its stored address is missing — type it again to repair the source");
      return;
    }
    get<{ value: string }>(`/admin/secrets/${urlSecretId}/value`)
      .then((got) => setUrl(got.value))
      .catch((e) => setLoadError((e as Error).message));
  }, [urlSecretId]);

  return (
    <div className="source-editor">
      <div className="row">
        <div>
          <label className="field">URL — the server's full address</label>
          <input
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://host:3845/mcp"
          />
        </div>
        <div>
          <label className="field">
            Bearer token —{" "}
            {source.bearer_secret
              ? dropToken ? "will be removed" : "blank keeps the current one"
              : "none stored"}
          </label>
          <SecretInput
            value={bearer}
            disabled={dropToken}
            placeholder={source.bearer_secret ? "blank = keep current" : "blank = no auth header"}
            onChange={setBearer}
          />
          {source.bearer_secret && (
            <label className="help" style={{ display: "flex", gap: 5, alignItems: "center" }}>
              <input
                type="checkbox"
                style={{ width: "auto" }}
                checked={dropToken}
                onChange={(e) => setDropToken(e.target.checked)}
              />
              remove the stored token — this server needs no auth
            </label>
          )}
        </div>
      </div>
      <label className="field">Which roles get these tools</label>
      <RoleTicks options={roleOptions} selected={roles} onChange={setRoles} />
      <div className="actions">
        <button
          className="small primary"
          disabled={busy || !url.trim() || roles.length === 0}
          onClick={() =>
            act(async () => {
              const result = await post<DataSourceSaveResult>(
                `/admin/projects/${projectId}/mcp_servers`,
                {
                  name: source.name,
                  url: url.trim(),
                  // Omitted keeps the stored token; "" removes it; a value rotates.
                  bearer: dropToken ? "" : bearer.trim() || undefined,
                  roles,
                },
              );
              onDone();
              return result;
            }, savedMessage)
          }
        >
          Save changes
        </button>
        <button className="small" disabled={busy} onClick={onDone}>
          cancel
        </button>
        {loadError && (
          <span className="help" style={{ color: "var(--critical)" }}>{loadError}</span>
        )}
      </div>
    </div>
  );
}

function DataSources({
  projectId,
  dash,
  secrets,
  act,
  busy,
}: {
  projectId: string;
  dash: Dashboard;
  secrets: SecretRow[];
  act: Act;
  busy: boolean;
}) {
  const sources = (dash.config["mcp_servers"] as McpServerEntry[] | undefined) ?? [];
  const [org, setOrg] = useState<OrgView | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [bearer, setBearer] = useState("");
  const [roles, setRoles] = useState<string[]>([]);

  useEffect(() => {
    get<OrgView>(`/observe/projects/${projectId}/org`).then(setOrg).catch(() => {});
  }, [projectId]);

  const roleOptions = roleOptionsOf(org);

  return (
    <>
      <p className="subtitle">
        A data source is an MCP server — a URL that offers tools. An agent's sandbox is a locked
        box whose only way in is an environment variable you granted, so the address travels as
        one: saving <code>ppc</code> stores its URL encrypted and hands the granted roles{" "}
        <code>$MCP_PPC_URL</code>. That variable <em>is</em> the delivery mechanism, not a side
        effect — which is why everything about a source is edited here and nowhere else.
      </p>
      <p className="help">
        One save does all of it: validates the URL, stores it and any token encrypted, grants
        them to the roles you tick, opens the firewall for that host, reloads the proxy (~15s),
        and calls the server to confirm it answers. To change one, press <b>edit</b> — no role
        editing, no repin, no CLI.
      </p>

      {sources.length > 0 && (
        <table style={{ marginBottom: 14 }}>
          <thead>
            <tr>
              <th>Name</th>
              <th>Address</th>
              <th>Roles</th>
              <th style={{ width: "32%" }}>Last check</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {sources.map((source) => (
              <Fragment key={source.name}>
                <tr>
                  <td className="mono">
                    {source.name}
                    <div className="help">
                      ${envVarName(source.url_secret)}
                      {source.bearer_secret && ` · $${envVarName(source.bearer_secret)}`}
                    </div>
                  </td>
                  <td className="mono">
                    {source.url_host}:{source.url_port}
                  </td>
                  <td>
                    <div className="pill-row">
                      {source.roles.map((slug) => (
                        <span key={slug} className="badge accent">
                          {slug}
                        </span>
                      ))}
                    </div>
                  </td>
                  <td>
                    {checkBadge(source.last_check)}
                    {source.last_check && (
                      <div className="help">
                        {source.last_check.detail} — {fmtWhen(source.last_check.at)}
                      </div>
                    )}
                  </td>
                  <td>
                    <div className="actions" style={{ margin: 0 }}>
                      <button
                        className="small"
                        disabled={busy}
                        onClick={() =>
                          act(async () => {
                            const check = await post<{ ok: boolean; detail: string }>(
                              `/admin/projects/${projectId}/mcp_servers/${source.name}/check`,
                              {},
                            );
                            if (!check.ok)
                              throw new Error(`'${source.name}': ${check.detail}`);
                            return check;
                          }, (result) =>
                            `'${source.name}': ${(result as { detail: string }).detail}`)
                        }
                      >
                        test
                      </button>
                      <button
                        className="small"
                        disabled={busy}
                        onClick={() =>
                          setEditing(editing === source.name ? null : source.name)
                        }
                      >
                        {editing === source.name ? "close" : "edit"}
                      </button>
                      <button
                        className="small danger"
                        disabled={busy}
                        onClick={() => {
                          if (
                            !confirm(
                              `Remove data source '${source.name}'? Its stored URL and token ` +
                                "secrets are deleted too; granted roles lose these tools on " +
                                "their next run.",
                            )
                          )
                            return;
                          act(
                            () => del(`/admin/projects/${projectId}/mcp_servers/${source.name}`),
                            `Removed '${source.name}'. Egress and the proxy update themselves within ~15s.`,
                          );
                        }}
                      >
                        delete
                      </button>
                    </div>
                  </td>
                </tr>
                {editing === source.name && (
                  <tr>
                    <td colSpan={5}>
                      <DataSourceEditor
                        projectId={projectId}
                        source={source}
                        secrets={secrets}
                        roleOptions={roleOptions}
                        act={act}
                        busy={busy}
                        onDone={() => setEditing(null)}
                      />
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      )}

      <label className="field">Add a data source</label>
      <div className="row">
        <div>
          <label className="field">Name (lowercase slug)</label>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="figma, analytics, crm…"
          />
          {name.trim() && (
            <div className="help" style={{ marginTop: 4 }}>
              granted roles will get <code>${envVarName(`mcp-${name.trim()}-url`)}</code> and
              call its tools as <code>mcp__{name.trim()}__*</code>
            </div>
          )}
        </div>
        <div>
          <label className="field">URL — the server's full address</label>
          <input
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://host:3845/mcp"
          />
        </div>
        <div>
          <label className="field">Bearer token (optional)</label>
          <SecretInput
            value={bearer}
            onChange={setBearer}
            placeholder="blank = no auth header"
          />
        </div>
      </div>
      <label className="field">Which roles get these tools (nobody, until you tick)</label>
      <RoleTicks options={roleOptions} selected={roles} onChange={setRoles} />
      <div className="actions">
        <button
          className="small primary"
          disabled={busy || !name.trim() || !url.trim() || roles.length === 0}
          onClick={() =>
            act(async () => {
              const result = await post<DataSourceSaveResult>(
                `/admin/projects/${projectId}/mcp_servers`,
                {
                  name: name.trim(),
                  url: url.trim(),
                  bearer: bearer.trim() || undefined,
                  roles,
                },
              );
              setName("");
              setUrl("");
              setBearer("");
              setRoles([]);
              return result;
            }, savedMessage)
          }
        >
          Save data source
        </button>
        <span className="help">
          A garbage URL is refused at this button, not discovered at 9am.
        </span>
      </div>
    </>
  );
}

function GithubWorkspace({
  projectId,
  dash,
  act,
  busy,
}: {
  projectId: string;
  dash: Dashboard;
  act: Act;
  busy: boolean;
}) {
  const [repo, setRepo] = useState("");
  return (
    <>
      <p className="subtitle">
        Each project gets a private GitHub repo as the agents' shared workspace — the runner
        clones it into the sandbox at lease time, so work persists between runs instead of dying
        with the container.
      </p>
      {dash.github_repo ? (
        <p className="help" style={{ marginBottom: 0 }}>
          <span className="badge good">connected</span> workspace repo:{" "}
          <code>{dash.github_repo}</code>
        </p>
      ) : (
        <>
          <label className="field">
            Repo — a bare name creates a private repo; owner/name attaches an existing one
          </label>
          <input
            value={repo}
            onChange={(e) => setRepo(e.target.value)}
            placeholder="my-project or owner/my-project"
          />
          <div className="actions">
            <button
              className="small primary"
              disabled={busy || !repo.trim()}
              onClick={() =>
                act(
                  () =>
                    post<{ github_repo: string }>(`/admin/projects/${projectId}/github`, {
                      repo: repo.trim(),
                    }),
                  (result) =>
                    `Workspace repo ready: ${(result as { github_repo: string }).github_repo}. ` +
                    "The runner clones it at lease time.",
                )
              }
            >
              Attach workspace repo
            </button>
          </div>
        </>
      )}
    </>
  );
}

function CredentialsManager({
  projectId,
  dash,
  secrets,
  refreshSecrets,
  act,
  busy,
}: {
  projectId: string;
  dash: Dashboard;
  secrets: SecretRow[];
  refreshSecrets: () => void;
  act: Act;
  busy: boolean;
}) {
  const [roles, setRoles] = useState<RoleSummary[]>([]);
  const [name, setName] = useState("");
  const [value, setValue] = useState("");
  const [grants, setGrants] = useState<string[]>([]);

  useEffect(() => {
    get<RoleSummary[]>("/admin/roles")
      .then((all) => setRoles(all.filter((r) => !r.is_ceo_role)))
      .catch(() => {});
  }, [projectId]);

  // Only the credentials the chairman created by hand. The rest are owned by
  // the thing that made them and are edited there, so one object never shows
  // up as three rows: billing lives in the billing card, a data source's
  // address and token live on the source, Slack's tokens live on the bridge.
  const slack = dash.config["slack"] as SlackConfig | null | undefined;
  const sources = (dash.config["mcp_servers"] as McpServerEntry[] | undefined) ?? [];
  const managed = new Set(
    [
      ...sources.flatMap((s) => [s.url_secret, s.bearer_secret]),
      slack?.bot_token_secret,
      slack?.signing_secret,
      slack?.app_token_secret,
    ].filter(Boolean) as string[],
  );
  const stored = secrets.filter((s) => s.kind !== "billing" && !managed.has(s.name));

  return (
    <>
      <div className="row">
        <div>
          <label className="field">Name</label>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="wordpress, github-token, stripe-key…"
          />
          {name.trim() && (
            <div className="help" style={{ marginTop: 4 }}>
              agents will see it as <code>${envVarName(name)}</code>
            </div>
          )}
        </div>
        <div>
          <label className="field">Value</label>
          <SecretInput
            value={value}
            onChange={setValue}
            placeholder={name.trim() === "wordpress" ? "username:xxxx xxxx xxxx xxxx" : "the key itself"}
          />
        </div>
      </div>
      <label className="field">Which roles receive it (nobody, until you grant)</label>
      <RoleChecks roles={roles} selected={grants} onChange={setGrants} />
      <div className="actions">
        <button
          className="small primary"
          disabled={busy || !name.trim() || !value}
          onClick={() =>
            act(async () => {
              await post("/admin/secrets", {
                project_id: projectId,
                name: name.trim(),
                value,
                role_scopes: grants,
                kind: "service",
              });
              setName("");
              setValue("");
              setGrants([]);
              refreshSecrets();
            }, "Stored. Effective on the very next run of any granted role — no restart needed.")
          }
        >
          Store credential
        </button>
      </div>

      {stored.length > 0 && (
        <table style={{ marginTop: 14 }}>
          <thead>
            <tr>
              <th>Credential</th>
              <th style={{ width: "32%" }}>Value</th>
              <th style={{ width: "34%" }}>Roles that receive it</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {stored.map((secret) => (
              <tr key={secret.id}>
                <td className="mono">
                  {secret.name}
                  <div className="help">
                    {secret.role_scopes.length ? `$${envVarName(secret.name)}` : "not injected"}
                  </div>
                </td>
                <td>
                  <StoredSecret
                    secretId={secret.id}
                    onRotate={async (next) => {
                      await post("/admin/secrets", {
                        project_id: projectId, name: secret.name, value: next,
                      });
                      refreshSecrets();
                    }}
                    note="Edit and save to rotate — live on the next run of any granted role."
                  />
                </td>
                <td>
                  <RoleChecks
                    roles={roles}
                    selected={secret.role_scopes}
                    onChange={(slugs) =>
                      act(async () => {
                        await post("/admin/secrets", {
                          project_id: projectId,
                          name: secret.name,
                          role_scopes: slugs,
                        });
                        refreshSecrets();
                      }, `Access to "${secret.name}" updated.`)
                    }
                  />
                  {secret.role_scopes.length === 0 && (
                    <span className="help">no roles — not injected anywhere</span>
                  )}
                </td>
                <td>
                  <button
                    className="small danger"
                    disabled={busy}
                    onClick={() => {
                      if (
                        !confirm(
                          `Delete credential '${secret.name}'? Granted roles lose it on ` +
                            "their next run.",
                        )
                      )
                        return;
                      act(async () => {
                        await del(`/admin/secrets/${secret.id}`);
                        refreshSecrets();
                      }, `Deleted '${secret.name}'.`);
                    }}
                  >
                    delete
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}

/** The rules a chairman reaches for from the Money page. Everything else — and
    these too — is editable under "Rules in force", which is generated from the
    server's schema. This list orders the money view; it never gates what can be
    changed. That distinction is the whole point: the old hand-kept list *was*
    the gate, and the rules a stuck company needed were not on it. */
const MONEY_GROUPS = ["Budgets", "Autonomy"];
const API_KEY_ONLY = new Set(["monthly_usd_cap"]);

/** One rule, typed from the schema. Numbers get their real bounds, booleans a
    checkbox, strings a text box — and every row says when it takes effect. */
function RuleField({
  field, value, onChange, current,
}: {
  field: ConstitutionField;
  value: string;
  onChange: (raw: string) => void;
  current: unknown;
}) {
  const changed = value !== String(current ?? "");
  return (
    <div>
      <label className="field">{field.label}</label>
      {field.type === "boolean" ? (
        <label style={{ display: "flex", gap: 6, alignItems: "center" }}>
          <input
            type="checkbox"
            style={{ width: "auto" }}
            checked={value === "true"}
            onChange={(e) => onChange(String(e.target.checked))}
          />
          {value === "true" ? "yes" : "no"}
        </label>
      ) : field.type === "string" ? (
        <input value={value} onChange={(e) => onChange(e.target.value)} />
      ) : (
        <input
          type="number"
          value={value}
          min={field.minimum ?? undefined}
          max={field.maximum ?? undefined}
          step={field.type === "integer" ? 1 : "any"}
          onChange={(e) => onChange(e.target.value)}
        />
      )}
      <p className="help" style={{ margin: "4px 0 0" }}>
        {field.help}
        {changed && (
          <>
            <br />
            <b>{String(current)} → {value || "(blank)"}</b>
          </>
        )}
      </p>
    </div>
  );
}

/** The reason box, the save button, and the confirmation that stands between
    them. A constitution change is in force from the next agent call, so the
    last thing before it lands should be a plain statement of what is about to
    be different — not a button that quietly did it. */
function AmendmentSave({
  projectId, changes, reason, setReason, act, busy, onSaved, extraJson, schema, cons,
}: {
  projectId: string;
  changes: Record<string, unknown>;
  reason: string;
  setReason: (value: string) => void;
  act: Act;
  busy: boolean;
  onSaved: () => void;
  extraJson?: string;
  schema: ConstitutionField[];
  cons: Record<string, unknown>;
}) {
  const [confirming, setConfirming] = useState(false);
  const count = Object.keys(changes).length;
  const advanced = (extraJson ?? "{}").trim() || "{}";
  const nothing = count === 0 && advanced === "{}";
  const labelOf = (key: string) => schema.find((f) => f.key === key)?.label ?? key;
  // A raw-JSON overlay bypasses the typed form, so it is worth naming in the
  // confirmation rather than letting it land unannounced alongside the fields.
  let advancedKeys: string[] = [];
  try {
    advancedKeys = Object.keys(JSON.parse(advanced));
  } catch {
    advancedKeys = [];
  }
  const badJson = advanced !== "{}" && advancedKeys.length === 0;

  async function save() {
    setConfirming(false);
    await act(async () => {
      const merged = { ...JSON.parse(advanced), ...changes };
      if (Object.keys(merged).length === 0)
        throw new Error("Nothing changed — edit a value first.");
      const result = await post<{ note: string }>(
        `/admin/projects/${projectId}/amendments`,
        { reason, changes: merged },
      );
      setReason("");
      onSaved();
      return result;
    }, (result) => (result as { note: string }).note);
  }

  return (
    <>
      <label className="field">Why this change? (required — recorded, shown to the CEO)</label>
      <input
        value={reason}
        placeholder="e.g. this is a memory-heavy job and forty facts is not enough"
        onChange={(e) => setReason(e.target.value)}
      />

      <div className="actions">
        <button
          className="primary"
          disabled={busy || reason.trim().length < 10 || nothing || badJson}
          onClick={() => setConfirming(true)}
        >
          {count > 0 ? `Review ${count} change${count === 1 ? "" : "s"}` : "Review change"}
        </button>
        {reason.trim().length > 0 && reason.trim().length < 10 && (
          <span className="help">the reason needs a real sentence (10+ characters)</span>
        )}
        {badJson && <span className="help">the JSON overlay is not valid JSON</span>}
      </div>

      {confirming && (
        <ConfirmModal
          title="This changes the rules your company runs under"
          confirmLabel="Change the rules"
          busy={busy}
          onConfirm={save}
          onCancel={() => setConfirming(false)}
        >
          <table>
            <thead>
              <tr><th>Rule</th><th>Now</th><th>Becomes</th></tr>
            </thead>
            <tbody>
              {Object.entries(changes).map(([key, value]) => (
                <tr key={key}>
                  <td>{labelOf(key)}<div className="help mono">{key}</div></td>
                  <td className="mono">{String(cons[key])}</td>
                  <td className="mono"><b>{String(value)}</b></td>
                </tr>
              ))}
              {advancedKeys.map((key) => (
                <tr key={`raw:${key}`}>
                  <td>{labelOf(key)}<div className="help mono">{key} — from the JSON overlay</div></td>
                  <td className="mono">{String(cons[key])}</td>
                  <td className="mono"><b>{String(JSON.parse(advanced)[key])}</b></td>
                </tr>
              ))}
            </tbody>
          </table>
          <p>What happens when you confirm:</p>
          <ul style={{ margin: "0 0 10px", paddingLeft: 18 }}>
            <li>
              <b>It is in force immediately.</b> The platform enforces the new values on the
              very next agent call — including runs already in flight.
            </li>
            <li>
              <b>There is no undo window.</b> To go back you amend it again; the old version
              stays in the project's history either way.
            </li>
            <li>
              <b>The CEO is told</b>, with your reason, in its next context pack — and it will
              re-plan against the new limits.
            </li>
            <li>
              Loosening a limit can cost real money: ceilings, wake brakes and budgets are what
              stop a company spending its way through the night.
            </li>
          </ul>
          <p style={{ marginBottom: 0 }} className="help">
            Recorded as: “{reason.trim()}”
          </p>
        </ConfirmModal>
      )}
    </>
  );
}

/** Turn the edited strings back into the types the constitution expects, and
    keep only what actually differs from what is in force. */
function changedRules(
  fields: ConstitutionField[],
  edits: Record<string, string>,
  cons: Record<string, unknown>,
): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const field of fields) {
    const raw = edits[field.key];
    if (raw === undefined || raw === String(cons[field.key] ?? "")) continue;
    if (field.type === "boolean") out[field.key] = raw === "true";
    else if (field.type === "string") out[field.key] = raw.trim() === "" ? null : raw.trim();
    else {
      const parsed = Number(raw);
      if (!Number.isFinite(parsed)) continue;
      out[field.key] = parsed;
    }
  }
  return out;
}

/** Eleven headings on one scrolling page is not navigation. Ordered by
    necessity, not by subsystem: the docs say the required path is brief →
    money → data sources, and everything after that is occasional. */
const SETTINGS_NAV = [
  { group: "The work" },
  { key: "brief", label: "Brief" },
  { key: "money", label: "Money" },
  { key: "data", label: "Data & credentials" },
  { group: "Connections" },
  { key: "integrations", label: "Integrations" },
  { group: "Governance" },
  { key: "rules", label: "Rules in force" },
  { group: "Housekeeping" },
  { key: "duplicate", label: "Duplicate & export" },
  { key: "advanced", label: "Advanced" },
] as const;

function Section({ on, children }: { on: boolean; children: React.ReactNode }) {
  return on ? <>{children}</> : null;
}

export default function Settings({
  projectId,
  dash,
  reload,
}: {
  projectId: string;
  dash: Dashboard;
  reload: () => void;
}) {
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [section, setSection] = useState<string>("brief");

  const [billingChoice, setBillingChoice] = useState<"api_key" | "subscription">(
    dash.billing_mode === "subscription" ? "subscription" : "api_key",
  );
  const [credential, setCredential] = useState("");
  const [busy, setBusy] = useState(false);

  const [runtime, setRuntime] = useState(String(dash.config["runtime"] ?? "agent_sdk"));
  const [heartbeatHours, setHeartbeatHours] = useState(
    String(dash.config["ceo_heartbeat_hours"] ?? 6),
  );

  const navigate = useNavigate();
  const [cloneSlug, setCloneSlug] = useState(() => nextInSeries(dash.slug, "-2"));
  const [cloneName, setCloneName] = useState(() => nextInSeries(dash.name, " (clone)"));
  // The usual reason to duplicate a company is to aim it at the next thing —
  // so the mandate is a field on the form, not something to fix afterwards.
  const [cloneGoal, setCloneGoal] = useState(dash.goal);
  const [goal, setGoal] = useState(dash.goal);

  const [amendReason, setAmendReason] = useState("");
  const [amendChanges, setAmendChanges] = useState("{}");
  // Every rule the constitution holds, described by the server well enough to
  // render an editor for. Values start at what is enforced right now; saving
  // proposes an amendment carrying only the ones you changed.
  const cons = dash.constitution as Record<string, unknown>;
  const [schema, setSchema] = useState<ConstitutionField[]>([]);
  const [edits, setEdits] = useState<Record<string, string>>({});
  useEffect(() => {
    get<ConstitutionField[]>("/admin/constitution/schema")
      .then((fields) => {
        setSchema(fields);
        setEdits(Object.fromEntries(fields.map((f) => [f.key, String(cons[f.key] ?? "")])));
      })
      .catch(() => {});
    // Re-seeding on every dash poll would fight the chairman's typing; the
    // constitution only moves when this page saves, and that calls reload().
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);
  const setRule = (key: string, raw: string) => setEdits((prev) => ({ ...prev, [key]: raw }));
  const changedGuardrails = changedRules(schema, edits, cons);
  const groups = [...new Set(schema.map((f) => f.group))];
  const { data: amendments, reload: reloadAmendments } = usePoll(
    () => get<AmendmentRow[]>(`/admin/projects/${projectId}/amendments`),
    10000,
  );

  // One fetch for the whole page: the billing card, the data sources and the
  // credentials table all need the same list, and the two cards that address a
  // secret by name (config only stores names) need it to find the id.
  const [secrets, setSecrets] = useState<SecretRow[]>([]);
  const refreshSecrets = useCallback(() => {
    get<SecretRow[]>(`/admin/secrets?project_id=${projectId}`).then(setSecrets).catch(() => {});
  }, [projectId]);
  useEffect(refreshSecrets, [refreshSecrets, dash.billing_secret_id]);

  const act: Act = async (fn, okMessage) => {
    setError(null);
    setMessage(null);
    setBusy(true);
    try {
      const result = await fn();
      setMessage(typeof okMessage === "function" ? okMessage(result) : okMessage);
      reload();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  async function connectBilling() {
    const name = billingChoice === "subscription" ? "claude-subscription-token" : "anthropic-api-key";
    await act(async () => {
      const secret = await post<{ id: string }>("/admin/secrets", {
        project_id: projectId,
        name,
        value: credential.trim(),
        role_scopes: [],
        kind: "billing",
      });
      await patch(`/admin/projects/${projectId}`, {
        billing_mode: billingChoice,
        billing_secret_id: secret.id,
      });
      setCredential("");
      // Preflight immediately: a bad credential should be news now, not after
      // a night of failed runs.
      const check = await post<{ ok: boolean; detail: string }>(
        `/admin/projects/${projectId}/billing_check`, {});
      if (!check.ok) throw new Error(`connected, but the check failed: ${check.detail}`);
    }, `Billing connected and verified: ${billingChoice} using "${name}". New runs use it immediately.`);
  }

  async function testBilling() {
    setError(null);
    setMessage(null);
    setBusy(true);
    try {
      const check = await post<{ ok: boolean; detail: string }>(
        `/admin/projects/${projectId}/billing_check`, {});
      if (check.ok) setMessage(check.detail);
      else setError(check.detail);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const billingReady = dash.billing_secret_id !== null;
  const runtimeNow = String(dash.config["runtime"] ?? "agent_sdk");

  async function cloneProject() {
    setError(null);
    setBusy(true);
    try {
      const twin = await post<CloneResult>(`/admin/projects/${projectId}/clone`, {
        slug: cloneSlug.trim(),
        name: cloneName.trim() || null,
        goal: cloneGoal.trim() || null,
      });
      // The twin's own data sources were tested with its own copied
      // credentials — a failure here is news worth stopping for, not a badge
      // to discover later.
      const broken = twin.copied.data_sources.filter((source) => !source.check.ok);
      if (broken.length) {
        alert(
          `Twin "${twin.slug}" created, but ${broken.length === 1 ? "a data source is" : "its data sources are"} ` +
            `not answering:\n\n${broken.map((s) => `"${s.name}": ${s.check.detail}`).join("\n")}\n\n` +
            "Fix the address in the twin's Settings → Data sources before starting it.",
        );
      }
      navigate(`/projects/${twin.id}`);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function downloadSetup() {
    setError(null);
    try {
      const bundle = await get<ProjectBundle>(`/admin/projects/${projectId}/export`);
      const blob = new Blob([JSON.stringify(bundle, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `${bundle.source_slug}-setup.json`;
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  return (
    <div className="settings-lay">
      <div className="srail">
        {SETTINGS_NAV.map((item) =>
          "group" in item ? (
            <div className="grp" key={item.group}>{item.group}</div>
          ) : (
            <button
              key={item.key}
              className={section === item.key ? "active" : ""}
              onClick={() => setSection(item.key)}
            >
              {item.label}
            </button>
          ),
        )}
      </div>

      <div>
      {message && <div className="notice-box">{message}</div>}
      {error && <div className="error">{error}</div>}

      {billingReady && runtimeNow === "agent_sdk" ? (
        <div className="notice-box" style={{ borderColor: "var(--good)", background: "rgba(12,163,12,0.08)" }}>
          ✓ <strong>Nothing needs doing on this page.</strong> Your agents are real Claude agents,
          and when they call Claude they pay with{" "}
          <strong>
            {dash.billing_secret_name}
            {dash.billing_mode === "subscription"
              ? " (your Claude subscription)"
              : " (a metered Anthropic API key)"}
          </strong>
          . Come back here only to change how agents pay, or to amend the project's rules.
        </div>
      ) : (
        <div className="error">
          {!billingReady
            ? "⚠ Agents have no way to pay for Claude yet — connect a credential below or every real run will fail."
            : "⚠ This project runs FAKE test agents (mock runtime) — nothing real will happen. Switch it under Advanced below."}
        </div>
      )}

      <Section on={section === "brief"}>
      <h2>Goal</h2>
      <div className="card">
        <p className="help" style={{ marginTop: 0 }}>
          The mandate every agent reads at the top of its context. Unlike the constitution — which
          constrains the company and is recorded as a formal amendment — this one simply
          redirects it, so it needs no written reason. The CEO is told what changed
          and asked to re-aim the backlog against it.
        </p>
        <textarea value={goal} onChange={(e) => setGoal(e.target.value)} minLength={10} />
        <div className="actions">
          <button
            className="primary"
            disabled={busy || goal.trim().length < 10 || goal.trim() === dash.goal}
            onClick={() =>
              act(
                () => patch(`/admin/projects/${projectId}`, { goal: goal.trim() }),
                "Goal changed. Agents read it on their next run; the CEO has been told to re-aim the backlog.",
              )
            }
          >
            Save goal
          </button>
          {goal.trim() !== dash.goal && (
            <button type="button" className="small" onClick={() => setGoal(dash.goal)}>
              Revert
            </button>
          )}
        </div>
      </div>

      </Section>

      <Section on={section === "money"}>
      <h2>How agents pay for Claude</h2>
      <div className="card">
        <p className="help" style={{ marginTop: 0 }}>
          Every agent run calls Claude, and that call needs a credential. Currently:{" "}
          {billingReady ? (
            <strong>
              {dash.billing_secret_name} — {dash.billing_mode === "subscription"
                ? "your Claude subscription (uses your plan's rate limits; no per-call charges)"
                : "an API key (each call bills your Anthropic account in dollars)"}
            </strong>
          ) : (
            <strong>none connected</strong>
          )}
          . Monthly safety cap:{" "}
          {dash.billing_mode === "subscription"
            ? `${Number(dash.constitution["monthly_token_cap"]).toLocaleString()} tokens`
            : `$${Number(dash.constitution["monthly_usd_cap"]).toLocaleString()}`}{" "}
          — the platform pauses the project by itself at 100%.
        </p>
        <label className="field">To change it, pick a method and paste the credential:</label>
        <div className="pill-row" style={{ marginBottom: 8 }}>
          <label style={{ display: "flex", gap: 6, alignItems: "center" }}>
            <input
              type="radio"
              style={{ width: "auto" }}
              checked={billingChoice === "api_key"}
              onChange={() => setBillingChoice("api_key")}
            />
            Anthropic API key — metered, USD budget
          </label>
          <label style={{ display: "flex", gap: 6, alignItems: "center" }}>
            <input
              type="radio"
              style={{ width: "auto" }}
              checked={billingChoice === "subscription"}
              onChange={() => setBillingChoice("subscription")}
            />
            Claude subscription — token budget
          </label>
        </div>
        <label className="field">
          {billingChoice === "subscription"
            ? "Subscription token — run `claude setup-token` in a terminal and paste the result (sk-ant-oat…)"
            : "API key from console.anthropic.com (sk-ant-api…)"}
        </label>
        <SecretInput
          value={credential}
          onChange={setCredential}
          placeholder={billingChoice === "subscription" ? "sk-ant-oat01-…" : "sk-ant-api03-…"}
        />
        <div className="actions">
          <button className="primary" disabled={busy || credential.trim().length < 10} onClick={connectBilling}>
            {busy ? "Connecting…" : "Connect"}
          </button>
          <span className="help">
            Stored encrypted. One click does all of it: store → attach → set mode.
          </span>
        </div>
        {billingChoice === "subscription" && (
          <p className="help">
            Subscription runs share your personal Claude rate limits and are for personal projects;
            use an API key for anything beyond that.
          </p>
        )}

        {secrets.filter((s) => s.kind === "billing").length > 0 && (
          <>
            <label className="field">Stored billing credentials for this project</label>
            <table>
              <thead>
                <tr>
                  <th>Name</th>
                  <th style={{ width: "30%" }}>Key</th>
                  <th>Status</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {/* Billing kind only: service credentials (WordPress passwords,
                    API keys for other services) can never pay for Claude — the
                    server refuses them as billing_secret_id. */}
                {secrets.filter((s) => s.kind === "billing").map((secret) => {
                  const active = secret.id === dash.billing_secret_id;
                  return (
                    <tr key={secret.id}>
                      <td className="mono">{secret.name}</td>
                      <td>
                        <StoredSecret secretId={secret.id} />
                      </td>
                      <td>
                        {active ? (
                          <span className="badge good">✓ ACTIVE — agents pay with this one</span>
                        ) : (
                          <span className="help">stored, not in use</span>
                        )}
                      </td>
                      <td>
                        {active ? (
                          <button className="small" disabled={busy} onClick={testBilling}>
                            test
                          </button>
                        ) : (
                          <button
                            className="small"
                            onClick={() =>
                              act(
                                () =>
                                  patch(`/admin/projects/${projectId}`, {
                                    billing_secret_id: secret.id,
                                  }),
                                `Agents now pay with "${secret.name}".`,
                              )
                            }
                          >
                            switch to this one
                          </button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </>
        )}
      </div>

      </Section>

      <Section on={section === "data"}>
      <h2>Data sources — MCP servers</h2>
      <div className="card">
        <DataSources
          projectId={projectId}
          dash={dash}
          secrets={secrets}
          act={act}
          busy={busy}
        />
      </div>

      </Section>

      <Section on={section === "data"}>
      <h2>Variables &amp; credentials</h2>
      <div className="card">
        <p className="help" style={{ marginTop: 0 }}>
          API keys, passwords and plain configuration values your agents need for outside
          services. <strong>Never paste a key into the boardroom</strong> — messages become model
          context and leave the box. Stored here, a credential is encrypted at rest and injected
          into the sandboxes of the roles you tick, as an environment variable; grants take effect
          on the next run. <em>Agents</em> are told the variable's name and never its value — but
          you are the chairman, so press the eye and read your own.
        </p>
        <CredentialsManager
          projectId={projectId}
          dash={dash}
          secrets={secrets}
          refreshSecrets={refreshSecrets}
          act={act}
          busy={busy}
        />
      </div>

      </Section>

      <Section on={section === "integrations"}>
      <h2>Slack bridge</h2>
      <div className="card">
        <SlackBridge
          projectId={projectId}
          dash={dash}
          secrets={secrets}
          act={act}
          busy={busy}
        />
      </div>

      </Section>

      <Section on={section === "integrations"}>
      <h2>GitHub workspace</h2>
      <div className="card">
        <GithubWorkspace projectId={projectId} dash={dash} act={act} busy={busy} />
      </div>

      </Section>

      <Section on={section === "duplicate"}>
      <h2>Duplicate this project</h2>
      <div className="card">
        <p className="help" style={{ marginTop: 0 }}>
          A running twin, right here: same teams, same people, same constitution, and the{" "}
          <i>exact</i> role versions this project runs — not whatever the role library has moved
          on to. Every credential comes across with its value, billing included, so there is
          nothing to re-paste; the twin's data sources are tested with its own copies before you
          land on it.
        </p>
        <p className="help">
          Left behind on purpose: this project's history — runs, tasks, messages, memory, files,
          amendments — and its Slack channel map, since two companies posting into one room helps
          nobody. The Slack tokens do come along, so you only re-map rooms.
        </p>
        <div className="row">
          <div>
            <label className="field">Twin's slug (a-z, 0-9, dashes)</label>
            <input
              value={cloneSlug}
              onChange={(e) => setCloneSlug(e.target.value)}
              pattern="[a-z0-9-]{2,64}"
            />
          </div>
          <div>
            <label className="field">Twin's name</label>
            <input value={cloneName} onChange={(e) => setCloneName(e.target.value)} />
          </div>
        </div>
        <label className="field">
          Twin's goal — the machinery is what you're reusing; point it wherever you like
        </label>
        <textarea value={cloneGoal} onChange={(e) => setCloneGoal(e.target.value)} minLength={10} />
        <div className="actions">
          <button
            className="primary"
            disabled={busy || !/^[a-z0-9-]{2,64}$/.test(cloneSlug.trim())}
            onClick={cloneProject}
          >
            {busy ? "Duplicating…" : "Create the twin"}
          </button>
          <span className="help">Starts stopped — review it, then press Start.</span>
        </div>
      </div>

      </Section>

      <Section on={section === "duplicate"}>
      <h2>Setup file</h2>
      <div className="card">
        <p className="help" style={{ marginTop: 0 }}>
          For <i>another</i> install. Downloads this project's design as one file: teams, role
          definitions, the constitution, configuration, and the <i>names</i> of its credentials —
          never their values, which cannot leave the deployment that encrypted them. Upload it on
          the New Project page; you'll re-enter credential values there and connect billing
          afterwards. Duplicating on this install needs none of that — use the twin above.
        </p>
        <div className="actions">
          <button onClick={downloadSetup}>Download setup file</button>
        </div>
      </div>

      </Section>

      <Section on={section === "advanced"}>
      <h2>Advanced</h2>
      <details className="card">
        <summary>
          Agent execution & planning cadence — you almost never need to touch these
        </summary>
        <div className="row" style={{ marginTop: 12 }}>
          <div>
            <label className="field">
              Agent execution — are the agents real, or test stand-ins?
            </label>
            <select value={runtime} onChange={(e) => setRuntime(e.target.value)}>
              <option value="agent_sdk">Real Claude agents (normal)</option>
              <option value="mock">
                Test stand-ins — scripted fakes for platform development; free, do nothing real
              </option>
            </select>
          </div>
          <div>
            <label className="field">
              CEO self-wake interval (hours) — the CEO plans on this clock even when idle,
              and every wake is a paid model call
            </label>
            <input
              type="number"
              min={0.1}
              step={0.5}
              value={heartbeatHours}
              onChange={(e) => setHeartbeatHours(e.target.value)}
            />
          </div>
        </div>
        <div className="actions">
          <button
            disabled={busy}
            onClick={() =>
              act(
                () =>
                  patch(`/admin/projects/${projectId}`, {
                    config: {
                      runtime,
                      ceo_heartbeat_hours: Number(heartbeatHours) || 6,
                    },
                  }),
                "Applied.",
              )
            }
          >
            Apply
          </button>
        </div>

        <label className="field">Webhook signing secrets</label>
        <p className="help" style={{ marginTop: 0 }}>
          These sign the inbound evidence connectors. They live only in project config — no run
          ever holds one, which is what makes webhook evidence impossible for an agent to forge.
          Point your senders at them; regenerating invalidates the old pair.
        </p>
        {dash.config["webhook_secret"] || dash.config["github_webhook_secret"] ? (
          <div className="row">
            <div>
              <label className="field">GitHub</label>
              <SecretInput readOnly value={String(dash.config["github_webhook_secret"] ?? "")} />
            </div>
            <div>
              <label className="field">Generic evidence sender</label>
              <SecretInput readOnly value={String(dash.config["webhook_secret"] ?? "")} />
            </div>
          </div>
        ) : (
          <p className="help">Not generated yet.</p>
        )}
        <div className="actions">
          <button
            disabled={busy}
            onClick={() => {
              if (
                dash.config["webhook_secret"] &&
                !confirm("Regenerate? Any sender still using the old secrets stops verifying.")
              )
                return;
              act(
                () => post(`/admin/projects/${projectId}/webhook_secrets`, {}),
                "New signing secrets generated — update your senders.",
              );
            }}
          >
            {dash.config["webhook_secret"] ? "Regenerate" : "Generate signing secrets"}
          </button>
        </div>
      </details>

      </Section>

      <Section on={section === "rules"}>
      <h2>Rules in force</h2>
      <div className="card">
        <p className="help" style={{ marginTop: 0 }}>
          Every limit the platform enforces, and every one of them yours to change. Edit what
          you need, say why, and confirm — <b>the new rules are in force on the very next agent
          call</b>. Nothing waits. Every change is recorded with your reason and disclosed to
          the CEO, which is what keeps the rulebook accountable: the agents cannot move these
          limits, and you always can.
        </p>
        {schema.length === 0 && <p className="help">Loading the rulebook…</p>}
        {groups.map((group) => (
          <div key={group}>
            <label className="field" style={{ marginTop: 14 }}>{group}</label>
            <div className="row">
              {schema
                .filter((f) => f.group === group)
                .filter((f) => !API_KEY_ONLY.has(f.key) || dash.billing_mode === "api_key")
                .map((f) => (
                  <RuleField
                    key={f.key}
                    field={f}
                    value={edits[f.key] ?? ""}
                    current={cons[f.key]}
                    onChange={(raw) => setRule(f.key, raw)}
                  />
                ))}
            </div>
          </div>
        ))}
        <AmendmentSave
          projectId={projectId}
          changes={changedGuardrails}
          reason={amendReason}
          setReason={setAmendReason}
          act={act}
          busy={busy}
          schema={schema}
          cons={cons}
          onSaved={reloadAmendments}
        />
      </div>

      </Section>

      <Section on={section === "money"}>
      <h2>Budgets &amp; guardrails</h2>
      <div className="card">
        <p className="help">
          The money limits the platform enforces right now. Edit what you need and confirm —
          they are in force on the next agent call, recorded with your reason, and disclosed
          to the CEO.
        </p>
        <div className="row">
          {schema
            .filter((f) => MONEY_GROUPS.includes(f.group))
            .filter((f) => !API_KEY_ONLY.has(f.key) || dash.billing_mode === "api_key")
            .map((f) => (
              <RuleField
                key={f.key}
                field={f}
                value={edits[f.key] ?? ""}
                current={cons[f.key]}
                onChange={(raw) => setRule(f.key, raw)}
              />
            ))}
        </div>
        <p className="help">
          Every other rule — memory, work discipline, wake brakes, reporting — is under{" "}
          <button className="small" onClick={() => setSection("rules")}>Rules in force</button>.
        </p>
        <details style={{ margin: "10px 0" }}>
          <summary className="help">
            Advanced: amend by raw JSON overlay (an escape hatch; every rule has a box now)
          </summary>
          <textarea
            className="mono"
            style={{ marginTop: 8 }}
            value={amendChanges}
            onChange={(e) => setAmendChanges(e.target.value)}
          />
        </details>
        <AmendmentSave
          projectId={projectId}
          changes={changedGuardrails}
          reason={amendReason}
          setReason={setAmendReason}
          act={act}
          busy={busy}
          schema={schema}
          cons={cons}
          extraJson={amendChanges}
          onSaved={() => {
            setAmendChanges("{}");
            reloadAmendments();
          }}
        />

        {amendments && amendments.length > 0 && (
          <>
            {/* The history of the rulebook. Nothing new lands here as pending
                any more, but rows written while the old cooling-off existed can
                still be waiting, so the countdown and cancel stay. */}
            <label className="field">Every change made to these rules</label>
            <table>
              <thead>
                <tr>
                  <th>Status</th>
                  <th style={{ width: "28%" }}>Reason</th>
                  <th style={{ width: "30%" }}>Changes</th>
                  <th>Effective</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {amendments.map((amendment) => (
                  <tr key={amendment.id}>
                    <td>
                      {amendmentBadge(amendment.status)}
                      {amendment.disclosed && <div className="help">disclosed to CEO</div>}
                    </td>
                    <td>{amendment.reason}</td>
                    <td>
                      <pre className="block" style={{ margin: 0 }}>
                        {JSON.stringify(amendment.changes, null, 1)}
                      </pre>
                    </td>
                    <td className="mono">
                      {fmtWhen(amendment.effective_at)}
                      {amendment.status === "pending" && (
                        <div className="help">takes effect in {hoursUntil(amendment.effective_at)}h</div>
                      )}
                    </td>
                    <td>
                      {amendment.status === "pending" && (
                        <button
                          className="small danger"
                          disabled={busy}
                          onClick={() => {
                            if (!confirm("Cancel this amendment? Its changes will never take effect."))
                              return;
                            act(async () => {
                              await post(`/admin/amendments/${amendment.id}/cancel`);
                              reloadAmendments();
                            }, "Amendment cancelled — the constitution stays as it is.");
                          }}
                        >
                          cancel
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
      </div>
      </Section>
      </div>
    </div>
  );
}
