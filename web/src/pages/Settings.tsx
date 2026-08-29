// App-wide settings: the two things that are true above every project.
//
// The model credential was storable before this screen and not *manageable* —
// it appeared as a gate on the Founding page when unset and vanished the
// moment it was set, so there was no way to see what was installed, test it,
// rotate it or take it away. That is the gap here.
//
// The Slack connection is the other half, and it exists because of how these
// installs are actually used: one workspace, every project, different
// channels. Store the tokens once and a project needs nothing but its channel
// map — which yaaf then creates for you.

import { useCallback, useEffect, useState } from "react";
import { del, get, post, put } from "../api";
import { SecretInput } from "../ui";

interface AnthropicSettings {
  configured: boolean;
  billing_mode: string;
  model: string;
  note: string;
}

interface SlackSettings {
  bot_token_configured: boolean;
  signing_secret_configured: boolean;
  app_token_configured: boolean;
  chairman_slack_user: string;
  channel_prefix: string;
  channel_visibility: string;
  auto_create_channels: boolean;
}

interface AppSettings {
  anthropic: AnthropicSettings;
  slack: SlackSettings;
}

/** One flash line under a card: the answer to the button you just pressed. */
function Flash({ note }: { note: { ok: boolean; text: string } | null }) {
  if (!note) return null;
  return <p className={note.ok ? "help" : "error"}>{note.text}</p>;
}

function ModelCard({
  settings, reload,
}: { settings: AnthropicSettings; reload: () => void }) {
  const [value, setValue] = useState("");
  const [mode, setMode] = useState(settings.billing_mode);
  const [model, setModel] = useState(settings.model);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => {
    setMode(settings.billing_mode);
    setModel(settings.model);
  }, [settings.billing_mode, settings.model]);

  async function run(fn: () => Promise<string>) {
    setBusy(true);
    setNote(null);
    try {
      setNote({ ok: true, text: await fn() });
      reload();
    } catch (err) {
      setNote({ ok: false, text: (err as Error).message });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card">
      <p className="help" style={{ marginTop: 0 }}>
        {settings.note}
      </p>
      <p className="help">
        {settings.configured ? (
          <>
            <span className="badge good">configured</span> an{" "}
            {settings.billing_mode === "subscription"
              ? "OAuth subscription token"
              : "Anthropic API key"}{" "}
            is installed, running <code>{settings.model}</code>. The value is never shown
            again — rotate it by pasting a new one.
          </>
        ) : (
          <>
            <span className="badge warn">not set</span> the Founder cannot run. Everything
            else works: the New Project form, the fire drill, and every project&apos;s own
            agents, which pay with their own keys.
          </>
        )}
      </p>

      <div className="row">
        <div>
          <label className="field">Key type</label>
          <select value={mode} disabled={busy} onChange={(e) => setMode(e.target.value)}>
            <option value="api_key">Anthropic API key</option>
            <option value="subscription">Claude subscription token</option>
          </select>
        </div>
        <div>
          <label className="field">
            {settings.configured ? "Replace the key — blank keeps the current one" : "The key"}
          </label>
          <SecretInput
            value={value}
            onChange={setValue}
            placeholder={mode === "subscription" ? "sk-ant-oat…" : "sk-ant-api…"}
            disabled={busy}
          />
        </div>
      </div>
      <p className="help">
        {mode === "subscription"
          ? "A subscription token comes from `claude setup-token` and starts sk-ant-oat."
          : "An API key comes from the Anthropic console and starts sk-ant-api."}{" "}
        Pasting one where the other belongs is refused here rather than failing on your
        first message.
      </p>

      <div className="actions">
        <button
          className="primary"
          disabled={busy || value.trim().length < 8}
          onClick={() =>
            run(async () => {
              await put("/admin/settings/anthropic", { value: value.trim(), mode });
              setValue("");
              return "Key saved. It is encrypted at rest; only the server reads it.";
            })
          }
        >
          {settings.configured ? "Replace key" : "Save key"}
        </button>
        {settings.configured && (
          <>
            <button
              disabled={busy}
              onClick={() =>
                run(async () => {
                  const result = await post<{ ok: boolean; detail: string }>(
                    "/admin/settings/anthropic/test");
                  if (!result.ok) throw new Error(result.detail);
                  return result.detail;
                })
              }
            >
              Test it
            </button>
            <button
              disabled={busy}
              onClick={() => {
                if (!confirm("Remove the platform key? The Founder stops being offered. "
                             + "No project is affected — they never used it."))
                  return;
                run(async () => {
                  await del("/admin/settings/anthropic");
                  return "Key removed.";
                });
              }}
            >
              Remove
            </button>
          </>
        )}
      </div>

      <label className="field">Model the Founder runs on</label>
      <div className="actions">
        <input
          value={model}
          disabled={busy}
          onChange={(e) => setModel(e.target.value)}
          style={{ maxWidth: 260 }}
        />
        <button
          disabled={busy || model.trim() === settings.model || model.trim().length < 3}
          onClick={() =>
            run(async () => {
              await put("/admin/settings/anthropic/model", { model: model.trim() });
              return `The Founder now runs on ${model.trim()}.`;
            })
          }
        >
          Save model
        </button>
      </div>
      <Flash note={note} />
    </div>
  );
}

