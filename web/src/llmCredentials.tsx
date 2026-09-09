// What projects pay Claude with, and the screen that keeps that list honest.
//
// The list used to be a flat table of every managed credential, ordered by
// name, with one fact per row: which projects were assigned. That was fine
// while an administrator created them by hand. It stopped being fine the day
// credentials moved above projects, because that migration turned every key
// the install had ever used into a permanent named row — most of them
// belonging to projects that had already ended, none of them deletable
// (a project still names what paid for it), all of them offered in every
// picker.
//
// So this screen answers two questions before anything else: *which of these
// is anything actually using*, and *how do I get the rest out of my way*.
// "In use" means a project that can still spend — the same line the Desk
// draws. Hiding is the way out, it is reversible, and it works on the whole
// selection at once, because at forty rows one-at-a-time is not a cleanup.

import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { del, get, patch, post } from "./api";
import { ConfirmModal, fmtAgo, fmtSoon, SecretInput } from "./ui";

export interface CredentialProject {
  id: string;
  name: string;
  status: string;
  /** Can this project still spend? A stopped or completed one cannot. */
  live: boolean;
}

export interface Credential {
  id: string;
  name: string;
  mode: string;
  created_at?: string;
  hidden_at?: string | null;
  /** True when at least one live project pays with it. The server decides. */
  in_use?: boolean;
  /** A digest of the value — never the value. Two credentials showing the
      same one are the same key, whatever their names claim. */
  token?: string | null;
  shares_token_with?: number;
  projects: CredentialProject[];
}

const endpoint = "/admin/settings/llm-credentials";
const modeLabel = (mode: string) => mode === "subscription" ? "Claude subscription · tokens" : "Anthropic API · USD";
const modeName = (mode: string) => mode === "subscription" ? "Claude subscription" : "Anthropic API key";
const modeMeter = (mode: string) => mode === "subscription" ? "metered in tokens" : "billed in USD";

/** The credential a bundle names, if this install has it. By id first (a
    Founder draft), then by name, case-insensitively (an export's hint).
    Reads the offered list, so a hidden credential resolves to nothing and the
    import asks the chairman to pick — which is the truthful outcome. */
export async function resolveCredential(id?: string | null, name?: string | null): Promise<Credential | null> {
  if (!id && !name) return null;
  const rows = await get<Credential[]>(endpoint).catch(() => [] as Credential[]);
  return rows.find((r) => r.id === id)
    ?? rows.find((r) => !!name && r.name.toLowerCase() === name.toLowerCase())
    ?? null;
}

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
    <p className="help">{rows?.length === 0 ? "No LLM credentials available. Add one in Settings to enable model runs. " : "Credentials are managed by your administrator. Hidden ones are not offered here. "}
      <Link to="/settings" target="_blank" rel="noreferrer">Manage credentials ↗</Link>{" "}
      <button type="button" className="small" onClick={reload}>Refresh</button>
    </p>
  </div>;
}

// ── triage ──────────────────────────────────────────────────────────────────────

type Bucket = "in_use" | "idle" | "unused" | "hidden";

/** The one classification the whole screen sorts, filters and reasons with.
    Hidden wins over everything: a hidden credential's assignments are history
    by construction, since hiding is refused while anything live pays with it. */
function bucketOf(row: Credential): Bucket {
  if (row.hidden_at) return "hidden";
  if (row.in_use) return "in_use";
  return row.projects.length ? "idle" : "unused";
}

const BUCKETS: { key: Bucket; label: string; blurb: string }[] = [
  { key: "in_use", label: "In use", blurb: "a live project pays with it" },
  { key: "idle", label: "Idle", blurb: "only projects that have ended ever used it" },
  { key: "unused", label: "Unused", blurb: "no project has ever been assigned to it" },
  { key: "hidden", label: "Hidden", blurb: "off every menu; still here, still reversible" },
];

