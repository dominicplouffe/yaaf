import { useEffect, useState } from "react";
import { AmendmentRow, Dashboard, get, OrgView, patch, post, RoleSummary } from "../../api";
import { fmtWhen, usePoll } from "../../hooks";

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

function amendmentBadge(status: AmendmentRow["status"]) {
  const cls = status === "applied" ? "good" : status === "pending" ? "warn" : "";
  return <span className={`badge ${cls}`}>{status}</span>;
}

function hoursUntil(iso: string): number {
  return Math.max(0, Math.ceil((new Date(iso).getTime() - Date.now()) / 3_600_000));
}

interface StoredSecret {
  id: string;
  name: string;
  project_id: string | null;
  role_scopes: string[];
  kind: "service" | "billing";
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
  chairman_slack_user: string;
  channel_map: Record<string, string>;
}

function SlackBridge({
  projectId,
  dash,
  act,
  busy,
}: {
  projectId: string;
  dash: Dashboard;
  act: (fn: () => Promise<unknown>, ok: string) => Promise<void>;
  busy: boolean;
}) {
  const existing = (dash.config["slack"] as SlackConfig | null | undefined) ?? null;
  const [org, setOrg] = useState<OrgView | null>(null);
  const [botToken, setBotToken] = useState("");
  const [signingSecret, setSigningSecret] = useState("");
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
          Token and signing secret are stored write-only — paste again only to rotate.
        </p>
      ) : (
        <p className="help">Not connected. You need a Slack app with the `chat:write` bot
          scope, installed to your workspace, and the bot invited to each channel you map.</p>
      )}
      <div className="row">
        <div>
          <label className="field">Bot token (xoxb-…){existing ? " — blank keeps current" : ""}</label>
          <input type="password" value={botToken} placeholder="xoxb-…"
                 onChange={(e) => setBotToken(e.target.value)} />
        </div>
        <div>
          <label className="field">Signing secret{existing ? " — blank keeps current" : ""}</label>
          <input type="password" value={signingSecret} placeholder="from Basic Information"
                 onChange={(e) => setSigningSecret(e.target.value)} />
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
              await patch(`/admin/projects/${projectId}`, {
                config: { slack: {
                  bot_token_secret: "slack-bot",
                  signing_secret: "slack-signing",
                  chairman_slack_user: chairmanUser.trim(),
                  channel_map: Object.fromEntries(mapped.map(([k, v]) => [k, v.trim()])),
                }},
              });
              setBotToken("");
              setSigningSecret("");
            }, "Slack connected. Mapped channels start mirroring within one janitor tick (~15s).")
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
        Inbound (optional): in the Slack app, enable Event Subscriptions for the bot event
        `message.channels` with request URL <code>{eventsUrl}</code> — Slack requires this to be
        reachable over public HTTPS (a tunnel like cloudflared works). Skip it and the mirror
        still runs; you just reply from the boardroom here instead.
      </p>
    </>
  );
}

