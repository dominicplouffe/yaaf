import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { del, get, patch, post } from "./api";
import { fmtAgo, fmtSoon, SecretInput } from "./ui";

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
      <table><thead><tr><th>Name</th><th>Type</th><th>Assigned projects</th><th>Plan usage</th><th>Actions</th></tr></thead><tbody>
        {filtered?.map(row => <tr key={row.id}><td><strong>{row.name}</strong></td><td data-label="Type">{modeLabel(row.mode)}</td><td data-label="Assigned projects">{row.projects.length ? row.projects.map((p, i) => <span key={p.id}>{i > 0 && ", "}<Link to={`/projects/${p.id}/settings`}>{p.name}</Link></span>) : <span className="muted">Not assigned</span>}</td><td data-label="Plan usage"><PlanMeterCompact credentialId={row.id} mode={row.mode} /></td><td className="llm-actions">
          <button className="small" disabled={busy || editing !== null || removing !== null} onClick={() => edit(row)} aria-label={`Edit ${row.name}`}>Edit</button>{" "}
          <button className="small danger" disabled={busy || editing !== null || removing !== null || row.projects.length > 0} title={row.projects.length ? "Reassign all projects before deleting" : `Delete ${row.name}`} onClick={() => { setRemoving(row); setError(""); }} aria-label={`Delete ${row.name}`}>Delete</button>
          {row.projects.length > 0 && <div className="help">In use · deletion locked</div>}
        </td></tr>)}
      </tbody></table>{filtered?.length === 0 && <p>No credentials match your search.</p>}
    </>}
  </section>;
}

// ── the plan meter behind a subscription credential ─────────────────────────────
//
// An API key is metered per call, in dollars, and the monthly budget is the only
// gauge that matters. A subscription token is different: Anthropic meters it
// against a five-hour session window and a seven-day window, and when either
// hits 100% every call is refused until it resets — no matter what the monthly
// cap says. A chairman whose runs started failing at 3pm deserves to see that
// the session window is full and clears at 3:22, not a generic credential error.
//
// The server reads the meter from the same endpoint Claude Code's `/usage` uses.
// It is Anthropic's number, not yaaf's: shown, never enforced.

export interface PlanWindow { key: string; label: string; utilization: number; resets_at: string | null }
export interface PlanUsage {
  credential_id: string; mode: string; available: boolean; reason: string | null;
  windows: PlanWindow[]; fetched_at: string; cached: boolean;
}

const usageUrl = (id: string, refresh: boolean) =>
  `${endpoint}/${id}/usage${refresh ? "?refresh=true" : ""}`;

/** Polls a credential's plan reading once a minute (the server caches for the
    same minute) and hands back a forced refresh for the reader who just
    watched a run finish. Does nothing for a non-subscription credential. */
export function usePlanUsage(credentialId: string | null, mode: string | null) {
  const [data, setData] = useState<PlanUsage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const active = !!credentialId && mode === "subscription";
  const load = useCallback((refresh: boolean) => {
    if (!active || !credentialId) return;
    setBusy(true);
    get<PlanUsage>(usageUrl(credentialId, refresh))
      .then((d) => { setData(d); setError(null); })
      .catch((e: Error) => setError(e.message))
      .finally(() => setBusy(false));
  }, [active, credentialId]);
  useEffect(() => {
    if (!active) { setData(null); setError(null); return; }
    load(false);
    const timer = setInterval(() => load(false), 60_000);
    return () => clearInterval(timer);
  }, [active, load]);
  return { data, error, busy, refresh: () => load(true), active };
}

const fillClass = (u: number) => (u >= 1 ? "over" : u >= 0.8 ? "warn" : "");
const pct = (u: number) => `${Math.min(999, Math.round(u * 100))}%`;

/** The sentence a reader needs before the bars: is anything about to stop runs? */
function planVerdict(windows: PlanWindow[]): { cls: string; text: string } {
  const full = windows.find((w) => w.utilization >= 1);
  if (full) {
    return { cls: "critical",
      text: `✖ ${full.label} is at 100% — Anthropic refuses calls and runs on this credential fail until it resets${full.resets_at ? ` ${fmtSoon(full.resets_at)}` : ""}` };
  }
  const near = windows.find((w) => w.utilization >= 0.8);
  if (near) {
    return { cls: "warn",
      text: `⚠ ${near.label} is at ${pct(near.utilization)} — runs will start failing at 100%${near.resets_at ? `; it resets ${fmtSoon(near.resets_at)}` : ""}` };
  }
  return { cls: "good", text: "✓ headroom on every window" };
}

