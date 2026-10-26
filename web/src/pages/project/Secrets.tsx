import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  ApprovalRow,
  Dashboard,
  del,
  get,
  IntegrationDecision,
  McpServerEntry,
  OrgView,
  post,
  ProjectIntegrations,
  put,
  SecretHistoryRow,
  SecretRow,
} from "../../api";
import { envVarName } from "../../datasource";
import { GrantChips, GrantOption, TeamDot } from "../../grants";
import { fmtWhen } from "../../hooks";
import { useProject } from "../../projectCtx";
import { grantOptions, grantRoles, RoleGrants } from "../../roles";
import { clip, CredentialValue, Drawer, RowMenu, SecretInput } from "../../ui";
import { Icon, type IconName } from "../../ui/Icon";

/** Secrets & variables — what agents receive as environment variables.

    It replaced a page that put an empty add form above the inventory, drew
    every team as a card inside every credential row (seven credentials made a
    5,171px page), saved a grant the instant its box was ticked, and masked a
    timezone exactly like a refresh token. Now: one line per variable, every
    edit in a drawer with one Save, an access map that answers "what does this
    team get?" on one screen, and plain values shown as what they are. */

type VarKind = "secret" | "plain" | "file";

const KIND: Record<VarKind, { label: string; icon: IconName; help: string }> = {
  secret: { label: "Secret", icon: "key", help: "Hidden until you reveal it" },
  plain: { label: "Plain value", icon: "type", help: "Shown as is. For IDs and settings" },
  file: { label: "File", icon: "file", help: "Agents get a path to it" },
};

const kindOf = (s: SecretRow): VarKind =>
  s.filename ? "file" : s.visibility === "plain" ? "plain" : "secret";

/** The secrets something else owns: a data source's address and tokens, the
    Slack bridge's tokens. They are listed, locked, and edited where they live —
    hiding them made "where is MCP_PPC_TOKEN?" a question with no answer on
    screen, and a delete here would quietly break the thing that owns them. */
export function managedOwners(dash: Dashboard): Map<string, string> {
  const owners = new Map<string, string>();
  const sources = (dash.integrations["mcp_servers"] as McpServerEntry[] | undefined) ?? [];
  for (const source of sources) {
    const oauth = (source.oauth ?? {}) as Record<string, unknown>;
    for (const name of [source.url_secret, source.bearer_secret,
                        oauth.refresh_secret as string | undefined,
                        oauth.client_secret_secret as string | undefined])
      if (name) owners.set(name, source.name);
  }
  const slack = (dash.integrations["slack"] ?? {}) as Record<string, unknown>;
  for (const key of ["bot_token_secret", "signing_secret", "app_token_secret"]) {
    const name = slack[key];
    if (typeof name === "string" && name) owners.set(name, "Slack");
  }
  return owners;
}

function fmtDay(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  const sameYear = d.getFullYear() === new Date().getFullYear();
  return d.toLocaleDateString(undefined, sameYear
    ? { month: "short", day: "numeric" } : { month: "short", day: "numeric", year: "numeric" });
}

function fmtBytes(n: number | undefined): string {
  if (n === undefined) return "";
  if (n < 1024) return `${n} B`;
  return `${(n / 1024).toFixed(n < 10240 ? 1 : 0)} KB`;
}

function valuePreview(s: SecretRow) {
  const kind = kindOf(s);
  if (kind === "file")
    return <>{s.filename}{s.size_bytes !== undefined && <span className="dim"> · {fmtBytes(s.size_bytes)}</span>}</>;
  if (kind === "plain" && s.value !== undefined) return <>{s.value}</>;
  return <><span className="dots">••••••••</span>{s.hint ?? ""}</>;
}

function KindIcon({ kind, locked }: { kind: VarKind; locked?: boolean }) {
  return (
    <span className={`vkind ${locked ? "locked" : kind}`} title={locked ? "Managed" : KIND[kind].label}>
      <Icon name={locked ? "lock" : KIND[kind].icon} size={14} />
    </span>
  );
}

/** A credential need an agent filed and nobody has answered yet. A data write
    is a tool_request too, but what it needs is a yes, and it carries a grant. */
export function pendingNeeds(approvals: ApprovalRow[]): ApprovalRow[] {
  return approvals.filter((a) => a.kind === "tool_request" && !a.grant && a.status === "pending");
}

type Drawn =
  | { kind: "edit"; id: string; confirmDelete?: boolean }
  | { kind: "add"; tab: "one" | "paste"; need?: ApprovalRow };

