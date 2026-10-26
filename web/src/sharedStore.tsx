import { useCallback, useEffect, useState } from "react";
import { del, get, OrgView, post, put } from "./api";
import { envVarName } from "./datasource";
import { GrantChips } from "./grants";
import { fmtWhen } from "./hooks";
import { grantOptions, RoleGrants } from "./roles";
import { CredentialValue, Drawer, RowMenu, SecretInput } from "./ui";
import { Icon } from "./ui/Icon";

/** The shared store (docs/SHARED_STORE.md): secrets, variables and data
    sources kept once, above every project.

    Two keys, and each side of the app holds one. The admin's section in
    Settings stores the values and says which projects may use each. A
    project's Secrets & variables and Data sources pages show what that project
    may use, and its chairs pick the teams. Neither screen offers the other's
    key — the server would refuse it anyway. */

export type SharedKind = "variables" | "data-sources";
type Availability = "all" | "listed";

interface ProjectUse {
  project_id: string;
  slug: string;
  name: string;
  roles: string[];
  teams: string[];
}

interface Check { ok: boolean; detail?: string; state?: string; at?: string; tools?: number }

/** A shared item as a project sees it: names and grants, never a value. */
export interface SharedRow {
  id: string;
  kind: "variable" | "data_source";
  name: string;
  /** A variable's one name; a source's address and (if it has one) token. */
  env: string | string[];
  type?: "secret" | "plain" | "file";
  filename?: string | null;
  url_host?: string;
  url_port?: number;
  access?: "inline" | "bulk";
  auth?: string;
  last_check?: Check | null;
  updated_at: string | null;
  roles: string[];
  teams: string[];
}

export interface ProjectShared {
  variables: SharedRow[];
  data_sources: SharedRow[];
}

interface StoreRow extends Omit<SharedRow, "roles" | "teams"> {
  shared_with: Availability;
  projects: ProjectUse[];
  value?: string;
  hint?: string;
  size_bytes?: number;
}

interface Store {
  variables: StoreRow[];
  data_sources: StoreRow[];
  projects: { id: string; slug: string; name: string }[];
}

const TYPE_LABEL = { secret: "Secret", plain: "Plain value", file: "File" } as const;

const envOf = (row: { env: string | string[] }) => (Array.isArray(row.env) ? row.env[0] : row.env);

export function useProjectShared(projectId: string) {
  const [view, setView] = useState<ProjectShared | null>(null);
  const reload = useCallback(() => {
    get<ProjectShared>(`/admin/projects/${projectId}/shared`).then(setView).catch(() => {});
  }, [projectId]);
  useEffect(reload, [reload]);
  return { view, reload };
}

// ── a project's side: which of our teams get it ─────────────────────────────

/** The rows a project may use from the store, under its own. Nothing here
    is revealable: the value is the admin's, and this page is everyone's on
    the project. */
export function ProjectSharedSection({ projectId, org, kind, rows, onSaved }: {
  projectId: string;
  org: OrgView | null;
  kind: SharedKind;
  rows: SharedRow[];
  onSaved: (message: string) => void;
}) {
  const options = grantOptions(org);
  const [open, setOpen] = useState<SharedRow | null>(null);
  if (rows.length === 0) return null;
  return (
    <>
      <div className="vgroup static">
        <Icon name="users" size={13} />
        <b>From the shared store</b>
        <span className="dim">· {rows.length}</span>
        <span className="vgroup-note">Kept once for every project. Choose who here gets it</span>
      </div>
      {rows.map((row) => (
        <div key={row.id} role="row" tabIndex={0} className="vrow click shared"
             onClick={() => setOpen(row)}
             onKeyDown={(e) => {
               if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setOpen(row); }
             }}>
          <span className="vname">
            <span className="vkind locked" title="Shared"><Icon name="users" size={14} /></span>
            <span className="vname-text">
              <code className="vnm">{kind === "variables" ? envOf(row) : row.name}</code>
              <span className="vsub">
                {kind === "variables"
                  ? `Shared · ${TYPE_LABEL[row.type ?? "secret"]}`
                  : `Shared · ${row.url_host}:${row.url_port}`}
              </span>
            </span>
          </span>
          <span className="vsub">Set by your administrator</span>
          <span><GrantChips options={options} selected={row.roles} /></span>
          <span className="vupd">{row.updated_at ? fmtWhen(row.updated_at) : ""}</span>
          <span />
        </div>
      ))}
      {open && (
        <SharedGrantDrawer projectId={projectId} org={org} kind={kind} row={open}
                           onClose={() => setOpen(null)}
                           onSaved={(message) => { setOpen(null); onSaved(message); }} />
      )}
    </>
  );
}

