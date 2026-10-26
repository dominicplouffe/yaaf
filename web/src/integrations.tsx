import { useCallback, useEffect, useState } from "react";
import {
  del,
  get,
  Integration,
  IntegrationDecision,
  McpServerEntry,
  OrgView,
  post,
  ProjectIntegrations,
  put,
  SourceState,
  TaskMirrorConfig,
} from "./api";
import { AccessPicker, AuthPicker, isFullUrl, OAuthClientFields, slugifyName, SourceAccess, SourceAuth } from "./datasource";
import { GrantChips, listJoin, TeamSwitches } from "./grants";
import { nameFromUrl, sourceHealth } from "./pages/project/DataSources";
import { grantOptions } from "./roles";
import { Drawer, RowMenu, SecretInput } from "./ui";
import { Icon } from "./ui/Icon";

/** Integrations — MCP servers the administrator connects once, for every
    project. The same server as a project's data source, held above the
    projects: one address, one sign-in, one set of allowed tools, and each
    project decides whether to use it. Two screens: the list (Settings, for an
    administrator) and a project's choice (its Data sources page). */

/** An integration has the health a data source has, read the same way. */
export function integrationHealth(integration: Integration) {
  return sourceHealth({
    name: integration.name, url_secret: "", roles: [], url_host: integration.url_host,
    url_port: integration.url_port, access: integration.access, auth: integration.auth,
    oauth: integration.oauth ?? undefined, last_check: integration.last_check,
  } as McpServerEntry);
}

async function startSignIn(name: string, tab: Window | null) {
  const started = await post<{ authorize_url: string }>(`/admin/integrations/${name}/oauth/start`, {});
  if (tab) tab.location.href = started.authorize_url;
  else window.open(started.authorize_url, "_blank");
}

const splitList = (raw: string) => raw.split(",").map((s) => s.trim()).filter(Boolean);

// ── Settings → Integrations ─────────────────────────────────────────────────────

