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

import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
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
  // The answer each row's last press produced, drawn full width beneath it.
  // Not in the cell: that column is 152px and this is a sentence.
  const [results, setResults] = useState<Record<string, PlanResult | null>>({});
  const noteResult = useCallback((id: string, r: PlanResult | null) =>
    setResults((prev) => (prev[id] === r ? prev : { ...prev, [id]: r })), []);
  // Bumped for a credential whose value was just replaced, to make it check
  // itself. Somebody who rotated a token did it to fix a dead meter; landing
  // them back on "no reading yet" and asking for one more press is the run-
  // around arriving one step later than before.
  const [recheck, setRecheck] = useState<Record<string, number>>({});
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
    const rotated = editing !== "new" && editing !== null
      && editing.mode === "subscription" && !!value.trim() ? editing.id : null;
    await act(
      async () => {
        if (editing === "new") await post(endpoint, { name: name.trim(), mode, value: value.trim() });
        else if (editing) await patch(`${endpoint}/${editing.id}`,
          { name: name.trim(), ...(value.trim() ? { value: value.trim() } : {}) });
        setEditing(null); setValue("");
      },
      () => "Credential saved. New runs use the updated credential.");
    if (rotated) setRecheck((prev) => ({ ...prev, [rotated]: Date.now() }));
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

      {shown.length > 0 && <div className="cred-table-wrap"><table className="cred-table">
        {/* Actions is 212 because that is what its widest row is: Edit,
            Restore and Delete side by side, with Delete's own gap. Sized to
            the common two-button row it overflowed the cell — table-layout is
            fixed, so the buttons hung past the table's edge rather than
            widening the column. */}
        <colgroup>
          <col style={{ width: 30 }} /><col /><col style={{ width: 118 }} />
          <col style={{ width: 168 }} /><col style={{ width: 152 }} />
          <col style={{ width: 212 }} />
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
            const said = results[row.id];
            return <Fragment key={row.id}>
              <tr className={row.hidden_at ? "is-hidden" : undefined}>
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
                <PlanMeterCompact credentialId={row.id} mode={row.mode} auto={!!row.in_use}
                  onRotate={() => edit(row)} checkNonce={recheck[row.id]}
                  onResult={(r) => noteResult(row.id, r)} /></td>
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
              </tr>
              {/* Full width, because the answer is a sentence and the cell that
                  holds the numbers is not. Sticky inside, because this table
                  scrolls sideways and the reader who scrolled right to reach
                  the button is the one being answered. */}
              {said && <tr className="plan-result">
                <td colSpan={6}>
                  <PlanResultLine result={said} onRotate={() => edit(row)} />
                </td>
              </tr>}
            </Fragment>;
          })}
        </tbody>
      </table></div>}
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
// The numbers arrive on their own: the SDK reports these windows to every agent
// run on the credential, and the server files them. Nothing on a clock asks for
// them — the poller that did earned a 429 — so the one gap is a credential
// nothing is running on, which is exactly what a full window produces. **Check
// now** is the door out of that gap, and it is a person's press rather than a
// timer: it asks Anthropic now, for this one subscription. The screen names when
// the number was reported, and a window whose reset time has passed is drawn as
// rolled over rather than as the percentage it died holding. It is Anthropic's
// number, not yaaf's: shown, never enforced.
//
// And the press answers. Every restraint on that call — the fifteen-second
// floor, a throttle being honoured, a token Anthropic refused — used to return
// the stored row and a 200, which on screen is indistinguishable from a press
// that worked. The credential this exists for is the one whose runs have
// stopped, and that is usually the one whose token is the problem, so the case
// that most needed a sentence was the only case that never got one. Below, a
// press sets a new number or a sentence saying why there is not one, and never
// nothing.

export interface PlanWindow {
  key: string; label: string; utilization: number; resets_at: string | null;
  /** The window's own reset time has passed, so this percentage measures a
      window that is over. Not the same as stale: the number was true, and what
      it was true about is gone. Drawn as a rollover, never as a bar — and never
      as 0%, which is a number nobody reported. */
  expired?: boolean;
}