function SlackCard({
  settings, reload,
}: { settings: SlackSettings; reload: () => void }) {
  const [botToken, setBotToken] = useState("");
  const [signingSecret, setSigningSecret] = useState("");
  const [appToken, setAppToken] = useState("");
  const [chairman, setChairman] = useState(settings.chairman_slack_user);
  const [prefix, setPrefix] = useState(settings.channel_prefix);
  const [visibility, setVisibility] = useState(settings.channel_visibility);
  const [autoCreate, setAutoCreate] = useState(settings.auto_create_channels);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => {
    setChairman(settings.chairman_slack_user);
    setPrefix(settings.channel_prefix);
    setVisibility(settings.channel_visibility);
    setAutoCreate(settings.auto_create_channels);
  }, [settings]);

  async function run(fn: () => Promise<string>) {
    setBusy(true);
    setNote(null);
    try {
      setNote({ ok: true, text: await fn() });
      reload();
    } catch (err) {
      setNote({ ok: false, text: (err as Error).message });
    } finally {
      setBusy(false);
    }
  }

  const connected = settings.bot_token_configured;

  return (
    <div className="card">
      <p className="help" style={{ marginTop: 0 }}>
        One workspace, every project. Store the tokens here and a new project needs
        nothing but its channels — which yaaf creates for you and names back. A project
        can still override any of this field by field in its own Settings, and switch
        Slack off entirely without unwiring anyone else.
      </p>
      <p className="help">
        {connected ? (
          <>
            <span className="badge good">connected</span> the bot token is installed
            {settings.signing_secret_configured ? ", signing secret set" : ""}
            {settings.app_token_configured ? ", Socket Mode ready" : ""}
            {settings.chairman_slack_user
              ? `, inbound enabled for ${settings.chairman_slack_user}`
              : "; no chairman Slack ID, so outbound mirroring only"}.
          </>
        ) : (
          <>
            <span className="badge warn">not connected</span> you need a Slack app with the{" "}
            <code>chat:write</code>, <code>channels:manage</code> and{" "}
            <code>groups:write</code> bot scopes, installed to your workspace.
          </>
        )}
      </p>

      <div className="row">
        <div>
          <label className="field">
            Bot token (xoxb-…){connected ? " — blank keeps current" : ""}
          </label>
          <SecretInput value={botToken} placeholder="xoxb-…" onChange={setBotToken}
                       disabled={busy} />
        </div>
        <div>
          <label className="field">
            Signing secret{settings.signing_secret_configured ? " — blank keeps current" : ""}
          </label>
          <SecretInput value={signingSecret} placeholder="from Basic Information"
                       onChange={setSigningSecret} disabled={busy} />
        </div>
        <div>
          <label className="field">
            App token (xapp-…) — chat from Slack, no public URL
            {settings.app_token_configured ? " — blank keeps current" : ""}
          </label>
          <SecretInput value={appToken} placeholder="xapp-… (enables Socket Mode)"
                       onChange={setAppToken} disabled={busy} />
        </div>
      </div>

      <div className="row">
        <div>
          <label className="field">
            Your Slack member ID (U…) — the only user the inbound path trusts
          </label>
          <input value={chairman} placeholder="U… (blank = outbound mirror only)"
                 disabled={busy} onChange={(e) => setChairman(e.target.value)} />
        </div>
        <div>
          <label className="field">Channel name prefix</label>
          <input value={prefix} placeholder="blank = the project's slug"
                 disabled={busy} onChange={(e) => setPrefix(e.target.value)} />
        </div>
        <div>
          <label className="field">New channels are</label>
          <select value={visibility} disabled={busy}
                  onChange={(e) => setVisibility(e.target.value)}>
            <option value="private">Private</option>
            <option value="public">Public</option>
          </select>
        </div>
      </div>
      <p className="help">
        Rooms are named <code>{`${prefix.trim() || "{project slug}"}-boardroom`}</code>,{" "}
        <code>{`${prefix.trim() || "{project slug}"}-leadership`}</code> and one per team.
        A blank prefix uses each project&apos;s slug, which is the only name guaranteed
        not to collide between two projects.
        {visibility === "private" && " Private rooms are invisible until somebody is "
          + "invited — yaaf invites your Slack member ID as it creates each one, so set it above."}
      </p>

      <label className="field" style={{ display: "flex", gap: 6, alignItems: "center" }}>
        <input type="checkbox" checked={autoCreate} disabled={busy}
               style={{ width: "auto", flex: "none" }}
               onChange={(e) => setAutoCreate(e.target.checked)} />
        Create a new project&apos;s channels automatically
      </label>
      <p className="help">
        On, creating a project provisions its rooms on the spot and tells you their names.
        Off, every project gets a &ldquo;Create channels&rdquo; button in its own Settings
        instead. Either way nothing is ever created twice: a name Slack already holds is
        adopted, not duplicated.
      </p>

      <div className="actions">
        <button
          className="primary"
          disabled={busy}
          onClick={() =>
            run(async () => {
              await put("/admin/settings/slack", {
                ...(botToken.trim() ? { bot_token: botToken.trim() } : {}),
                ...(signingSecret.trim() ? { signing_secret: signingSecret.trim() } : {}),
                ...(appToken.trim() ? { app_token: appToken.trim() } : {}),
                chairman_slack_user: chairman.trim(),
                channel_prefix: prefix.trim(),
                channel_visibility: visibility,
                auto_create_channels: autoCreate,
              });
              setBotToken("");
              setSigningSecret("");
              setAppToken("");
              return "Slack connection saved. Every project that has not overridden "
                + "it inherits these now.";
            })
          }
        >
          {connected ? "Update connection" : "Connect Slack"}
        </button>
        {connected && (
          <>
            <button
              disabled={busy}
              onClick={() =>
                run(async () => {
                  const result = await post<{ ok: boolean; detail: string }>(
                    "/admin/settings/slack/test");
                  if (!result.ok) throw new Error(result.detail);
                  return result.detail;
                })
              }
            >
              Test connection
            </button>
            <button
              disabled={busy}
              onClick={() => {
                if (!confirm("Forget the app-wide Slack connection? Projects that inherit it "
                             + "stop mirroring. Nothing is deleted in Slack and no "
                             + "message is lost — the bus is canonical."))
                  return;
                run(async () => {
                  await del("/admin/settings/slack");
                  return "Connection forgotten. Projects with their own tokens are "
                    + "unaffected.";
                });
              }}
            >
              Disconnect
            </button>
          </>
        )}
      </div>
      <Flash note={note} />
      <p className="help">
        To chat from Slack: in the Slack app, enable <b>Socket Mode</b> (Settings → Socket
        Mode), create an app-level token with <code>connections:write</code>, paste it
        above, and subscribe the bot to <code>message.channels</code>. Your messages in a
        project&apos;s mapped boardroom reach its CEO instantly and the replies mirror
        back — no public URL, no tunnel.
      </p>
    </div>
  );
}

export default function Settings() {
  const [settings, setSettings] = useState<AppSettings | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(() => {
    get<AppSettings>("/admin/settings").then(setSettings).catch((e) => setError(e.message));
  }, []);

  useEffect(reload, [reload]);

  return (
    <>
      <h1>Settings</h1>
      <p className="subtitle">
        What is true above every project. A project&apos;s own rules — its budget, its
        constitution, who it hires — live in that project, because they bind that project.
        These two do not: they are this install&apos;s wiring.
      </p>
      {error && <div className="error">{error}</div>}
      {settings === null ? (
        <div className="card muted">Loading…</div>
      ) : (
        <>
          <h2>Model credential</h2>
          <ModelCard settings={settings.anthropic} reload={reload} />
          <h2>Slack connection</h2>
          <SlackCard settings={settings.slack} reload={reload} />
        </>
      )}
    </>
  );
}