function SharedGrantDrawer({ projectId, org, kind, row, onClose, onSaved }: {
  projectId: string;
  org: OrgView | null;
  kind: SharedKind;
  row: SharedRow;
  onClose: () => void;
  onSaved: (message: string) => void;
}) {
  const [teams, setTeams] = useState<string[]>(row.roles);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const label = kind === "variables" ? `$${envOf(row)}` : `${row.name}'s tools`;
  const dirty = [...teams].sort().join() !== [...row.roles].sort().join();

  async function save() {
    setBusy(true);
    setError(null);
    try {
      await put(`/admin/projects/${projectId}/shared/${kind}/${row.id}`, { roles: teams });
      onSaved(`Saved who gets ${label}. It applies on each team's next run.`);
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }

  return (
    <Drawer
      title={<code>{kind === "variables" ? envOf(row) : row.name}</code>}
      sub="From the shared store. Its value is kept by your administrator."
      icon={<span className="vkind locked"><Icon name="users" size={14} /></span>}
      onClose={onClose}
      footer={<>
        <span style={{ flex: 1 }} />
        <button disabled={busy} onClick={onClose}>Cancel</button>
        <button className="primary" disabled={busy || !dirty} onClick={save}>
          {busy ? "Saving…" : "Save changes"}
        </button>
      </>}
    >
      {error && <div className="error" style={{ margin: 0 }}>{error}</div>}
      <p className="help" style={{ marginTop: 0 }}>
        {kind === "variables"
          ? <>Agents on the teams you choose get <code>${envOf(row)}</code>, as they do a variable of
              this project&apos;s own. Nobody here can read or change its value: ask your
              administrator to rotate it, and every project using it gets the new one.</>
          : <>The teams you choose can call this server&apos;s tools. Its address and sign-in are kept
              by your administrator.</>}
      </p>
      <RoleGrants org={org} selected={teams} onChange={setTeams}
                  secretName={kind === "variables" ? row.name : undefined} />
    </Drawer>
  );
}

// ── the admin's side: the store itself ──────────────────────────────────────

type Drawn =
  | { kind: "variables"; id?: string }
  | { kind: "data-sources"; id?: string };

export function SharedStoreSection() {
  const [store, setStore] = useState<Store | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [flash, setFlash] = useState<string | null>(null);
  const [drawn, setDrawn] = useState<Drawn | null>(null);

  const reload = useCallback(() => {
    get<Store>("/admin/shared").then((s) => { setStore(s); setError(null); })
      .catch((e) => setError((e as Error).message));
  }, []);
  useEffect(reload, [reload]);

  const saved = (message: string) => {
    setFlash(message);
    setDrawn(null);
    reload();
  };

  if (!store) return error ? <div className="error">{error}</div> : <div className="card muted">Loading…</div>;

  const row = drawn?.id
    ? (drawn.kind === "variables" ? store.variables : store.data_sources).find((r) => r.id === drawn.id)
    : undefined;

  return (
    <>
      <div className="pagehead-row">
        <div>
          <h2 style={{ marginTop: 0 }}>Shared store</h2>
          <p className="subtitle" style={{ marginBottom: 0 }}>
            Secrets, variables and data sources kept once for many projects. You choose which
            projects may use each one. Each project&apos;s chairs choose which of their teams get it.
          </p>
        </div>
        <div className="actions" style={{ marginTop: 0 }}>
          <button onClick={() => setDrawn({ kind: "data-sources" })}>
            <Icon name="plug" size={15} /> Add data source
          </button>
          <button className="primary" onClick={() => setDrawn({ kind: "variables" })}>
            <Icon name="plus" size={15} /> Add variable
          </button>
        </div>
      </div>
      {error && <div className="error">{error}</div>}
      {flash && <p className="help">{flash}</p>}

      <h3>Secrets &amp; variables</h3>
      <StoreList rows={store.variables} kind="variables" onOpen={(id) => setDrawn({ kind: "variables", id })}
                 empty="Nothing shared yet. Add a login or key several projects use, such as EMAIL_USERNAME and EMAIL_PASSWORD." />
      <h3>Data sources</h3>
      <StoreList rows={store.data_sources} kind="data-sources"
                 onOpen={(id) => setDrawn({ kind: "data-sources", id })}
                 empty="No shared data sources. OAuth servers cannot be shared: add those to each project." />

      {drawn && (
        <StoreDrawer key={`${drawn.kind}:${drawn.id ?? "new"}`} kind={drawn.kind} row={row}
                     projects={store.projects} onClose={() => setDrawn(null)} onSaved={saved}
                     onChanged={reload} />
      )}
    </>
  );
}

function StoreList({ rows, kind, onOpen, empty }: {
  rows: StoreRow[];
  kind: SharedKind;
  onOpen: (id: string) => void;
  empty: string;
}) {
  return (
    <div className="vlist" role="table" aria-label={kind === "variables" ? "Shared variables" : "Shared data sources"}>
      <div className="vrow vhead" role="row">
        <span role="columnheader">{kind === "variables" ? "Name agents see" : "Source"}</span>
        <span role="columnheader">{kind === "variables" ? "Value" : "Address"}</span>
        <span role="columnheader">Projects using it</span>
        <span role="columnheader" className="vupd">Updated</span>
        <span />
      </div>
      {rows.length === 0 && <div className="vempty">{empty}</div>}
      {rows.map((r) => (
        <div key={r.id} role="row" tabIndex={0} className="vrow click" onClick={() => onOpen(r.id)}
             onKeyDown={(e) => {
               if (e.target === e.currentTarget && (e.key === "Enter" || e.key === " ")) {
                 e.preventDefault();
                 onOpen(r.id);
               }
             }}>
          <span className="vname">
            <span className={`vkind ${kind === "variables" ? r.type ?? "secret" : ""}`}>
              <Icon name={kind === "variables" ? (r.type === "file" ? "file" : r.type === "plain" ? "type" : "key") : "plug"} size={14} />
            </span>
            <span className="vname-text">
              <code className="vnm">{kind === "variables" ? envOf(r) : r.name}</code>
              <span className="vsub">{r.shared_with === "all" ? "Any project may use it" : "Listed projects only"}</span>
            </span>
          </span>
          <span className="vval">
            {kind === "data-sources" ? <>{r.url_host}:{r.url_port}</>
              : r.type === "file" ? <>{r.filename}</>
              : r.value !== undefined ? <>{r.value}</>
              : <><span className="dots">••••••••</span>{r.hint ?? ""}</>}
          </span>
          <span className="gchips">
            {r.projects.length === 0 ? <span className="dim">none yet</span>
              : r.projects.map((p) => (
                <span key={p.project_id} className="gchip" title={p.teams.join(", ") || "no team yet"}>
                  {p.name}{p.roles.length === 0 && <span className="dim"> · nobody</span>}
                </span>
              ))}
          </span>
          <span className="vupd">{r.updated_at ? fmtWhen(r.updated_at) : ""}</span>
          <RowMenu label={`More for ${r.name}`} items={[
            { label: "Edit", icon: "edit", onClick: () => onOpen(r.id) },
          ]} />
        </div>
      ))}
    </div>
  );
}

function StoreDrawer({ kind, row, projects, onClose, onSaved, onChanged }: {
  kind: SharedKind;
  row?: StoreRow;
  projects: Store["projects"];
  onClose: () => void;
  onSaved: (message: string) => void;
  onChanged: () => void;
}) {
  const creating = !row;
  const [name, setName] = useState(row?.name ?? "");
  const [type, setType] = useState<"secret" | "plain" | "file">(row?.type ?? "secret");
  const [value, setValue] = useState({ value: "", filename: null as string | null });
  const [replacing, setReplacing] = useState(creating);
  const [url, setUrl] = useState("");
  const [bearer, setBearer] = useState("");
  const [access, setAccess] = useState<"inline" | "bulk">(row?.access ?? "inline");
  const [availability, setAvailability] = useState<Availability>(row?.shared_with ?? "listed");
  const [revealed, setRevealed] = useState<string | null>(null);
  const [adding, setAdding] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run(fn: () => Promise<unknown>, after?: (result: unknown) => void) {
    setBusy(true);
    setError(null);
    try {
      const result = await fn();
      after?.(result);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const save = () => run(async () => {
    if (kind === "variables") {
      await post("/admin/shared/variables", {
        name: name.trim(), shared_with: availability,
        ...(type !== "file" ? { visibility: type } : {}),
        ...(replacing && value.value ? { value: value.value } : {}),
        ...(replacing && value.value && type === "file" ? { filename: value.filename ?? row?.filename ?? "" } : {}),
      });
      onSaved(`Saved $${envVarName(name)}. Projects that receive it get the change on their next run.`);
    } else {
      if (!url.trim()) throw new Error("Paste the server's address to save it — it is checked when you do.");
      const saved = await post<{ check: Check }>("/admin/shared/data-sources", {
        name: name.trim(), url: url.trim(), access, shared_with: availability,
        ...(bearer ? { bearer } : {}),
      });
      onSaved(`Saved '${name.trim()}' — ${saved.check.ok ? "" : "but "}${saved.check.detail ?? ""}`);
    }
  });

  const remove = () => run(async () => {
    await del(`/admin/shared/${kind}/${row!.id}`);
    onSaved(`Deleted ${row!.name} from the shared store.`);
  });

  const reveal = () => run(async () => {
    const got = await get<{ value: string | null; binary?: boolean }>(`/admin/shared/variables/${row!.id}/value`);
    setRevealed(got.binary ? "binary — nothing readable to show" : got.value ?? "");
  });

  const listProject = () => run(async () => {
    await post(`/admin/shared/${kind}/${row!.id}/projects`, { project_id: adding });
    setAdding("");
    onChanged();
  });

  const revokeProject = (projectId: string) => run(async () => {
    await post(`/admin/shared/${kind}/${row!.id}/revoke`, { project_id: projectId });
    onChanged();
  });

  const listed = new Set(row?.projects.map((p) => p.project_id) ?? []);
  const dirty = creating || kind === "data-sources" || (replacing && !!value.value)
    || availability !== row?.shared_with || type !== row?.type;
  const ready = dirty && name.trim().length >= 2 && (kind === "variables"
    ? !creating || (type === "file" ? !!value.value && value.filename !== null : !!value.value)
    : !!url.trim());

  return (
    <Drawer
      title={creating ? (kind === "variables" ? "Add a shared variable" : "Add a shared data source")
        : <code>{kind === "variables" ? envOf(row!) : row!.name}</code>}
      sub="Kept once, encrypted. Projects you allow choose which of their teams get it."
      onClose={onClose}
      footer={<>
        {!creating && (
          <button className="danger-text" disabled={busy} onClick={() => setConfirming(true)}>
            <Icon name="trash" size={14} /> Delete
          </button>
        )}
        <span style={{ flex: 1 }} />
        <button disabled={busy} onClick={onClose}>Cancel</button>
        <button className="primary" disabled={busy || !ready} onClick={save}>
          {busy ? "Saving…" : creating ? "Add it" : "Save changes"}
        </button>
      </>}
    >
      {error && <div className="error" style={{ margin: 0 }}>{error}</div>}
      {confirming && row && (
        <div className="confirmbox" role="alert">
          <b>Delete {row.name}?</b>
          <span>
            {row.projects.length
              ? `${row.projects.map((p) => p.name).join(", ")} still ${row.projects.length === 1 ? "has" : "have"} it. Remove it from each project first.`
              : "No project uses it. This can't be undone."}
          </span>
          <div className="actions" style={{ marginTop: 0 }}>
            <button className="danger" disabled={busy || row.projects.length > 0} onClick={remove}>Delete</button>
            <button disabled={busy} onClick={() => setConfirming(false)}>Keep it</button>
          </div>
        </div>
      )}
      <div className="tswitch-field">
        <label className="lbl" htmlFor="shared-name">Name</label>
        <input id="shared-name" className="mono" value={name} disabled={!creating} autoFocus={creating}
               placeholder={kind === "variables" ? "EMAIL_PASSWORD" : "crm"}
               onChange={(e) => setName(e.target.value)} />
        {kind === "variables" && name.trim() && (
          <span className="help">Agents will see <code>${envVarName(name)}</code>.</span>
        )}
      </div>

      {kind === "variables" ? (
        <>
          <div className="tswitch-field">
            <span className="lbl">Type</span>
            <div className="kindpick" role="radiogroup">
              {(["secret", "plain", "file"] as const).map((k) => {
                const locked = !creating && (k === "file") !== (row?.type === "file");
                return (
                  <button key={k} type="button" role="radio" aria-checked={type === k}
                          className={type === k ? "on" : ""} disabled={locked}
                          onClick={() => { setType(k); if (creating) setValue({ value: "", filename: null }); }}>
                    <b>{TYPE_LABEL[k]}</b>
                  </button>
                );
              })}
            </div>
          </div>
          <div className="tswitch-field">
            <div className="tswitch-head">
              <span className="lbl">Value</span>
              {!creating && (
                <button className="linklike" onClick={() => { setReplacing(!replacing); setRevealed(null); }}>
                  {replacing ? "Keep the current one" : "Replace value"}
                </button>
              )}
            </div>
            {replacing ? (
              type === "file" ? <CredentialValue value={value.value} filename={value.filename} fileOnly onChange={setValue} />
                : type === "plain" ? <input value={value.value} onChange={(e) => setValue({ value: e.target.value, filename: null })} />
                : <SecretInput value={value.value} placeholder="The value itself"
                               onChange={(v) => setValue({ value: v, filename: null })} />
            ) : (
              <div className="valbox">
                <span className="valbox-v mono">
                  {revealed ?? (row?.value !== undefined ? row.value
                    : row?.type === "file" ? row.filename : <><span className="dots">••••••••••••</span>{row?.hint ?? ""}</>)}
                </span>
                {revealed === null && row?.value === undefined && (
                  <button className="small" disabled={busy} onClick={reveal}><Icon name="eye" size={13} /> Reveal</button>
                )}
              </div>
            )}
          </div>
        </>
      ) : (
        <>
          <div className="tswitch-field">
            <label className="lbl" htmlFor="shared-url">Address</label>
            <SecretInput id="shared-url" value={url} defaultShown onChange={setUrl}
                         placeholder={row ? `${row.url_host}:${row.url_port} — paste the full address to save` : "https://mcp.example.com/mcp"} />
          </div>
          <div className="tswitch-field">
            <label className="lbl" htmlFor="shared-bearer">Bearer token</label>
            <SecretInput id="shared-bearer" value={bearer} onChange={setBearer}
                         placeholder={row?.auth === "bearer" ? "Leave empty to keep the current token" : "Optional"} />
          </div>
          <div className="tswitch-field">
            <span className="lbl">Results</span>
            <div className="kindpick" role="radiogroup">
              {(["inline", "bulk"] as const).map((a) => (
                <button key={a} type="button" role="radio" aria-checked={access === a}
                        className={access === a ? "on" : ""} onClick={() => setAccess(a)}>
                  <b>{a === "inline" ? "Into the conversation" : "To a file"}</b>
                </button>
              ))}
            </div>
          </div>
          {row?.last_check && (
            <p className={row.last_check.ok ? "help" : "error"}>
              Last check: {row.last_check.detail}{row.last_check.at && <> · {fmtWhen(row.last_check.at)}</>}
            </p>
          )}
        </>
      )}

      <div className="tswitch-field">
        <span className="lbl">Which projects may use it</span>
        <div className="kindpick" role="radiogroup">
          <button type="button" role="radio" aria-checked={availability === "listed"}
                  className={availability === "listed" ? "on" : ""} onClick={() => setAvailability("listed")}>
            <b>Projects I list</b><span>Only the projects you add below</span>
          </button>
          <button type="button" role="radio" aria-checked={availability === "all"}
                  className={availability === "all" ? "on" : ""} onClick={() => setAvailability("all")}>
            <b>Every project</b><span>Any project&apos;s chairs may give it to their teams</span>
          </button>
        </div>
      </div>

      {row && (
        <div className="tswitch-field">
          <span className="lbl">Projects with access</span>
          {row.projects.length === 0 && <span className="help">None yet.</span>}
          <ul className="hist">
            {row.projects.map((p) => (
              <li key={p.project_id}>
                <span><b>{p.name}</b> · {p.teams.length ? p.teams.join(", ") : <span className="dim">no team has it yet</span>}</span>
                <button className="small" disabled={busy} onClick={() => revokeProject(p.project_id)}>Remove</button>
              </li>
            ))}
          </ul>
          <div className="actions" style={{ justifyContent: "flex-start", marginTop: 6 }}>
            <select value={adding} onChange={(e) => setAdding(e.target.value)} aria-label="Project to add">
              <option value="">Add a project…</option>
              {projects.filter((p) => !listed.has(p.id)).map((p) => (
                <option key={p.id} value={p.id}>{p.name}</option>
              ))}
            </select>
            <button disabled={busy || !adding} onClick={listProject}>Add</button>
          </div>
          <span className="help">
            Adding a project gives it access. Its chairs then choose which teams get it.
          </span>
        </div>
      )}
    </Drawer>
  );
}