/** What a press did, when a press is what produced this answer.
    Absent on an ordinary screen load, which asked nothing and must not look
    like a press that worked. */
export interface Asked {
  state: "fresh" | "floored" | "throttled" | "rejected" | "unreachable" | "unreadable";
  /** Seconds until this token may be asked again, when waiting is the remedy.
      Null on a rejection: a dead token is not fixed by patience, and a
      countdown there points the reader away from the only thing that works. */
  retry_after_s: number | null;
}

export interface PlanUsage {
  credential_id: string; mode: string; available: boolean; reason: string | null;
  windows: PlanWindow[]; fetched_at: string; cached: boolean;
  asked?: Asked;
  /** The subscription this credential's value is, without the value. Several
      rows routinely hold one subscription and share one meter, so a screen
      showing them together moves them together on this. */
  token_digest?: string | null;
}

/** The answer a press owes the person who pressed. */
export interface PlanResult {
  ok: boolean;
  text: string;
  /** A rejected token: the row offers the one action that fixes it. */
  rotate?: boolean;
}

const usageUrl = (id: string, refresh: boolean) =>
  `${endpoint}/${id}/usage${refresh ? "?refresh=true" : ""}`;

// ── one subscription, several rows ──────────────────────────────────────────────
//
// The meter is filed under the token, not the credential, because the migration
// that lifted keys above projects made one row per historical key and fourteen
// rows can be one subscription. So a press on one row is an answer about all of
// them, and a screen that updated only the row that was pressed would leave its
// siblings insisting on a number the server had already replaced — the screen
// disagreeing with itself, in the one place built for comparing credentials.
//
// Keyed on the digest the answer carries, so nothing has to be threaded through
// props and the full meter on the project pages joins the same wire for free.
// The last answer is retained because a press inside the floor can be settled
// from it instantly rather than waited out.

interface Shared { last: PlanUsage; at: number; subs: Set<(d: PlanUsage) => void> }
const shared = new Map<string, Shared>();

function subscribe(digest: string | null | undefined, fn: (d: PlanUsage) => void) {
  if (!digest) return () => {};
  const entry = shared.get(digest) ?? { last: null as never, at: 0, subs: new Set() };
  entry.subs.add(fn);
  shared.set(digest, entry);
  return () => { entry.subs.delete(fn); };
}

/** Hand a fresh answer to every other meter on the same subscription. They take
    the numbers and nothing else: the sentence belongs to the row that was
    pressed, and three of them from one press is noise. */
function publish(data: PlanUsage, from: (d: PlanUsage) => void) {
  const digest = data.token_digest;
  if (!digest) return;
  const entry = shared.get(digest) ?? { last: data, at: Date.now(), subs: new Set() };
  entry.last = data; entry.at = Date.now();
  shared.set(digest, entry);
  entry.subs.forEach((fn) => { if (fn !== from) fn(data); });
}

/** An answer for this subscription newer than the press that was floored, if a
    sibling's press already fetched one. */
function freshlyShared(digest: string | null | undefined, since: number): PlanUsage | null {
  const entry = digest ? shared.get(digest) : undefined;
  return entry && entry.at >= since ? entry.last : null;
}

const fillClass = (u: number) => (u >= 1 ? "over" : u >= 0.8 ? "warn" : "");
const pct = (u: number) => `${Math.min(999, Math.round(u * 100))}%`;

/** The windows that still describe something. A window past its own reset has
    turned over, and its percentage belongs to a window that is finished: it may
    not raise an alarm, cap a verdict, or fill a bar. This is the whole of the
    bug it fixes — the window at 100% is the one that stops the runs, a stopped
    run reports nothing, so without this the screen says "runs fail until it
    resets at 3:22pm" at seven in the evening and never stops saying it. */
const live = (windows: PlanWindow[]) => windows.filter((w) => !w.expired);

