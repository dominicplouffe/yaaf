// App-wide settings: the three things that are true above every project, and
// your own account.
//
// Two of the three — the Founder's own model credential and the Slack connection —
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
//
// The box's own settings and its People belong to the instance admin, and the
// server refuses them to anyone else. So they are not drawn for anyone else
// either: a rail entry that opens onto a refusal is a door painted on a wall.
// Everybody gets Account, because everybody has a password.

import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { del, get, post, put, UserRow } from "../api";
import { PasswordCard, SessionsCard, TokensCard } from "../account";
import { Credential, LlmCredentials, ModelPrices, useProviders } from "../llmCredentials";
import { useMe } from "../me";
import { People } from "../people";
import { SecretInput } from "../ui";

interface AnthropicSettings {
  configured: boolean;
  billing_mode: string;
  /** Which provider the platform credential pays; the Founder runs on it. */
  provider: string;
  model: string;
  note: string;
  /** Its own key, or one of the LLM credentials, borrowed — named, never shown. */
  source?: { source: "own" | "llm_credential" | null; credential_id?: string; name?: string };
}

/** Point the Founder at a key the install already holds for projects, so one
    key is enough to start. */
function BorrowCard({
  settings, reload,
}: { settings: AnthropicSettings; reload: () => void }) {
  const [existing, setExisting] = useState<Credential[]>([]);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);
  useEffect(() => {
    get<Credential[]>("/admin/settings/llm-credentials").then(setExisting).catch(() => {});
  }, []);
  const borrowed = settings.source?.source === "llm_credential" ? settings.source : null;
  if (!existing.length && !borrowed) return null;

  async function borrow(id: string) {
    setBusy(true);
    setNote(null);
    try {
      await put("/admin/founder/credential/llm", { credential_id: id });
      setNote({ ok: true, text: "The Founder now drafts on that key." });
      reload();
    } catch (err) {
      setNote({ ok: false, text: (err as Error).message });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card" style={{ marginBottom: 10 }}>
      <p className="help" style={{ marginTop: 0 }}>
        {borrowed
          ? <>The Founder drafts on <strong>{borrowed.name}</strong>, one of your LLM
              credentials. Rotating that credential rotates this too. Paste a key below to
              give the Founder its own instead.</>
          : <>Use one of your LLM credentials for drafting, rather than a second key:</>}
      </p>
      <div className="actions" style={{ flexWrap: "wrap", justifyContent: "flex-start" }}>
        {existing.filter((c) => c.id !== borrowed?.credential_id).map((c) => (
          <button key={c.id} disabled={busy} onClick={() => borrow(c.id)}>
            Use {c.name}
          </button>
        ))}
      </div>
      <Flash note={note} />
    </div>
  );
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
  const [provider, setProvider] = useState(settings.provider || "anthropic");
  const [baseUrl, setBaseUrl] = useState("");
  const [model, setModel] = useState(settings.model);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);
  // Any provider the Founder can run on: Anthropic's CLI takes a key or a
  // subscription; every other provider's chat API takes an API key.
  const sellers = useProviders();
  const seller = sellers.find((p) => p.name === provider);
  const kinds = (seller?.kinds ?? []).filter(
    (k) => provider === "anthropic" || k.mode === "api_key");
  const anthropic = provider === "anthropic";

  useEffect(() => {
    setMode(settings.billing_mode);
    setProvider(settings.provider || "anthropic");
    setModel(settings.model);
  }, [settings.billing_mode, settings.provider, settings.model]);

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
        {settings.configured && settings.source?.source === "llm_credential" ? (
          <>
            <span className="badge good">configured</span> drafting on the LLM credential{" "}
            <strong>{settings.source.name}</strong>, running <code>{settings.model}</code>.
          </>
        ) : settings.configured ? (
          <>
            <span className="badge good">configured</span>{" "}
            {settings.provider && settings.provider !== "anthropic"
              ? `${sellers.find((p) => p.name === settings.provider)?.label
                  ?? settings.provider} API key`
              : settings.billing_mode === "subscription"
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
          <label className="field">Provider</label>
          <select value={provider} disabled={busy}
                  onChange={(e) => { setProvider(e.target.value); setMode("api_key"); }}>
            {(sellers.length ? sellers : [{ name: "anthropic", label: "Anthropic" }])
              .map((p) => <option key={p.name} value={p.name}>{p.label}</option>)}
          </select>
        </div>
        <div>
          <label className="field">Key type</label>
          <select value={mode} disabled={busy} onChange={(e) => setMode(e.target.value)}>
            {kinds.length ? kinds.map((k) => (
              <option key={k.mode} value={k.mode}>{k.bills.split(",")[0]}</option>
            )) : <option value="api_key">API key</option>}
          </select>
        </div>
        <div>
          <label className="field">
            {settings.configured ? "Replace the key — blank keeps the current one" : "The key"}
          </label>
          <SecretInput
            value={value}
            onChange={setValue}
            placeholder={!anthropic ? "the key" : mode === "subscription" ? "sk-ant-oat…" : "sk-ant-api…"}
            disabled={busy}
          />
        </div>
      </div>
      {seller?.credential_base_url && (
        <div>
          <label className="field">The server&apos;s base URL</label>
          <input value={baseUrl} disabled={busy} placeholder="http://ollama:11434/v1"
                 onChange={(e) => setBaseUrl(e.target.value)} />
        </div>
      )}
      <p className="help">
        {!anthropic
          ? "On any provider but Anthropic the Founder runs on its chat API, with this key and "
            + "the model below — one of that provider's — which is saved with it."
          : mode === "subscription"
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
              await put("/admin/settings/anthropic", {
                value: value.trim(), mode, provider,
                ...(baseUrl.trim() ? { base_url: baseUrl.trim() } : {}),
                // The model travels with a key of another provider: the two are
                // one decision, and the server refuses them apart.
                ...(model.trim() !== settings.model || provider !== settings.provider
                  ? { model: model.trim() } : {}),
              });
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

type SectionKey = "credentials" | "prices" | "founder" | "slack" | "people" | "account";

const SECTIONS: ({ group: string; admin: boolean } | { key: SectionKey; label: string; admin: boolean })[] = [
  { group: "This box", admin: true },
  { key: "credentials", label: "LLM credentials", admin: true },
  { key: "prices", label: "Model prices", admin: true },
  { key: "founder", label: "Founder", admin: true },
  { key: "slack", label: "Slack", admin: true },
  { key: "people", label: "People", admin: true },
  { group: "You", admin: false },
  { key: "account", label: "Account", admin: false },
];

/** The state of a section, said in the rail. Three words at most: this is the
    line that decides whether the section is worth opening, not a summary of
    what is inside it. */
function railNote(
  key: SectionKey,
  s: AppSettings | null,
  users: UserRow[] | null,
  email: string,
): { text: string; tone: string } {
  if (key === "account") return { text: email, tone: "" };
  if (key === "people") {
    if (!users) return { text: "…", tone: "" };
    const active = users.filter((u) => u.active);
    const waiting = active.filter((u) => !u.has_password).length;
    return waiting
      ? { text: `${waiting} not set up yet`, tone: "warn" }
      : { text: `${active.length} ${active.length === 1 ? "person" : "people"}`, tone: "good" };
  }
  if (!s) return { text: "…", tone: "" };
  if (key === "credentials") {
    const { total, in_use, hidden, offerable } = s.llm_credentials;
    if (total === 0) return { text: "none yet", tone: "warn" };
    if (in_use === 0) return { text: `${offerable} offered · none in use`, tone: "warn" };
    return { text: `${in_use} in use of ${offerable}${hidden ? ` · ${hidden} hidden` : ""}`,
             tone: "good" };
  }
  if (key === "prices") return { text: "what models cost", tone: "" };
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
  const { me } = useMe();
  const admin = Boolean(me?.is_admin);
  const [settings, setSettings] = useState<AppSettings | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [users, setUsers] = useState<UserRow[] | null>(null);
  const [usersError, setUsersError] = useState<string | null>(null);
  const [params, setParams] = useSearchParams();
  const visible = SECTIONS.filter((s) => admin || !s.admin);
  const keys = visible.flatMap((s) => ("key" in s ? [s.key] : []));
  // An admin still lands where they always did; everybody else has one place
  // to land, and it is their own account.
  const asked = params.get("section") as SectionKey | null;
  const section: SectionKey = asked && keys.includes(asked) ? asked : admin ? "credentials" : "account";

  const reload = useCallback(() => {
    if (!admin) return;
    get<AppSettings>("/admin/settings").then(setSettings).catch((e) => setError(e.message));
  }, [admin]);

  const reloadUsers = useCallback(() => {
    if (!admin) return;
    get<UserRow[]>("/admin/users")
      .then((rows) => {
        setUsers(rows);
        setUsersError(null);
      })
      .catch((e) => setUsersError((e as Error).message));
  }, [admin]);

  useEffect(reload, [reload]);
  useEffect(reloadUsers, [reloadUsers]);

  const platform = section === "credentials" || section === "founder" || section === "slack";

  return (
    <>
      <h1>Settings</h1>
      <p className="subtitle">
        {admin ? (
          <>
            What is true above every project. A project&apos;s own rules — its budget, its
            constitution, who it hires, who may decide on it — live in that project, because
            they bind that project. The credentials and connections every project draws on,
            and the people who may sign in at all, live here.
          </>
        ) : (
          <>
            Your own account. A project&apos;s rules — its budget, its constitution, who may
            decide on it — live in that project&apos;s own Settings. The box&apos;s settings
            belong to whoever runs it.
          </>
        )}
      </p>
      {error && platform && <div className="error">{error}</div>}
      <div className="settings-lay">
        <div className="srail">
          {visible.map((item) => {
            if ("group" in item) {
              // One heading over one entry is a label for nothing.
              return admin ? <div className="grp" key={item.group}>{item.group}</div> : null;
            }
            const note = railNote(item.key, settings, users, me?.email ?? "");
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
          {platform && settings === null ? (
            <div className="card muted">Loading…</div>
          ) : (
            <>
              {section === "credentials" && <LlmCredentials onChanged={reload} />}
              {section === "prices" && <ModelPrices />}
              {section === "founder" && settings && (
                <>
                  <h2 style={{ marginTop: 0 }}>The key the Founder drafts on</h2>
                  <BorrowCard settings={settings.anthropic} reload={reload} />
                  <ModelCard settings={settings.anthropic} reload={reload} />
                </>
              )}
              {section === "slack" && settings && (
                <>
                  <h2 style={{ marginTop: 0 }}>Slack connection</h2>
                  <SlackCard settings={settings.slack} reload={reload} />
                </>
              )}
            </>
          )}
          {section === "people" && (
            <>
              <h2 style={{ marginTop: 0 }}>People</h2>
              <People users={users} loadError={usersError} reload={reloadUsers} />
            </>
          )}
          {section === "account" && (
            <>
              <h2 style={{ marginTop: 0 }}>Your password</h2>
              <PasswordCard />
              <h2>Personal tokens</h2>
              <TokensCard />
              <h2>Where you are signed in</h2>
              <SessionsCard />
            </>
          )}
        </div>
      </div>
    </>
  );
}
