// App-wide settings: what is true above every project, and your own account.
//
// Most of it — the Founder's own model credential, the model lineups, the web
// search backend, the Slack connection — is small, stateful and read once a
// quarter. The LLM credentials projects pay with are a working list that on a
// real install runs to dozens of rows. Stacking all three on one scroll meant the rarely-touched cards sat
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
import { del, get, post, put, SearchSettings, UserRow } from "../api";
import { PasswordCard, SessionsCard, TokensCard } from "../account";
import { Credential, ModelPrices } from "../llmCredentials";
import { useMe } from "../me";
import { People } from "../people";
import { ProvidersSection } from "../providerCards";
import {
  BaseUrlFields, DEFAULT_PROVIDER, KeyHelp, KeyInput, ModelSelect, ProviderInfo, useModels,
  useProviders,
} from "../providers";
import { providerStatus, Readiness, READINESS } from "../readiness";
import { ModelsSection } from "../settingsModels";
import { SecretInput } from "../ui";

interface PlatformSettings {
  configured: boolean;
  billing_mode: string;
  /** Which provider the platform credential pays; the Founder runs on it. */
  provider: string;
  /** The model the Founder runs on: its reference, resolved on that provider. */
  model: string;
  /** As set — a class (`@frontier`) until somebody names a model. */
  model_ref?: string;
  note: string;
  /** Its own key, or one of the LLM credentials, borrowed — named, never shown. */
  source?: { source: "own" | "llm_credential" | null; credential_id?: string; name?: string };
}

/** Point the Founder at a key the install already holds for projects, so one
    key is enough to start. */
function BorrowCard({
  settings, reload,
}: { settings: PlatformSettings; reload: () => void }) {
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
  platform?: PlatformSettings;
  /** The same section, under the name it had when it could pay nobody else. */
  anthropic: PlatformSettings;
  slack: SlackSettings;
  llm_credentials: CredentialCounts;
}

/** One flash line under a card: the answer to the button you just pressed. */
function Flash({ note }: { note: { ok: boolean; text: string } | null }) {
  if (!note) return null;
  return <p className={note.ok ? "help" : "error"}>{note.text}</p>;
}

/** Kinds the Founder can be paid with. It runs in the control plane's own
    process: on a provider's API with a key, or — for a login only the Claude
    Code CLI can hold — on that CLI, which the server drives for it. */
const founderKinds = (seller: ProviderInfo | undefined) =>
  (seller?.kinds ?? []).filter((k) => k.mode === "api_key" || k.default_harness === "claude_agent_sdk");

const PLATFORM = "/admin/settings/platform-credential";