export default function Secrets({
  projectId, dash, org, secrets, refreshSecrets, notice,
}: {
  projectId: string;
  dash: Dashboard;
  org: OrgView | null;
  secrets: SecretRow[];
  refreshSecrets: () => void;
  /** Says what just happened, at the top of the page, and reloads the project. */
  notice: (message: string) => void;
}) {
  const { approvals, reloadApprovals } = useProject();
  const [query, setQuery] = useState("");
  const [team, setTeam] = useState("");
  const [view, setView] = useState<"list" | "map">("list");
  const [showManaged, setShowManaged] = useState(false);
  const [drawn, setDrawn] = useState<Drawn | null>(null);

  const owners = managedOwners(dash);
  const options = grantOptions(org);
  const service = secrets.filter((s) => s.kind !== "billing");
  const own = service.filter((s) => !owners.has(s.name));
  const managed = service.filter((s) => owners.has(s.name));
  const needs = pendingNeeds(approvals);

  const q = query.trim().toLowerCase();
  const matches = (s: SecretRow) =>
    (!q || s.name.toLowerCase().includes(q) || envVarName(s.name).toLowerCase().includes(q))
    && (!team || s.role_scopes.includes(team));
  const shown = own.filter(matches);

  const close = () => setDrawn(null);
  const saved = (message: string) => {
    refreshSecrets();
    reloadApprovals();
    notice(message);
    setDrawn(null);
  };

  return (
    <>
      <div className="pagehead-row">
        <div>
          <h2 style={{ marginTop: 0 }}>Secrets &amp; variables</h2>
          <p className="subtitle" style={{ marginBottom: 0 }}>
            Values your agents receive as environment variables. Agents see the name, never the
            value. Changes reach a team on its next run.
          </p>
        </div>
        <div className="actions" style={{ marginTop: 0 }}>
          <button onClick={() => setDrawn({ kind: "add", tab: "paste" })}>
            <Icon name="paste" size={15} /> Paste .env
          </button>
          <button className="primary" onClick={() => setDrawn({ kind: "add", tab: "one" })}>
            <Icon name="plus" size={15} /> Add variable
          </button>
        </div>
      </div>

      {needs.map((need) => {
        const asker = grantRoles(org, need.requested_by).find((r) => r.asked);
        const who = asker ? org?.teams.flatMap((t) => t.agents)
          .find((a) => a.id === need.requested_by)?.name ?? asker.teams[0] : "An agent";
        return (
          <div className="needbar" key={need.id} role="status">
            <Icon name="user" size={18} />
            <div className="needbar-text">
              <b>{who}{asker ? ` (${asker.teams.join(" + ")})` : ""} asked for a credential:</b>{" "}
              {need.title}
              {need.body && <div className="help">{clip(need.body, 180)}</div>}
            </div>
            <button className="primary small"
                    onClick={() => setDrawn({ kind: "add", tab: "one", need })}>
              Add it
            </button>
            <Link to="/inbox" className="btnlink small">Open in Inbox</Link>
          </div>
        );
      })}

      <div className="listbar">
        <label className="search-field">
          <Icon name="search" size={14} />
          <input type="search" placeholder="Find a variable" value={query}
                 aria-label="Find a variable" onChange={(e) => setQuery(e.target.value)} />
        </label>
        <select value={team} onChange={(e) => setTeam(e.target.value)}
                aria-label="Show what one team gets" className="listbar-select">
          <option value="">Every team</option>
          {options.map((o) => <option key={o.slug} value={o.slug}>What {o.name} gets</option>)}
        </select>
        <div className="seg" role="tablist" aria-label="View">
          <button role="tab" aria-selected={view === "list"} className={view === "list" ? "on" : ""}
                  onClick={() => setView("list")}>
            <Icon name="list" size={14} /> List
          </button>
          <button role="tab" aria-selected={view === "map"} className={view === "map" ? "on" : ""}
                  onClick={() => setView("map")}>
            <Icon name="grid" size={14} /> Access map
          </button>
        </div>
      </div>

      {view === "map" ? (
        <AccessMap projectId={projectId} dash={dash} options={options}
                   rows={shown} onSaved={saved} />
      ) : (
        <div className="vlist" role="table" aria-label="Variables">
          <div className="vrow vhead" role="row">
            <span role="columnheader">Name agents see</span>
            <span role="columnheader">Value</span>
            <span role="columnheader">Who gets it</span>
            <span role="columnheader" className="vupd">Updated</span>
            <span />
          </div>
          {own.length === 0 ? (
            <div className="vempty">
              <b>No variables yet.</b> Add the API keys, passwords and settings your agents need
              for outside services. Never paste one into the chat: messages become model context.
            </div>
          ) : shown.length === 0 ? (
            <div className="vempty">
              {team
                ? <>{options.find((o) => o.slug === team)?.name ?? team} gets nothing you added by hand.</>
                : <>No variable matches “{query}”.</>}{" "}
              <button className="linklike" onClick={() => { setQuery(""); setTeam(""); }}>
                Show everything
              </button>
            </div>
          ) : shown.map((s) => {
            const kind = kindOf(s);
            const open = () => setDrawn({ kind: "edit", id: s.id });
            return (
              <div key={s.id} role="row" tabIndex={0}
                   className={`vrow click${drawn?.kind === "edit" && drawn.id === s.id ? " sel" : ""}`}
                   onClick={open}
                   onKeyDown={(e) => {
                     // Only the row itself: Enter on its ⋯ button bubbles here too.
                     if (e.target === e.currentTarget && (e.key === "Enter" || e.key === " ")) {
                       e.preventDefault();
                       open();
                     }
                   }}>
                <span className="vname">
                  <KindIcon kind={kind} />
                  <span className="vname-text">
                    <code className="vnm">{envVarName(s.name)}</code>
                    <span className="vsub">{kind === "file" ? "File · agents get its path" : KIND[kind].label}</span>
                  </span>
                </span>
                <span className="vval">{valuePreview(s)}</span>
                <span><GrantChips options={options} selected={s.role_scopes} /></span>
                <span className="vupd">{fmtDay(s.updated_at)}</span>
                <RowMenu label={`More for ${envVarName(s.name)}`} items={[
                  { label: "Edit", icon: "edit", onClick: open },
                  { label: `Copy $${envVarName(s.name)}`, icon: "copy",
                    onClick: () => { navigator.clipboard?.writeText(`$${envVarName(s.name)}`).catch(() => {}); } },
                  { label: "Delete…", icon: "trash", danger: true,
                    onClick: () => setDrawn({ kind: "edit", id: s.id, confirmDelete: true }) },
                ]} />
              </div>
            );
          })}
          {managed.length > 0 && (
            <>
              <button className="vgroup" aria-expanded={showManaged}
                      onClick={() => setShowManaged(!showManaged)}>
                <Icon name={showManaged ? "down" : "right"} size={14} />
                <Icon name="lock" size={13} />
                <b>Set up by your data sources and Slack</b>
                <span className="dim">· {managed.length}</span>
                <span className="vgroup-note">Read-only here</span>
              </button>
              {showManaged && managed.filter(matches).map((s) => {
                const owner = owners.get(s.name)!;
                return (
                  <div key={s.id} role="row" className="vrow managed">
                    <span className="vname">
                      <KindIcon kind="secret" locked />
                      <span className="vname-text">
                        <code className="vnm">{envVarName(s.name)}</code>
                        <span className="vsub">{owner === "Slack" ? "Slack bridge" : `Belongs to ${owner}`}</span>
                      </span>
                    </span>
                    <span className="vval"><span className="dots">••••••••</span></span>
                    <span><GrantChips options={options} selected={s.role_scopes} /></span>
                    <span className="vupd">{fmtDay(s.updated_at)}</span>
                    <Link className="btnlink small" title="Edit where it lives"
                          to={`/projects/${projectId}/settings?s=${owner === "Slack" ? "integrations" : "sources"}`}>
                      <Icon name="arrow" size={14} />
                    </Link>
                  </div>
                );
              })}
            </>
          )}
        </div>
      )}
      <p className="help" style={{ marginTop: 10 }}>
        <Icon name="lock" size={12} /> The credential that pays for model calls is under{" "}
        <Link to={`/projects/${projectId}/settings?s=money`}>Money</Link>.
      </p>

      {drawn?.kind === "edit" && (() => {
        const row = secrets.find((s) => s.id === drawn.id);
        return row ? (
          <VarDrawer key={row.id} projectId={projectId} row={row} org={org}
                     startDeleting={!!drawn.confirmDelete} onClose={close} onSaved={saved} />
        ) : null;
      })()}
      {drawn?.kind === "add" && (
        <AddDrawer projectId={projectId} org={org} existing={service} owners={owners}
                   tab={drawn.tab} need={drawn.need} onClose={close} onSaved={saved} />
      )}
    </>
  );
}