/** The sentence a reader needs before the bars: is anything about to stop runs? */
function planVerdict(all: PlanWindow[]): { cls: string; text: string } {
  const windows = live(all);
  // The server answers `available: false` when every window has turned over, so
  // this is the belt to the server's braces: a verdict is a sentence about
  // windows, and there is none to make about none of them.
  if (!windows.length) return { cls: "", text: "every window has reset since a run last reported one" };
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

/** How often an open screen re-reads the meter.

    It used to be 60 seconds, and every one of those reads was a live call to
    Anthropic from every open tab — which is what earned the 429. This one is
    free: it re-reads the local row that finished runs write, so an open tab
    picks up what has landed since instead of showing nine o'clock at noon. Ten
    minutes is paced to what it watches — windows five hours and seven days
    long, where nothing a reader can act on moves faster. Asking Anthropic is
    never on this timer; that is **Check now**, and only when pressed. */
const REREAD_MS = 10 * 60_000;

/** How long a success line stays before clearing itself.

    "Checked just now" still sitting there ten minutes later is the same lie as
    a bar drawn for a window that has reset, and this screen spends a lot of
    effort not telling that one. A failure is different and stays: a rejected
    token is a standing condition, not a notification. */
const RESULT_MS = 30_000;

/** How long a press may run before it admits it is still running. The upstream
    timeout is ten seconds, and ten seconds of a button that only says
    "Checking…" is indistinguishable from a hang. */
const SLOW_MS = 3_000;

/** A wait short enough to sit through rather than report. The floor is fifteen
    seconds; anything on that scale is absorbed silently, and anything longer is
    a sentence somebody needs to read. */
const ABSORB_MS = 20_000;

const shortLabel = (w: PlanWindow) =>
  w.key === "five_hour" ? "session" : w.key === "seven_day" ? "week" : w.label;

const headline = (d: PlanUsage) => live(d.windows)
  .filter((w) => w.key === "five_hour" || w.key === "seven_day")
  .map((w) => `${shortLabel(w)} ${pct(w.utilization)}`);

/** What a successful press actually changed, in the reader's words.

    A check that finds the same numbers is the normal outcome and a real answer:
    it is the difference between "nothing happened" and "nothing has changed",
    which is the whole question somebody presses this button to settle. */
function changeSentence(before: PlanUsage | null, after: PlanUsage): string {
  const was = new Map((before?.windows ?? []).map((w) => [w.key, w.utilization]));
  const moved = live(after.windows)
    .filter((w) => was.has(w.key) && was.get(w.key) !== w.utilization)
    .map((w) => `${shortLabel(w)} ${pct(was.get(w.key)!)} → ${pct(w.utilization)}`);
  if (moved.length) return `✓ Checked just now — ${moved.join(", ")}`;
  const now = headline(after);
  if (!now.length) return "✓ Checked just now";
  return was.size
    ? `✓ Checked just now — unchanged (${now.join(", ")})`
    : `✓ Checked just now — ${now.join(", ")}`;
}

/** The sentence a press that could not ask owes the person who pressed.

    The standing reading is its own sentence rather than a second clause: one
    line carrying two em-dashes is a line nobody finishes reading, and the fact
    that the old number is still on screen is exactly the part that stops a
    failed press from looking like a successful one. */
function refusalSentence(asked: Asked, fetchedAt: string | null): PlanResult {
  const standing = fetchedAt ? ` Still showing the reading from ${fmtAgo(fetchedAt)}.` : "";
  const when = (s: number) => fmtSoon(new Date(Date.now() + s * 1000).toISOString());
  switch (asked.state) {
    case "rejected":
      // No countdown, on purpose: waiting fixes nothing here, and offering one
      // sends the reader away from the only thing that does.
      return { ok: false, rotate: true,
        text: `✕ Anthropic rejected this token — it may have expired or been revoked.${standing}` };
    case "throttled":
      return { ok: false, text: `⏲ Anthropic is rate-limiting this token — try again ${
        asked.retry_after_s ? when(asked.retry_after_s) : "shortly"}.${standing}` };
    case "unreadable":
      return { ok: false,
        text: `✕ Anthropic answered with something this could not read.${standing}` };
    case "unreachable":
      return { ok: false, text: `✕ Could not reach Anthropic.${standing}` };
    default:
      // `floored` reaches here only when absorbing it has already failed once.
      return { ok: false, text: `Anthropic could not be asked just now.${standing}` };
  }
}

/** Reads a credential's plan meter: one local row read on mount, again every ten
    minutes while the screen is open, and a live call to Anthropic when somebody
    presses for one.

    A tick may improve the screen and never degrade it. It does not raise the
    busy flag — the button would blink "Checking…" unbidden every ten minutes —
    and a tick that fails leaves the last good reading standing rather than
    replacing a real number with an error, which is honest because that number
    carries the time it was reported.

    A **press** is the opposite: it always answers. Every path out of it sets a
    new number or a sentence saying why there is not one, because a press whose
    outcomes all look identical is a button nobody can trust — which is what
    this one was for as long as it existed. Does nothing for a non-subscription
    credential. */
export function usePlanUsage(credentialId: string | null, mode: string | null) {
  const [data, setData] = useState<PlanUsage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [slow, setSlow] = useState(false);
  const [result, setResult] = useState<PlanResult | null>(null);
  const active = !!credentialId && mode === "subscription";

  // What a press has to reach after an await, without re-creating the callback
  // and restarting the ten-minute timer underneath it.
  const alive = useRef(true);
  const latest = useRef<PlanUsage | null>(null);
  const retried = useRef(false);
  const timers = useRef<number[]>([]);
  // Which request the screen is currently waiting on. Arming a row fires the
  // local read and the press within a millisecond of each other, and the local
  // read answers in eight while the press is still out — so without a ticket
  // the quick one lowers the busy flag on the slow one, and a ten-second call
  // sits behind a button that says "Check now" and looks like it did nothing.
  // Only the newest request may speak for the row.
  const seq = useRef(0);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      timers.current.forEach((t) => clearTimeout(t));
      timers.current = [];
    };
  }, []);

  const adopt = useCallback((d: PlanUsage) => {
    latest.current = d; setData(d); setError(null);
  }, []);

  // A sibling's press is an answer about this row too. Numbers only: the
  // sentence belongs to whoever pressed, and three of them is noise.
  //
  // Its own last sentence goes with them. "Checked just now — session 16%"
  // standing over a row that now reads 21% is the screen contradicting itself,
  // which is the whole thing sharing these readings is here to prevent.
  const digest = data?.token_digest;
  useEffect(() => subscribe(digest, (d) => { adopt(d); setResult(null); }),
            [digest, adopt]);

  const settle = useCallback((ticket: number, r: PlanResult | null) => {
    if (!alive.current || ticket !== seq.current) return;
    setBusy(false); setSlow(false); setResult(r);
    if (r?.ok) {
      timers.current.push(window.setTimeout(
        () => { if (alive.current) setResult(null); }, RESULT_MS));
    }
  }, []);

  const load = useCallback((refresh: boolean, quiet = false) => {
    if (!active || !credentialId) return;
    const pressedAt = Date.now();
    const before = latest.current;
    const ticket = ++seq.current;
    const mine = () => alive.current && ticket === seq.current;
    if (!quiet) {
      setBusy(true); setResult(null); setSlow(false);
      timers.current.push(window.setTimeout(
        () => { if (mine()) setSlow(true); }, SLOW_MS));
    }
    get<PlanUsage>(usageUrl(credentialId, refresh))
      .then((d) => {
        if (!alive.current) return;
        // A reading is always worth taking, whoever asked for it — arming a row
        // fires the local read and the press together, and refusing the quick
        // one's data leaves the cell blank for the length of the slow one. The
        // ticket governs what may *speak* for the row, not what may fill it.
        adopt(d);
        if (!mine() || quiet) return;
        if (!refresh) { settle(ticket, null); return; }
        const asked = d.asked;
        if (!asked || asked.state === "fresh") {
          publish(d, adopt);
          settle(ticket, { ok: true, text: changeSentence(before, d) });
          return;
        }
        // The floor is fifteen seconds. Sit through it rather than report it:
        // somebody pressing twice is somebody who wants a number, and "you
        // pressed six seconds ago" is the run-around in politer words.
        if (asked.state === "floored" && !retried.current
            && (asked.retry_after_s ?? 0) * 1000 <= ABSORB_MS) {
          // A sibling row may already have fetched what this press was floored
          // behind, in which case there is nothing left to wait for.
          const ready = freshlyShared(d.token_digest, pressedAt);
          if (ready) {
            adopt(ready);
            settle(ticket, { ok: true, text: changeSentence(before, ready) });
            return;
          }
          retried.current = true;
          timers.current.push(window.setTimeout(
            () => { if (mine()) load(true); },
            (asked.retry_after_s ?? 0) * 1000 + 500));
          return;  // still busy — the press has not finished, it is waiting
        }
        retried.current = false;
        settle(ticket, refusalSentence(asked, d.fetched_at));
      })
      .catch((e: Error) => {
        if (!mine() || quiet) return;
        // The reading this could not replace stays on screen; the failure is a
        // sentence beside it, never a replacement for it.
        if (!latest.current) setError(e.message);
        settle(ticket, { ok: false, text: `✕ Could not reach the server — ${e.message}` });
      });
  }, [active, credentialId, adopt, settle]);

  useEffect(() => {
    if (!active) { setData(null); setError(null); setResult(null); return; }
    load(false);
    const timer = setInterval(() => load(false, true), REREAD_MS);
    return () => clearInterval(timer);
  }, [active, load]);

  return {
    data, error, busy, slow, result, active,
    refresh: () => { retried.current = false; load(true); },
  };
}

