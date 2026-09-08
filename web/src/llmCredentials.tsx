import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { del, get, patch, post } from "./api";
import { SecretInput } from "./ui";

interface Credential {
  id: string;
  name: string;
  mode: string;
  projects: { id: string; name: string }[];
}
const endpoint = "/admin/settings/llm-credentials";
const modeLabel = (mode: string) => mode === "subscription" ? "Claude subscription · tokens" : "Anthropic API · USD";

export function CredentialPicker({ value, onChange, disabled = false }: {
  value: string; onChange: (id: string, mode: string) => void; disabled?: boolean;
}) {
  const [rows, setRows] = useState<Credential[] | null>(null);
  const [error, setError] = useState("");
  const reload = () => { setError(""); get<Credential[]>(endpoint).then(setRows).catch(e => setError(e.message)); };
  useEffect(reload, []);
  return <div>
    <label className="field">LLM credential
      <select value={value} disabled={disabled || rows === null} onChange={e => {
        const row = rows?.find(r => r.id === e.target.value);
        onChange(row?.id ?? "", row?.mode ?? "api_key");
      }}>
        <option value="">{rows === null ? "Loading credentials…" : "Select a credential"}</option>
        {rows?.map(row => <option key={row.id} value={row.id}>{row.name} — {modeLabel(row.mode)}</option>)}
      </select>
    </label>
    {error && <p role="alert" className="error">{error} <button type="button" onClick={reload}>Retry</button></p>}
    <p className="help">{rows?.length === 0 ? "No LLM credentials available. Add one in Settings to enable model runs. " : "Credentials are managed by your administrator. "}
      <Link to="/settings" target="_blank" rel="noreferrer">Manage credentials ↗</Link>{" "}
      <button type="button" className="small" onClick={reload}>Refresh</button>
    </p>
  </div>;
}

export function LlmCredentials() {
  const [rows, setRows] = useState<Credential[] | null>(null);
  const [editing, setEditing] = useState<Credential | "new" | null>(null);
  const [name, setName] = useState("");
  const [mode, setMode] = useState("api_key");
  const [value, setValue] = useState("");
  const [search, setSearch] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [removing, setRemoving] = useState<Credential | null>(null);
  const reload = async () => setRows(await get<Credential[]>(endpoint));
  useEffect(() => { reload().catch(e => setError(e.message)); }, []);
  function edit(row: Credential | "new") {
    setEditing(row); setName(row === "new" ? "" : row.name);
    setMode(row === "new" ? "api_key" : row.mode); setValue(""); setError(""); setMessage("");
  }
  async function save(e: React.FormEvent) {
    e.preventDefault(); setBusy(true); setError("");
    try {
      if (editing === "new") await post(endpoint, { name: name.trim(), mode, value: value.trim() });
      else if (editing) await patch(`${endpoint}/${editing.id}`, { name: name.trim(), ...(value.trim() ? { value: value.trim() } : {}) });
      setEditing(null); setValue(""); setMessage("Credential saved. New runs use the updated credential."); await reload();
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  async function remove() {
    if (!removing) return;
    setBusy(true); setError("");
    try { await del(`${endpoint}/${removing.id}`); setRemoving(null); setMessage("Credential deleted."); await reload(); }
    catch (e) { setError((e as Error).message); await reload().catch(() => {}); }
    finally { setBusy(false); }
  }
  const filtered = rows?.filter(r => r.name.toLowerCase().includes(search.toLowerCase()));
  return <section className="card llm-credentials" aria-label="LLM credentials">
    <div className="llm-header"><div><h2>LLM credentials</h2><p className="help">Create named API keys and subscription tokens, then assign them to projects. Values are encrypted at rest.</p></div>
      <button type="button" className="primary" disabled={busy || editing !== null || removing !== null} onClick={() => edit("new")}>Add credential</button></div>
    {error && <p className="error" role="alert">{error}</p>}
    {message && <p role="status">{message}</p>}
    {editing && <form onSubmit={save} className="card">
      <h3>{editing === "new" ? "Add credential" : "Edit credential"}</h3>
      <fieldset disabled={busy} style={{ border: 0, padding: 0 }}>
        <label className="field">Name<input autoFocus required maxLength={120} value={name} onChange={e => setName(e.target.value)} placeholder="e.g. Production — engineering" /></label>
        <label className="field">Type<select value={mode} disabled={editing !== "new"} onChange={e => setMode(e.target.value)}><option value="api_key">Anthropic API key</option><option value="subscription">Claude subscription token</option></select></label>
        <label className="field" htmlFor="llm-credential-value">{editing === "new" ? "Credential value" : "Replace credential value (optional)"}</label>
        <SecretInput id="llm-credential-value" value={value} onChange={setValue} placeholder={editing === "new" ? "Paste credential" : "Leave blank to keep current value"} />
        <p className="help">{mode === "subscription" ? "Use the token generated by claude setup-token." : "Use an API key from your Anthropic account."}</p>
        {editing !== "new" && editing.projects.length > 0 && <p role="note">Replacing this value affects new runs in {editing.projects.length} assigned project(s).</p>}
        <div className="actions"><button className="primary" disabled={!name.trim() || (editing === "new" && value.trim().length < 8)}>{busy ? "Saving…" : "Save credential"}</button><button type="button" onClick={() => { setEditing(null); setValue(""); }}>Cancel</button></div>
      </fieldset>
    </form>}
    {removing && <div className="card" role="alertdialog" aria-label="Confirm credential deletion">
      <h3>Delete “{removing.name}”?</h3><p>This permanently removes the stored credential. It cannot be undone.</p>
      <button className="danger" disabled={busy} onClick={remove}>{busy ? "Deleting…" : "Delete credential"}</button>{" "}<button disabled={busy} onClick={() => setRemoving(null)}>Cancel</button>
    </div>}
    {rows === null ? <p role="status">Loading credentials… <button onClick={() => reload().catch(e => setError(e.message))}>Retry</button></p> : rows.length === 0 ? <p>No credentials yet. Add your first credential to connect a project.</p> : <>
      <label className="field">Search credentials<input type="search" value={search} onChange={e => setSearch(e.target.value)} placeholder="Search by name" /></label>
      <table><thead><tr><th>Name</th><th>Type</th><th>Assigned projects</th><th>Actions</th></tr></thead><tbody>
        {filtered?.map(row => <tr key={row.id}><td><strong>{row.name}</strong></td><td data-label="Type">{modeLabel(row.mode)}</td><td data-label="Assigned projects">{row.projects.length ? row.projects.map((p, i) => <span key={p.id}>{i > 0 && ", "}<Link to={`/projects/${p.id}/settings`}>{p.name}</Link></span>) : <span className="muted">Not assigned</span>}</td><td className="llm-actions">
          <button className="small" disabled={busy || editing !== null || removing !== null} onClick={() => edit(row)} aria-label={`Edit ${row.name}`}>Edit</button>{" "}
          <button className="small danger" disabled={busy || editing !== null || removing !== null || row.projects.length > 0} title={row.projects.length ? "Reassign all projects before deleting" : `Delete ${row.name}`} onClick={() => { setRemoving(row); setError(""); }} aria-label={`Delete ${row.name}`}>Delete</button>
          {row.projects.length > 0 && <div className="help">In use · deletion locked</div>}
        </td></tr>)}
      </tbody></table>{filtered?.length === 0 && <p>No credentials match your search.</p>}
    </>}
  </section>;
}