/** Full meter: one bar per window, a verdict on top, provenance underneath.
    Draws nothing for an API-key credential — there is no plan to draw. */
export function PlanMeter({ credentialId, mode, title = "Claude subscription plan" }: {
  credentialId: string | null; mode: string | null; title?: string;
}) {
  const { data, error, busy, refresh, active } = usePlanUsage(credentialId, mode);
  if (!active) return null;
  const refreshButton = (
    <button type="button" className="linkish" disabled={busy} onClick={refresh}>
      {busy ? "reading…" : "refresh"}
    </button>
  );
  let body;
  if (!data && !error) {
    body = <p className="help" style={{ margin: "4px 0" }}>Reading the plan meter…</p>;
  } else if (error) {
    body = <p className="help" style={{ margin: "4px 0" }}>Plan meter not reachable — {error}. {refreshButton}</p>;
  } else if (data && !data.available) {
    body = <p className="help" style={{ margin: "4px 0" }}>Plan meter unavailable — {data.reason}. {refreshButton}</p>;
  } else if (data) {
    const verdict = planVerdict(data.windows);
    body = (
      <>
        <div className={`plan-verdict ${verdict.cls}`}>{verdict.text}</div>
        {data.windows.map((w) => (
          <div className="plan-window" key={w.key}>
            <div className="plan-row">
              <span>{w.label}</span>
              <span className="num">{pct(w.utilization)} used</span>
            </div>
            <div className="track">
              <div className={`fill ${fillClass(w.utilization)}`} style={{ width: `${Math.min(w.utilization, 1) * 100}%` }} />
            </div>
            <div className="foot">{w.resets_at ? `resets ${fmtSoon(w.resets_at)}` : " "}</div>
          </div>
        ))}
        <div className="plan-foot">
          <span className="asof">as of {fmtAgo(data.fetched_at)}</span>
          {refreshButton}
        </div>
      </>
    );
  }
  return (
    <div className="plan-meter">
      <div className="plan-head">
        <span className="label">{title}</span>
      </div>
      {body}
      <p className="help" style={{ marginBottom: 0 }}>
        Anthropic's own meter for this subscription — the same numbers as <code>/usage</code> in
        Claude Code. Every project on this credential draws from it. Shown here, not enforced
        here: the monthly cap is yaaf's separate limit.
      </p>
    </div>
  );
}

/** One line for a table cell: session and week, with resets underneath. */
export function PlanMeterCompact({ credentialId, mode }: { credentialId: string; mode: string }) {
  const { data, error, busy, refresh, active } = usePlanUsage(credentialId, mode);
  if (!active) return <span className="muted" title="API keys are metered per call, in dollars; there is no plan window to fill.">— metered per call</span>;
  if (!data && !error) return <span className="help">reading…</span>;
  if (error || (data && !data.available)) {
    return (
      <span className="plan-compact">
        <span>
          unavailable{" "}
          <button type="button" className="linkish" disabled={busy} onClick={refresh}>{busy ? "reading…" : "retry"}</button>
        </span>
        <span className="help">{error ?? data?.reason}</span>
      </span>
    );
  }
  const shown = data!.windows.filter((w) => w.key === "five_hour" || w.key === "seven_day");
  const short = (w: PlanWindow) => (w.key === "five_hour" ? "session" : "week");
  const full = shown.find((w) => w.utilization >= 1);
  return (
    <span className="plan-compact">
      <span>
        {shown.map((w) => (
          <span key={w.key} title={`${w.label}: ${pct(w.utilization)} used${w.resets_at ? `, resets ${fmtSoon(w.resets_at)}` : ""}`} style={{ marginRight: 10 }}>
            <span className={`mini-meter ${fillClass(w.utilization)}`}><i style={{ width: `${Math.min(w.utilization, 1) * 100}%` }} /></span>
            {short(w)} {pct(w.utilization)}
          </span>
        ))}
      </span>
      {full ? (
        <span className="plan-verdict critical" style={{ margin: 0 }}>
          runs on this credential fail until the {short(full)} window resets{full.resets_at ? ` ${fmtSoon(full.resets_at)}` : ""}
        </span>
      ) : (
        <span className="help">
          {shown.filter((w) => w.resets_at).map((w) => `${short(w)} resets ${fmtSoon(w.resets_at)}`).join(" · ") || " "}
        </span>
      )}
    </span>
  );
}