/** The answer a press owes, drawn. Success is transient confirmation and clears
    itself; a failure is a standing condition and stays until the next press.

    A rejected token always offers the way out, because naming a dead credential
    and stopping there is the same dead end in a politer voice. On the
    credentials table that is this row's own Edit form; anywhere else — the
    project pages, where the credential is not editable — it is the screen that
    holds it. */
export function PlanResultLine({ result, onRotate }: {
  result: PlanResult; onRotate?: () => void;
}) {
  return (
    <span className={`plan-result-line ${result.ok ? "ok" : "bad"}`}
          role={result.ok ? "status" : "alert"}>
      {result.text}
      {result.rotate && <>{" "}
        {onRotate
          ? <button type="button" className="linkish" onClick={onRotate}>Rotate it</button>
          : <Link to="/settings">Rotate it in Settings ↗</Link>}
      </>}
    </span>
  );
}

/** One button, one name, everywhere. It was three — `ask`, `ask Anthropic` and
    `refresh` — for one action, which is three things to learn about a control
    that does one thing. */
function CheckNow({ busy, slow, onClick, className = "small" }: {
  busy: boolean; slow: boolean; onClick: () => void; className?: string;
}) {
  return (
    <span className="check-now">
      <button type="button" className={className} disabled={busy} onClick={onClick}
              title="Ask Anthropic for this subscription's windows now">
        {busy && <span className="working-dot" aria-hidden="true" />}
        {busy ? "Checking…" : "Check now"}
      </button>
      {/* Ten seconds of a silent spinner is indistinguishable from a hang. */}
      {busy && slow && <span className="help">still waiting on Anthropic…</span>}
    </span>
  );
}