function CredentialsManager({
  projectId,
  act,
  busy,
}: {
  projectId: string;
  act: (fn: () => Promise<unknown>, ok: string) => Promise<void>;
  busy: boolean;
}) {
  const [roles, setRoles] = useState<RoleSummary[]>([]);
  const [stored, setStored] = useState<StoredSecret[]>([]);
  const [name, setName] = useState("");
  const [value, setValue] = useState("");
  const [grants, setGrants] = useState<string[]>([]);

  function refresh() {
    // Service credentials only — billing credentials live in the billing card
    // and can never be role-granted (the server refuses).
    get<StoredSecret[]>(`/admin/secrets?project_id=${projectId}`)
      .then((all) => setStored(all.filter((s) => s.kind !== "billing")))
      .catch(() => {});
  }
  useEffect(() => {
    get<RoleSummary[]>("/admin/roles")
      .then((all) => setRoles(all.filter((r) => !r.is_ceo_role)))
      .catch(() => {});
    refresh();
  }, [projectId]);

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
          <input
            type="password"
            value={value}
            onChange={(e) => setValue(e.target.value)}
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
              refresh();
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
              <th>Injected as</th>
              <th style={{ width: "45%" }}>Roles that receive it</th>
            </tr>
          </thead>
          <tbody>
            {stored.map((secret) => (
              <tr key={secret.id}>
                <td className="mono">{secret.name}</td>
                <td className="mono">
                  {secret.role_scopes.length ? `$${envVarName(secret.name)}` : "—"}
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
                        refresh();
                      }, `Access to "${secret.name}" updated.`)
                    }
                  />
                  {secret.role_scopes.length === 0 && (
                    <span className="help">no roles — not injected anywhere</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}

/** One decision, one button: pick how this project pays for models, paste the
    credential, Connect does everything (store encrypted secret + attach it +
    set the billing mode). The status card always reflects the server. */
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

  const [billingChoice, setBillingChoice] = useState<"api_key" | "subscription">(
    dash.billing_mode === "subscription" ? "subscription" : "api_key",
  );
  const [credential, setCredential] = useState("");
  const [busy, setBusy] = useState(false);

  const [runtime, setRuntime] = useState(String(dash.config["runtime"] ?? "agent_sdk"));
  const [heartbeatHours, setHeartbeatHours] = useState(
    String(dash.config["ceo_heartbeat_hours"] ?? 6),
  );

  const [amendReason, setAmendReason] = useState("");
  const [amendChanges, setAmendChanges] = useState('{\n  "queue_ceiling": 8\n}');
  const { data: amendments, reload: reloadAmendments } = usePoll(
    () => get<AmendmentRow[]>(`/admin/projects/${projectId}/amendments`),
    10000,
  );

  const [secrets, setSecrets] = useState<SecretRow[]>([]);
  useEffect(() => {
    get<SecretRow[]>(`/admin/secrets?project_id=${projectId}`).then(setSecrets).catch(() => {});
  }, [projectId, dash.billing_secret_id]);

  async function act(fn: () => Promise<unknown>, okMessage: string) {
    setError(null);
    setMessage(null);
    setBusy(true);
    try {
      await fn();
      setMessage(okMessage);
      reload();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

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
    }, `Billing connected: ${billingChoice} using "${name}". New runs use it immediately.`);
  }

  const billingReady = dash.billing_secret_id !== null;
  const runtimeNow = String(dash.config["runtime"] ?? "agent_sdk");

  return (
    <>
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
        <input
          type="password"
          value={credential}
          onChange={(e) => setCredential(e.target.value)}
          placeholder={billingChoice === "subscription" ? "sk-ant-oat01-…" : "sk-ant-api03-…"}
        />
        <div className="actions">
          <button className="primary" disabled={busy || credential.trim().length < 10} onClick={connectBilling}>
            {busy ? "Connecting…" : "Connect"}
          </button>
          <span className="help">
            Stored encrypted, write-only. One click does all of it: store → attach → set mode.
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
                        {active ? (
                          <span className="badge good">✓ ACTIVE — agents pay with this one</span>
                        ) : (
                          <span className="help">stored, not in use</span>
                        )}
                      </td>
                      <td>
                        {!active && (
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

      <h2>Service credentials</h2>
      <div className="card">
        <p className="help" style={{ marginTop: 0 }}>
          API keys and passwords your agents need for outside services. <strong>Never paste a key
          into the boardroom</strong> — messages become model context and leave the box. Stored
          here, a credential is encrypted at rest and injected only into the sandboxes of roles you
          tick, as an environment variable. Its value never appears in any prompt, message, or the
          UI again; agents are told the variable's <em>name</em> only. Changing grants takes effect
          on the next run.
        </p>
        <CredentialsManager
          projectId={projectId}
          act={act}
          busy={busy}
        />
      </div>

      <h2>Slack bridge</h2>
      <div className="card">
        <SlackBridge projectId={projectId} dash={dash} act={act} busy={busy} />
      </div>

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
      </details>

      <h2>Constitution amendment</h2>
      <div className="card">
        <p className="help">
          Amendments take effect after a 72-hour cooling-off and are disclosed to the CEO. A forcing
          function you can edit instantly is just a default — the delay is the point.
        </p>
        <label className="field">Reason (recorded, shown to the CEO)</label>
        <input value={amendReason} onChange={(e) => setAmendReason(e.target.value)} />
        <label className="field">Changes (JSON overlay onto the current constitution)</label>
        <textarea
          className="mono"
          value={amendChanges}
          onChange={(e) => setAmendChanges(e.target.value)}
        />
        <div className="actions">
          <button
            disabled={busy || amendReason.trim().length < 10}
            onClick={() =>
              act(async () => {
                const changes = JSON.parse(amendChanges);
                await post(`/admin/projects/${projectId}/amendments`, {
                  reason: amendReason,
                  changes,
                });
                setAmendReason("");
                reloadAmendments();
              }, "Amendment proposed — in force after the cooling-off, then disclosed to the CEO.")
            }
          >
            Propose amendment
          </button>
        </div>

        {amendments && amendments.length > 0 && (
          <>
            <label className="field">Amendments proposed so far</label>
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
    </>
  );
}