const ORDER: Record<Bucket, number> = { in_use: 0, idle: 1, unused: 2, hidden: 3 };

function StateChip({ bucket }: { bucket: Bucket }) {
  const text = { in_use: "in use", idle: "idle", unused: "unused", hidden: "hidden" }[bucket];
  return <span className={`chip${bucket === "in_use" ? " accent" : ""}`}>{text}</span>;
}

/** Which projects, and whether any of them can still spend. The finished ones
    stay on the row — they are the reason the credential cannot be deleted, and
    a row that says "idle" without saying *after what* is a dead end. */
function UsedBy({ row }: { row: Credential }) {
  if (row.projects.length === 0) return <span className="muted">never assigned</span>;
  const live = row.projects.filter((p) => p.live);
  const done = row.projects.filter((p) => !p.live);
  return <>
    {live.map((p, i) => <span key={p.id}>{i > 0 && ", "}
      <Link to={`/projects/${p.id}/settings`}>{p.name}</Link></span>)}
    {live.length > 0 && done.length > 0 && <br />}
    {done.length > 0 && <span className="muted">
      {live.length > 0 ? "also " : ""}
      {done.slice(0, 3).map((p, i) => <span key={p.id}>{i > 0 && ", "}
        <Link className="muted" to={`/projects/${p.id}/settings`}>{p.name}</Link> ({p.status})</span>)}
      {done.length > 3 && `, +${done.length - 3} more`}
    </span>}
  </>;
}

interface BulkResult { hidden?: { name: string }[]; restored?: { name: string }[];
  refused: { id: string; name: string | null; reason: string }[] }

/** The sentence a bulk action owes: what moved, and why anything did not. */
function bulkSentence(result: BulkResult, verb: "Hid" | "Restored"): string {
  const moved = (result.hidden ?? result.restored ?? []).length;
  const head = moved === 0 ? `Nothing ${verb === "Hid" ? "hidden" : "restored"}.`
    : `${verb} ${moved} credential${moved === 1 ? "" : "s"}.`;
  if (result.refused.length === 0) return head;
  const named = result.refused.slice(0, 2)
    .map((r) => `${r.name ?? r.id} — ${r.reason}`).join("; ");
  const rest = result.refused.length > 2 ? `, and ${result.refused.length - 2} more` : "";
  return `${head} ${result.refused.length} refused: ${named}${rest}.`;
}