/** Full meter: one bar per window, a verdict on top, provenance underneath.
    Draws nothing for an API-key credential — there is no plan to draw. */
export function PlanMeter({ credentialId, mode, title = "Claude subscription plan", onRotate }: {
  credentialId: string | null; mode: string | null; title?: string; onRotate?: () => void;
}) {
  const { data, error, busy, slow, result, refresh, active } = usePlanUsage(credentialId, mode);
  if (!active) return null;
  // The one control on this page that reaches Anthropic. The server floors it
  // at one call per subscription every fifteen seconds and honours any
  // throttle — and now says which of those happened, so a press that could not
  // ask no longer looks exactly like one that did.
  const check = <CheckNow busy={busy} slow={slow} onClick={refresh} className="small" />;
  let body;
  if (!data && !error) {
    body = <p className="help" style={{ margin: "4px 0" }}>Reading the plan meter…</p>;
  } else if (data && !data.available) {
    body = <p className="help" style={{ margin: "4px 0" }}>
      Plan meter unavailable — {data.reason}
    </p>;
  } else if (error) {
    body = <p className="help" style={{ margin: "4px 0" }}>Plan meter not reachable — {error}.</p>;
  } else if (data) {
    const verdict = planVerdict(data.windows);
    body = (
      <>
        <div className={`plan-verdict ${verdict.cls}`}>{verdict.text}</div>
        {data.windows.map((w) => (
          <div className="plan-window" key={w.key}>
            <div className="plan-row">
              <span>{w.label}</span>
              {/* An empty track and a dash, not 0%: this window did reset, and
                  how full the new one is is a thing only a run can report. */}
              <span className="num">{w.expired ? "—" : `${pct(w.utilization)} used`}</span>
            </div>
            <div className="track">
              {!w.expired && (
                <div className={`fill ${fillClass(w.utilization)}`} style={{ width: `${Math.min(w.utilization, 1) * 100}%` }} />
              )}
            </div>
            <div className="foot">
              {w.expired
                ? `reset ${fmtAgo(w.resets_at)} — the next run reports the new window`
                : w.resets_at ? `resets ${fmtSoon(w.resets_at)}` : " "}
            </div>
          </div>
        ))}
      </>
    );
  }
  return (
    <div className="plan-meter">
      <div className="plan-head">
        <span className="label">{title}</span>
      </div>
      {body}
      {/* The answer to the press, under the thing it is about. */}
      {result && <PlanResultLine result={result} onRotate={onRotate} />}
      <div className="plan-foot">
        <span className="asof">{data?.fetched_at ? `reported ${fmtAgo(data.fetched_at)}` : "not checked yet"}</span>
        {check}
      </div>
      <p className="help" style={{ marginBottom: 0 }}>
        Anthropic's own meter for this subscription — the same numbers as <code>/usage</code> in
        Claude Code. The SDK reports them on every agent run, so the meter moves as this
        credential is used; <strong>Check now</strong> asks Anthropic directly, which is how to
        get a number on a credential that has gone quiet, and it says what came back either
        way. Every project on it draws from the same windows. Shown here, not enforced here:
        the monthly cap is yaaf's separate limit.
      </p>
    </div>
  );
}

