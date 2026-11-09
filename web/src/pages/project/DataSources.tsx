import { useEffect, useState } from "react";
import {
  Dashboard, DataSourceTool, del, get, McpServerEntry, OrgView, post, SecretRow, SourceState,
  ToolPermission,
} from "../../api";
import {
  AccessPicker,
  AuthPicker,
  BLANK_DATA_SOURCE,
  DataSourceDraft,
  dataSourceBody,
  isFullUrl,
  OAuthClientFields,
  savedName,
  slugifyName,
  SourceAuth,
} from "../../datasource";
import { GrantChips, listJoin, TeamSwitches } from "../../grants";
import { fmtWhen } from "../../hooks";
import { grantOptions } from "../../roles";
import { Drawer, RowMenu, SecretInput } from "../../ui";
import { Icon } from "../../ui/Icon";

/** Data sources — the MCP servers agents call as tools.

    The old table gave every source six columns and four buttons, and one red
    word for every way a source can fail: a wrong port, a refused token, a
    proxy in the way and a server with no tools all read "unreachable", while
    an OAuth source showed "not connected" and "unreachable" side by side.
    Now each source has one state in words, with the one button that fixes it
    beside it, and the raw detail underneath for whoever is debugging. */

type Tone = "good" | "warn" | "critical";

interface Health {
  tone: Tone;
  label: string;
  when: string;
  /** The one action that moves this state, if there is one. */
  fix: "connect" | "reconnect" | "test" | "edit" | null;
}

const STATE: Record<SourceState, { tone: Tone; label: string; fix: Health["fix"] }> = {
  ok: { tone: "good", label: "Working", fix: null },
  unauthorized: { tone: "critical", label: "Credential refused", fix: "edit" },
  not_found: { tone: "critical", label: "Wrong address", fix: "edit" },
  not_mcp: { tone: "critical", label: "Not an MCP server", fix: "edit" },
  no_tools: { tone: "warn", label: "Offers no tools", fix: "test" },
  blocked: { tone: "critical", label: "Blocked by the network", fix: "test" },
  timeout: { tone: "critical", label: "Not answering", fix: "test" },
  unreachable: { tone: "critical", label: "Can't connect", fix: "test" },
  needs_signin: { tone: "warn", label: "Needs sign-in", fix: "connect" },
  needs_reauth: { tone: "critical", label: "Sign-in expired", fix: "reconnect" },
};

export function sourceAuthOf(source: McpServerEntry): SourceAuth {
  return source.auth ?? (source.bearer_secret ? "bearer" : "none");
}

/** One state per source. The OAuth connection outranks the last check: a
    check run before sign-in failed for the reason the sign-in fixes, and
    showing both is how one row came to say two contradictory things. */
export function sourceHealth(source: McpServerEntry): Health {
  const last = source.last_check;
  const checked = last ? `checked ${fmtWhen(last.at)}` : "";
  if (sourceAuthOf(source) === "oauth") {
    const status = source.oauth?.status;
    if (status === "needs_reauth")
      return { ...STATE.needs_reauth, when: "The provider rejected the token" };
    if (status !== "connected")
      return { ...STATE.needs_signin, when: status === "pending" ? "Sign-in started, not finished" : "Not connected yet" };
  }
  if (!last) return { tone: "warn", label: "Never checked", when: "", fix: "test" };
  if (last.ok)
    return { ...STATE.ok, when: [last.tools ? `${last.tools} tools` : "", checked].filter(Boolean).join(" · ") };
  const known = last.state ? STATE[last.state] : undefined;
  return { ...(known ?? STATE.unreachable), when: checked };
}

/** Start the browser sign-in for an OAuth source. The tab must be opened by
    the click itself (popup blockers kill anything opened after an await), so
    callers pass one pre-opened blank and we point it at the provider. */
async function startOAuthSignIn(projectId: string, name: string, tab: Window | null) {
  const started = await post<{ authorize_url: string; note: string }>(
    `/admin/projects/${projectId}/mcp_servers/${name}/oauth/start`, {});
  if (tab) tab.location.href = started.authorize_url;
  else window.open(started.authorize_url, "_blank");
  return started;
}

interface SaveResult {
  name: string;
  check: { ok: boolean; detail: string; state?: SourceState };
  needs_connect: boolean;
  note: string;
}