export function IntegrationsSection() {
  const [rows, setRows] = useState<Integration[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [drawn, setDrawn] = useState<{ name?: string; confirmDelete?: boolean } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [connecting, setConnecting] = useState<string | null>(null);

  const reload = useCallback(() => {
    get<Integration[]>("/admin/integrations").then(setRows).catch((e) => setError(e.message));
  }, []);
  useEffect(reload, [reload]);

  // While a sign-in tab is open, poll so the page sees the callback land.
  useEffect(() => {
    if (!connecting) return;
    const startedAt = Date.now();
    const timer = window.setInterval(() => {
      if (Date.now() - startedAt > 120_000) setConnecting(null);
      else reload();
    }, 3000);
    return () => window.clearInterval(timer);
  }, [connecting, reload]);
  useEffect(() => {
    if (connecting && rows?.find((r) => r.name === connecting)?.oauth?.status === "connected") {
      setNotice(`'${connecting}' is connected for every project that uses it.`);
      setConnecting(null);
    }
  }, [rows, connecting]);

  async function test(name: string) {
    setBusy(name);
    setError(null);
    try {
      const check = await post<{ detail: string }>(`/admin/integrations/${name}/check`, {});
      setNotice(`'${name}': ${check.detail}`);
      reload();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  async function connect(name: string) {
    const tab = window.open("", "_blank");
    setBusy(name);
    try {
      await startSignIn(name, tab);
      setConnecting(name);
    } catch (e) {
      tab?.close();
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  return (
    <>
      <div className="pagehead-row">
        <div>
          <h2 style={{ marginTop: 0 }}>Integrations</h2>
          <p className="subtitle" style={{ marginBottom: 0 }}>
            The tools the company already pays for — a CRM, a task manager, its docs — connected
            once for every project. A project uses one only when it says so, and the Founder asks
            about the ones that overlap a new project&apos;s goal, so nobody rebuilds what you
            already have.
          </p>
        </div>
        <div className="actions" style={{ marginTop: 0 }}>
          <button className="primary" onClick={() => setDrawn({})}>
            <Icon name="plus" size={15} /> Add integration
          </button>
        </div>
      </div>
      {error && <div className="error">{error}</div>}
      {notice && <div className="notice-box">{notice}</div>}
      <div className="vlist">
        {rows === null && <div className="card muted">Loading…</div>}
        {rows?.length === 0 && (
          <div className="vempty">
            <b>No integrations yet.</b> Add the MCP server of a service every project could use —
            HubSpot, Notion, Trello, GitHub — and say what it is for.
          </div>
        )}
        {rows?.map((row) => {
          const h = integrationHealth(row);
          const fix = h.fix === "connect" || h.fix === "reconnect" ? (
            <button className="small primary" disabled={busy === row.name} onClick={() => connect(row.name)}>
              {h.fix === "reconnect" ? "Reconnect" : "Connect"}
            </button>
          ) : h.fix === "test" ? (
            <button className="small" disabled={busy === row.name} onClick={() => test(row.name)}>
              <Icon name="refresh" size={13} /> {busy === row.name ? "Testing…" : "Test again"}
            </button>
          ) : h.fix === "edit" ? (
            <button className="small" onClick={() => setDrawn({ name: row.name })}>
              <Icon name="edit" size={13} /> Fix it
            </button>
          ) : null;
          return (
            <div className="srow" key={row.id}>
              <div className={`sstate ${h.tone}`}>
                <span className="sdot" />
                <span>{h.label}{h.when && <small>{h.when}</small>}</span>
              </div>
              <div className="sname">
                <code className="vnm">{row.name}</code>
                <span className="help">{row.description || `${row.url_host}:${row.url_port}`}</span>
                <span className="gchips" style={{ marginTop: 6 }}>
                  {row.covers.map((c) => <span className="gchip" key={c}>{c}</span>)}
                  {row.task_mirror && <span className="gchip"><Icon name="check" size={12} /> task manager</span>}
                  {row.tools && <span className="gchip">{row.tools.length} allowed tool{row.tools.length === 1 ? "" : "s"}</span>}
                </span>
              </div>
              <div className="swho">
                <span className="vsub">Used by</span>
                {row.used_by?.length
                  ? <span>{listJoin(row.used_by.map((p) => p.name))}</span>
                  : <span className="muted">No project yet</span>}
              </div>
              <div className="sacts">
                {fix}
                <RowMenu label={`More for ${row.name}`} items={[
                  { label: "Test connection", icon: "refresh", onClick: () => test(row.name) },
                  { label: "Edit", icon: "edit", onClick: () => setDrawn({ name: row.name }) },
                  { label: "Remove…", icon: "trash", danger: true,
                    onClick: () => setDrawn({ name: row.name, confirmDelete: true }) },
                ]} />
              </div>
              {h.tone !== "good" && row.last_check && !row.last_check.ok && row.auth !== "oauth" && (
                <div className={`swhy ${h.tone}`}>{row.last_check.detail}</div>
              )}
            </div>
          );
        })}
      </div>
      {drawn && (
        <IntegrationDrawer
          key={drawn.name ?? "add"}
          integration={drawn.name ? rows?.find((r) => r.name === drawn.name) : undefined}
          taken={(rows ?? []).map((r) => r.name)}
          startDeleting={!!drawn.confirmDelete}
          onClose={() => setDrawn(null)}
          onSaved={(message, connectName) => {
            setDrawn(null);
            setNotice(message);
            if (connectName) setConnecting(connectName);
            reload();
          }}
        />
      )}
    </>
  );
}

interface Draft {
  name: string;
  url: string;
  description: string;
  covers: string;
  auth: SourceAuth;
  bearer: string;
  clientId: string;
  clientSecret: string;
  scope: string;
  access: SourceAccess;
  restrict: boolean;
  tools: string;
  mirror: boolean;
  mirrorJson: string;
}

const MIRROR_EXAMPLE: TaskMirrorConfig = {
  create_tool: "create_card",
  create_arguments: { list_id: "YOUR-LIST-ID", name: "{title}", desc: "{description}\n\n{url}" },
  id_path: "id",
  update_tool: "update_card",
  update_arguments: { card_id: "{external_id}", name: "[{status}] {title}" },
};

interface SaveResult {
  name: string;
  check: { ok: boolean; detail: string; state?: SourceState };
  needs_connect: boolean;
}

function IntegrationDrawer({ integration, taken, startDeleting, onClose, onSaved }: {
  integration?: Integration;
  taken: string[];
  startDeleting: boolean;
  onClose: () => void;
  onSaved: (message: string, connecting?: string) => void;
}) {
  const editing = !!integration;
  const [draft, setDraft] = useState<Draft>({
    name: integration?.name ?? "",
    url: "",
    description: integration?.description ?? "",
    covers: (integration?.covers ?? []).join(", "),
    auth: integration?.auth ?? "none",
    bearer: "",
    clientId: "",
    clientSecret: "",
    scope: integration?.oauth?.scope ?? "",
    access: integration?.access ?? "inline",
    restrict: !!integration?.tools,
    tools: (integration?.tools ?? []).join(", "),
    mirror: !!integration?.task_mirror,
    mirrorJson: JSON.stringify(integration?.task_mirror ?? MIRROR_EXAMPLE, null, 2),
  });
  const set = (patch: Partial<Draft>) => setDraft((prev) => ({ ...prev, ...patch }));
  const [nameTouched, setNameTouched] = useState(editing);
  const [probe, setProbe] = useState<{ check: SaveResult["check"]; auth: SourceAuth } | null>(null);
  const [confirming, setConfirming] = useState(startDeleting);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!integration) return;
    get<{ url: string }>(`/admin/integrations/${integration.name}/address`)
      .then((got) => set({ url: got.url }))
      .catch((e) => setError((e as Error).message));
  }, [integration]);

  async function runProbe(url: string) {
    if (editing || !isFullUrl(url)) return;
    try {
      const found = await post<{ check: SaveResult["check"]; auth: SourceAuth }>(
        "/admin/integrations/probe", { url: url.trim() });
      setProbe(found);
      set({ auth: found.auth });
    } catch (e) {
      setProbe({ check: { ok: false, detail: (e as Error).message }, auth: draft.auth });
    }
  }

  let mirrorConfig: TaskMirrorConfig | null = null;
  let mirrorError: string | null = null;
  if (draft.mirror) {
    try {
      mirrorConfig = JSON.parse(draft.mirrorJson) as TaskMirrorConfig;
    } catch {
      mirrorError = "Not valid JSON yet.";
    }
  }
  const name = draft.name.replace(/-+$/, "");
  const clash = !editing && taken.includes(name);
  const ready = name.length >= 2 && isFullUrl(draft.url) && !clash && !mirrorError;

  async function save() {
    setBusy(true);
    setError(null);
    const tab = draft.auth === "oauth" ? window.open("", "_blank") : null;
    const oauth = draft.auth === "oauth";
    try {
      const result = await post<SaveResult>("/admin/integrations", {
        name,
        url: draft.url.trim(),
        description: draft.description,
        covers: splitList(draft.covers),
        auth: draft.auth,
        // Blank keeps a stored token on an edit; on an add there is none to keep.
        bearer: draft.auth === "bearer" ? draft.bearer.trim() || undefined
          : draft.auth === "none" && integration?.auth === "bearer" ? "" : undefined,
        access: draft.access,
        tools: draft.restrict ? splitList(draft.tools) : undefined,
        client_id: oauth ? draft.clientId.trim() || undefined : undefined,
        client_secret: oauth ? draft.clientSecret.trim() || undefined : undefined,
        scope: oauth ? draft.scope.trim() || undefined : undefined,
      });
      if (draft.mirror || integration?.task_mirror) {
        await put(`/admin/integrations/${name}/task-mirror`, { task_mirror: draft.mirror ? mirrorConfig : null });
      }
      if (result.needs_connect) {
        await startSignIn(result.name, tab);
        onSaved(`Saved '${result.name}'. Finish the provider's sign-in in the tab that opened.`, result.name);
      } else {
        tab?.close();
        onSaved(`Saved '${result.name}' — ${result.check.ok ? "" : "but "}${result.check.detail}.`);
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
      await del(`/admin/integrations/${integration!.name}`);
      onSaved(`Removed '${integration!.name}'.`);
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }

  const using = integration?.used_by ?? [];
  return (
    <Drawer
      title={editing ? <code>{integration!.name}</code> : "Add an integration"}
      sub={editing ? <>{integration!.url_host}:{integration!.url_port}</> : "An MCP server every project can choose to use."}
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
          {busy ? "Saving…" : editing ? "Save changes" : draft.auth === "oauth" ? "Add and sign in" : "Add and test"}
        </button>
      </>}
    >
      {error && <div className="error" style={{ margin: 0 }}>{error}</div>}
      {confirming && integration && (
        <div className="confirmbox" role="alert">
          <b>Remove {integration.name}?</b>
          <span>
            {using.length
              ? `${listJoin(using.map((p) => p.name))} still ${using.length === 1 ? "uses" : "use"} it. Each project stops using it in its own Data sources page first, so its chairman sees it go.`
              : "No project uses it. Its address and sign-in are deleted."}
          </span>
          <div className="actions" style={{ marginTop: 0 }}>
            <button className="danger" disabled={busy || using.length > 0} onClick={remove}>Remove integration</button>
            <button disabled={busy} onClick={() => setConfirming(false)}>Keep it</button>
          </div>
        </div>
      )}
      <div className="tswitch-field">
        <label className="lbl" htmlFor="intg-url">Address</label>
        <input id="intg-url" className="mono" value={draft.url} autoFocus={!editing}
               placeholder="https://mcp.hubspot.com/mcp"
               onChange={(e) => {
                 const url = e.target.value;
                 set({ url, ...(nameTouched ? {} : { name: nameFromUrl(url) }) });
               }}
               onBlur={(e) => runProbe(e.target.value)} />
        {probe && (
          <span className="help">
            {probe.check.ok ? `Found it — ${probe.check.detail}.`
              : probe.check.state === "needs_signin" ? "It asks you to sign in with OAuth — saving opens its sign-in."
              : probe.auth === "bearer" ? "It wants a credential: paste its token below."
              : probe.check.detail}
          </span>
        )}
      </div>
      {!editing && (
        <div className="tswitch-field">
          <label className="lbl" htmlFor="intg-name">Name</label>
          <input id="intg-name" className="mono" value={draft.name} placeholder="hubspot, notion, trello…"
                 onChange={(e) => { setNameTouched(true); set({ name: slugifyName(e.target.value) }); }} />
          <span className={clash ? "error" : "help"} style={clash ? { margin: 0 } : undefined}>
            {clash ? `There is already an integration called '${name}'.`
              : name ? <>Agents call its tools as <code>mcp__{name}__*</code>.</>
              : "Lowercase letters, digits and dashes."}
          </span>
        </div>
      )}
      <div className="tswitch-field">
        <label className="lbl" htmlFor="intg-desc">What it is</label>
        <input id="intg-desc" value={draft.description} placeholder="The company CRM: contacts, deals and email campaigns."
               onChange={(e) => set({ description: e.target.value })} />
        <label className="lbl" htmlFor="intg-covers" style={{ marginTop: 8 }}>What it is for</label>
        <input id="intg-covers" value={draft.covers} placeholder="crm, email marketing"
               onChange={(e) => set({ covers: e.target.value })} />
        <span className="help">
          A few words, comma-separated. The Founder asks about this integration when a new
          project&apos;s goal touches one of them, and agents read them before building the same thing.
        </span>
      </div>
      <div className="tswitch-field">
        <AuthPicker value={draft.auth} onChange={(auth) => set({ auth })}
                    oauthNote={<>Saving opens the provider&apos;s sign-in. One sign-in serves every project that uses it, and its tokens are refreshed automatically.</>} />
        {draft.auth === "bearer" && (
          <SecretInput value={draft.bearer} onChange={(bearer) => set({ bearer })}
                       placeholder={editing && integration?.auth === "bearer" ? "Blank keeps the current token" : "Paste the token"} />
        )}
        {draft.auth === "oauth" && (
          <OAuthClientFields clientId={draft.clientId} clientSecret={draft.clientSecret} scope={draft.scope}
                             onClientId={(clientId) => set({ clientId })}
                             onClientSecret={(clientSecret) => set({ clientSecret })}
                             onScope={(scope) => set({ scope })} />
        )}
      </div>
      <AccessPicker value={draft.access} onChange={(access) => set({ access })} />
      <div className="tswitch-field">
        <label className="help" style={{ display: "flex", gap: 6, alignItems: "center" }}>
          <input type="checkbox" style={{ width: "auto", minHeight: 0 }} checked={draft.restrict}
                 onChange={(e) => set({ restrict: e.target.checked })} />
          Only some of its tools may be called freely
        </label>
        {draft.restrict && (
          <>
            <input className="mono" value={draft.tools} placeholder="search_contacts, get_deal"
                   onChange={(e) => set({ tools: e.target.value })} />
            <span className="help">
              Every other tool — sending a campaign, deleting a contact — needs the chairman&apos;s
              approval for that one call, in every project.
            </span>
          </>
        )}
      </div>
      <div className="tswitch-field">
        <label className="help" style={{ display: "flex", gap: 6, alignItems: "center" }}>
          <input type="checkbox" style={{ width: "auto", minHeight: 0 }} checked={draft.mirror}
                 onChange={(e) => set({ mirror: e.target.checked })} />
          It is a task manager a project may mirror its task board into
        </label>
        {draft.mirror && (
          <>
            <textarea className="mono" rows={9} value={draft.mirrorJson}
                      onChange={(e) => set({ mirrorJson: e.target.value })} />
            <span className={mirrorError ? "error" : "help"} style={mirrorError ? { margin: 0 } : undefined}>
              {mirrorError ?? <>Which tool makes a card and which updates it, and their arguments.
                <code>{"{title}"}</code>, <code>{"{description}"}</code>, <code>{"{status}"}</code>,{" "}
                <code>{"{url}"}</code> and <code>{"{external_id}"}</code> are filled in per task;{" "}
                <code>id_path</code> is where the new card&apos;s id is in the create result.
                yaaf&apos;s board stays the record — this copies it out.</>}
            </span>
          </>
        )}
      </div>
    </Drawer>
  );
}

// ── a project's integrations (its Data sources page) ────────────────────────────

export function ProjectIntegrationsBlock({ projectId, org, notice }: {
  projectId: string;
  org: OrgView | null;
  notice: (message: string) => void;
}) {
  const [view, setView] = useState<ProjectIntegrations | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [choosing, setChoosing] = useState<Integration | IntegrationDecision | null>(null);
  const options = grantOptions(org);

  const reload = useCallback(() => {
    get<ProjectIntegrations>(`/admin/projects/${projectId}/integrations`)
      .then(setView).catch((e) => setError(e.message));
  }, [projectId]);
  useEffect(reload, [reload]);

  async function decline(name: string) {
    const reason = window.prompt(`Why doesn't this project use ${name}? (optional)`) ?? undefined;
    try {
      await put(`/admin/projects/${projectId}/integrations/${name}`, { decision: "decline", reason });
      notice(`Declined '${name}' for this project.`);
      reload();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function forget(name: string) {
    try {
      await del(`/admin/projects/${projectId}/integrations/${name}`);
      notice(`'${name}' is undecided again.`);
      reload();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  if (view === null) return error ? <div className="error">{error}</div> : null;
  const total = view.used.length + view.declined.length + view.undecided.length;
  if (total === 0) return null;
  return (
    <>
      <h3 style={{ marginTop: 28 }}>Shared integrations</h3>
      <p className="help" style={{ marginTop: 0 }}>
        Connected once by an administrator for every project. Using one gives the teams you
        choose its tools under the administrator&apos;s sign-in and allowed tools; declining one
        tells the Founder and your agents not to raise it again.
      </p>
      {error && <div className="error">{error}</div>}
      <div className="vlist">
        {view.used.map((row) => {
          const h = integrationHealth(row);
          return (
            <div className="srow" key={row.id}>
              <div className={`sstate ${h.tone}`}>
                <span className="sdot" />
                <span>{h.label}{h.when && <small>{h.when}</small>}</span>
              </div>
              <div className="sname">
                <code className="vnm">{row.name}</code>
                <span className="help">{row.description}</span>
                <span className="gchips" style={{ marginTop: 6 }}>
                  {row.covers.map((c) => <span className="gchip" key={c}>{c}</span>)}
                  {row.mirror_tasks && <span className="gchip"><Icon name="check" size={12} /> task board mirrored here</span>}
                </span>
              </div>
              <div className="swho">
                <span className="vsub">Who gets its tools</span>
                <GrantChips options={options} selected={row.roles} />
              </div>
              <div className="sacts">
                <RowMenu label={`More for ${row.name}`} items={[
                  { label: "Change who gets it", icon: "edit", onClick: () => setChoosing(row) },
                  { label: "Stop using it", icon: "trash", danger: true, onClick: () => decline(row.name) },
                ]} />
              </div>
              {h.tone !== "good" && (
                <div className={`swhy ${h.tone}`}>An administrator fixes this one in Settings → Integrations.</div>
              )}
            </div>
          );
        })}
        {view.undecided.map((row) => (
          <div className="srow" key={row.id}>
            <div className="sstate warn"><span className="sdot" /><span>Not decided</span></div>
            <div className="sname">
              <code className="vnm">{row.name}</code>
              <span className="help">{row.description}</span>
              <span className="gchips" style={{ marginTop: 6 }}>
                {row.covers.map((c) => <span className="gchip" key={c}>{c}</span>)}
              </span>
            </div>
            <div className="swho" />
            <div className="sacts">
              <button className="small primary" onClick={() => setChoosing(row)}>Use it</button>
              <button className="small" onClick={() => decline(row.name)}>Decline</button>
            </div>
          </div>
        ))}
        {view.declined.map((row) => (
          <div className="srow" key={row.id}>
            <div className="sstate"><span className="sdot" /><span>Declined</span></div>
            <div className="sname">
              <code className="vnm">{row.name}</code>
              <span className="help">{row.reason || row.description}</span>
            </div>
            <div className="swho" />
            <div className="sacts">
              <button className="small" onClick={() => setChoosing(row)}>Use it after all</button>
              <RowMenu label={`More for ${row.name}`} items={[
                { label: "Mark undecided", icon: "refresh", onClick: () => forget(row.name) },
              ]} />
            </div>
          </div>
        ))}
      </div>
      {choosing && (
        <UseDrawer projectId={projectId} integration={choosing} options={options}
                   onClose={() => setChoosing(null)}
                   onSaved={(message) => { setChoosing(null); notice(message); reload(); }} />
      )}
    </>
  );
}

function UseDrawer({ projectId, integration, options, onClose, onSaved }: {
  projectId: string;
  integration: Integration | IntegrationDecision;
  options: ReturnType<typeof grantOptions>;
  onClose: () => void;
  onSaved: (message: string) => void;
}) {
  const prior = "decision" in integration && integration.decision === "use" ? integration : null;
  const [roles, setRoles] = useState<string[]>(prior?.roles ?? []);
  const [mirror, setMirror] = useState<boolean>(prior?.mirror_tasks ?? false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    setBusy(true);
    setError(null);
    try {
      await put(`/admin/projects/${projectId}/integrations/${integration.name}`, {
        decision: "use", roles, mirror_tasks: integration.task_mirror ? mirror : undefined,
      });
      onSaved(`'${integration.name}' is in use. It applies on each team's next run.`);
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }

  return (
    <Drawer
      title={<>Use <code>{integration.name}</code></>}
      sub={integration.description || `${integration.url_host}:${integration.url_port}`}
      onClose={onClose}
      footer={<>
        <span style={{ flex: 1 }} />
        <button disabled={busy} onClick={onClose}>Cancel</button>
        <button className="primary" disabled={busy || roles.length === 0} onClick={save}>
          {busy ? "Saving…" : "Use it"}
        </button>
      </>}
    >
      {error && <div className="error" style={{ margin: 0 }}>{error}</div>}
      <p className="help" style={{ marginTop: 0 }}>
        Its sign-in{integration.tools ? " and allowed tools" : ""} are the administrator&apos;s and
        apply as they are. {integration.tools
          ? `Freely callable: ${integration.tools.join(", ") || "nothing"}; anything else asks you first, one call at a time.`
          : ""}
      </p>
      <TeamSwitches options={options} selected={roles} onChange={setRoles}
                    label="Who gets its tools" what="its tools" credential={false}
                    emptyHint="No teams yet — add one on the Team page first." />
      {integration.task_mirror && (
        <label className="help" style={{ display: "flex", gap: 6, alignItems: "center" }}>
          <input type="checkbox" style={{ width: "auto", minHeight: 0 }} checked={mirror}
                 onChange={(e) => setMirror(e.target.checked)} />
          Mirror this project&apos;s task board into {integration.name}. yaaf&apos;s board stays the
          record; the cards follow it.
        </label>
      )}
    </Drawer>
  );
}

// ── founding: the New Project form ──────────────────────────────────────────────

/** One integration as a new project decides about it. `use: null` is
    undecided — left out of what is posted, so the project can raise it later. */
export interface FoundingChoice {
  use: boolean | null;
  roles: string[];
  mirror: boolean;
  reason?: string | null;
}

export type FoundingChoices = Record<string, FoundingChoice>;

export function choicesFromBundle(
  decisions: { name: string; use: boolean; roles: string[]; reason?: string | null; mirror_tasks?: boolean }[] | undefined,
): FoundingChoices {
  return Object.fromEntries((decisions ?? []).map((d) => [d.name, {
    use: d.use, roles: d.roles ?? [], mirror: !!d.mirror_tasks, reason: d.reason ?? null,
  }]));
}

export function choicesBody(choices: FoundingChoices) {
  return Object.entries(choices)
    .filter(([, c]) => c.use !== null)
    .map(([name, c]) => ({
      name, use: !!c.use, roles: c.use ? c.roles : [], mirror_tasks: !!c.use && c.mirror,
      reason: c.reason || undefined,
    }));
}

/** Which of an integration's purposes the goal already talks about. Loose on
    purpose: it only decides which cards open asking, never what is posted. */
export function overlaps(goal: string, integration: Integration): string[] {
  const text = goal.toLowerCase();
  return integration.covers.filter((c) => c && text.includes(c.toLowerCase()));
}

export function FoundingIntegrations({ goal, roleOptions, value, onChange }: {
  goal: string;
  roleOptions: { slug: string; teams: string[]; people?: string[] }[];
  value: FoundingChoices;
  onChange: (next: FoundingChoices) => void;
}) {
  const [catalog, setCatalog] = useState<Integration[] | null>(null);
  useEffect(() => {
    get<Integration[]>("/admin/integrations").then(setCatalog).catch(() => setCatalog([]));
  }, []);
  if (!catalog || catalog.length === 0) return null;

  const set = (name: string, patch: Partial<FoundingChoice>) => {
    const prior = value[name] ?? { use: null, roles: [], mirror: false };
    onChange({ ...value, [name]: { ...prior, ...patch } });
  };
  const asking = catalog.filter((i) => overlaps(goal, i).length > 0 && value[i.name]?.use == null);
  const options = roleOptions.map((o) => ({ slug: o.slug, name: o.teams.join(" + "), people: o.people, hue: o.teams[0] }));

  return (
    <details className="opt-sec" open={asking.length > 0 || Object.keys(value).length > 0}>
      <summary><h2>Your company&apos;s integrations</h2>
        <span className="help">
          {asking.length
            ? `${listJoin(asking.map((i) => i.name))} ${asking.length === 1 ? "does" : "do"} part of what this goal describes`
            : "Tools an administrator connected for every project"}
        </span>
      </summary>
      <p className="help">
        Using one gives the teams you pick its tools under the administrator&apos;s sign-in, so
        this project uses what the company already pays for instead of building its own.
        Anything left undecided can be chosen later in Settings → Data sources.
      </p>
      {catalog.map((integration) => {
        const choice = value[integration.name] ?? { use: null, roles: [], mirror: false };
        const hits = overlaps(goal, integration);
        return (
          <div className="card" key={integration.id} style={{ marginBottom: 10 }}>
            <div style={{ display: "flex", gap: 8, alignItems: "baseline", flexWrap: "wrap" }}>
              <code className="vnm">{integration.name}</code>
              <span className="help">{integration.description}</span>
            </div>
            <span className="gchips" style={{ margin: "6px 0" }}>
              {integration.covers.map((c) => (
                <span className={`gchip${hits.includes(c) ? " on" : ""}`} key={c}>{c}</span>
              ))}
            </span>
            {hits.length > 0 && choice.use == null && (
              <p className="help" style={{ margin: "4px 0" }}>
                Your goal mentions {listJoin(hits)}. Use {integration.name} rather than have the
                project build its own?
              </p>
            )}
            <div className="radio-cards" role="radiogroup" aria-label={`Use ${integration.name}?`}>
              {([[true, "Use it"], [false, "Not for this project"], [null, "Decide later"]] as const).map(([v, label]) => (
                <button type="button" role="radio" key={String(v)} aria-checked={choice.use === v}
                        className={choice.use === v ? "on" : ""} onClick={() => set(integration.name, { use: v })}>
                  <b>{label}</b>
                </button>
              ))}
            </div>
            {choice.use === true && (
              <div style={{ marginTop: 10 }}>
                <TeamSwitches options={options} selected={choice.roles}
                              onChange={(roles) => set(integration.name, { roles })}
                              label="Who gets its tools" what="its tools" credential={false}
                              emptyHint="Pick your teams' roles above first." />
                {integration.task_mirror && (
                  <label className="help" style={{ display: "flex", gap: 6, alignItems: "center" }}>
                    <input type="checkbox" style={{ width: "auto", minHeight: 0 }} checked={choice.mirror}
                           onChange={(e) => set(integration.name, { mirror: e.target.checked })} />
                    Mirror the task board into {integration.name} — yaaf&apos;s board stays the record
                  </label>
                )}
              </div>
            )}
            {choice.use === false && (
              <input style={{ marginTop: 10 }} value={choice.reason ?? ""} placeholder="Why not (optional) — e.g. this one uses Mailchimp"
                     onChange={(e) => set(integration.name, { reason: e.target.value })} />
            )}
          </div>
        );
      })}
    </details>
  );
}