/** One line for a table cell: session and week, with resets underneath.

    `auto` decides whether it reads on its own. This was written when every
    reading was a live call to Anthropic, where forty rows meant forty upstream
    calls a minute about thirty-eight keys nothing was running on. The reading
    is a local row now and the arithmetic is gone, but the restraint is kept on
    a second reason that has not changed: the meter on a credential no project
    is running is a number nobody came here to read, and forty of them bury the
    two rows that can fail a run tonight. Those read themselves; the rest wait
    to be asked.

    Every row carries its own **Check now** all the same, because this table is
    where a chairman compares credentials and a row whose runs have stopped has
    no other way to get a number. On an unarmed row it is the only button, and
    one press does both: arming it would otherwise buy "no reading yet" — a row
    nothing has run on is precisely a row with nothing filed — and then ask for
    a second press to do the thing the first one was for. */
export function PlanMeterCompact({ credentialId, mode, auto = true, onRotate, onResult,
                                   checkNonce }: {
  credentialId: string; mode: string; auto?: boolean; checkNonce?: number;
  onRotate?: () => void; onResult?: (r: PlanResult | null) => void;
}) {
  const [armed, setArmed] = useState(auto);
  const { data, error, busy, slow, result, refresh, active } =
    usePlanUsage(armed ? credentialId : null, mode);

  // A press on a row that has not armed has to do both things, and arming is a
  // state change — `refresh` captured before it still sees an inactive hook and
  // does nothing, so the first press on an idle row would only arm it and the
  // reader would have to press again for the thing they pressed for. The press
  // is remembered and fired once the hook is live.
  const [pending, setPending] = useState(false);
  useEffect(() => {
    if (!pending || !active) return;
    setPending(false);
    refresh();
  }, [pending, active, refresh]);
  const press = () => { if (armed) refresh(); else { setArmed(true); setPending(true); } };

  // A value that was just replaced checks itself, so the press that fixed the
  // token is the press that proves it fixed.
  const asked = useRef(checkNonce);
  useEffect(() => {
    if (checkNonce === undefined || checkNonce === asked.current) return;
    asked.current = checkNonce;
    if (armed) refresh(); else { setArmed(true); setPending(true); }
  }, [checkNonce, armed, refresh]);

  // The sentence is lifted to a full-width line under the row: this cell is
  // 152px wide and already wraps, and "Anthropic rejected this token — it may
  // have expired" is not a thing to read four words at a time.
  useEffect(() => { onResult?.(result); }, [result, onResult]);

  if (mode !== "subscription") {
    return <span className="muted" title="API keys are metered per call, in dollars; there is no plan window to fill.">— metered per call</span>;
  }
  // `pending` counts as busy: the press has been made and has not answered yet,
  // and a button that goes back to "Check now" in between invites a second one.
  const check = <CheckNow busy={busy || pending} slow={slow} className="small" onClick={press} />;
  if (!active) {
    // Never a zero and never an empty bar: nobody has asked yet, and that is a
    // different thing from a plan with nothing left on it.
    return <span className="plan-compact"><span className="help">not checked yet</span>
      <span>{check}</span></span>;
  }
  if (!data && !error) {
    return <span className="plan-compact">
      <span className="help">reading…</span><span>{check}</span></span>;
  }

  // A failed press never costs the reading it could not replace. This used to
  // test `error` first and put an error message where the bars had been, which
  // is the one thing the design note in `plan_probe` says not to do.
  const shown = data?.available
    ? live(data.windows).filter((w) => w.key === "five_hour" || w.key === "seven_day")
    : [];
  if (!shown.length) {
    // Short in the cell, whole on hover, and explained at full width by the
    // line a press leaves under the row. The server's reason is a sentence
    // written for a reader; four words at a time in a 152px column is not
    // reading it, and printing it here as well as in the result line says the
    // same thing twice on one screen.
    return (
      <span className="plan-compact">
        <span className="help" title={data?.reason ?? error ?? undefined}>no reading yet</span>
        <span>{check}</span>
      </span>
    );
  }
  const full = shown.find((w) => w.utilization >= 1);
  return (
    <span className="plan-compact">
      <span>
        {shown.map((w) => (
          // Label above the bar, not beside it: an 88px track and "session 16%"
          // are 164px of content in a 152px column, and side by side they
          // overflowed under the Actions buttons and lost the number.
          <span className="plan-mini" key={w.key} title={`${w.label}: ${pct(w.utilization)} used${w.resets_at ? `, resets ${fmtSoon(w.resets_at)}` : ""}`}>
            <span className="plan-mini-label">{shortLabel(w)} <b>{pct(w.utilization)}</b></span>
            <span className={`mini-meter ${fillClass(w.utilization)}`}><i style={{ width: `${Math.min(w.utilization, 1) * 100}%` }} /></span>
          </span>
        ))}
      </span>
      {full ? (
        <span className="plan-verdict critical" style={{ margin: 0 }}>
          runs on this credential fail until the {shortLabel(full)} window resets{full.resets_at ? ` ${fmtSoon(full.resets_at)}` : ""}
        </span>
      ) : (
        <span className="help">
          {shown.filter((w) => w.resets_at).map((w) => `${shortLabel(w)} resets ${fmtSoon(w.resets_at)}`).join(" · ") || " "}
        </span>
      )}
      <span className="plan-asof">reported {fmtAgo(data!.fetched_at)}</span>
      <span>{check}</span>
    </span>
  );
}