export function LlmCredentials({ onChanged }: { onChanged?: () => void } = {}) {
  const [rows, setRows] = useState<Credential[] | null>(null);
  const [editing, setEditing] = useState<Credential | "new" | null>(null);
  const [name, setName] = useState("");
  const [mode, setMode] = useState("api_key");
  const [value, setValue] = useState("");
  const [search, setSearch] = useState("");
  // The filter lives in the URL: "the nine I hid" is a thing worth linking to,
  // and a bulk action that reloads the list must not silently drop you back
  // into "all" while you are still working through the hidden shelf.
  const [params, setParams] = useSearchParams();
  const filter = (BUCKETS.some((b) => b.key === params.get("show"))
    ? params.get("show") : "all") as Bucket | "all";
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const show = useCallback((next: Bucket | "all") => {
    const merged = new URLSearchParams(params);
    if (next === "all") merged.delete("show"); else merged.set("show", next);
    setParams(merged, { replace: true });
    setPicked(new Set());
  }, [params, setParams]);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [removing, setRemoving] = useState<Credential | null>(null);
  const [hiding, setHiding] = useState<Credential[] | null>(null);

  // One fetch, hidden included: the counts on the filters are only honest if
  // the screen can see everything it is counting.
  const reload = useCallback(async () => {
    setRows(await get<Credential[]>(`${endpoint}?include_hidden=true`));
  }, []);
  useEffect(() => { reload().catch(e => setError(e.message)); }, [reload]);

  const counts = useMemo(() => {
    const out: Record<Bucket, number> = { in_use: 0, idle: 0, unused: 0, hidden: 0 };
    (rows ?? []).forEach((r) => { out[bucketOf(r)] += 1; });
    return out;
  }, [rows]);

  // Searching a key's digest is asking "which rows are this one key?", and the
  // honest answer includes the ones already hidden — otherwise "same key as 13
  // others" shows you nine and the count looks wrong.
  const term = search.trim().toLowerCase();
  const grouping = (rows ?? []).some((r) => !!r.token && r.token === term);

  const shown = useMemo(() => {
    return (rows ?? [])
      .filter((r) => grouping || (filter === "all" ? !r.hidden_at : bucketOf(r) === filter))
      .filter((r) => !term || r.name.toLowerCase().includes(term)
        || r.token === term
        || r.projects.some((p) => p.name.toLowerCase().includes(term)))
      .sort((a, b) => ORDER[bucketOf(a)] - ORDER[bucketOf(b)] || a.name.localeCompare(b.name));
  }, [rows, filter, term, grouping]);

  // Selection is for one verb at a time: on the hidden shelf it restores, and
  // everywhere else it hides — and an in-use row can do neither.
  const restoring = filter === "hidden";
  // Selectable means "this row would actually move". A key group shows hidden
  // and visible rows together, so "not in use" is no longer enough to mean
  // hideable.
  const selectable = shown.filter((r) => (restoring ? !!r.hidden_at
                                                    : !r.in_use && !r.hidden_at));
  const chosen = selectable.filter((r) => picked.has(r.id));
  const hideable = (rows ?? []).filter((r) => !r.hidden_at && !r.in_use);

  function edit(row: Credential | "new") {
    setEditing(row); setName(row === "new" ? "" : row.name);
    setMode(row === "new" ? "api_key" : row.mode); setValue(""); setError(""); setMessage("");
  }

  async function act<T>(fn: () => Promise<T>, done: (result: T) => string) {
    setBusy(true); setError("");
    try {
      setMessage(done(await fn()));
      await reload();
    } catch (e) {
      setError((e as Error).message);
      await reload().catch(() => {});
    } finally {
      setBusy(false);
      // The page around this list counts credentials in its navigation. A
      // bulk hide that leaves "2 in use of 15" standing beside a list of six
      // is the screen disagreeing with itself.
      onChanged?.();
    }
  }

  async function save(e: React.FormEvent) {
    e.preventDefault();
    await act(
      async () => {
        if (editing === "new") await post(endpoint, { name: name.trim(), mode, value: value.trim() });
        else if (editing) await patch(`${endpoint}/${editing.id}`,
          { name: name.trim(), ...(value.trim() ? { value: value.trim() } : {}) });
        setEditing(null); setValue("");
      },
      () => "Credential saved. New runs use the updated credential.");
  }

  async function move(targets: Credential[], undo: boolean) {
    const ids = targets.map((r) => r.id);
    await act(
      () => post<BulkResult>(`${endpoint}/${undo ? "unhide" : "hide"}`, { ids }),
      (result) => bulkSentence(result, undo ? "Restored" : "Hid"));
    setPicked(new Set());
    setHiding(null);
  }

  return <section className="card llm-credentials" aria-label="LLM credentials">
    <div className="llm-header">
      <div>
        <h2>LLM credentials</h2>
        <p className="help">What projects pay Claude with. Values are encrypted at rest and
          never shown back — rotate one rather than reading it. A credential a project ever
          named cannot be deleted; <b>hide</b> it instead and it leaves every menu without
          leaving the record.</p>
      </div>
      <button type="button" className="primary" disabled={busy || editing !== null}
              onClick={() => edit("new")}>Add credential</button>
    </div>

    {error && <p className="error" role="alert">{error}</p>}
    {message && <p className="notice-box" role="status">{message}</p>}

    {editing && <form onSubmit={save} className="card cred-form">
      <h3>{editing === "new" ? "Add credential" : `Edit “${editing.name}”`}</h3>
      <fieldset disabled={busy} style={{ border: 0, padding: 0 }}>
        <label className="field">Name<input autoFocus required maxLength={120} value={name}
          onChange={e => setName(e.target.value)} placeholder="e.g. Production — engineering" /></label>
        <label className="field">Type<select value={mode} disabled={editing !== "new"}
          onChange={e => setMode(e.target.value)}>
          <option value="api_key">Anthropic API key</option>
          <option value="subscription">Claude subscription token</option>
        </select></label>
        {editing !== "new" && <p className="help">The type is fixed: it decides how every
          project on this credential is metered, and changing it under them would rewrite
          what their budgets mean.</p>}
        <label className="field" htmlFor="llm-credential-value">
          {editing === "new" ? "Credential value" : "Replace credential value (optional)"}</label>
        <SecretInput id="llm-credential-value" value={value} onChange={setValue}
          placeholder={editing === "new" ? "Paste credential" : "Leave blank to keep current value"} />
        <p className="help">{mode === "subscription"
          ? "Use the token generated by claude setup-token — it starts sk-ant-oat."
          : "Use an API key from your Anthropic account — it starts sk-ant-api."}</p>
        {editing !== "new" && editing.projects.filter(p => p.live).length > 0 &&
          <p className="notice-box">Replacing this value changes what{" "}
            {editing.projects.filter(p => p.live).map(p => p.name).join(", ")} pays with on
            the next run.</p>}
        <div className="actions">
          <button className="primary" disabled={!name.trim() || (editing === "new" && value.trim().length < 8)}>
            {busy ? "Saving…" : "Save credential"}</button>
          <button type="button" onClick={() => { setEditing(null); setValue(""); }}>Cancel</button>
        </div>
      </fieldset>
    </form>}

    {removing && <ConfirmModal title={`Delete “${removing.name}”?`} confirmLabel="Delete credential"
      danger busy={busy} onCancel={() => setRemoving(null)}
      onConfirm={() => act(() => del(`${endpoint}/${removing.id}`), () => "Credential deleted.")
        .then(() => setRemoving(null))}>
      <p>No project has ever been assigned to this credential, so nothing loses its record
        when it goes. The stored value is destroyed and cannot be recovered.</p>
    </ConfirmModal>}

    {hiding && <ConfirmModal title={hiding.length === 1 ? `Hide “${hiding[0].name}”?`
      : `Hide ${hiding.length} credentials?`} confirmLabel="Hide" busy={busy}
      onCancel={() => setHiding(null)} onConfirm={() => move(hiding, false)}>
      <p>They leave this page, the project picker, the Founder&apos;s list and every MCP
        spec, and none of them can be assigned again while hidden. Nothing is deleted:
        projects that already ended keep naming what paid for them, and one click under{" "}
        <b>Hidden</b> puts any of these back.</p>
      {hiding.length > 1 && <p className="help">{hiding.map((r) => r.name).join(", ")}</p>}
    </ConfirmModal>}

    {rows === null ? <p role="status">Loading credentials…</p>
      : rows.length === 0 ? <p className="help">No credentials yet. Add your first one to
        connect a project.</p> : <>

      <div className="cred-filters" role="group" aria-label="Filter credentials">
        <button type="button" aria-pressed={filter === "all"}
          className={`chip${filter === "all" ? " accent" : ""}`}
          onClick={() => show("all")}>all {rows.length - counts.hidden}</button>
        {BUCKETS.map((b) => (
          <button key={b.key} type="button" aria-pressed={filter === b.key}
            className={`chip${filter === b.key ? " accent" : ""}`} title={b.blurb}
            disabled={counts[b.key] === 0 && filter !== b.key}
            onClick={() => show(b.key)}>{b.label.toLowerCase()} {counts[b.key]}</button>
        ))}
        <span className="grow" />
        <input type="search" value={search} aria-label="Search credentials"
          onChange={e => setSearch(e.target.value)} placeholder="Search name or project" />
      </div>

      <p className="help cred-blurb">
        {grouping
          ? `All ${shown.length} credentials holding one key, hidden ones included. They are `
            + "interchangeable, whatever their names say — keep the clearest and hide the rest."
          : filter === "all"
          ? "Everything on the menus. Hidden credentials are on their own shelf."
          : BUCKETS.find((b) => b.key === filter)?.blurb}
        {/* Not while looking at one key: "hide all 13" there reads as an offer
            about the twelve rows on screen, and it is not. Inside a group the
            checkboxes are the right verb — which copy to keep is a choice. */}
        {filter === "all" && !grouping && !term && hideable.length >= 3 && <>
          {" "}Nothing is using {hideable.length} of them.{" "}
          <button type="button" className="linkish" disabled={busy}
            onClick={() => setHiding(hideable)}>Hide all {hideable.length}</button>
        </>}
      </p>

      {chosen.length > 0 && <div className="cred-bulk" role="status">
        <span><b>{chosen.length}</b> selected</span>
        <button type="button" className="primary" disabled={busy}
          onClick={() => restoring ? move(chosen, true) : setHiding(chosen)}>
          {restoring ? "Restore" : "Hide"} {chosen.length}</button>
        <button type="button" className="small" disabled={busy}
          onClick={() => setPicked(new Set())}>Clear</button>
      </div>}

      {shown.length > 0 && <table className="cred-table">
        <colgroup>
          <col style={{ width: 30 }} /><col /><col style={{ width: 118 }} />
          <col style={{ width: 168 }} /><col style={{ width: 152 }} />
          <col style={{ width: 146 }} />
        </colgroup>
        <thead><tr>
          <th className="pick">{selectable.length > 0 && <input type="checkbox"
            aria-label={restoring ? "Select every restorable credential"
              : "Select every hideable credential"}
            checked={chosen.length > 0 && chosen.length === selectable.length}
            onChange={e => setPicked(e.target.checked
              ? new Set(selectable.map((r) => r.id)) : new Set())} />}</th>
          <th>Name</th><th>Type</th><th>Used by</th><th>Plan usage</th><th>Actions</th>
        </tr></thead>
        <tbody>
          {shown.map(row => {
            const bucket = bucketOf(row);
            const canPick = restoring ? !!row.hidden_at : !row.in_use;
            return <tr key={row.id} className={row.hidden_at ? "is-hidden" : undefined}>
              <td className="pick">{canPick && <input type="checkbox"
                aria-label={`Select ${row.name}`} checked={picked.has(row.id)}
                onChange={e => setPicked(prev => {
                  const next = new Set(prev);
                  if (e.target.checked) next.add(row.id); else next.delete(row.id);
                  return next;
                })} />}</td>
              <td><strong>{row.name}</strong> <StateChip bucket={bucket} />
                {/* The migration named these after the project variable they
                    came from, so a row called "anthropic-api-key" can hold a
                    subscription token and fourteen rows can be one key. The
                    name cannot tell them apart; this can. */}
                {!!row.shares_token_with && row.token && (
                  <div className="help">
                    same key as {row.shares_token_with} other
                    {row.shares_token_with === 1 ? "" : "s"} —{" "}
                    <button type="button" className="linkish"
                      onClick={() => { show("all"); setSearch(row.token!); }}>show them</button>
                  </div>
                )}
                {row.hidden_at && <div className="help">hidden {fmtAgo(row.hidden_at)}</div>}</td>
              <td data-label="Type">{modeName(row.mode)}
                <div className="help">{modeMeter(row.mode)}</div></td>
              <td data-label="Used by"><UsedBy row={row} /></td>
              <td data-label="Plan usage">
                <PlanMeterCompact credentialId={row.id} mode={row.mode} auto={!!row.in_use} /></td>
              <td className="llm-actions">
                {/* Edit is on a hidden row too: restoring one whose name has
                    since been taken is refused, and renaming it is the way
                    out — a shelf you cannot rename anything on is a trap. */}
                <button className="small" disabled={busy || editing !== null}
                  onClick={() => edit(row)} aria-label={`Edit ${row.name}`}>Edit</button>
                {row.hidden_at ? <button className="small" disabled={busy}
                  onClick={() => move([row], true)}>Restore</button>
                  : <button className="small" disabled={busy || row.in_use}
                      title={row.in_use
                        ? "A live project pays with this — reassign or stop it first"
                        : `Hide ${row.name}`}
                      onClick={() => setHiding([row])} aria-label={`Hide ${row.name}`}>Hide</button>}
                {row.projects.length === 0 && <button className="small danger" disabled={busy}
                  onClick={() => { setRemoving(row); setError(""); }}
                  aria-label={`Delete ${row.name}`}>Delete</button>}
              </td>
            </tr>;
          })}
        </tbody>
      </table>}
      {shown.length === 0 && <p className="help">
        {search.trim() ? "No credentials match your search."
          : filter === "hidden" ? "Nothing is hidden."
          : "Nothing in this state."}</p>}
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
  /** Set when Anthropic is rate-limiting the meter and told us how long to
      wait. While it is set there is no point offering a retry: the server
      honours the wait and answers from cache, and pressing through a rate
      limit is what keeps a rate limit alive. */
  retry_after_s?: number;
}

const usageUrl = (id: string, refresh: boolean) =>
  `${endpoint}/${id}/usage${refresh ? "?refresh=true" : ""}`;

/** Reads a credential's plan meter. One local row read, on mount.

    It used to poll every 60 seconds. There is nothing to poll now: the meter
    is harvested from the credential's own agent runs rather than fetched from
    Anthropic, so re-asking the server cannot produce a newer number — only a
    run can. The refresh button re-reads the row for a reader who just watched
    one finish. Does nothing for a non-subscription credential. */
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
    body = <p className="help" style={{ margin: "4px 0" }}>
      Plan meter {data.retry_after_s ? "paused" : "unavailable"} — {data.reason}
      {!data.retry_after_s && <>. {refreshButton}</>}
    </p>;
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
          <span className="asof">reported by a run {fmtAgo(data.fetched_at)}</span>
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
        Claude Code, reported by the SDK on each agent run rather than polled, so it moves when
        this credential is used. Every project on it draws from the same windows. Shown here,
        not enforced here: the monthly cap is yaaf's separate limit.
      </p>
    </div>
  );
}

