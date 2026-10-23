import { CredentialPicker, PlanMeter, ProviderCredentials } from "../../llmCredentials";
import { plainStatus } from "../../ui/kit";
import { Fragment, useCallback, useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
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
  SlackProvision,
} from "../../api";
import {
  BLANK_DATA_SOURCE,
  DataSourceDraft,
  DataSourceFields,
  DataSourceIntro,
  dataSourceBody,
  envVarName,
  RoleOption,
  savedName,
  SourceAuth,
  sourceReady,
} from "../../datasource";
import { fmtWhen, usePoll } from "../../hooks";
import type { FounderStatus, PresetCard } from "../StartProject";
import { RoleGrants } from "../../roles";
import { MembersPanel } from "./Members";
import { ConfirmModal, CredentialValue, fmtUnit, SecretInput, StoredSecret } from "../../ui";

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
  /** Set => the credential IS a file; its variable holds the path, not the value. */
  filename: string | null;
}

/** Config addresses a secret by name (`slack.bot_token_secret`, a data source's
    `url_secret`); revealing one needs its id. One lookup, used by every card. */
function secretByName(secrets: SecretRow[], name: string | null | undefined) {
  return name ? secrets.find((s) => s.name === name) : undefined;
}

function amendmentBadge(status: AmendmentRow["status"]) {
  const cls = status === "applied" ? "good" : status === "pending" ? "warn" : "";
  return <span className={`badge ${cls}`}>{plainStatus(status)}</span>;
}

function hoursUntil(iso: string): number {
  return Math.max(0, Math.ceil((new Date(iso).getTime() - Date.now()) / 3_600_000));
}

interface SlackConfig {
  /** Every field optional now: an absent one is inherited from the app-wide
      Slack connection, and `enabled: false` switches Slack off for this project
      however the platform is wired. */
  enabled?: boolean;
  bot_token_secret?: string;
  signing_secret?: string;
  app_token_secret?: string;
  chairman_slack_user?: string;
  channel_prefix?: string;
  channel_visibility?: string;
  channel_map: Record<string, string>;
}

/** What the server says this project's Slack wiring actually resolves to, and
    where each field came from. The sources are the point: an empty "bot token"
    box means nothing on its own — "not configured" and "inherited from the
    connection you set up once" are opposite situations and look identical. */
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

/** "inherited" / "this project's" — said once, in one place, so every row of
    the form says it the same way. */