function ModelCard({
  settings, reload,
}: { settings: PlatformSettings; reload: () => void }) {
  const [value, setValue] = useState("");
  const [mode, setMode] = useState(settings.billing_mode);
  const [provider, setProvider] = useState(settings.provider || DEFAULT_PROVIDER);
  const [baseUrl, setBaseUrl] = useState("");
  const [model, setModel] = useState(settings.model_ref ?? settings.model);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);
  const sellers = useProviders().filter((p) => p.protocol);
  const catalog = useModels();
  const seller = sellers.find((p) => p.name === provider);
  const kinds = founderKinds(seller);
  const kind = kinds.find((k) => k.mode === mode) ?? kinds[0];
  const saved = settings.model_ref ?? settings.model;

  useEffect(() => {
    setMode(settings.billing_mode);
    setProvider(settings.provider || DEFAULT_PROVIDER);
    setModel(settings.model_ref ?? settings.model);
  }, [settings.billing_mode, settings.provider, settings.model, settings.model_ref]);

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

  const sellerLabel = sellers.find((p) => p.name === settings.provider)?.label ?? settings.provider;
  return (
    <div className="card">
      <p className="help" style={{ marginTop: 0 }}>
        {settings.note}
      </p>
      <p className="help">
        {settings.configured && settings.source?.source === "llm_credential" ? (
          <>
            <span className="badge good">configured</span> drafting on the LLM credential{" "}
            <strong>{settings.source.name}</strong> ({sellerLabel}), running{" "}
            <code>{settings.model}</code>.
          </>
        ) : settings.configured ? (
          <>
            <span className="badge good">configured</span> a {sellerLabel}{" "}
            {settings.billing_mode === "subscription" ? "subscription" : "API key"} is
            installed, running <code>{settings.model}</code>. The value is never shown
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
                  onChange={(e) => { setProvider(e.target.value); setMode("api_key"); setBaseUrl(""); }}>
            {sellers.length === 0 && <option value={provider}>Loading providers…</option>}
            {sellers.map((p) => <option key={p.name} value={p.name}>{p.label}</option>)}
          </select>
        </div>
        {kinds.length > 1 && <div>
          <label className="field">Key type</label>
          <select value={mode} disabled={busy} onChange={(e) => setMode(e.target.value)}>
            {kinds.map((k) => <option key={k.mode} value={k.mode}>{k.bills.split(",")[0]}</option>)}
          </select>
        </div>}
      </div>
      {seller?.credential_base_url && <BaseUrlFields provider={seller} value={baseUrl}
        onChange={setBaseUrl} secret={value} disabled={busy} />}
      <label className="field">
        {settings.configured ? `Replace the ${(kind?.label ?? "key").toLowerCase()} — blank keeps the current one`
          : kind?.label ?? "The key"}
      </label>
      <KeyInput kind={kind} value={value} onChange={setValue} disabled={busy}
        placeholder={seller?.local ? "Leave blank — it takes no key" : undefined} />
      <KeyHelp provider={seller} kind={kind} />
      <p className="help">
        The Founder runs on this provider&apos;s own API — or, for a Claude subscription, on
        the Claude Code CLI — with the model below. Unless you name one, that is the
        provider&apos;s Frontier model. Pasting a key where another kind belongs is refused
        here rather than failing on your first message.
      </p>

      <div className="actions">
        <button
          className="primary"
          disabled={busy || (!seller?.local && value.trim().length < 8)
            || (!!seller?.credential_base_url && !baseUrl.trim())}
          onClick={() =>
            run(async () => {
              await put(PLATFORM, {
                value: value.trim(), mode, provider,
                ...(baseUrl.trim() ? { base_url: baseUrl.trim() } : {}),
                // The model travels with a key of another provider: the two are
                // one decision, and the server refuses them apart.
                ...(model.trim() !== saved || provider !== settings.provider
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
                  const result = await post<{ ok: boolean; detail: string }>(`${PLATFORM}/test`);
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
                  await del(PLATFORM);
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
        <ModelSelect value={model} onChange={setModel} info={catalog} registry={sellers}
          primary={provider} providersShown={[provider]} disabled={busy} />
        <button
          disabled={busy || model.trim() === saved || model.trim().length < 2}
          onClick={() =>
            run(async () => {
              const said = await put<{ model: string }>(`${PLATFORM}/model`, { model: model.trim() });
              return `The Founder now runs on ${said.model ?? model.trim()}.`;
            })
          }
        >
          Save model
        </button>
      </div>
      {settings.model_ref && settings.model_ref !== settings.model &&
        <p className="help">{settings.model_ref} resolves to <code>{settings.model}</code> on{" "}
          {sellerLabel}.</p>}
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

// ── web search: the install's own backend ───────────────────────────────────────

function WebSearchSection({ reload: reloadRail }: { reload: () => void }) {
  const [data, setData] = useState<SearchSettings | null>(null);
  const [backend, setBackend] = useState("");
  const [key, setKey] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [price, setPrice] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);
  const [stranded, setStranded] = useState<string[]>([]);
  const adopt = useCallback((d: SearchSettings) => {
    setData(d);
    const chosen = d.configured?.backend ?? d.backends[0]?.name ?? "";
    setBackend(chosen);
    setBaseUrl(d.configured?.base_url ?? "");
    setPrice(String(d.configured?.price_usd ?? d.backends.find((b) => b.name === chosen)?.suggested_usd ?? ""));
  }, []);
  useEffect(() => {
    get<SearchSettings>("/admin/settings/web-search").then(adopt)
      .catch((e) => setNote({ ok: false, text: (e as Error).message }));
  }, [adopt]);
  async function run(fn: () => Promise<string>) {
    setBusy(true); setNote(null);
    try { setNote({ ok: true, text: await fn() }); reloadRail(); }
    catch (e) { setNote({ ok: false, text: (e as Error).message }); } finally { setBusy(false); }
  }
  if (!data) return <div className="card muted">Loading…</div>;
  const chosen = data.backends.find((b) => b.name === backend);
  const current = data.configured;
  const sameBackend = current?.backend === backend;
  return <>
    <h2 style={{ marginTop: 0 }}>Web search</h2>
    <p className="help">A role granted the web is promised search. Claude, GPT and Gemini
      search on their own APIs; most other providers — and the OpenCode CLI — do not, and a
      web role on one of them is refused until this install has a search backend of its own.
      Every query is billed into the run at the price below, under its cap, and the key never
      reaches an agent&apos;s shell.</p>
    <div className="card">
      <p className="help" style={{ marginTop: 0 }}>{current
        ? <><span className="badge good">configured</span> {current.label}, ${current.price_usd} a query.</>
        : <><span className="badge warn">not set</span> web roles run only where the provider or the harness searches.</>}</p>
      <div className="row">
        <div><label className="field">Backend</label>
          <select value={backend} disabled={busy} onChange={(e) => {
            setBackend(e.target.value);
            const next = data.backends.find((b) => b.name === e.target.value);
            setPrice(String(next?.suggested_usd ?? "")); setBaseUrl(next?.base_url ?? "");
          }}>
            {data.backends.map((b) => <option key={b.name} value={b.name}>{b.label}</option>)}
          </select></div>
        <div><label className="field">Price per query (USD)</label>
          <input type="number" min={0} step="any" value={price} disabled={busy}
            onChange={(e) => setPrice(e.target.value)} /></div>
      </div>
      {chosen && !chosen.base_url && <label className="field">Address
        <input value={baseUrl} disabled={busy} placeholder="http://searxng:8080"
          onChange={(e) => setBaseUrl(e.target.value)} /></label>}
      {chosen?.key && <>
        <label className="field">{current?.has_key && sameBackend ? "Replace the key — blank keeps the current one" : "API key"}</label>
        <SecretInput value={key} onChange={setKey} disabled={busy} />
      </>}
      <p className="help">{chosen?.suggested_usd !== undefined && `Suggested from ${chosen.label}'s entry rate: $${chosen.suggested_usd} a query — set what your plan actually charges. `}
        {chosen?.console_url && <a href={chosen.console_url} target="_blank" rel="noreferrer">
          Get a {chosen.label} key ↗</a>}</p>
      <div className="actions">
        <button className="primary" disabled={busy || !backend
          || (!!chosen?.key && !key.trim() && !(sameBackend && current?.has_key))}
          onClick={() => run(async () => {
            adopt(await put<SearchSettings>("/admin/settings/web-search", {
              backend, key: key.trim() || null, base_url: baseUrl.trim() || null,
              price_usd: price === "" ? null : Number(price) }));
            setKey("");
            return "Saved. From the next run, web roles are honoured on every provider.";
          })}>{current ? "Update" : "Save"}</button>
        {current && <>
          <button disabled={busy} onClick={() => run(async () => {
            const said = await post<{ ok: boolean; detail: string }>("/admin/settings/web-search/test");
            if (!said.ok) throw new Error(said.detail);
            return said.detail;
          })}>Test it</button>
          <button disabled={busy} onClick={() => {
            if (!confirm("Remove the search backend? Web roles on providers that cannot search "
                         + "are refused at their next run.")) return;
            run(async () => {
              const said = await del<{ unrunnable: string[] }>("/admin/settings/web-search");
              setStranded(said.unrunnable ?? []);
              adopt(await get<SearchSettings>("/admin/settings/web-search"));
              return "Removed.";
            });
          }}>Remove</button>
        </>}
      </div>
      <Flash note={note} />
      {stranded.length > 0 && <div className="notice-box" role="alert">
        <b>These seats can no longer run:</b>
        <ul>{stranded.map((line) => <li key={line}>{line}</li>)}</ul></div>}
    </div>
  </>;
}

type SectionKey = "providers" | "prices" | "models" | "search" | "founder" | "slack" | "people" | "account";

const SECTIONS: ({ group: string; admin: boolean } | { key: SectionKey; label: string; admin: boolean })[] = [
  { group: "This box", admin: true },
  { key: "providers", label: "Providers", admin: true },
  { key: "models", label: "Models", admin: true },
  { key: "prices", label: "Model prices", admin: true },
  { key: "search", label: "Web search", admin: true },
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
  readiness: Readiness | null,
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
  if (key === "providers") {
    if (!readiness) return { text: "…", tone: "" };
    const rows = readiness.providers;
    if (rows.length === 0) return { text: "none connected", tone: "warn" };
    const blocked = rows.filter((p) => providerStatus(p).tone === "critical").length;
    if (blocked) return { text: `${blocked} of ${rows.length} blocked`, tone: "warn" };
    const attention = rows.filter((p) => providerStatus(p).tone === "warn").length;
    return attention
      ? { text: `${rows.length - attention} ready · ${attention} to look at`, tone: "good" }
      : { text: `${rows.length} ready`, tone: "good" };
  }
  if (!s) return { text: "…", tone: "" };
  if (key === "prices") return { text: "what models cost", tone: "" };
  if (key === "models") return { text: "what each class runs on", tone: "" };
  if (key === "search") return { text: "for providers that cannot", tone: "" };
  if (key === "founder") {
    const founder = s.platform ?? s.anthropic;
    return founder.configured
      ? { text: founder.model, tone: "good" }
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
  // "credentials" is what the section was called before it was Providers; old
  // links, and the refusal texts that still say "Settings → LLM credentials",
  // land on the same place.
  const raw = params.get("section");
  const asked = (raw === "credentials" ? "providers" : raw) as SectionKey | null;
  const section: SectionKey = asked && keys.includes(asked) ? asked : admin ? "providers" : "account";
  const [readiness, setReadiness] = useState<Readiness | null>(null);

  const reload = useCallback(() => {
    if (!admin) return;
    get<AppSettings>("/admin/settings").then(setSettings).catch((e) => setError(e.message));
    get<Readiness>(READINESS).then(setReadiness).catch(() => {});
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

  const platform = section === "founder" || section === "slack";

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
            const note = railNote(item.key, settings, users, me?.email ?? "", readiness);
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
              {section === "providers" && <ProvidersSection onChanged={reload} />}
              {section === "prices" && <ModelPrices />}
              {section === "models" && <ModelsSection />}
              {section === "search" && <WebSearchSection reload={reload} />}
              {section === "founder" && settings && (
                <>
                  <h2 style={{ marginTop: 0 }}>The key the Founder drafts on</h2>
                  <BorrowCard settings={settings.platform ?? settings.anthropic} reload={reload} />
                  <ModelCard settings={settings.platform ?? settings.anthropic} reload={reload} />
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
