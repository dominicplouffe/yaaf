// App-wide settings: the three things that are true above every project.
//
// Two of them — the Founder's own model credential and the Slack connection —
// are small, stateful and read once a quarter. The third, the LLM credentials
// projects pay with, is a working list that on a real install runs to dozens
// of rows. Stacking all three on one scroll meant the rarely-touched cards sat
// below a table that was itself the reason nobody scrolled: to change a Slack
// channel prefix you first travelled past forty credentials.
//
// So the page is a rail, like a project's own Settings, and the rail carries
// each section's state — which is the answer to "is anything wrong here?"
// without opening anything. The section lives in the URL, so a link to the
// credentials list is a link to the credentials list.

import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { del, get, post, put } from "../api";
import { LlmCredentials } from "../llmCredentials";
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

interface CredentialCounts {
  total: number;
  hidden: number;
  in_use: number;
  offerable: number;
}

interface AppSettings {
  anthropic: AnthropicSettings;
  slack: SlackSettings;
  llm_credentials: CredentialCounts;
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
        Create a new project&apos;s boardroom automatically
      </label>
      <p className="help">
        On, creating a project makes its <b>boardroom</b> on the spot and tells you the
        name — the one room every project has, and the one you talk to the CEO in. Off, it
        makes nothing. Either way leadership and the team rooms wait behind
        &ldquo;Create missing channels&rdquo; in the project&apos;s own Settings →
        Integrations → Slack, so a new project never costs your workspace half a dozen
        channels nobody has spoken in yet. Nothing is ever created twice: a name Slack
        already holds is adopted, not duplicated.
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

type SectionKey = "credentials" | "founder" | "slack";

const SECTIONS: { key: SectionKey; label: string }[] = [
  { key: "credentials", label: "LLM credentials" },
  { key: "founder", label: "Founder" },
  { key: "slack", label: "Slack" },
];

/** The state of a section, said in the rail. Three words at most: this is the
    line that decides whether the section is worth opening, not a summary of
    what is inside it. */
function railNote(key: SectionKey, s: AppSettings): { text: string; tone: string } {
  if (key === "credentials") {
    const { total, in_use, hidden, offerable } = s.llm_credentials;
    if (total === 0) return { text: "none yet", tone: "warn" };
    if (in_use === 0) return { text: `${offerable} offered · none in use`, tone: "warn" };
    return { text: `${in_use} in use of ${offerable}${hidden ? ` · ${hidden} hidden` : ""}`,
             tone: "good" };
  }
  if (key === "founder") {
    return s.anthropic.configured
      ? { text: s.anthropic.model, tone: "good" }
      : { text: "no key set", tone: "warn" };
  }
  return s.slack.bot_token_configured
    ? { text: s.slack.chairman_slack_user ? "connected · chat in" : "connected · outbound only",
        tone: "good" }
    : { text: "not connected", tone: "" };
}

export default function Settings() {
  const [settings, setSettings] = useState<AppSettings | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [params, setParams] = useSearchParams();
  const section = (SECTIONS.find((s) => s.key === params.get("section"))?.key
    ?? "credentials") as SectionKey;

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
        The credentials and connections every project draws on live here.
      </p>
      {error && <div className="error">{error}</div>}
      {settings === null ? (
        <div className="card muted">Loading…</div>
      ) : (
        <div className="settings-lay">
          <div className="srail">
            {SECTIONS.map((item) => {
              const note = railNote(item.key, settings);
              return (
                <button
                  key={item.key}
                  className={section === item.key ? "active" : ""}
                  onClick={() => setParams({ section: item.key }, { replace: true })}
                >
                  {item.label}
                  <span className={`srail-note ${note.tone}`}>{note.text}</span>
                </button>
              );
            })}
          </div>

          <div>
            {section === "credentials" && <LlmCredentials onChanged={reload} />}
            {section === "founder" && (
              <>
                <h2 style={{ marginTop: 0 }}>The Founder&apos;s own model credential</h2>
                <ModelCard settings={settings.anthropic} reload={reload} />
              </>
            )}
            {section === "slack" && (
              <>
                <h2 style={{ marginTop: 0 }}>Slack connection</h2>
                <SlackCard settings={settings.slack} reload={reload} />
              </>
            )}
          </div>
        </div>
      )}
    </>
  );
}