function Origin({ source }: { source: string | undefined }) {
  if (source === "project") return <span className="badge">this project</span>;
  if (source === "global") return <span className="badge good">inherited</span>;
  return null;
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
  act: Act;
  busy: boolean;
}) {
  const existing = (dash.integrations["slack"] as SlackConfig | null | undefined) ?? null;
  const [view, setView] = useState<SlackView | null>(null);
  const [org, setOrg] = useState<OrgView | null>(null);
  const [botToken, setBotToken] = useState("");
  const [signingSecret, setSigningSecret] = useState("");
  const [appToken, setAppToken] = useState("");
  const [chairmanUser, setChairmanUser] = useState(existing?.chairman_slack_user ?? "");
  const [prefix, setPrefix] = useState(existing?.channel_prefix ?? "");
  const [channels, setChannels] = useState<Record<string, string>>(existing?.channel_map ?? {});
  const [provisioned, setProvisioned] = useState<SlackProvision | null>(null);
  const [showOverrides, setShowOverrides] = useState(false);

  const loadView = useCallback(() => {
    get<SlackView>(`/admin/projects/${projectId}/slack`).then(setView).catch(() => {});
  }, [projectId]);

  useEffect(() => {
    get<OrgView>(`/observe/projects/${projectId}/org`).then(setOrg).catch(() => {});
    loadView();
  }, [projectId, loadView]);

  // The dash reloads after every save; the channel map may have been rewritten
  // by a provisioning run, so the inputs follow it rather than the other way.
  useEffect(() => {
    setChannels(existing?.channel_map ?? {});
  }, [existing?.channel_map]);

  function setChannel(key: string, value: string) {
    setChannels((prev) => ({ ...prev, [key]: value }));
  }

  const off = existing?.enabled === false;
  const mapped = Object.entries(channels).filter(([, v]) => v.trim());
  const eventsUrl = `${location.origin}/webhooks/slack/${projectId}`;
  const anyToken = view?.sources.bot_token !== undefined;
  const overriding = ["bot_token", "signing_secret", "app_token", "chairman_slack_user",
                      "channel_prefix"].filter((f) => view?.sources[f] === "project");

  /** The whole slack block, rebuilt from state. PATCH merges at the level of
      `integrations`, so a partial block would silently drop the fields it
      omits — this is the one place that decides what the block contains. */
  function slackBlock(overrides: Partial<SlackConfig> = {}): SlackConfig {
    const block: SlackConfig = {
      channel_map: Object.fromEntries(mapped.map(([k, v]) => [k, v.trim()])),
    };
    if (botToken.trim() || existing?.bot_token_secret) block.bot_token_secret = "slack-bot";
    if (signingSecret.trim() || existing?.signing_secret) block.signing_secret = "slack-signing";
    if (appToken.trim() || existing?.app_token_secret) block.app_token_secret = "slack-app";
    if (chairmanUser.trim()) block.chairman_slack_user = chairmanUser.trim();
    if (prefix.trim()) block.channel_prefix = prefix.trim();
    if (existing?.channel_visibility) block.channel_visibility = existing.channel_visibility;
    if (existing?.enabled === false) block.enabled = false;
    return { ...block, ...overrides };
  }

  function channelInput(key: string, label: string, expected?: string) {
    return (
      <div key={key}>
        <label className="field">{label}</label>
        <input
          value={channels[key] ?? ""}
          onChange={(e) => setChannel(key, e.target.value)}
          placeholder={expected ? `C… (blank = not mirrored; #${expected} when created)`
                               : "C… (blank = not mirrored)"}
        />
      </div>
    );
  }

  const teamRooms = (org?.teams ?? []).map((team) => ({
    key: `team:${team.id}`, label: `Team room: ${team.name}`,
  }));
  const expectedByKey = Object.fromEntries(
    (view?.rooms ?? []).map((room) => [room.key, room.expected_name]));

  return (
    <>
      <p className="subtitle">
        The bus in Postgres stays the source of truth; Slack is a mirror plus an inbound path
        for you alone. Mapped channels mirror outward within ~15s. A Slack outage never blocks
        the project — messages queue and retry.
      </p>

      {/* Where this project's wiring comes from. Everything below is read
          against this sentence, so it goes first. */}
      {off ? (
        <p className="help">
          <span className="badge warn">off for this project</span> nothing mirrors and
          nothing arrives, however the app-wide Slack connection is configured. Every message
          still on the bus.
        </p>
      ) : view?.can_mirror ? (
        <p className="help">
          <span className="badge good">connected</span> mirroring {mapped.length} channel(s)
          {" "}using{" "}
          {overriding.length === 0
            ? "the app-wide Slack connection"
            : `this project's own ${overriding.length === 1 ? "token" : "tokens"}`}
          {view.can_receive ? "; inbound enabled for " : "; outbound only"}
          {view.can_receive ? <code>{view.chairman_slack_user}</code> : null}.
        </p>
      ) : view?.global_configured ? (
        <p className="help">
          <span className="badge good">ready</span> this project inherits the app-wide
          Slack connection and needs only its channels. Create them below and yaaf will
          tell you their names.
        </p>
      ) : (
        <p className="help">
          <span className="badge warn">not connected</span> connect Slack once in{" "}
          <a href="#/settings">Settings → Slack connection</a> and every project inherits it.
          You can also give this project its own tokens below.
        </p>
      )}

      {/* The rooms. This is the part that is genuinely per-project, so it is
          the part the screen leads with. */}
      <label className="field">Channels — Slack channel IDs, bot invited to each</label>
      <div className="row">
        {channelInput("boardroom", "Boardroom (chairman ↔ CEO)", expectedByKey["boardroom"])}
        {channelInput("leadership", "Leadership (CEO + leads)", expectedByKey["leadership"])}
      </div>
      <div className="row">
        {teamRooms.map((room) => channelInput(room.key, room.label, expectedByKey[room.key]))}
      </div>

      {!off && (view?.global_configured || anyToken) && (
        <>
          <div className="actions">
            <button
              disabled={busy}
              onClick={() =>
                act(async () => {
                  const result = await post<SlackProvision>(
                    `/admin/projects/${projectId}/slack/channels`);
                  setProvisioned(result);
                  loadView();
                  return result;
                }, (result) => (result as SlackProvision).detail)
              }
            >
              Create missing channels
            </button>
            <span className="help" style={{ margin: 0 }}>
              Makes every {view?.channel_visibility ?? "private"} room this project is
              missing — leadership and one per team, named{" "}
              <code>{`${view?.channel_prefix ?? dash.slug}-leadership`}</code> and so on —
              invites you to each, and fills the boxes above. Creating a project only makes
              its boardroom; this is the button for the rest, and for the rooms a team
              hired later still needs. Safe to run twice — an existing name is adopted,
              never duplicated.
            </span>
          </div>
          {provisioned && (
            <table>
              <tbody>
                {provisioned.channels.map((row) => (
                  <tr key={row.key}>
                    <td style={{ width: "30%" }}>{row.label}</td>
                    <td>
                      {row.error ? (
                        <span className="error">{row.error}</span>
                      ) : (
                        <>
                          <code>#{row.name}</code>{" "}
                          <span className="help">
                            {row.already_mapped ? "already mapped"
                              : row.created ? (row.invited ? "created, you are in it"
                                                           : "created")
                              : "adopted"}
                          </span>
                        </>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}

      {/* Overrides. Folded away by default because the answer for almost every
          project is "inherit everything and just map channels". */}
      <div className="actions">
        <button className="small" onClick={() => setShowOverrides(!showOverrides)}>
          {showOverrides ? "Hide" : "Override the Slack connection for this project"}
          {overriding.length > 0 && !showOverrides ? ` (${overriding.length} set)` : ""}
        </button>
      </div>

      {showOverrides && (
        <>
          <p className="help">
            Each field is inherited until you fill it in. Use this for a project that lives
            in a <em>different</em> Slack workspace than the rest — it needs its own tokens
            and nothing else special.
          </p>

          {existing && (
            <>
              <label className="field">This project&apos;s own stored tokens</label>
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
                                project_id: projectId, name: secret.name, value,
                                role_scopes: [],
                              });
                            }}
                            note="These never enter a sandbox — the bridge decrypts them
                                  server-side."
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
              <label className="field">
                Bot token (xoxb-…) <Origin source={view?.sources.bot_token} />
              </label>
              <SecretInput value={botToken} placeholder="xoxb-… (blank = inherit)"
                           onChange={setBotToken} />
            </div>
            <div>
              <label className="field">
                Signing secret <Origin source={view?.sources.signing_secret} />
              </label>
              <SecretInput value={signingSecret} placeholder="blank = inherit"
                           onChange={setSigningSecret} />
            </div>
            <div>
              <label className="field">
                App token (xapp-…) <Origin source={view?.sources.app_token} />
              </label>
              <SecretInput value={appToken} placeholder="xapp-… (blank = inherit)"
                           onChange={setAppToken} />
            </div>
          </div>
          <div className="row">
            <div>
              <label className="field">
                Your Slack member ID (U…){" "}
                <Origin source={view?.sources.chairman_slack_user} />
              </label>
              <input value={chairmanUser}
                     placeholder={view?.chairman_slack_user
                       ? `blank = inherit ${view.chairman_slack_user}` : "U…"}
                     onChange={(e) => setChairmanUser(e.target.value)} />
            </div>
            <div>
              <label className="field">
                Channel name prefix <Origin source={view?.sources.channel_prefix} />
              </label>
              <input value={prefix}
                     placeholder={`blank = ${view?.channel_prefix ?? dash.slug}`}
                     onChange={(e) => setPrefix(e.target.value)} />
            </div>
          </div>
        </>
      )}

      <div className="actions">
        <button
          className="primary"
          disabled={busy}
          onClick={() =>
            act(async () => {
              // Credentials are stored as ungranted service secrets: the bridge
              // decrypts them server-side; no agent sandbox ever receives them.
              for (const [name, value] of [
                ["slack-bot", botToken], ["slack-signing", signingSecret],
                ["slack-app", appToken],
              ] as [string, string][]) {
                if (value.trim()) {
                  await post("/admin/secrets", { project_id: projectId, name,
                    value: value.trim(), role_scopes: [], kind: "service" });
                }
              }
              await patch(`/admin/projects/${projectId}`,
                          { integrations: { slack: slackBlock() } });
              setBotToken("");
              setSigningSecret("");
              setAppToken("");
              loadView();
            }, "Saved. Mirroring follows within seconds.")
          }
        >
          Save Slack settings
        </button>
        {!off ? (
          <button
            disabled={busy}
            onClick={() => {
              if (!confirm("Turn Slack off for this project? It stops mirroring and stops "
                           + "accepting inbound messages, whatever the app-wide Slack "
                           + "connection says. The bus keeps everything."))
                return;
              act(
                () => patch(`/admin/projects/${projectId}`,
                            { integrations: { slack: slackBlock({ enabled: false }) } })
                  .then(loadView),
                "Slack is off for this project. The bus is unaffected.",
              );
            }}
          >
            Turn off for this project
          </button>
        ) : (
          <button
            disabled={busy}
            onClick={() =>
              act(
                () => patch(`/admin/projects/${projectId}`,
                            { integrations: { slack: slackBlock({ enabled: true }) } })
                  .then(loadView),
                "Slack is on again for this project.",
              )
            }
          >
            Turn back on
          </button>
        )}
        {overriding.length > 0 && (
          <button
            disabled={busy}
            onClick={() => {
              if (!confirm("Drop this project's own Slack tokens and inherit the app-wide "
                           + "Slack connection again? The channel map is kept."))
                return;
              act(async () => {
                await patch(`/admin/projects/${projectId}`, {
                  integrations: {
                    slack: {
                      channel_map: Object.fromEntries(mapped.map(([k, v]) => [k, v.trim()])),
                    },
                  },
                });
                setChairmanUser("");
                setPrefix("");
                loadView();
              }, "Back to the app-wide Slack connection.");
            }}
          >
            Reset to the app-wide connection
          </button>
        )}
      </div>

      <p className="help">
        To chat from Slack, Socket Mode is set up once in{" "}
        <a href="#/settings">Settings → Slack connection</a> and inherited here. The Event
        Subscriptions request URL <code>{eventsUrl}</code> remains the alternative for
        boxes that are publicly reachable.
      </p>
    </>
  );
}

interface DataSourceSaveResult {
  name: string;
  check: { ok: boolean; detail: string };
  needs_connect?: boolean;
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

/** The stored mode, with the legacy inference the server also applies: entries
    saved before OAuth existed carry no `auth`, and for them a token means bearer. */
function sourceAuthOf(source: McpServerEntry): SourceAuth {
  return source.auth ?? (source.bearer_secret ? "bearer" : "none");
}

function oauthBadge(source: McpServerEntry) {
  const status = source.oauth?.status;
  if (status === "connected") return <span className="badge good">connected</span>;
  if (status === "pending") return <span className="badge warn">sign-in pending</span>;
  if (status === "needs_reauth") return <span className="badge critical">needs re-auth</span>;
  return <span className="badge warn">not connected</span>;
}

/** Start the browser sign-in for an OAuth source. The tab must be opened by
    the click itself (popup blockers kill anything opened after an await), so
    callers pass one pre-opened blank and we point it at the provider. */
async function startOAuthSignIn(projectId: string, name: string, tab: Window | null) {
  const started = await post<{ authorize_url: string; note: string }>(
    `/admin/projects/${projectId}/mcp_servers/${name}/oauth/start`,
    {},
  );
  if (tab) tab.location.href = started.authorize_url;
  else window.open(started.authorize_url, "_blank");
  return started;
}

/** The org's teams as grant targets — one entry per distinct role, carrying
    the team(s) running it, which is how RoleTicks labels the boxes. */
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

function savedMessage(result: unknown): string {
  const saved = result as DataSourceSaveResult;
  if (saved.needs_connect)
    return `Saved '${saved.name}' — finish the provider's sign-in in the opened tab; ` +
      "this page will show it connect.";
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
  const [loadError, setLoadError] = useState<string | null>(null);
  const [dropToken, setDropToken] = useState(false);
  // Not prefilled from source.oauth.client_id: that id may be a dynamic
  // registration the server owns, and re-submitting it while a URL change
  // resets the connection would keep a client whose secret was just dropped.
  // The Advanced box is for a client the chairman pastes by hand.
  const [draft, setDraft] = useState<DataSourceDraft>({
    ...BLANK_DATA_SOURCE,
    name: source.name,
    roles: source.roles,
    access: source.access ?? "inline",
    auth: sourceAuthOf(source),
    scope: source.oauth?.scope ?? "",
  });
  const { url, auth } = draft;
  const heldToken = !!source.bearer_secret && sourceAuthOf(source) === "bearer";

  useEffect(() => {
    setLoadError(null);
    if (!urlSecretId) {
      // Either the list has not arrived yet, or the row really is gone — this
      // effect re-runs the moment an id shows up, so only the latter sticks.
      setLoadError("its stored address is missing — type it again to repair the source");
      return;
    }
    get<{ value: string }>(`/admin/secrets/${urlSecretId}/value`)
      .then((got) => setDraft((prev) => ({ ...prev, url: got.value })))
      .catch((e) => setLoadError((e as Error).message));
  }, [urlSecretId]);

  return (
    <div className="source-editor">
      <DataSourceFields
        value={draft}
        onChange={setDraft}
        roleOptions={roleOptions}
        showName={false}
        bearerLabel={
          <>
            Bearer token —{" "}
            {heldToken
              ? dropToken ? "will be removed" : "blank keeps the current one"
              : "none stored"}
          </>
        }
        bearerPlaceholder={heldToken ? "blank = keep current" : "paste the credential"}
        bearerDisabled={dropToken}
        belowBearer={heldToken && (
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
        rolesLabel="Which roles get this source"
      />
      <div className="actions">
        <button
          className="small primary"
          disabled={busy || !url.trim() || draft.roles.length === 0}
          onClick={() => {
            // Pre-open the tab in the click itself, before any await, or the
            // popup blocker eats the provider's sign-in page. Editing a
            // connected source's URL resets it server-side and answers
            // needs_connect, so an OAuth save may need the tab even when the
            // pre-save status was 'connected' — open it whenever auth is oauth.
            const tab = auth === "oauth" ? window.open("", "_blank") : null;
            act(async () => {
              try {
                const result = await post<DataSourceSaveResult>(
                  `/admin/projects/${projectId}/mcp_servers`,
                  {
                    ...dataSourceBody(draft),
                    // Omitted keeps the stored token; "" removes it; a value rotates.
                    bearer:
                      auth === "bearer" ? (dropToken ? "" : draft.bearer.trim() || undefined)
                      : auth === "none" && source.bearer_secret ? "" : undefined,
                  },
                );
                if (result.needs_connect) await startOAuthSignIn(projectId, source.name, tab);
                else tab?.close();
                onDone();
                return result;
              } catch (e) {
                tab?.close();
                throw e;
              }
            }, savedMessage);
          }}
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
  reload,
}: {
  projectId: string;
  dash: Dashboard;
  secrets: SecretRow[];
  act: Act;
  busy: boolean;
  reload: () => void;
}) {
  const sources = (dash.integrations["mcp_servers"] as McpServerEntry[] | undefined) ?? [];
  const [org, setOrg] = useState<OrgView | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState<DataSourceDraft>(BLANK_DATA_SOURCE);
  // The source whose sign-in tab is open right now. While set, the dash is
  // re-polled so the page notices the callback landing without a refresh.
  const [connecting, setConnecting] = useState<string | null>(null);

  useEffect(() => {
    get<OrgView>(`/observe/projects/${projectId}/org`).then(setOrg).catch(() => {});
  }, [projectId]);

  useEffect(() => {
    if (!connecting) return;
    const startedAt = Date.now();
    const timer = window.setInterval(() => {
      if (Date.now() - startedAt > 120_000) setConnecting(null);
      else reload();
    }, 3000);
    return () => window.clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [connecting]);
  useEffect(() => {
    if (connecting &&
        sources.find((s) => s.name === connecting)?.oauth?.status === "connected")
      setConnecting(null);
  }, [sources, connecting]);

  const roleOptions = roleOptionsOf(org);

  return (
    <>
      <DataSourceIntro>
        To change one, press <b>edit</b> — no role editing, no repin, no CLI.
      </DataSourceIntro>

      {sources.length > 0 && (
        <table style={{ marginBottom: 14 }}>
          <thead>
            <tr>
              <th>Name</th>
              <th>Address</th>
              <th>Read as</th>
              <th>Roles</th>
              <th style={{ width: "28%" }}>Last check</th>
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
                    {(source.access ?? "inline") === "bulk" ? (
                      <>
                        <span className="badge accent">bulk</span>
                        <div className="help">to a file, via data_fetch</div>
                      </>
                    ) : (
                      <>
                        <span className="badge">inline</span>
                        <div className="help">into the model's context</div>
                      </>
                    )}
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
                    {sourceAuthOf(source) === "oauth" && <>{oauthBadge(source)}{" "}</>}
                    {checkBadge(source.last_check)}
                    {source.oauth?.status === "connected" && source.oauth.expires_at && (
                      <div className="help">
                        token refreshes before {fmtWhen(source.oauth.expires_at)}
                      </div>
                    )}
                    {source.last_check && (
                      <div className="help">
                        {source.last_check.detail} — {fmtWhen(source.last_check.at)}
                      </div>
                    )}
                  </td>
                  <td>
                    <div className="actions" style={{ margin: 0 }}>
                      {sourceAuthOf(source) === "oauth" && (
                        <button
                          className={`small${source.oauth?.status === "connected" ? "" : " primary"}`}
                          disabled={busy}
                          onClick={() => {
                            // Opened in the click itself — see startOAuthSignIn.
                            const tab = window.open("", "_blank");
                            act(async () => {
                              try {
                                const started = await startOAuthSignIn(
                                  projectId, source.name, tab);
                                setConnecting(source.name);
                                return started;
                              } catch (e) {
                                tab?.close();
                                throw e;
                              }
                            }, `Sign-in opened for '${source.name}' — finish it in the ` +
                               "new tab; this page will show it connect.");
                          }}
                        >
                          {source.oauth?.status === "connected" ||
                           source.oauth?.status === "needs_reauth"
                            ? "reconnect" : "connect"}
                        </button>
                      )}
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
                                "secrets are deleted too" +
                                (sourceAuthOf(source) === "oauth"
                                  ? " (including its OAuth tokens)" : "") +
                                "; granted roles lose these tools on their next run.",
                            )
                          )
                            return;
                          act(
                            () => del(`/admin/projects/${projectId}/mcp_servers/${source.name}`),
                            `Removed '${source.name}'. Granted roles lose it on their next run.`,
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
                    <td colSpan={6}>
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
      <DataSourceFields value={draft} onChange={setDraft} roleOptions={roleOptions} />
      <div className="actions">
        <button
          className="small primary"
          disabled={busy || !sourceReady(draft)}
          onClick={() => {
            const name = savedName(draft);
            // Pre-open the sign-in tab inside the click, before any await —
            // a tab opened after the network round-trip is popup-blocked.
            const tab = draft.auth === "oauth" ? window.open("", "_blank") : null;
            act(async () => {
              try {
                const result = await post<DataSourceSaveResult>(
                  `/admin/projects/${projectId}/mcp_servers`,
                  dataSourceBody(draft),
                );
                if (result.needs_connect) {
                  await startOAuthSignIn(projectId, name, tab);
                  setConnecting(name);
                } else tab?.close();
                setDraft(BLANK_DATA_SOURCE);
                return result;
              } catch (e) {
                tab?.close();
                throw e;
              }
            }, savedMessage);
          }}
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
  org,
  secrets,
  refreshSecrets,
  act,
  busy,
}: {
  projectId: string;
  dash: Dashboard;
  org: OrgView | null;
  secrets: SecretRow[];
  refreshSecrets: () => void;
  act: Act;
  busy: boolean;
}) {
  const [name, setName] = useState("");
  const [value, setValue] = useState("");
  const [filename, setFilename] = useState<string | null>(null);
  const [grants, setGrants] = useState<string[]>([]);

  // Only the credentials the chairman created by hand. The rest are owned by
  // the thing that made them and are edited there, so one object never shows
  // up as three rows: billing lives in the billing card, a data source's
  // address and token live on the source, Slack's tokens live on the bridge.
  const slack = dash.integrations["slack"] as SlackConfig | null | undefined;
  const sources = (dash.integrations["mcp_servers"] as McpServerEntry[] | undefined) ?? [];
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
              {filename !== null && ", holding the path to the file"}
            </div>
          )}
        </div>
        <div>
          <label className="field">Value</label>
          <CredentialValue
            value={value}
            filename={filename}
            onChange={(next) => { setValue(next.value); setFilename(next.filename); }}
            placeholder={name.trim() === "wordpress" ? "username:xxxx xxxx xxxx xxxx" : "the key itself"}
          />
        </div>
      </div>
      <label className="field">Who receives it (nobody, until you grant)</label>
      <RoleGrants org={org} selected={grants} onChange={setGrants} secretName={name} />
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
                ...(filename === null ? {} : { filename }),
              });
              setName("");
              setValue("");
              setFilename(null);
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
              <th style={{ width: "34%" }}>Who receives it</th>
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
                    {secret.filename && ` → ${secret.filename}`}
                  </div>
                </td>
                <td>
                  <StoredSecret
                    secretId={secret.id}
                    // A file rotates as a file: the door takes base64 and the
                    // shape is fixed at creation, so re-sending the name keeps
                    // it one. What the eye shows is the decoded text.
                    isFile={!!secret.filename}
                    onRotate={async (next) => {
                      await post("/admin/secrets", {
                        project_id: projectId, name: secret.name, value: next,
                        ...(secret.filename ? { filename: secret.filename } : {}),
                      });
                      refreshSecrets();
                    }}
                    note="Edit and save to rotate — live on the next run of any granted role."
                  />
                </td>
                <td>
                  <RoleGrants
                    org={org}
                    secretName={secret.name}
                    selected={secret.role_scopes}
                    onChange={(slugs: string[]) =>
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

/** A rule the platform is not currently reading, and why.
 *
 *  Only one budget cap is ever enforced — `policy.check_budget` picks it from
 *  the project's billing mode — and the cycle rhythm exists for Operate
 *  projects only. The old page *hid* the unused USD cap, which is the same
 *  mistake as a hand-kept editable list: a rule you cannot see is a rule you
 *  cannot change. These stay on screen, dimmed, saying why they are asleep. */
function dormantReason(key: string, dash: Dashboard): string | null {
  if (key === "monthly_usd_cap" && dash.billing_mode !== "api_key")
    return "Not enforced right now — this project bills by subscription, so the token cap is the live budget.";
  if (key === "monthly_token_cap" && dash.billing_mode === "api_key")
    return "Not enforced right now — this project bills by API key, so the USD cap is the live budget.";
  if ((key === "cycle_frequency" || key === "cycle_day") && dash.mode !== "operate")
    return `Operate projects only — this one is in ${dash.mode} mode, so no cycle rolls.`;
  return null;
}

/** Two rules move money but live on other tabs by their group. Naming them
    beats copying them onto Money: a rule editable in two places with one shared
    edit state is exactly what this page is being dug out of. */
const MONEY_ELSEWHERE = [
  { key: "max_usd_per_run", theme: "Pace & brakes" },
  { key: "approval_threshold_usd", theme: "Autonomy" },
];

/** The IANA zones this browser knows. `timezone` is validated server-side
    against exactly this list, so typing it blind was a guessing game. */
function timezoneList(): string[] {
  const intl = Intl as unknown as { supportedValuesOf?: (k: string) => string[] };
  try {
    return intl.supportedValuesOf?.("timeZone") ?? [];
  } catch {
    return [];
  }
}

/** One rule, one row: the sentence on the left, the value on the right.
    Numbers get their real bounds, booleans a checkbox, a rule with a fixed set
    of answers gets that set — and every row says what it was and what it is
    about to become. */
function RuleField({
  field, value, onChange, current, dormant, flash,
}: {
  field: ConstitutionField;
  value: string;
  onChange: (raw: string) => void;
  current: unknown;
  dormant: string | null;
  flash: boolean;
}) {
  const changed = value !== String(current ?? "");
  // "Changed from default" is the only cheap way to see what this project has
  // actually been tuned away from stock without diffing against the docs.
  const stock = field.default === null || field.default === undefined
    ? "" : String(field.default);
  const offStock = String(current ?? "") !== stock;
  return (
    <div className={`rule${changed ? " dirty" : ""}${dormant ? " dimmed" : ""}` +
                    `${flash ? " flash" : ""}`} id={`rule-${field.key}`}>
      <div>
        <div className="lbl">
          {field.label}
          {offStock && !changed && (
            <span className="badge" style={{ marginLeft: 8 }}>changed from default</span>
          )}
        </div>
        <p className="help">
          {field.help}
          {stock !== "" && <> <span className="mono">default {stock}</span></>}
          {dormant && <><br /><i>{dormant}</i></>}
          {changed && (
            <>
              <br />
              <b>{String(current) || "(blank)"} → {value || "(blank)"}</b>
            </>
          )}
        </p>
      </div>
      <div className="ctl">
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
        ) : field.choices ? (
          <select value={value} onChange={(e) => onChange(e.target.value)}>
            {field.choices.map((choice) => (
              <option key={choice} value={choice}>{choice}</option>
            ))}
          </select>
        ) : field.key === "timezone" ? (
          <>
            <input list="tzlist" value={value} onChange={(e) => onChange(e.target.value)} />
            <datalist id="tzlist">
              {timezoneList().map((zone) => <option key={zone} value={zone} />)}
            </datalist>
          </>
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
      </div>
    </div>
  );
}

/** The reason box, the save button, and the confirmation that stands between
    them. A constitution change is in force from the next agent call, so the
    last thing before it lands should be a plain statement of what is about to
    be different — not a button that quietly did it.

    It rides in a tray pinned to the bottom of the pane, because edits span
    tabs: the question the old flat page could not answer was "what have I
    actually touched?", and scrolling thirty-nine fields to find out is not an
    answer. Nothing pending, no tray. */
function AmendmentSave({
  projectId, changes, reason, setReason, act, busy, onSaved, onDiscard,
  extraJson, schema, cons,
}: {
  projectId: string;
  changes: Record<string, unknown>;
  reason: string;
  setReason: (value: string) => void;
  act: Act;
  busy: boolean;
  onSaved: () => void;
  onDiscard: () => void;
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

  // No pending edit, no tray — the bar only exists to answer a question the
  // chairman has actually asked by typing in a box.
  if (nothing && !badJson) return null;

  return (
    <>
      <div className="rule-tray">
        <b>
          {count > 0
            ? `${count} pending change${count === 1 ? "" : "s"}`
            : "A pending change from the JSON overlay"}
        </b>
        {count > 0 && (
          <p className="diffs">
            {Object.entries(changes).map(([key, value], i) => (
              <Fragment key={key}>
                {i > 0 && " · "}
                {labelOf(key)} <span className="mono">{String(cons[key])}→{String(value)}</span>
              </Fragment>
            ))}
          </p>
        )}
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
          <button type="button" disabled={busy} onClick={onDiscard}>Discard</button>
          {reason.trim().length > 0 && reason.trim().length < 10 && (
            <span className="help">the reason needs a real sentence (10+ characters)</span>
          )}
          {badJson && <span className="help">the JSON overlay is not valid JSON</span>}
        </div>
      </div>

      {confirming && (
        <ConfirmModal
          title="This changes the rules your project runs under"
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
              stop a project spending its way through the night.
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

/** Re-pick a card on a project that is already running.

    The rules a card writes have always been editable here, one box at a time —
    which for the tempo card is thirteen boxes across three tabs, and is the
    "too many levers" problem relocated from the creation form to this page
    rather than solved. Picking a card stages the whole bundle into the same
    dirty-field state a typed edit uses, so the existing save files it as ONE
    amendment with one written reason, in force from the next agent call.

    A project whose rules were edited individually is reported as hand-tuned and
    never rounded to the nearest card. Telling a chairman he is "on Working
    hours" when he is not is how he later reads a blurb as a description of his
    own project and believes something untrue about what it costs. */
function CardPicker({
  title, help, cards, cons, edits, setEdits,
}: {
  title: string;
  help: string;
  cards: PresetCard[];
  cons: Record<string, unknown>;
  edits: Record<string, string>;
  setEdits: (fn: (prev: Record<string, string>) => Record<string, string>) => void;
}) {
  if (!cards.length) return null;
  const asEdit = (value: unknown) => String(value ?? "");
  // What the project IS right now, and what it would be if the staged edits
  // were saved. The second is what the picker highlights: a chairman who has
  // just clicked a card should see that card selected, before he saves.
  const matches = (card: PresetCard, read: (key: string) => unknown) =>
    Object.entries(card.constitution).every(([k, v]) => asEdit(read(k)) === asEdit(v));
  const staged = cards.find((c) => matches(c, (k) => (k in edits ? edits[k] : cons[k])));
  const inForce = cards.find((c) => matches(c, (k) => cons[k]));

  return (
    <div className="card" style={{ marginBottom: 12 }}>
      <label className="field">{title}</label>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(210px, 1fr))",
        gap: 10, marginTop: 6 }}>
        {cards.map((card) => (
          <button key={card.value} type="button"
            onClick={() => setEdits((prev) => ({
              ...prev,
              ...Object.fromEntries(
                Object.entries(card.constitution).map(([k, v]) => [k, asEdit(v)])),
            }))}
            className="textblock"
            style={{
              font: "inherit", textAlign: "left", cursor: "pointer",
              background: staged?.value === card.value
                ? "rgba(var(--accent-rgb), 0.09)" : "var(--surface)",
              border: staged?.value === card.value
                ? "1px solid var(--accent)" : "1px solid var(--border)",
              borderRadius: 8, padding: "12px 14px",
            }}>
            <div style={{ fontWeight: 650, fontSize: 14.5, color: "var(--ink)" }}>
              {staged?.value === card.value ? "◉" : "○"} {card.label}
            </div>
            {card.headline && (
              <div style={{ fontSize: 12.5, color: "var(--accent)", marginTop: 4,
                fontVariantNumeric: "tabular-nums" }}>{card.headline}</div>
            )}
            <div style={{ fontSize: 12.5, color: "var(--ink-2)", marginTop: 6 }}>{card.blurb}</div>
          </button>
        ))}
      </div>
      <p className="help" style={{ marginTop: 10 }}>
        {inForce
          ? `In force: ${inForce.label}. `
          : "In force: hand-tuned — these rules were set individually, so no card "
            + "describes this project. Picking one replaces all of them. "}
        {help}
      </p>
      {staged && inForce && staged.value !== inForce.value && (
        <p className="help" style={{ marginTop: 6, color: "var(--warn, var(--ink-2))" }}>
          Staged, not saved: {Object.keys(staged.constitution).length} rules move from
          {" "}<b>{inForce.label}</b> to <b>{staged.label}</b>, some of them on other tabs.
          Give a reason below and save, and it binds from the next agent call.
        </p>
      )}
    </div>
  );
}

/** The three dials, read before the rulebook: which card each family is set
    to, in one line apiece, so "how is this project set up?" is answered
    without opening three tabs. Each line jumps to the tab that changes it. */
function DialSummary({
  presets, cons, usd, onOpen,
}: {
  presets: FounderStatus["presets"];
  cons: Record<string, unknown>;
  usd: boolean;
  onOpen: (theme: string) => void;
}) {
  const cap = Number(usd ? cons.monthly_usd_cap : cons.monthly_token_cap) || 0;
  const wall = cap * (Number(cons.hard_stop_multiple) || 1);
  const amount = (n: number) => usd ? fmtUnit(Math.round(n), "usd") : `${fmtUnit(n, "tokens")} tokens`;
  // Two lines ask him about a purchase — the threshold (at or over) and the
  // per-purchase line (over, when set) — and the lower one is the one he meets.
  const threshold = Number(cons.approval_threshold_usd) || 0;
  const perPurchase = Number(cons.max_usd_per_purchase) || 0;
  const asksAt = perPurchase > 0 && perPurchase < threshold
    ? { usd: perPurchase, words: "over" } : { usd: threshold, words: "at or over" };
  const asEdit = (value: unknown) => String(value ?? "");
  const inForce = (cards: PresetCard[]) => cards.find((card) =>
    Object.entries(card.constitution).every(([k, v]) => asEdit(cons[k]) === asEdit(v)));
  const dials: { name: string; theme: string; cards: PresetCard[] }[] = [
    { name: "Say-so", theme: "Autonomy", cards: presets.autonomy },
    { name: "Pace", theme: "Pace & brakes", cards: presets.tempo },
    { name: "Quality bar", theme: "The bar", cards: presets.bar },
  ];
  return (
    <div className="dial-summary">
      <div className="dial-row">
        <span className="dial-name">Money</span>
        <span className="dial-value">
          <b>{amount(cap)} a month</b>
          <span className="help"> · pauses itself at {amount(wall)}
            {asksAt.usd > 0
              ? ` · asks you before any purchase ${asksAt.words} $${asksAt.usd.toLocaleString()}`
              : " · asks you before every purchase"}</span>
        </span>
        <button className="small" onClick={() => onOpen("Money")}>Change</button>
      </div>
      {dials.filter((d) => d.cards.length).map((d) => {
        const card = inForce(d.cards);
        return (
          <div key={d.name} className="dial-row">
            <span className="dial-name">{d.name}</span>
            <span className="dial-value">
              <b>{card ? card.label : "Hand-tuned"}</b>
              {card?.headline && <span className="help"> · {card.headline}</span>}
              {!card && <span className="help"> · set rule by rule, so no preset describes it</span>}
            </span>
            <button className="small" onClick={() => onOpen(d.theme)}>Change</button>
          </div>
        );
      })}
    </div>
  );
}

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
  { key: "people", label: "People" },
  { key: "rules", label: "Rules in force" },
  { group: "Housekeeping" },
  { key: "duplicate", label: "Duplicate & export" },
  { key: "advanced", label: "Advanced" },
] as const;

function Section({ on, children }: { on: boolean; children: React.ReactNode }) {
  return on ? <>{children}</> : null;
}

/** The unforgeable-evidence doors. Secrets are shown exactly once — at project
    creation or on regenerate — because a secret the UI can always re-show is a
    secret every screen-share leaks. */
function WebhookEndpoints({ projectId }: { projectId: string }) {
  const [fresh, setFresh] = useState<{ github: string; generic: string } | null>(null);
  const origin = window.location.origin;
  return (
    <div>
      <p className="help" style={{ marginTop: 0 }}>
        Evidence arriving signed at these endpoints is <b>platform-attested</b> — no agent
        can forge it, because the signing secrets never enter a sandbox. Point GitHub's
        webhook (HMAC, <code>X-Hub-Signature-256</code>) and any other sender
        (<code>X-Yaaf-Signature</code>) here:
      </p>
      <table>
        <tbody>
          <tr>
            <td>GitHub</td>
            <td><code>{origin}/webhooks/github/{projectId}</code></td>
          </tr>
          <tr>
            <td>Generic</td>
            <td><code>{origin}/webhooks/generic/{projectId}</code></td>
          </tr>
        </tbody>
      </table>
      {fresh ? (
        <div className="strip" style={{ marginTop: 10 }}>
          <div className="grow">
            <b>New secrets — copy them now; they are not shown again.</b>
            <div className="sb" style={{ fontFamily: "var(--mono, monospace)", userSelect: "all" }}>
              github: {fresh.github}
            </div>
            <div className="sb" style={{ fontFamily: "var(--mono, monospace)", userSelect: "all" }}>
              generic: {fresh.generic}
            </div>
          </div>
        </div>
      ) : (
        <p className="help">
          The secrets were shown once when this project was created. Lost them?
          Regenerating invalidates the old ones everywhere they are configured.
        </p>
      )}
      <button className="small danger" style={{ marginTop: 6 }}
        onClick={async () => {
          const out = await post<{ github_webhook_secret: string; webhook_secret: string }>(
            `/admin/projects/${projectId}/webhook_secrets`, {});
          setFresh({ github: out.github_webhook_secret, generic: out.webhook_secret });
        }}>
        Regenerate secrets…
      </button>
    </div>
  );
}

export default function Settings({
  projectId,
  dash,
  org,
  reload,
}: {
  projectId: string;
  dash: Dashboard;
  /** The roster, polled once for the whole project page — the tabs used to
      fetch /org each and disagree with each other. */
  org: OrgView | null;
  reload: () => void;
}) {
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // In the URL, not in a useState: half a dozen places across the app say
  // "Settings → Rules in force", and every one of them used to land the
  // chairman on Brief with no way to link any deeper.
  const [params, setParams] = useSearchParams();
  const section = params.get("s") ?? "brief";
  const theme = params.get("t") ?? "";
  const go = (next: string, tab?: string) =>
    setParams(tab ? { s: next, t: tab } : { s: next }, { replace: true });

  const [billingCredentialId, setBillingCredentialId] = useState(dash.billing_secret_id ?? "");
  const [busy, setBusy] = useState(false);

  const [runtime, setRuntime] = useState(String(dash.integrations["runtime"] ?? "agent_sdk"));

  const navigate = useNavigate();
  const [cloneSlug, setCloneSlug] = useState(() => nextInSeries(dash.slug, "-2"));
  const [cloneName, setCloneName] = useState(() => nextInSeries(dash.name, " (clone)"));
  // The usual reason to duplicate a project is to aim it at the next thing —
  // so the mandate is a field on the form, not something to fix afterwards.
  const [cloneGoal, setCloneGoal] = useState(dash.goal);
  const [goal, setGoal] = useState(dash.goal);
  const savedOrders = (dash.standing_orders ?? []).join("\n");
  const savedRetired = (dash.retired_orders ?? []).join("\n");
  const [orders, setOrders] = useState(savedOrders);
  const [retired, setRetired] = useState(savedRetired);
  const briefLines = (text: string) =>
    text.split("\n").map((l) => l.replace(/^\s*[-•*]\s*/, "").trim()).filter(Boolean);
  const briefDirty = goal.trim() !== dash.goal || orders.trim() !== savedOrders
    || retired.trim() !== savedRetired;

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
  const discardEdits = () => {
    setEdits(Object.fromEntries(schema.map((f) => [f.key, String(cons[f.key] ?? "")])));
    setAmendChanges("{}");
    setAmendReason("");
  };
  // The tabs, ordered by the server. Fourteen groups become six headings a
  // chairman recognises; the groups survive as subheads inside one.
  const themes = [...new Set(
    [...schema].sort((a, b) => a.theme_rank - b.theme_rank).map((f) => f.theme),
  )];
  const activeTheme = themes.includes(theme) ? theme : themes[0] ?? "";
  const dirtyPerTheme = (name: string) =>
    schema.filter((f) => f.theme === name && f.key in changedGuardrails).length;
  // A rule linked to from another tab gets a moment of highlight, otherwise
  // "Most one run may spend is over on Pace & brakes" just moves the hunt.
  const [flashKey, setFlashKey] = useState<string | null>(null);
  function jumpToRule(key: string, tab: string) {
    go("rules", tab);
    setFlashKey(key);
    window.setTimeout(() => {
      document.getElementById(`rule-${key}`)?.scrollIntoView({
        behavior: "smooth", block: "center",
      });
    }, 0);
    window.setTimeout(() => setFlashKey(null), 1600);
  }
  const { data: amendments, reload: reloadAmendments } = usePoll(
    () => get<AmendmentRow[]>(`/admin/projects/${projectId}/amendments`),
    10000,
  );
  // The same cards the new-project form offers, from the same endpoint, so a
  // card cannot read one way while it is being picked and another way
  // afterwards. Best-effort: the pickers simply do not render without them,
  // and every rule underneath stays editable by hand as it always was.
  const [presets, setPresets] = useState<FounderStatus["presets"] | null>(null);
  useEffect(() => {
    get<FounderStatus>("/admin/founder/status")
      .then((found) => setPresets(found.presets))
      .catch(() => {});
  }, []);

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

  // An empty pick on a project that has a credential is a detach: the server
  // takes "" as "none", the project cannot run until another is assigned, and
  // the credential is free to be deleted. Nothing else on this page can free one.
  const detaching = !billingCredentialId && !!dash.billing_secret_id;
  async function connectBilling() {
    await act(() => patch(`/admin/projects/${projectId}`, {
      billing_secret_id: billingCredentialId,
    }), detaching
      ? "Credential detached. Agents cannot run until one is assigned again."
      : "Billing credential updated. New runs use this credential.");
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
  const runtimeNow = String(dash.integrations["runtime"] ?? "agent_sdk");

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
      // The tokens made it but the rooms did not — said now, while the
      // chairman is looking, not discovered three days into a silent mirror.
      if (twin.copied.slack_rooms_dropped) {
        alert(
          `Twin "${twin.slug}" created with the Slack tokens but no rooms: the channel map ` +
            "stays behind so two projects never post into one room. Map the twin's rooms in " +
            "its Settings → Integrations when you want it mirrored.",
        );
      }
      // An Explore twin whose source window had already passed starts without
      // one. Silence here would read as "it kept the deadline", and mode is
      // frozen — so the window is the only part of the shape still settable.
      if (twin.copied.deadline_dropped) {
        alert(
          `Twin "${twin.slug}" created without a deadline: the window it was copied from ` +
            "has already passed, and inheriting it would have closed this project at birth. " +
            "Set a new one in the twin's Settings.",
        );
      }
      // The source's credential has since been hidden, so the twin has none.
      // It cannot run that way, and finding out at the first run is finding
      // out too late.
      if (twin.copied.billing_credential_hidden) {
        alert(
          `Twin "${twin.slug}" created without a billing credential: the source paid with ` +
            `"${twin.copied.billing_credential_hidden}", which has since been hidden. ` +
            "Pick a credential in the twin's Settings → Money before starting it.",
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
      const bundle = await get<ProjectBundle>(
        `/admin/projects/${projectId}/export?include_values=true`);
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
              onClick={() => go(item.key)}
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
        <div className="notice-box" style={{ borderColor: "var(--good)", background: "rgba(var(--good-rgb), 0.1)" }}>
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
          {/* Mock first: a mock project needs nothing to pay with, and telling
              it to add a key sent the reader after the wrong fix. */}
          {runtimeNow === "mock"
            ? "⚠ This project runs FAKE test agents (mock runtime) — nothing real will happen. Switch it under Advanced below."
            : "⚠ Agents have no way to pay for their model yet — connect a credential below or every real run will fail."}
        </div>
      )}

      <Section on={section === "brief"}>
      <h2>The brief</h2>
      <div className="card">
        <p className="help" style={{ marginTop: 0 }}>
          What every agent reads first, on every run: the goal, then your standing orders, then
          what you have taken back. When you change your mind in chat, the CEO writes it back
          here for you to confirm; you can also edit it yourself. Saving replaces the old
          version in every agent's instructions — nothing is added next to it — and the CEO is
          told to bring its files and checks into line.
          {dash.brief_confirmed_at && <> Last confirmed {fmtWhen(dash.brief_confirmed_at)}.</>}
        </p>
        <label className="grant-label">The goal</label>
        <textarea value={goal} onChange={(e) => setGoal(e.target.value)} minLength={10} />
        <label className="grant-label">Standing orders — one per line, with the number</label>
        <textarea value={orders} rows={Math.max(3, briefLines(orders).length + 1)}
          placeholder={"Max 15% of the book in one name\nNo stop-loss on core holdings"}
          onChange={(e) => setOrders(e.target.value)} />
        <label className="grant-label">No longer applies — one per line</label>
        <textarea value={retired} rows={Math.max(2, briefLines(retired).length + 1)}
          placeholder="25% trailing stop on every position"
          onChange={(e) => setRetired(e.target.value)} />
        <div className="actions">
          <button
            className="primary"
            disabled={busy || goal.trim().length < 10 || !briefDirty}
            onClick={() =>
              act(
                () => patch(`/admin/projects/${projectId}`, {
                  goal: goal.trim(),
                  standing_orders: briefLines(orders),
                  retired_orders: briefLines(retired),
                }),
                "Brief saved. Every agent reads it from its next run; the CEO has been told to bring its files in line.",
              )
            }
          >
            Save the brief
          </button>
          {briefDirty && (
            <button type="button" className="small" onClick={() => {
              setGoal(dash.goal); setOrders(savedOrders); setRetired(savedRetired);
            }}>
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
          — the plan. The platform pauses the project by itself at{" "}
          {Number(dash.constitution["hard_stop_multiple"] ?? 3)}× that, the hard stop.
        </p>
        <CredentialPicker value={billingCredentialId} onChange={id => setBillingCredentialId(id)} disabled={busy} />
        <div className="actions">
          <button className={detaching ? "danger" : "primary"} disabled={busy || billingCredentialId === (dash.billing_secret_id ?? "")} onClick={connectBilling}>{detaching ? "Detach credential" : "Save assignment"}</button>
          <button disabled={busy || !billingReady} onClick={testBilling}>Test connection</button>
        </div>
        <p className="help">Changing the assignment applies to new runs. The selected credential also determines whether budgets use USD or tokens. Clearing the pick detaches the credential: the project keeps its rules but cannot run until one is assigned, and the credential can then be deleted in Settings.</p>
        <PlanMeter credentialId={dash.billing_secret_id} mode={dash.billing_mode}
          title={`Plan usage for ${dash.billing_secret_name ?? "this credential"}`} />
        <ProviderCredentials projectId={projectId} disabled={busy} />
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
          reload={reload}
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
          org={org}
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

      <h2>Inbound evidence webhooks</h2>
      <div className="card">
        <WebhookEndpoints projectId={projectId} />
      </div>

      </Section>

      <Section on={section === "duplicate"}>
      <h2>Duplicate this project</h2>
      <div className="card">
        <p className="help" style={{ marginTop: 0 }}>
          A running twin, right here: same teams, same people, same constitution, and the{" "}
          <i>exact</i> role versions this project runs — not whatever the role library has moved
          on to. Service credentials are copied and the shared LLM credential stays assigned, so there is
          nothing to re-paste; the twin's data sources are tested with its own copies before you
          land on it.
        </p>
        <p className="help">
          Left behind on purpose: this project's history — runs, tasks, messages, memory, files,
          amendments.
        </p>
        <p className="help" style={{ color: "var(--warn, var(--ink-2))" }}>
          ⚠ A twin is the same <i>shape</i> of project: its mode ({dash.mode}) and its teams are
          frozen at creation, exactly as this project's were. To change either — or anything else
          the creation form asks — don't twin it: download the setup file below and edit
          everything on the New Project page instead.
        </p>
        <p className="help" style={{ color: "var(--warn, var(--ink-2))" }}>
          ⚠ The twin talks to the outside on its own wiring. Slack tokens come along but the
          channel map does not (two projects posting into one room helps nobody) — re-map rooms
          in the twin's Settings → Integrations. It also mints its own webhook signing secrets,
          so any outside sender pointed at this project's evidence webhooks must be re-pointed
          at the twin's URLs and secrets before its inbound evidence can be attested.
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
          A complete backup, as one JSON file: teams and their schedules, role definitions, the
          constitution, configuration, data sources — and project service credential <i>values</i>,
          Slack tokens and webhook signing keys, decrypted. Shared LLM values stay in Settings. Upload it on the New Project page
          and the form prefills. Select an administrator-managed LLM credential for billing. It holds the keys it
          names, so store it the way you store passwords. Duplicating on this install is simpler
          still — use the twin above.
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
          Agent execution — you almost never need to touch this
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
        </div>
        <div className="actions">
          <button
            disabled={busy}
            onClick={() =>
              act(
                () =>
                  patch(`/admin/projects/${projectId}`, {
                    integrations: { runtime },
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
        {dash.integrations["webhook_secret"] || dash.integrations["github_webhook_secret"] ? (
          <div className="row">
            <div>
              <label className="field">GitHub</label>
              <SecretInput readOnly value={String(dash.integrations["github_webhook_secret"] ?? "")} />
            </div>
            <div>
              <label className="field">Generic evidence sender</label>
              <SecretInput readOnly value={String(dash.integrations["webhook_secret"] ?? "")} />
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
                dash.integrations["webhook_secret"] &&
                !confirm("Regenerate? Any sender still using the old secrets stops verifying.")
              )
                return;
              act(
                () => post(`/admin/projects/${projectId}/webhook_secrets`, {}),
                "New signing secrets generated — update your senders.",
              );
            }}
          >
            {dash.integrations["webhook_secret"] ? "Regenerate" : "Generate signing secrets"}
          </button>
        </div>
      </details>

      </Section>

      <Section on={section === "people"}>
      <h2>People</h2>
      <MembersPanel projectId={projectId} dash={dash} reload={reload} />
      </Section>

      <Section on={section === "rules"}>
      <h2>Rules in force</h2>
      <div className="card">
        <p className="help" style={{ marginTop: 0 }}>
          Every limit the platform enforces. Agents cannot move them; you can. Change one, say
          why, and it is <b>in force from the next agent call</b>, recorded with your reason
          and disclosed to the CEO.
        </p>
        {schema.length === 0 && <p className="help">Loading the rulebook…</p>}
        {presets && <DialSummary presets={presets} cons={cons}
          usd={dash.billing_mode === "api_key"} onOpen={(t) => go("rules", t)} />}

        {/* Six tabs rather than fourteen headings on one scroll. The dot says
            which tab holds an edit, so a pending change is never somewhere you
            have to remember. */}
        <div className="tabs">
          {themes.map((name) => {
            const dirty = dirtyPerTheme(name);
            return (
              <button
                key={name}
                className={`tab${name === activeTheme ? " active" : ""}`}
                onClick={() => go("rules", name)}
              >
                {name}
                {dirty > 0 && <span className="badge accent" style={{ marginLeft: 6 }}>{dirty}</span>}
              </button>
            );
          })}
        </div>

        {activeTheme === "Pace & brakes" && presets && (
          <CardPicker
            title="How often should it think?"
            help={"Thirteen rules: the daily and hourly run ceilings, how often each agent "
              + "may wake, whether agents buy a run just to read each other's messages, how "
              + "often the CEO plans, and how far ahead work may be scheduled. Getting the "
              + "tempo wrong at creation is the cheapest mistake on this page to fix."}
            cards={presets.tempo} cons={cons} edits={edits} setEdits={setEdits} />
        )}
        {activeTheme === "The bar" && presets && (
          <CardPicker
            title="How good does it have to be, and what may it spend?"
            help={"What the work is worth: the budget and the wall behind it, which model "
              + "the writing and checking seats are hired on, and whether evidence has to "
              + "be verified before it counts."}
            cards={presets.bar} cons={cons} edits={edits} setEdits={setEdits} />
        )}
        {activeTheme === "Autonomy" && presets && (
          <CardPicker
            title="Who holds the verbs?"
            help={"Five rules deciding who accepts scope, changes the plan, signs the date, "
              + "judges an unverifiable close, and scopes the credentials. Your routes, "
              + "your veto and your pause never close either way."}
            cards={presets.autonomy} cons={cons} edits={edits} setEdits={setEdits} />
        )}
        {[...new Set(schema.filter((f) => f.theme === activeTheme).map((f) => f.group))]
          .map((group, _i, all) => (
            <div key={group}>
              {/* One group on the tab means the subhead only repeats the tab. */}
              {all.length > 1 && <div className="rule-grp">{group}</div>}
              {schema
                .filter((f) => f.theme === activeTheme && f.group === group)
                .map((f) => (
                  <RuleField
                    key={f.key}
                    field={f}
                    value={edits[f.key] ?? ""}
                    current={cons[f.key]}
                    dormant={dormantReason(f.key, dash)}
                    flash={flashKey === f.key}
                    onChange={(raw) => setRule(f.key, raw)}
                  />
                ))}
            </div>
          ))}

        {activeTheme === "Money" && (
          <p className="help" style={{ marginTop: 14 }}>
            Two more rules move money, filed under what they brake rather than what they
            cost:{" "}
            {MONEY_ELSEWHERE.map((item, i) => {
              const field = schema.find((f) => f.key === item.key);
              if (!field) return null;
              return (
                <Fragment key={item.key}>
                  {i > 0 && " and "}
                  <button className="small" onClick={() => jumpToRule(item.key, item.theme)}>
                    {field.label}
                  </button>{" "}
                  ({item.theme})
                </Fragment>
              );
            })}
            .
          </p>
        )}

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
          onDiscard={discardEdits}
          onSaved={() => {
            setAmendChanges("{}");
            reloadAmendments();
          }}
        />

        <details style={{ marginTop: 16 }}>
          <summary className="help">
            Change history — every amendment ever made to these rules
          </summary>
          {amendments && amendments.length > 0 ? (
            /* Nothing new lands here as pending any more, but rows written
               while the old cooling-off existed can still be waiting, so the
               countdown and cancel stay. */
            <table style={{ marginTop: 8 }}>
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
          ) : (
            <p className="help">Nothing amended yet — this project runs on the rules it was born with.</p>
          )}
        </details>

        <details>
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
      </div>

      </Section>

      <Section on={section === "money"}>
      <h2>Budget in force</h2>
      <div className="card">
        <p className="help" style={{ marginTop: 0 }}>
          What the platform is enforcing on this project's spending right now. These are
          constitution rules, so they are changed in one place — with a written reason that
          is recorded and shown to the CEO.
        </p>
        <dl className="kv">
          {schema
            .filter((f) => f.theme === "Money" || MONEY_ELSEWHERE.some((m) => m.key === f.key))
            .filter((f) => !dormantReason(f.key, dash))
            // The budget itself leads; the two levers filed elsewhere follow.
            .sort((a, b) => Number(a.theme !== "Money") - Number(b.theme !== "Money"))
            .map((f) => (
              <Fragment key={f.key}>
                <dt>{f.label}</dt>
                <dd className="mono">{String(cons[f.key])}</dd>
              </Fragment>
            ))}
        </dl>
        <div className="actions">
          <button className="primary" onClick={() => go("rules", "Money")}>
            Change these rules →
          </button>
        </div>
      </div>

      </Section>
      </div>
    </div>
  );
}