function savedMessage(saved: SaveResult): string {
  if (saved.needs_connect)
    return `Saved '${saved.name}'. Finish the provider's sign-in in the tab that opened; this page shows it connect.`;
  return `Saved '${saved.name}' — ${saved.check.ok ? "" : "but "}${saved.check.detail}. ${saved.note}`;
}

/** "mcp.linear.app" → "linear"; an IP address suggests nothing. */
export function nameFromUrl(url: string): string {
  try {
    const host = new URL(url.trim()).hostname;
    if (/^[\d.]+$/.test(host) || host.includes(":")) return "";
    const labels = host.split(".").filter((l) => !["mcp", "api", "www", "server"].includes(l));
    return slugifyName(labels.length > 1 ? labels[labels.length - 2] : labels[0] ?? "");
  } catch {
    return "";
  }
}

type Drawn = { kind: "add" } | { kind: "edit"; name: string; confirmDelete?: boolean }
  | { kind: "tools"; name: string };

export default function DataSources({
  projectId, dash, org, secrets, notice, reload,
}: {
  projectId: string;
  dash: Dashboard;
  org: OrgView | null;
  secrets: SecretRow[];
  notice: (message: string) => void;
  reload: () => void;
}) {
  const sources = (dash.integrations["mcp_servers"] as McpServerEntry[] | undefined) ?? [];
  const options = grantOptions(org);
  const [drawn, setDrawn] = useState<Drawn | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // The source whose sign-in tab is open right now. While set, the dash is
  // re-polled so the page notices the callback landing without a refresh.
  const [connecting, setConnecting] = useState<string | null>(null);
  // Every tool every source offers, with its permission. Read whole: a project
  // has a handful of sources and each a few dozen tools at most.
  const [tools, setTools] = useState<DataSourceTool[]>([]);
  const loadTools = () => {
    get<DataSourceTool[]>(`/admin/projects/${projectId}/data_source_tools`)
      .then(setTools)
      .catch(() => setTools([]));
  };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(loadTools, [projectId, dash]);

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
    if (connecting && sources.find((s) => s.name === connecting)?.oauth?.status === "connected") {
      notice(`'${connecting}' is connected.`);
      setConnecting(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sources, connecting]);

  async function test(name: string) {
    setBusy(name);
    setError(null);
    try {
      const check = await post<{ ok: boolean; detail: string }>(
        `/admin/projects/${projectId}/mcp_servers/${name}/check`, {});
      notice(`'${name}': ${check.detail}`);
      loadTools();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  async function connect(name: string) {
    // Opened in the click itself — see startOAuthSignIn.
    const tab = window.open("", "_blank");
    setBusy(name);
    setError(null);
    try {
      await startOAuthSignIn(projectId, name, tab);
      setConnecting(name);
    } catch (e) {
      tab?.close();
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  const healths = sources.map(sourceHealth);
  const count = (tone: Tone) => healths.filter((h) => h.tone === tone).length;

  return (
    <>
      <div className="pagehead-row">
        <div>
          <h2 style={{ marginTop: 0 }}>Data sources</h2>
          <p className="subtitle" style={{ marginBottom: 0 }}>
            MCP servers your agents call as tools. Each keeps its own address and sign-in, and is
            checked when you save it. Every call goes through yaaf: tools that read are allowed,
            tools that write are refused until you allow them.
          </p>
        </div>
        <div className="actions" style={{ marginTop: 0 }}>
          <button className="primary" onClick={() => setDrawn({ kind: "add" })}>
            <Icon name="plus" size={15} /> Add data source
          </button>
        </div>
      </div>
      {error && <div className="error">{error}</div>}
      {sources.length > 0 && (
        <div className="listbar">
          {count("critical") > 0 && <span className="chip critical">{count("critical")} can't be used</span>}
          {count("warn") > 0 && <span className="chip warn">{count("warn")} need attention</span>}
          {count("good") > 0 && <span className="chip ok">{count("good")} working</span>}
        </div>
      )}
      <div className="vlist">
        {sources.length === 0 && (
          <div className="vempty">
            <b>No data sources yet.</b> Add an MCP server — Notion, Linear, your warehouse, an ads
            API — and the teams you choose can call its tools.
          </div>
        )}
        {sources.map((source, i) => {
          const h = healths[i];
          const fixButton = h.fix === "connect" || h.fix === "reconnect" ? (
            <button className="small primary" disabled={busy === source.name}
                    onClick={() => connect(source.name)}>
              {h.fix === "reconnect" ? "Reconnect" : "Connect"}
            </button>
          ) : h.fix === "test" ? (
            <button className="small" disabled={busy === source.name} onClick={() => test(source.name)}>
              <Icon name="refresh" size={13} /> {busy === source.name ? "Testing…" : "Test again"}
            </button>
          ) : h.fix === "edit" ? (
            <button className="small" onClick={() => setDrawn({ kind: "edit", name: source.name })}>
              <Icon name="edit" size={13} /> Fix it
            </button>
          ) : null;
          return (
            <div className="srow" key={source.name}>
              <div className={`sstate ${h.tone}`}>
                <span className="sdot" />
                <span>{h.label}{h.when && <small>{h.when}</small>}</span>
              </div>
              <div className="sname">
                <code className="vnm">{source.name}</code>
                <span className="shost">{source.url_host}:{source.url_port}</span>
                <span className="gchips" style={{ marginTop: 6 }}>
                  <span className="gchip">
                    <Icon name={sourceAuthOf(source) === "none" ? "plug" : sourceAuthOf(source) === "oauth" ? "user" : "key"} size={12} />
                    {sourceAuthOf(source) === "oauth" ? "OAuth sign-in" : sourceAuthOf(source) === "bearer" ? "Bearer token" : "No sign-in"}
                  </span>
                  <span className="gchip">
                    {(source.access ?? "inline") === "bulk" ? "Results to a file" : "Results into the conversation"}
                  </span>
                  <ToolCountChip tools={tools.filter((t) => t.source === source.name && !t.gone)}
                    onOpen={() => setDrawn({ kind: "tools", name: source.name })} />
                </span>
              </div>
              <div className="swho">
                <span className="vsub">Who gets its tools</span>
                <GrantChips options={options} selected={source.roles} />
              </div>
              <div className="sacts">
                {fixButton}
                <RowMenu label={`More for ${source.name}`} items={[
                  { label: "Tools & permissions", icon: "shield", onClick: () => setDrawn({ kind: "tools", name: source.name }) },
                  { label: "Test connection", icon: "refresh", onClick: () => test(source.name) },
                  { label: "Edit", icon: "edit", onClick: () => setDrawn({ kind: "edit", name: source.name }) },
                  { label: "Remove…", icon: "trash", danger: true,
                    onClick: () => setDrawn({ kind: "edit", name: source.name, confirmDelete: true }) },
                ]} />
              </div>
              {h.tone !== "good" && source.last_check && !source.last_check.ok
                && sourceAuthOf(source) !== "oauth" && (
                <div className={`swhy ${h.tone}`}>{source.last_check.detail}</div>
              )}
            </div>
          );
        })}
      </div>

      {drawn?.kind === "tools" && (
        <ToolsDrawer
          projectId={projectId}
          source={drawn.name}
          tools={tools.filter((t) => t.source === drawn.name)}
          onChanged={loadTools}
          onTest={() => test(drawn.name)}
          testing={busy === drawn.name}
          onClose={() => setDrawn(null)}
        />
      )}
      {drawn && drawn.kind !== "tools" && (
        <SourceDrawer
          key={drawn.kind === "edit" ? drawn.name : "add"}
          projectId={projectId}
          org={org}
          secrets={secrets}
          source={drawn.kind === "edit" ? sources.find((s) => s.name === drawn.name) : undefined}
          taken={sources.map((s) => s.name)}
          startDeleting={drawn.kind === "edit" && !!drawn.confirmDelete}
          onClose={() => setDrawn(null)}
          onSaved={(message, connectName) => {
            setDrawn(null);
            if (connectName) setConnecting(connectName);
            notice(message);
          }}
        />
      )}
    </>
  );
}

function SourceDrawer({ projectId, org, secrets, source, taken, startDeleting, onClose, onSaved }: {
  projectId: string;
  org: OrgView | null;
  secrets: SecretRow[];
  source?: McpServerEntry;
  taken: string[];
  startDeleting: boolean;
  onClose: () => void;
  onSaved: (message: string, connecting?: string) => void;
}) {
  const editing = !!source;
  const options = grantOptions(org);
  const [draft, setDraft] = useState<DataSourceDraft>(source ? {
    ...BLANK_DATA_SOURCE,
    name: source.name,
    roles: source.roles,
    access: source.access ?? "inline",
    auth: sourceAuthOf(source),
    scope: source.oauth?.scope ?? "",
  } : BLANK_DATA_SOURCE);
  const set = (patch: Partial<DataSourceDraft>) => setDraft((prev) => ({ ...prev, ...patch }));
  const [nameTouched, setNameTouched] = useState(editing);
  const [probe, setProbe] = useState<{ check: SaveResult["check"]; auth: SourceAuth } | null>(null);
  const [probing, setProbing] = useState(false);
  const [probedUrl, setProbedUrl] = useState("");
  const [dropToken, setDropToken] = useState(false);
  const [loadedUrl, setLoadedUrl] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(startDeleting);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Editing: the address is fetched and shown outright — an address is not a
  // password, and you cannot fix a port you cannot see.
  const urlSecretId = source ? secrets.find((s) => s.name === source.url_secret)?.id : undefined;
  useEffect(() => {
    if (!source) return;
    if (!urlSecretId) {
      setError("its stored address is missing — type it again to repair the source");
      return;
    }
    get<{ value: string }>(`/admin/secrets/${urlSecretId}/value`)
      .then((got) => { set({ url: got.value }); setLoadedUrl(got.value); })
      .catch((e) => setError((e as Error).message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [urlSecretId]);

  async function runProbe(url: string) {
    if (editing || !isFullUrl(url) || url.trim() === probedUrl) return;
    setProbing(true);
    setProbedUrl(url.trim());
    try {
      const found = await post<{ check: SaveResult["check"]; auth: SourceAuth }>(
        `/admin/projects/${projectId}/mcp_servers/probe`, { url: url.trim() });
      setProbe(found);
      set({ auth: found.auth });
    } catch (e) {
      setProbe({ check: { ok: false, detail: (e as Error).message }, auth: draft.auth });
    } finally {
      setProbing(false);
    }
  }

  const heldToken = !!source?.bearer_secret && sourceAuthOf(source) === "bearer";
  const name = savedName(draft);
  const clash = !editing && taken.includes(name);
  const onlyTeamsChanged = editing && source && loadedUrl !== null
    && draft.url.trim() === loadedUrl.trim() && draft.auth === sourceAuthOf(source)
    && draft.access === (source.access ?? "inline") && !draft.bearer && !dropToken
    && !draft.clientId && !draft.clientSecret && draft.scope === (source.oauth?.scope ?? "");
  const unchanged = onlyTeamsChanged
    && [...draft.roles].sort().join() === [...source!.roles].sort().join();
  const ready = name.length >= 2 && isFullUrl(draft.url) && draft.roles.length > 0 && !clash
    && !unchanged;

  async function save() {
    setBusy(true);
    setError(null);
    if (onlyTeamsChanged) {
      try {
        await post(`/admin/projects/${projectId}/mcp_servers/${source!.name}/roles`, { roles: draft.roles });
        onSaved(`Saved who gets '${source!.name}'. It applies on each team's next run.`);
      } catch (e) {
        setError((e as Error).message);
        setBusy(false);
      }
      return;
    }
    // Pre-open the sign-in tab inside the click, before any await — a tab
    // opened after the network round-trip is popup-blocked.
    const tab = draft.auth === "oauth" ? window.open("", "_blank") : null;
    try {
      const result = await post<SaveResult>(`/admin/projects/${projectId}/mcp_servers`, {
        ...dataSourceBody(draft),
        ...(editing ? {
          // Omitted keeps the stored token; "" removes it; a value rotates.
          bearer: draft.auth === "bearer" ? (dropToken ? "" : draft.bearer.trim() || undefined)
            : draft.auth === "none" && source?.bearer_secret ? "" : undefined,
        } : {}),
      });
      if (result.needs_connect) {
        await startOAuthSignIn(projectId, result.name, tab);
        onSaved(savedMessage(result), result.name);
      } else {
        tab?.close();
        onSaved(savedMessage(result));
      }
    } catch (e) {
      tab?.close();
      setError((e as Error).message);
      setBusy(false);
    }
  }

  async function remove() {
    setBusy(true);
    setError(null);
    try {
      await del(`/admin/projects/${projectId}/mcp_servers/${source!.name}`);
      onSaved(`Removed '${source!.name}'. Teams that had it lose its tools on their next run.`);
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }

  const people = options.filter((o) => source?.roles.includes(o.slug)).flatMap((o) => o.people ?? []);
  const detected = probe && !editing ? probe : null;

  return (
    <Drawer
      title={editing ? <code>{source!.name}</code> : "Add a data source"}
      sub={editing ? <>{source!.url_host}:{source!.url_port}</> : "An MCP server: an address that offers tools."}
      onClose={onClose}
      footer={<>
        {editing && (
          <button className="danger-text" disabled={busy} onClick={() => setConfirming(true)}>
            <Icon name="trash" size={14} /> Remove
          </button>
        )}
        <span style={{ flex: 1 }} />
        <button disabled={busy} onClick={onClose}>Cancel</button>
        <button className="primary" disabled={busy || !ready} onClick={save}>
          {busy ? "Saving…" : editing ? "Save changes"
            : draft.auth === "oauth" ? "Add and sign in" : "Add and test"}
        </button>
      </>}
    >
      {error && <div className="error" style={{ margin: 0 }}>{error}</div>}
      {confirming && source && (
        <div className="confirmbox" role="alert">
          <b>Remove {source.name}?</b>
          <span>
            Its stored address and token are deleted too{sourceAuthOf(source) === "oauth" ? ", including its OAuth tokens" : ""}.{" "}
            {people.length ? `${listJoin(people)} ${people.length === 1 ? "loses" : "lose"} its tools on the next run.` : ""}
          </span>
          <div className="actions" style={{ marginTop: 0 }}>
            <button className="danger" disabled={busy} onClick={remove}>Remove data source</button>
            <button disabled={busy} onClick={() => setConfirming(false)}>Keep it</button>
          </div>
        </div>
      )}
      <div className="tswitch-field">
        <label className="lbl" htmlFor="src-url">Address</label>
        <input id="src-url" className="mono" value={draft.url} autoFocus={!editing}
               placeholder="https://host:3845/mcp"
               onChange={(e) => {
                 const url = e.target.value;
                 set({ url, ...(nameTouched ? {} : { name: nameFromUrl(url) }) });
               }}
               onBlur={(e) => runProbe(e.target.value)}
               onKeyDown={(e) => { if (e.key === "Enter") runProbe(draft.url); }} />
        {!!draft.url.trim() && !isFullUrl(draft.url) ? (
          <span className="error" style={{ margin: 0 }}>
            Not a full address — like https://host:3845/mcp. localhost won't work: sandboxes are
            containers, so use the host's network address.
          </span>
        ) : (
          <span className="help">The server's full address, path included.</span>
        )}
      </div>
      {probing && <div className="detect"><Icon name="clock" size={15} /> Checking the address…</div>}
      {detected && !probing && (
        <div className={`detect ${detected.check.ok || detected.check.state === "needs_signin" || detected.auth === "bearer" ? "good" : "bad"}`}>
          <Icon name={detected.check.ok || detected.check.state === "needs_signin" || detected.auth === "bearer" ? "check" : "alert"} size={15} />
          <span>
            {detected.check.ok ? <><b>Found it.</b> {detected.check.detail}. No sign-in needed.</>
              : detected.check.state === "needs_signin" ? <><b>Found it.</b> It asks you to sign in with OAuth — saving opens its sign-in in a new tab.</>
              : detected.auth === "bearer" ? <><b>Found it.</b> It wants a credential: paste its token below.</>
              : <><b>No answer yet.</b> {detected.check.detail}. You can still add it and test again later.</>}
            <span className="help" style={{ display: "block" }}>Wrong guess? Pick another way under “Sign-in” below.</span>
          </span>
        </div>
      )}
      {!editing && (
        <div className="tswitch-field">
          <label className="lbl" htmlFor="src-name">Name</label>
          <input id="src-name" className="mono" value={draft.name} placeholder="linear, analytics, crm…"
                 onChange={(e) => { setNameTouched(true); set({ name: slugifyName(e.target.value) }); }} />
          <span className={clash ? "error" : "help"} style={clash ? { margin: 0 } : undefined}>
            {clash ? `There is already a source called '${name}'. Pick another name, or edit that one.`
              : name ? <>Agents call its tools as <code>mcp__{name}__*</code>, through yaaf: its address and token never reach them.</>
              : "Lowercase letters, digits and dashes. Filled in from the address."}
          </span>
        </div>
      )}
      <div className="tswitch-field">
        <AuthPicker value={draft.auth} onChange={(auth) => set({ auth })} />
        {draft.auth === "bearer" && (
          <>
            <SecretInput value={draft.bearer} disabled={dropToken}
                         onChange={(bearer) => set({ bearer })}
                         placeholder={heldToken ? "Blank keeps the current token" : "Paste the token"} />
            {heldToken && (
              <label className="help" style={{ display: "flex", gap: 6, alignItems: "center" }}>
                <input type="checkbox" style={{ width: "auto", minHeight: 0 }} checked={dropToken}
                       onChange={(e) => setDropToken(e.target.checked)} />
                Remove the stored token — this server needs no sign-in
              </label>
            )}
          </>
        )}
        {draft.auth === "oauth" && (
          <OAuthClientFields clientId={draft.clientId} clientSecret={draft.clientSecret} scope={draft.scope}
                             onClientId={(clientId) => set({ clientId })}
                             onClientSecret={(clientSecret) => set({ clientSecret })}
                             onScope={(scope) => set({ scope })} />
        )}
      </div>
      <AccessPicker value={draft.access} onChange={(access) => set({ access })} />
      <TeamSwitches options={options} selected={draft.roles} onChange={(roles) => set({ roles })}
                    label="Who gets its tools" what="its tools" credential={false}
                    emptyHint="No teams yet — add one on the Team page first. A source nobody receives can't be saved." />
      {draft.roles.length === 0 && (
        <span className="help">Switch on at least one team. A source nobody receives is an address stored for no one.</span>
      )}
    </Drawer>
  );
}


/** "12 tools · 3 denied", clickable — the count a source row needs, and the
    way into the list behind it. */
function ToolCountChip({ tools, onOpen }: { tools: DataSourceTool[]; onOpen: () => void }) {
  if (tools.length === 0) return null;
  const denied = tools.filter((t) => t.permission === "deny").length;
  return (
    <button type="button" className="gchip tool-count" onClick={onOpen}
            title="See every tool and whether agents may call it">
      <Icon name="shield" size={12} />
      {tools.length} tool{tools.length === 1 ? "" : "s"}
      {denied > 0 && <> · {denied} denied</>}
    </button>
  );
}

const KIND_LABEL: Record<DataSourceTool["kind"], { label: string; tone: string }> = {
  read: { label: "Reads", tone: "ok" },
  write: { label: "Writes", tone: "critical" },
  unknown: { label: "Unclear", tone: "warn" },
};

const DECIDED: Record<DataSourceTool["decided_by"], string> = {
  default: "set by what it does",
  allowlist: "set by the list saved with the source",
  chairman: "set by you",
  approval: "set by you, on the CEO's request",
};

type ToolFilter = "all" | "allow" | "deny" | "write";

/** Every tool a source offers, what it does, and the switch that decides
    whether an agent's call to it is forwarded. The switch is yours alone: the
    CEO can ask (it lands in your Inbox), and nothing it or anyone says in
    conversation moves it. In force on the next call any agent makes. */
function ToolsDrawer({ projectId, source, tools, onChanged, onTest, testing, onClose }: {
  projectId: string;
  source: string;
  tools: DataSourceTool[];
  onChanged: () => void;
  onTest: () => void;
  testing: boolean;
  onClose: () => void;
}) {
  const [filter, setFilter] = useState<ToolFilter>("all");
  const [query, setQuery] = useState("");
  const [saving, setSaving] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const live = tools.filter((t) => !t.gone);
  const gone = tools.filter((t) => t.gone);
  const count = (f: ToolFilter) => live.filter((t) => matches(t, f)).length;
  const q = query.trim().toLowerCase();
  const shown = live.filter((t) => matches(t, filter)
    && (!q || t.name.toLowerCase().includes(q) || (t.description ?? "").toLowerCase().includes(q)));

  async function setPermission(tool: DataSourceTool, permission: ToolPermission) {
    if (tool.permission === permission) return;
    setSaving(tool.name);
    setError(null);
    try {
      await post(`/admin/projects/${projectId}/mcp_servers/${source}/permissions`,
        { tool: tool.name, permission });
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(null);
    }
  }

  return (
    <Drawer wide title={<><code>{source}</code> tools</>}
      sub={live.length
        ? `${live.length} tool${live.length === 1 ? "" : "s"} · ${count("allow")} allowed · ${count("deny")} denied`
        : "No tools recorded yet"}
      onClose={onClose}
      footer={<>
        <span className="help" style={{ flex: 1 }}>
          Changes apply on the next call any agent makes. The CEO can ask you to change one; only you can.
        </span>
        <button disabled={testing} onClick={onTest}>
          <Icon name="refresh" size={13} /> {testing ? "Checking…" : "Check for new tools"}
        </button>
        <button className="primary" onClick={onClose}>Done</button>
      </>}
    >
      {error && <div className="error" style={{ margin: 0 }}>{error}</div>}
      {live.length === 0 ? (
        <div className="vempty">
          <b>No tools recorded yet.</b> They are recorded when the source answers a check — test the
          connection once it is reachable{" "}(and signed in, for OAuth). Until then every call to it is refused.
        </div>
      ) : (
        <>
          <div className="tool-bar">
            <div className="pills" role="tablist">
              {(["all", "allow", "deny", "write"] as ToolFilter[]).map((f) => (
                <button key={f} type="button" role="tab" aria-selected={filter === f}
                        className={filter === f ? "active" : ""} onClick={() => setFilter(f)}>
                  {{ all: "All", allow: "Allowed", deny: "Denied", write: "Writes" }[f]}
                  <span className="pill-n">{count(f)}</span>
                </button>
              ))}
            </div>
            <input className="tool-search" value={query} placeholder="Find a tool…"
                   onChange={(e) => setQuery(e.target.value)} />
          </div>
          <div className="tool-list">
            {shown.map((t) => (
              <ToolRow key={t.name} tool={t} saving={saving === t.name}
                       onSet={(permission) => setPermission(t, permission)} />
            ))}
            {shown.length === 0 && <div className="vempty">Nothing matches.</div>}
          </div>
        </>
      )}
      {gone.length > 0 && (
        <details className="tool-gone">
          <summary className="help">
            {gone.length} tool{gone.length === 1 ? " is" : "s are"} no longer offered by the server
          </summary>
          <p className="help">
            Kept with their permission, which applies again if the server offers them again:{" "}
            {gone.map((t) => <code key={t.name} style={{ marginRight: 6 }}>{t.name} ({t.permission})</code>)}
          </p>
        </details>
      )}
    </Drawer>
  );
}

function matches(t: DataSourceTool, f: ToolFilter): boolean {
  if (f === "all") return true;
  if (f === "write") return t.kind !== "read";
  return t.permission === f;
}

function ToolRow({ tool, saving, onSet }: {
  tool: DataSourceTool;
  saving: boolean;
  onSet: (permission: ToolPermission) => void;
}) {
  const [open, setOpen] = useState(false);
  const kind = KIND_LABEL[tool.kind];
  const params = Object.entries(tool.input_schema?.properties ?? {});
  const required = new Set(tool.input_schema?.required ?? []);
  return (
    <div className={`trow ${tool.permission}`}>
      <div className="tmain">
        <div className="thead">
          <button type="button" className="tname" aria-expanded={open} onClick={() => setOpen(!open)}>
            <span className={`caret${open ? " open" : ""}`}>▸</span>
            <code>{tool.name}</code>
          </button>
          <span className={`chip ${kind.tone}`} title={tool.kind_reason}>{kind.label}</span>
        </div>
        {tool.description && <p className={`tdesc${open ? " open" : ""}`}>{tool.description}</p>}
        {open && (
          <div className="tdetail">
            <div><span className="dim">Why “{kind.label.toLowerCase()}”:</span> {tool.kind_reason}.</div>
            <div><span className="dim">Permission:</span> {DECIDED[tool.decided_by]}
              {tool.decided_at && <> · {fmtWhen(tool.decided_at)}</>}</div>
            {params.length > 0 ? (
              <div>
                <span className="dim">Takes:</span>{" "}
                {params.map(([name, spec]) => (
                  <code key={name} className="tparam" title={spec.description ?? ""}>
                    {name}{required.has(name) ? "" : "?"}{spec.type ? `: ${spec.type}` : ""}
                  </code>
                ))}
              </div>
            ) : <div className="dim">Takes no arguments.</div>}
          </div>
        )}
      </div>
      <div className="permseg" role="radiogroup" aria-label={`Can agents call ${tool.name}?`}>
        {(["allow", "deny"] as ToolPermission[]).map((p) => (
          <button key={p} type="button" role="radio" aria-checked={tool.permission === p}
                  disabled={saving} className={`${p}${tool.permission === p ? " on" : ""}`}
                  onClick={() => onSet(p)}>
            {p === "allow" ? "Allow" : "Deny"}
          </button>
        ))}
      </div>
    </div>
  );
}