function KindPicker({ value, onChange, fileLocked }: {
  value: VarKind;
  onChange: (next: VarKind) => void;
  /** A credential's shape is fixed at creation: a file stays a file, a string
      can never become one. Only secret ↔ plain moves after the fact. */
  fileLocked?: boolean;
}) {
  return (
    <div className="tswitch-field">
      <span className="lbl">Type</span>
      <div className="kindpick" role="radiogroup">
        {(Object.keys(KIND) as VarKind[]).map((k) => {
          const locked = fileLocked && (k === "file") !== (value === "file");
          return (
            <button key={k} type="button" role="radio" aria-checked={value === k}
                    className={value === k ? "on" : ""} disabled={locked}
                    title={locked ? "A credential's shape is fixed when it is stored" : undefined}
                    onClick={() => onChange(k)}>
              <b><Icon name={KIND[k].icon} size={13} /> {KIND[k].label}</b>
              <span>{KIND[k].help}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

function VarDrawer({ projectId, row, org, startDeleting, onClose, onSaved }: {
  projectId: string;
  row: SecretRow;
  org: OrgView | null;
  startDeleting: boolean;
  onClose: () => void;
  onSaved: (message: string) => void;
}) {
  const env = envVarName(row.name);
  const original = kindOf(row);
  const [kind, setKind] = useState<VarKind>(original);
  const [teams, setTeams] = useState<string[]>(row.role_scopes);
  const [revealed, setRevealed] = useState<string | null>(null);
  const [replacing, setReplacing] = useState(false);
  const [next, setNext] = useState({ value: "", filename: null as string | null });
  const [confirming, setConfirming] = useState(startDeleting);
  const [history, setHistory] = useState<SecretHistoryRow[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    get<SecretHistoryRow[]>(`/admin/secrets/${row.id}/history`).then(setHistory).catch(() => setHistory([]));
  }, [row.id, row.updated_at]);

  const sameTeams = [...teams].sort().join() === [...row.role_scopes].sort().join();
  const dirty = !sameTeams || kind !== original || (replacing && !!next.value);

  async function reveal() {
    setError(null);
    try {
      const got = await get<{ value: string | null; binary?: boolean; size_bytes?: number }>(
        `/admin/secrets/${row.id}/value`);
      setRevealed(got.binary ? `${(got.size_bytes ?? 0).toLocaleString()} bytes of binary — nothing readable to show`
        : got.value ?? "");
      get<SecretHistoryRow[]>(`/admin/secrets/${row.id}/history`).then(setHistory).catch(() => {});
    } catch (e) {
      setError((e as Error).message);
    }
  }

  function copy(text: string) {
    navigator.clipboard?.writeText(text).then(
      () => { setCopied(true); window.setTimeout(() => setCopied(false), 1200); },
      () => setError("the browser refused clipboard access — select the text and copy it by hand"),
    );
  }

  async function save() {
    setBusy(true);
    setError(null);
    try {
      await post("/admin/secrets", {
        project_id: projectId,
        name: row.name,
        role_scopes: teams,
        ...(kind !== original && kind !== "file" ? { visibility: kind } : {}),
        ...(replacing && next.value ? { value: next.value } : {}),
        ...(replacing && next.value && row.filename ? { filename: next.filename ?? row.filename } : {}),
      });
      onSaved(`Saved $${env}. Teams get it on their next run.`);
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }

  async function remove() {
    setBusy(true);
    setError(null);
    try {
      await del(`/admin/secrets/${row.id}`);
      onSaved(`Deleted $${env}. Teams that had it lose it on their next run.`);
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }

  const reached = grantRoles(org).filter((r) => row.role_scopes.includes(r.slug)).flatMap((r) => r.people);

  let valueUI;
  if (replacing) {
    valueUI = row.filename ? (
      <CredentialValue value={next.value} filename={next.filename} fileOnly
                       onChange={setNext} />
    ) : kind === "plain" ? (
      <input autoFocus value={next.value} placeholder="The new value"
             onChange={(e) => setNext({ value: e.target.value, filename: null })} />
    ) : (
      <SecretInput autoFocus value={next.value} placeholder="Paste the new value"
                   onChange={(value) => setNext({ value, filename: null })} />
    );
  } else if (original === "file") {
    valueUI = revealed !== null ? (
      <textarea className="mono" rows={6} readOnly value={revealed} />
    ) : (
      <div className="valbox">
        <span className="valbox-v"><Icon name="file" size={13} /> {row.filename}
          {row.size_bytes !== undefined && <span className="dim"> · {fmtBytes(row.size_bytes)}</span>}</span>
        <button className="small" onClick={reveal}><Icon name="eye" size={13} /> Reveal</button>
      </div>
    );
  } else {
    const shownValue = original === "plain" && row.value !== undefined ? row.value : revealed;
    valueUI = (
      <div className="valbox">
        <span className="valbox-v mono">
          {shownValue !== null ? shownValue : <><span className="dots">••••••••••••</span>{row.hint ?? ""}</>}
        </span>
        {shownValue === null
          ? <button className="small" onClick={reveal}><Icon name="eye" size={13} /> Reveal</button>
          : <button className="small" onClick={() => copy(shownValue)}>
              <Icon name={copied ? "check" : "copy"} size={13} /> {copied ? "Copied" : "Copy"}
            </button>}
        {revealed !== null && original === "secret" && (
          <button className="small" onClick={() => setRevealed(null)}>Hide</button>
        )}
      </div>
    );
  }

  return (
    <Drawer
      title={<code>{env}</code>}
      sub={<>Stored as {row.name}{row.created_at && <> · added {fmtDay(row.created_at)}</>}</>}
      icon={<KindIcon kind={original} />}
      onClose={onClose}
      footer={<>
        <button className="danger-text" disabled={busy} onClick={() => setConfirming(true)}>
          <Icon name="trash" size={14} /> Delete
        </button>
        <span style={{ flex: 1 }} />
        <button disabled={busy} onClick={onClose}>Cancel</button>
        <button className="primary" disabled={busy || !dirty} onClick={save}>
          {busy ? "Saving…" : "Save changes"}
        </button>
      </>}
    >
      {error && <div className="error" style={{ margin: 0 }}>{error}</div>}
      {confirming && (
        <div className="confirmbox" role="alert">
          <b>Delete ${env}?</b>
          <span>
            {reached.length ? `${reached.join(", ")} ${reached.length === 1 ? "loses" : "lose"} it`
              : "Nobody receives it, so nobody loses it"} on the next run. This can't be undone.
          </span>
          <div className="actions" style={{ marginTop: 0 }}>
            <button className="danger" disabled={busy} onClick={remove}>Delete variable</button>
            <button disabled={busy} onClick={() => setConfirming(false)}>Keep it</button>
          </div>
        </div>
      )}
      <KindPicker value={kind} onChange={setKind} fileLocked />
      <div className="tswitch-field">
        <div className="tswitch-head">
          <span className="lbl">Value</span>
          {!replacing
            ? <button className="linklike" onClick={() => { setReplacing(true); setRevealed(null); }}>
                {row.filename ? "Replace file" : "Replace value"}
              </button>
            : <button className="linklike" onClick={() => { setReplacing(false); setNext({ value: "", filename: null }); }}>
                Keep the current one
              </button>}
        </div>
        {valueUI}
        {!replacing && original === "secret" && (
          <span className="help"><Icon name="lock" size={11} /> Each reveal is logged to the history below.</span>
        )}
        {original === "file" && (
          <span className="help">Agents get <code>${env}</code> holding the path to <code>{row.filename}</code>, never its contents.</span>
        )}
      </div>
      <RoleGrants org={org} selected={teams} onChange={setTeams} secretName={row.name} />
      <div className="tswitch-field">
        <span className="lbl">History</span>
        {history === null ? <span className="help">Loading…</span>
          : history.length === 0 ? <span className="help">Nothing recorded yet.</span>
          : (
            <ul className="hist">
              {history.slice(0, 12).map((h, i) => (
                <li key={i}>
                  <time dateTime={h.at} title={fmtWhen(h.at)}>{fmtDay(h.at)}</time>
                  <span><b>{h.by}</b> · {h.what}{h.reason && <span className="dim"> — “{h.reason}”</span>}</span>
                </li>
              ))}
            </ul>
          )}
      </div>
    </Drawer>
  );
}

interface Parsed { key: string; value: string; kind: "secret" | "plain"; replaces: boolean; owned: boolean }

/** A pasted .env, read the forgiving way: comments and blanks skipped, an
    `export ` prefix dropped, one pair of surrounding quotes taken off. Names
    that sound like credentials start as secrets; the reader can disagree. */
export function parseDotenv(text: string, existing: SecretRow[], owners: Map<string, string>): Parsed[] {
  const seen = new Set<string>();
  const out: Parsed[] = [];
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith("#") || !line.includes("=")) continue;
    const at = line.indexOf("=");
    const key = line.slice(0, at).replace(/^export\s+/, "").trim();
    let value = line.slice(at + 1).trim();
    if (value.length >= 2 && (value[0] === '"' || value[0] === "'") && value.endsWith(value[0]))
      value = value.slice(1, -1);
    if (!key || !value || seen.has(envVarName(key))) continue;
    seen.add(envVarName(key));
    const match = existing.find((s) => envVarName(s.name) === envVarName(key));
    out.push({
      key, value,
      kind: /KEY|TOKEN|SECRET|PASS|PWD|PRIVATE|AUTH|CREDENTIAL/i.test(key) ? "secret" : "plain",
      replaces: !!match,
      owned: !!match && owners.has(match.name),
    });
  }
  return out;
}

function AddDrawer({ projectId, org, existing, owners, tab: startTab, need, onClose, onSaved }: {
  projectId: string;
  org: OrgView | null;
  existing: SecretRow[];
  owners: Map<string, string>;
  tab: "one" | "paste";
  need?: ApprovalRow;
  onClose: () => void;
  onSaved: (message: string) => void;
}) {
  const [tab, setTab] = useState<"one" | "paste">(need ? "one" : startTab);
  const [name, setName] = useState("");
  const [kind, setKind] = useState<VarKind>("secret");
  const [value, setValue] = useState({ value: "", filename: null as string | null });
  const [teams, setTeams] = useState<string[]>([]);
  const [pasted, setPasted] = useState("");
  const [kinds, setKinds] = useState<Record<string, "secret" | "plain">>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // The team whose agent asked is the answer nine times out of ten — proposed,
  // never assumed: it is switched on, and the chairman can switch it off.
  useEffect(() => {
    if (!need || !org) return;
    const asked = grantRoles(org, need.requested_by).find((r) => r.asked);
    if (asked) setTeams((prev) => (prev.length ? prev : [asked.slug]));
  }, [need, org]);

  const rows = useMemo(() => parseDotenv(pasted, existing, owners), [pasted, existing, owners]);
  const kindFor = (r: Parsed) => kinds[r.key] ?? r.kind;
  const taken = existing.find((s) => envVarName(s.name) === envVarName(name.trim()));

  async function addOne() {
    setBusy(true);
    setError(null);
    const body = name.trim();
    try {
      if (need) {
        await post(`/admin/approvals/${need.id}/fulfill`, {
          secret_name: body, value: value.value, roles: teams,
          ...(value.filename === null ? {} : { filename: value.filename }),
        });
        if (kind === "plain")
          await post("/admin/secrets", { project_id: projectId, name: body, visibility: "plain" });
        onSaved(`Stored $${envVarName(body)} and answered the request. The CEO hears it was fulfilled.`);
      } else {
        await post("/admin/secrets", {
          project_id: projectId, name: body, value: value.value, role_scopes: teams,
          kind: "service", visibility: kind === "plain" ? "plain" : "secret",
          ...(value.filename === null ? {} : { filename: value.filename }),
        });
        onSaved(`Added $${envVarName(body)}.${teams.length ? " Granted teams get it on their next run." : " Nobody gets it until you switch a team on."}`);
      }
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }

  async function addMany() {
    setBusy(true);
    setError(null);
    const failed: string[] = [];
    let added = 0;
    for (const r of rows) {
      if (r.owned) continue;
      try {
        await post("/admin/secrets", {
          project_id: projectId, name: r.key, value: r.value, kind: "service",
          visibility: kindFor(r),
          // A name that exists keeps who has it; only new ones take the switches.
          ...(r.replaces ? {} : { role_scopes: teams }),
        });
        added++;
      } catch (e) {
        failed.push(`${r.key}: ${(e as Error).message}`);
      }
    }
    if (failed.length) {
      setError(`Added ${added}; ${failed.length} refused — ${failed.join(" · ")}`);
      setBusy(false);
      return;
    }
    onSaved(`Added ${added} variable${added === 1 ? "" : "s"}.`);
  }

  const valueReady = kind === "file" ? value.filename !== null && !!value.value : !!value.value;
  const asker = need ? org?.teams.flatMap((t) => t.agents).find((a) => a.id === need.requested_by)?.name : null;

  return (
    <Drawer
      title={need ? "Answer a request" : "Add variables"}
      sub={need ? <>{asker ?? "An agent"} asked: {need.title}</>
        : "Encrypted when stored. Agents get the name, never the value."}
      onClose={onClose}
      footer={<>
        <span style={{ flex: 1 }} />
        <button disabled={busy} onClick={onClose}>Cancel</button>
        {tab === "one" ? (
          <button className="primary"
                  disabled={busy || !name.trim() || !valueReady || (need && teams.length === 0)
                    || (!!taken && owners.has(taken.name))}
                  onClick={addOne}>
            {busy ? "Saving…" : need ? "Store it and answer" : taken ? "Replace value" : "Add variable"}
          </button>
        ) : (
          <button className="primary" disabled={busy || rows.filter((r) => !r.owned).length === 0}
                  onClick={addMany}>
            {busy ? "Saving…" : `Add ${rows.filter((r) => !r.owned).length} variable${rows.filter((r) => !r.owned).length === 1 ? "" : "s"}`}
          </button>
        )}
      </>}
    >
      {!need && (
        <div className="dtabs" role="tablist">
          <button role="tab" aria-selected={tab === "one"} className={tab === "one" ? "on" : ""}
                  onClick={() => setTab("one")}>One variable</button>
          <button role="tab" aria-selected={tab === "paste"} className={tab === "paste" ? "on" : ""}
                  onClick={() => setTab("paste")}>Paste .env</button>
        </div>
      )}
      {error && <div className="error" style={{ margin: 0 }}>{error}</div>}
      {tab === "one" ? (
        <>
          <div className="tswitch-field">
            <label className="lbl" htmlFor="var-name">Name</label>
            <input id="var-name" className="mono" autoFocus value={name} autoComplete="off"
                   placeholder="SEMRUSH_API_KEY"
                   onChange={(e) => setName(e.target.value)}
                   onPaste={(e) => {
                     // Several KEY=value lines pasted into the name box are a
                     // .env, not a name: take them to the tab that reads one.
                     const text = e.clipboardData.getData("text");
                     if (!need && /\n/.test(text.trim()) && text.includes("=")) {
                       e.preventDefault();
                       setPasted(text);
                       setTab("paste");
                     }
                   }} />
            <span className="help">
              {name.trim()
                ? taken
                  ? owners.has(taken.name)
                    ? <>That name belongs to {owners.get(taken.name)} — change it there.</>
                    : <>Already stored: saving replaces its value and keeps who has it.</>
                  : <>Agents will see <code>${envVarName(name)}</code>.</>
                : "Letters, digits, dashes or underscores. Paste several KEY=value lines and they split into rows."}
            </span>
          </div>
          <KindPicker value={kind} onChange={(k) => { setKind(k); setValue({ value: "", filename: null }); }} />
          <div className="tswitch-field">
            <span className="lbl">Value</span>
            {kind === "file" ? (
              <CredentialValue value={value.value} filename={value.filename} fileOnly onChange={setValue} />
            ) : kind === "plain" ? (
              <input value={value.value} placeholder="America/Toronto"
                     onChange={(e) => setValue({ value: e.target.value, filename: null })} />
            ) : (
              <SecretInput value={value.value} placeholder="The key itself"
                           onChange={(v) => setValue({ value: v, filename: null })} />
            )}
          </div>
          <RoleGrants org={org} selected={teams} onChange={setTeams}
                      askedByAgentId={need?.requested_by} secretName={name.trim() || undefined} />
        </>
      ) : (
        <>
          <div className="tswitch-field">
            <label className="lbl" htmlFor="dotenv">Paste from a .env file</label>
            <textarea id="dotenv" className="mono" rows={7} spellCheck={false} autoFocus
                      value={pasted} onChange={(e) => setPasted(e.target.value)}
                      placeholder={"SEMRUSH_API_KEY=…\nREPORT_TIMEZONE=America/New_York"} />
            <span className="help">Lines starting with # are skipped. Nothing is stored until you press Add.</span>
          </div>
          {rows.length > 0 && (
            <div className="tswitch-field">
              <span className="lbl">{rows.length} found</span>
              <div className="parsed">
                {rows.map((r) => (
                  <div className="envrow" key={r.key}>
                    <code className="envk">{envVarName(r.key)}</code>
                    <span className="envv">{kindFor(r) === "secret" ? `••••••${r.value.slice(-4)}` : r.value}</span>
                    {r.owned ? <span className="chip">skipped: managed</span>
                      : r.replaces ? <span className="chip warn">replaces</span> : <span className="chip">new</span>}
                    <select aria-label={`Type of ${r.key}`} value={kindFor(r)} disabled={r.owned}
                            onChange={(e) => setKinds({ ...kinds, [r.key]: e.target.value as "secret" | "plain" })}>
                      <option value="secret">Secret</option>
                      <option value="plain">Plain value</option>
                    </select>
                  </div>
                ))}
              </div>
              <span className="help">
                Names with KEY, TOKEN, SECRET or PASS in them start as secrets. A name that already
                exists gets the new value and keeps who has it.
              </span>
            </div>
          )}
          <RoleGrants org={org} selected={teams} onChange={setTeams}
                      secretName={rows.length === 1 ? rows[0].key : undefined} />
        </>
      )}
    </Drawer>
  );
}

/** Every grant on one screen: variables, data sources and the shared
    integrations this project uses as rows, teams as columns. The question it answers — "what does the Data team get?" — took a
    5,000px scroll on the old page. Changes stage (an amber ring) and save as
    one batch, because a grant that saves on a stray click hands a key to a
    team before anyone meant it to. */
function AccessMap({ projectId, dash, options, rows, onSaved }: {
  projectId: string;
  dash: Dashboard;
  options: GrantOption[];
  rows: SecretRow[];
  onSaved: (message: string) => void;
}) {
  const sources = (dash.integrations["mcp_servers"] as McpServerEntry[] | undefined) ?? [];
  // The shared integrations in use: a grant like any other, so a map that
  // says "every grant" has to show them.
  const [shared, setShared] = useState<IntegrationDecision[]>([]);
  useEffect(() => {
    get<ProjectIntegrations>(`/admin/projects/${projectId}/integrations`)
      .then((view) => setShared(view.used)).catch(() => setShared([]));
  }, [projectId]);
  const [pending, setPending] = useState<Record<string, true>>({});
  const [hot, setHot] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const key = (kind: "v" | "s" | "i", name: string, slug: string) => `${kind}\u0000${name}\u0000${slug}`;
  const base = (kind: "v" | "s" | "i", name: string): string[] =>
    kind === "v" ? rows.find((r) => r.name === name)?.role_scopes ?? []
      : kind === "i" ? shared.find((s) => s.name === name)?.roles ?? []
      : sources.find((s) => s.name === name)?.roles ?? [];
  const isOn = (kind: "v" | "s" | "i", name: string, slug: string) =>
    base(kind, name).includes(slug) !== !!pending[key(kind, name, slug)];
  const toggle = (k: string) => setPending((prev) => {
    const copy = { ...prev };
    if (copy[k]) delete copy[k]; else copy[k] = true;
    return copy;
  });
  const after = (kind: "v" | "s" | "i", name: string) =>
    [...base(kind, name).filter((slug) => !pending[key(kind, name, slug)]),
     ...options.map((o) => o.slug).filter((slug) => !base(kind, name).includes(slug) && pending[key(kind, name, slug)])];

  const changes = Object.keys(pending).map((k) => {
    const [kind, name, slug] = k.split("\u0000") as ["v" | "s" | "i", string, string];
    return { kind, name, slug, on: isOn(kind, name, slug) };
  });
  const teamName = (slug: string) => options.find((o) => o.slug === slug)?.name ?? slug;
  const sentence = changes.map((c) =>
    `${teamName(c.slug)} ${c.on ? "gets" : "loses"} ${c.kind === "v" ? envVarName(c.name) : `${c.name}'s tools`}`);
  const peopleOf = (slugs: string[]) =>
    options.filter((o) => slugs.includes(o.slug)).reduce((n, o) => n + (o.people?.length || 1), 0);

  async function save() {
    setBusy(true);
    setError(null);
    const touched = [...new Set(changes.map((c) => `${c.kind}\u0000${c.name}`))];
    const emptied = touched.filter((t) => !t.startsWith("v")
      && after(t[0] as "s" | "i", t.split("\u0000")[1]).length === 0);
    if (emptied.length) {
      setError(`A data source or integration needs at least one team — ${emptied.map((t) => t.split("\u0000")[1]).join(", ")} would have none. Remove or decline it on Data sources instead.`);
      setBusy(false);
      return;
    }
    try {
      for (const t of touched) {
        const [kind, name] = t.split("\u0000") as ["v" | "s" | "i", string];
        if (kind === "v")
          await post("/admin/secrets", { project_id: projectId, name, role_scopes: after("v", name) });
        else if (kind === "i")
          await put(`/admin/projects/${projectId}/integrations/${name}`, { decision: "use", roles: after("i", name) });
        else
          await post(`/admin/projects/${projectId}/mcp_servers/${name}/roles`, { roles: after("s", name) });
      }
      const n = changes.length;
      setPending({});
      get<ProjectIntegrations>(`/admin/projects/${projectId}/integrations`)
        .then((view) => setShared(view.used)).catch(() => {});
      onSaved(`Saved ${n} change${n === 1 ? "" : "s"}. They apply on each team's next run.`);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (options.length === 0)
    return <p className="help">No teams yet — there is nobody to give anything to.</p>;

  const cell = (kind: "v" | "s" | "i", name: string, slug: string, label: string) => {
    const k = key(kind, name, slug);
    const on = isOn(kind, name, slug);
    return (
      <td key={slug} className={hot === slug ? "hot" : ""}>
        <button className={`mcell${on ? " on" : ""}${pending[k] ? " changed" : ""}`}
                aria-pressed={on} aria-label={`${label} for ${teamName(slug)}`}
                onClick={() => toggle(k)}>
          <Icon name="check" size={14} />
        </button>
      </td>
    );
  };
  const total = (kind: "v" | "s" | "i", name: string) => {
    const n = peopleOf(options.map((o) => o.slug).filter((slug) => isOn(kind, name, slug)));
    return <td className="mtot">{n ? `${n} ${n === 1 ? "person" : "people"}` : <span className="warn-text">nobody</span>}</td>;
  };

  return (
    <>
      {error && <div className="error">{error}</div>}
      <div className="mapwrap">
        <table className="accessmap">
          <thead>
            <tr>
              <th className="mrow">Click a square to give or take away. Nothing saves until you press Save.</th>
              {options.map((o) => (
                <th key={o.slug} className={hot === o.slug ? "hot" : ""}>
                  <button className="mteam" onClick={() => setHot(hot === o.slug ? "" : o.slug)}
                          title="Highlight this column">
                    <b><TeamDot hue={o.hue ?? o.name} />{o.name}</b>
                    <small>{(o.people ?? []).join(", ")}</small>
                  </button>
                </th>
              ))}
              <th />
            </tr>
          </thead>
          <tbody>
            <tr className="msect"><th colSpan={options.length + 2}>Your variables</th></tr>
            {rows.map((r) => (
              <tr key={r.id}>
                <th className="mrow"><span className="vname"><KindIcon kind={kindOf(r)} />
                  <code className="vnm">{envVarName(r.name)}</code></span></th>
                {options.map((o) => cell("v", r.name, o.slug, envVarName(r.name)))}
                {total("v", r.name)}
              </tr>
            ))}
            {sources.length > 0 && (
              <tr className="msect"><th colSpan={options.length + 2}>Data sources — their address and token travel together</th></tr>
            )}
            {sources.map((s) => (
              <tr key={s.name}>
                <th className="mrow"><span className="vname"><span className="vkind"><Icon name="plug" size={13} /></span>
                  <code className="vnm">{s.name}</code><span className="dim small">tools</span></span></th>
                {options.map((o) => cell("s", s.name, o.slug, `${s.name}'s tools`))}
                {total("s", s.name)}
              </tr>
            ))}
            {shared.length > 0 && (
              <tr className="msect"><th colSpan={options.length + 2}>Shared integrations — the administrator&apos;s sign-in, your choice of teams</th></tr>
            )}
            {shared.map((s) => (
              <tr key={s.id}>
                <th className="mrow"><span className="vname"><span className="vkind"><Icon name="plug" size={13} /></span>
                  <code className="vnm">{s.name}</code><span className="dim small">tools</span></span></th>
                {options.map((o) => cell("i", s.name, o.slug, `${s.name}'s tools`))}
                {total("i", s.name)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {changes.length > 0 && (
        <div className="savebar" role="status">
          <div className="savebar-what">
            <b>{changes.length} unsaved change{changes.length === 1 ? "" : "s"}.</b> {sentence.join(" · ")}
          </div>
          <div className="actions" style={{ marginTop: 0 }}>
            <button disabled={busy} onClick={() => setPending({})}>Discard</button>
            <button className="primary" disabled={busy} onClick={save}>
              {busy ? "Saving…" : `Save ${changes.length} change${changes.length === 1 ? "" : "s"}`}
            </button>
          </div>
        </div>
      )}
    </>
  );
}