/** One line for a table cell: session and week, with resets underneath.

    `auto` decides whether it reads on its own. Every reading is a live call to
    Anthropic behind a one-minute cache, so a list of forty credentials that
    all poll is forty upstream calls a minute to answer a question about
    thirty-eight keys nothing is running on. The rows that can fail a run
    tonight read themselves; the rest wait to be asked. */
export function PlanMeterCompact({ credentialId, mode, auto = true }: {
  credentialId: string; mode: string; auto?: boolean;
}) {
  const [armed, setArmed] = useState(auto);
  const { data, error, busy, refresh, active } = usePlanUsage(armed ? credentialId : null, mode);
  if (mode !== "subscription") return <span className="muted" title="API keys are metered per call, in dollars; there is no plan window to fill.">— metered per call</span>;
  if (!active) return <button type="button" className="linkish" onClick={() => setArmed(true)}>read plan</button>;
  if (!data && !error) return <span className="help">reading…</span>;
  if (error || (data && !data.available)) {
    const waiting = !!data?.retry_after_s;
    return (
      <span className="plan-compact">
        <span>
          {waiting ? "paused" : "unavailable"}{" "}
          {!waiting && (
            <button type="button" className="linkish" disabled={busy} onClick={refresh}>{busy ? "reading…" : "retry"}</button>
          )}
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
