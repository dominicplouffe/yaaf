// Shared attention-UI pieces for the Desk and the project tabs: countdown
// formatting, inline note capture (this app never uses window.prompt), the
// approve/deny pair, and CSV export. Kept deliberately small — these are the
// primitives the redesign repeats everywhere so the pattern is learned once.

import { useEffect, useState } from "react";
import { Activity, Dashboard, get, post, RunRow } from "./api";

// ── time ────────────────────────────────────────────────────────────────────────

export function hoursUntil(iso: string): number {
  return (new Date(iso).getTime() - Date.now()) / 3_600_000;
}

export function hoursSince(iso: string): number {
  return -hoursUntil(iso);
}

/** "41h left" / "3d left" / "expired" — countdowns always name their unit. */
export function fmtLeft(iso: string): string {
  const h = hoursUntil(iso);
  if (h <= 0) return "expired";
  if (h < 48) return `${Math.max(1, Math.round(h))}h left`;
  return `${Math.round(h / 24)}d left`;
}

/** "in 12 min" / "in under a minute" / "any moment now" — a minute-scale
    countdown. fmtLeft rounds to whole hours, which turns every sub-hour wait
    into "1h left" and makes a throttle that clears in four minutes look like a
    wall. Anything the reader is expected to wait out needs a number that moves. */
export function fmtSoon(iso: string | null): string {
  if (!iso) return "shortly";
  const m = (new Date(iso).getTime() - Date.now()) / 60_000;
  if (m <= 0) return "any moment now";
  if (m < 1) return "in under a minute";
  if (m < 90) return `in ${Math.round(m)} min`;
  return `in ${Math.round(m / 60)}h`;
}

/** "2m ago" / "3h ago" / "5d ago" for scanning; pair with fmtWhen for the record. */
export function fmtAgo(iso: string | null): string {
  if (!iso) return "—";
  const m = (Date.now() - new Date(iso).getTime()) / 60_000;
  if (m < 1) return "just now";
  if (m < 60) return `${Math.round(m)}m ago`;
  if (m < 48 * 60) return `${Math.round(m / 60)}h ago`;
  return `${Math.round(m / (24 * 60))}d ago`;
}

/** Urgency class for an expiry: serious under 12h, warn under 48h. */
export function clockClass(iso: string): string {
  const h = hoursUntil(iso);
  return h <= 12 ? "serious" : h <= 48 ? "warn" : "muted";
}

/** Kill-date chip for a hypothesis: calm far out, warn inside a week. */
export function killChip(iso: string, status: string) {
  if (status !== "active") {
    return <span className="chip serious">expired {fmtWhenShort(iso)}</span>;
  }
  const h = hoursUntil(iso);
  const cls = h <= 7 * 24 ? "warn" : "ok";
  return (
    <span className={`chip ${cls}`}>
      kill {fmtWhenShort(iso)} · {fmtLeft(iso)}
    </span>
  );
}

export function fmtWhenShort(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

// ── provenance ──────────────────────────────────────────────────────────────────

/** Every screen says when its numbers are from. A reader who reconciles against
    their own records can't trust a figure that won't name its own timestamp,
    and a stale number that says so beats a fresh-looking one that lies. */
export function AsOf({ at, error }: { at: Date | null; error?: string | null }) {
  if (!at) return null;
  return (
    <div className="asof-foot">
      <span className="asof">
        as of {at.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" })}
      </span>
      {error ? (
        <span className="chip serious">not refreshing — {error}</span>
      ) : (
        <>
          <span className="working-dot" aria-hidden="true" />
          <span className="asof">live</span>
        </>
      )}
    </div>
  );
}

/** Placeholder rows that hold the layout still while the first poll lands.
    A jumping page reads as broken; a still one reads as loading. */
export function Skeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div className="wbox">
      <div className="skel-rows" aria-busy="true" aria-label="Loading">
        {Array.from({ length: rows }, (_, i) => (
          <div key={i} className="skel" style={{ width: `${88 - i * 11}%` }} />
        ))}
      </div>
    </div>
  );
}

// ── budget pace ─────────────────────────────────────────────────────────────────

/** At the current month-to-date rate, when does spend hit the cap? Null when
    it lands after the month ends (or there's no spend yet) — the calm case. */
export function paceDate(spent: number, cap: number): Date | null {
  if (spent <= 0 || cap <= 0 || spent >= cap) return null;
  const now = new Date();
  const rate = spent / now.getDate();
  const daysToCap = (cap - spent) / rate;
  const capDay = new Date(now.getTime() + daysToCap * 86_400_000);
  const monthEnd = new Date(now.getFullYear(), now.getMonth() + 1, 0, 23, 59);
  return capDay <= monthEnd ? capDay : null;
}

// ── is it working? ──────────────────────────────────────────────────────────────

/** A run that stopped without finishing. Each of these was paid for and
    produced nothing, which is why they are counted apart from failures of the
    work itself. */
const ENDED_EARLY: Record<string, string> = {
  reaped: "timed out",
  killed: "killed for looping",
  failed: "failed outright",
  terminated_refusals: "hit the refusal cap",
};

/** The day's run outcomes. A third of runs ending early is the single loudest
    signal that something is in the way, and it used to be a grey aside. */
export function runHealth(runs: RunRow[]) {
  const today = new Date().toDateString();
  const todays = runs.filter(
    (r) => r.started_at && new Date(r.started_at).toDateString() === today,
  );
  const clean = todays.filter((r) => r.status === "done").length;
  // The resident chat session is presence, not work — the project bar has
  // excluded it from "Working" since day one, and a tile that disagreed was
  // how one live session read as an agent stuck for four hours.
  const running = todays.filter(
    (r) => r.status === "running" && r.kind !== "chat_session",
  ).length;
  const early = todays.filter((r) => r.status in ENDED_EARLY);
  const usd = todays.reduce((sum, r) => sum + (r.cost_usd ?? 0), 0);
  const tokens = todays.reduce(
    (sum, r) => sum + r.tokens_in + r.tokens_out + r.cache_creation + r.cache_read,
    0,
  );

  // Ordered by count so the sentence leads with the dominant failure mode.
  const byReason = Object.entries(ENDED_EARLY)
    .map(([status, phrase]) => ({
      phrase,
      count: early.filter((r) => r.status === status).length,
    }))
    .filter((r) => r.count > 0)
    .sort((a, b) => b.count - a.count);

  const ratio = todays.length > 0 ? early.length / todays.length : 0;
  return { todays, clean, running, early, byReason, usd, tokens, ratio };
}

/** The breakdown as a sentence, because "12 failures" tells nobody what to do. */
export function earlyRunSentence(h: ReturnType<typeof runHealth>): string {
  if (h.early.length === 0) return "";
  const parts = h.byReason.map((r) => `${r.count} ${r.phrase}`);
  const list =
    parts.length === 1 ? parts[0] : `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}`;
  return `${h.early.length} of ${h.todays.length} runs ended before finishing — ${list}.`;
}

/** Why the company is quiet, in one sentence.

    "Working now: 0" used to read "an idle project costs nothing" whatever the
    cause, so a company that had spent its whole daily allowance by 01:22 and a
    company with an empty board looked identical. They are not the same thing:
    one needs nothing from you, the other has work piled up behind a wall and a
    known time it comes down. */
export function idleSentence(a: Activity): string {
  const waiting: string[] = [];
  if (a.waiting.ready_tasks) waiting.push(`${a.waiting.ready_tasks} task(s) ready`);
  if (a.waiting.notices) waiting.push(`${a.waiting.notices} notice(s) undelivered`);
  const behind = waiting.length ? ` — ${waiting.join(", ")}` : "";

  switch (a.blocked_by) {
    case "daily_runs":
      return `out of runs for today (${a.daily_runs.used} of ${a.daily_runs.cap})` +
        `, resumes ${fmtLeft(a.daily_runs.resets_at).replace(" left", "")} from now${behind}`;
    case "wake_ceiling":
      return `every agent with work has hit its hourly wake ceiling, clearing ` +
        `${fmtSoon(a.wake_ceiling.frees_at)}${behind}`;
    case "concurrency":
      return `at the concurrency cap (${a.concurrency.in_flight} of ${a.concurrency.cap}) — working`;
    case "agents_busy":
      return "every agent that has work is already running";
    default:
      return waiting.length
        ? `nothing blocking — a run should start shortly${behind}`
        : "nothing waiting; an idle project costs nothing";
  }
}

/** The reasons the platform writes for itself when it pauses a project. A
    chairman-typed reason is anything else — and the two defaults below mean
    nobody typed one at all, which the brief says out loud rather than
    presenting "paused by chairman" as an explanation. */
const UNSPOKEN = new Set(["paused by chairman", "stopped by chairman"]);

/** Why this company is not working, in one brief, whatever the cause.

    A project stops for four different reasons — the chairman paused it, the
    chairman stopped it, it spent its budget, or its billing credential died —
    and until now each one surfaced somewhere different, or (for a cap) nowhere
    at all. One reader asking one question deserves one answer in one place. */
export interface StatusBrief {
  tone: string;
  chip: string;
  headline: string;
  detail: string;
  /** Set on the briefs that are waiting out a clock, and only those. `sig`
      names the condition and `clearsAt` is the moment the platform expects it
      gone — together they let a reader put the banner away, and bring it back
      by itself if the wait outlives its own promise. Paused and stopped carry
      neither: those are not waits, and dismissing them would hide the reason
      the company is not running. */
  sig?: string;
  clearsAt?: string | null;
}

export function statusBrief(dash: Dashboard): StatusBrief | null {
  const reason =
    dash.status_reason && !UNSPOKEN.has(dash.status_reason)
      ? dash.status_reason
      : "no reason was recorded";

  if (dash.status === "stopped")
    return {
      tone: "critical",
      chip: "stopped",
      headline: `Stopped — ${reason}.`,
      detail:
        "This is terminal: the record stays readable, but the project can never " +
        "run again. Duplicate it from Settings to carry the work forward.",
    };

  if (dash.status === "paused")
    return {
      tone: "serious",
      chip: "paused",
      headline: `Paused — ${reason}.`,
      detail:
        "Nothing new starts until you resume. Anything that was running was " +
        "stopped when the project paused; the board and the record are intact.",
    };

  if (dash.status !== "running") return null;

  const a = dash.activity;
  const waiting: string[] = [];
  if (a.waiting.ready_tasks) waiting.push(`${a.waiting.ready_tasks} ready task(s)`);
  if (a.waiting.claimed_tasks) waiting.push(`${a.waiting.claimed_tasks} claimed task(s)`);
  if (a.waiting.notices) waiting.push(`${a.waiting.notices} undelivered notice(s)`);
  const behind = waiting.length
    ? `${waiting.join(", ")} waiting behind it.`
    : "Nothing is waiting behind it.";

  if (a.blocked_by === "daily_runs")
    return {
      tone: "serious",
      chip: "capped",
      headline: `Out of runs for today — ${a.daily_runs.used} of ${a.daily_runs.cap} used.`,
      detail:
        `Nothing starts until the count resets at 00:00 UTC, ` +
        `${fmtLeft(a.daily_runs.resets_at).replace(" left", "")} from now. ${behind} ` +
        "Runs that died still spent the allowance. Raise 'Runs per day, whole " +
        "project' in Settings if this is tighter than you meant it to be.",
      sig: "daily_runs",
      clearsAt: a.daily_runs.resets_at,
    };

  // The ceiling is a trailing hour per agent, not a clock hour, so "resumes as
  // the hour rolls forward" named no moment anyone could check — and a warning
  // with nothing falsifiable in it reads as a fault that will never go away.
  if (a.blocked_by === "wake_ceiling")
    return {
      tone: "serious",
      chip: "throttled",
      headline:
        `Every agent with work has hit its hourly wake ceiling ` +
        `(${a.wake_ceiling.cap}/hour).`,
      detail:
        `The oldest wake ages out ${fmtSoon(a.wake_ceiling.frees_at)} and work ` +
        `starts again on its own — nothing here needs you. ${behind} ` +
        "Raise 'Wakes per agent per hour' in Settings only if the cap is tighter " +
        "than you meant it to be.",
      sig: "wake_ceiling",
      clearsAt: a.wake_ceiling.frees_at,
    };

  // concurrency and agents_busy both mean the company is working. Saying
  // nothing is the correct answer; a brief that fires when things are fine is
  // how a reader learns to stop reading it.
  return null;
}

/** Where the money stands, in whichever unit this project actually bills in.
    Read by both the project bar and the budget meter so they can never disagree. */
export function budgetOf(dash: Dashboard) {
  const usdMode = dash.billing_mode === "api_key";
  const unit = usdMode ? ("usd" as const) : ("tokens" as const);
  const spent = usdMode
    ? dash.month_to_date.usd
    : dash.month_to_date.tokens_in + dash.month_to_date.tokens_out +
      dash.month_to_date.cache_creation + dash.month_to_date.cache_read;
  const cap = usdMode ? dash.caps.monthly_usd_cap : dash.caps.monthly_token_cap;
  const warnRatio = Number(dash.constitution["budget_warn_ratio"] ?? 0.8);
  const frac = cap > 0 ? spent / cap : 0;
  return { usdMode, unit, spent, cap, warnRatio, frac };
}

export function fmtUnit(n: number, unit: "usd" | "tokens"): string {
  if (unit === "usd") return `$${n.toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
  // Token budgets run to hundreds of millions — compact or they're unreadable.
  if (n >= 10_000) {
    return new Intl.NumberFormat(undefined, {
      notation: "compact",
      maximumFractionDigits: 1,
    }).format(n);
  }
  return n.toLocaleString();
}

/** CEOs write paragraph-long bet statements; attention rows get the first
    clause, the full text lives where the bet does. */
export function clip(s: string, max = 150): string {
  if (s.length <= max) return s;
  return s.slice(0, max).replace(/\s+\S*$/, "") + "…";
}

// ── secrets: masked to the eye, never withheld from the chairman ────────────────
//
// There is one human on this system and they hold the master token. Hiding a
// value they could read out of Postgres protects nothing and costs a debugging
// session every time something will not connect. So: password-typed by default,
// one press from readable. What a *role* receives is a separate question,
// decided by grants at lease time, and none of this touches it.

/** The icon names the action, not the state — a slashed eye means "hide this",
    so you never have to remember which way the same glyph was pointing. */
function EyeIcon({ hiding }: { hiding: boolean }) {
  return (
    <svg
      width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"
    >
      <path d="M1.5 12S5.5 4.5 12 4.5 22.5 12 22.5 12 18.5 19.5 12 19.5 1.5 12 1.5 12z" />
      <circle cx="12" cy="12" r="3.2" />
      {hiding && <line x1="3.5" y1="20.5" x2="20.5" y2="3.5" />}
    </svg>
  );
}

function EyeButton({ shown, onClick, busy }: {
  shown: boolean; onClick: () => void; busy?: boolean;
}) {
  return (
    <button
      type="button"
      className="small eye"
      disabled={busy}
      title={shown ? "hide" : "show"}
      aria-label={shown ? "Hide value" : "Show value"}
      onClick={onClick}
    >
      <EyeIcon hiding={shown} />
    </button>
  );
}

/** A value the chairman is *typing*: password-typed with an eye, so a mispaste
    is visible before it is saved rather than after an agent run fails on it. */
export function SecretInput({
  value, onChange, placeholder, defaultShown = false, disabled, readOnly, autoFocus,
}: {
  value: string;
  onChange?: (value: string) => void;
  placeholder?: string;
  /** An address is not a password: showing it is how a typo gets seen. */
  defaultShown?: boolean;
  disabled?: boolean;
  /** For a value that is already stored and simply needs reading back. */
  readOnly?: boolean;
  autoFocus?: boolean;
}) {
  const [shown, setShown] = useState(defaultShown);
  return (
    <div className="secret-field">
      <input
        type={shown ? "text" : "password"}
        className={shown && readOnly ? "mono" : undefined}
        value={value}
        placeholder={placeholder}
        disabled={disabled}
        readOnly={readOnly}
        autoFocus={autoFocus}
        onChange={(e) => onChange?.(e.target.value)}
      />
      <EyeButton shown={shown} onClick={() => setShown(!shown)} />
    </div>
  );
}

/** A value already *stored*: masked until asked for, then fetched (one press,
    one call, one audit row), copyable, and — when `onRotate` is given —
    editable in place, which is the whole "how do I change this?" answer. */
export function StoredSecret({ secretId, onRotate, note }: {
  secretId: string;
  onRotate?: (value: string) => Promise<void>;
  note?: string;
}) {
  const [value, setValue] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  async function toggle() {
    if (value !== null) {
      setValue(null);
      setDraft("");
      setError(null);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const got = await get<{ value: string }>(`/admin/secrets/${secretId}/value`);
      setValue(got.value);
      setDraft(got.value);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function save() {
    if (!onRotate) return;
    setBusy(true);
    setError(null);
    try {
      await onRotate(draft);
      setValue(draft);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="secret-field-stack">
      <div className="secret-field">
        {value === null ? (
          <input type="password" value="••••••••••••" readOnly tabIndex={-1} />
        ) : (
          <input
            className="mono"
            value={draft}
            readOnly={!onRotate}
            onChange={(e) => setDraft(e.target.value)}
          />
        )}
        <EyeButton shown={value !== null} busy={busy} onClick={toggle} />
        {value !== null && (
          <button
            type="button"
            className="small"
            onClick={() => {
              navigator.clipboard.writeText(draft).then(
                () => { setCopied(true); setTimeout(() => setCopied(false), 1200); },
                () => setError("the browser refused clipboard access"),
              );
            }}
          >
            {copied ? "copied" : "copy"}
          </button>
        )}
        {value !== null && onRotate && draft !== value && (
          <button type="button" className="small primary" disabled={busy} onClick={save}>
            save
          </button>
        )}
      </div>
      {error && <div className="help" style={{ color: "var(--critical)" }}>{error}</div>}
      {note && value !== null && <div className="help">{note}</div>}
    </div>
  );
}

// ── inline note capture (this app never window.prompt()s) ───────────────────────

export function NoteAction({
  label,
  className,
  placeholder,
  confirmLabel,
  requireNote = false,
  onSubmit,
}: {
  label: string;
  className?: string;
  placeholder: string;
  confirmLabel?: string;
  requireNote?: boolean;
  onSubmit: (note: string) => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function go() {
    if (requireNote && !note.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await onSubmit(note.trim());
      setOpen(false);
      setNote("");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (!open) {
    return (
      <button className={className} onClick={() => setOpen(true)}>
        {label}
      </button>
    );
  }
  return (
    <span className="note-action">
      <input
        autoFocus
        value={note}
        placeholder={placeholder}
        onChange={(e) => setNote(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") go();
          if (e.key === "Escape") {
            setOpen(false);
            setNote("");
          }
        }}
      />
      <button className={className} disabled={busy || (requireNote && !note.trim())} onClick={go}>
        {confirmLabel ?? label}
      </button>
      <button className="small" onClick={() => { setOpen(false); setNote(""); }}>
        cancel
      </button>
      {error && <span className="help" style={{ color: "var(--critical)" }}>{error}</span>}
    </span>
  );
}

/** The approve/deny pair, identical wherever an approval appears (Desk,
    Overview, Boardroom rail, Records → Decisions) — a view of one decision,
    not a second system. */
export function DecideButtons({ approvalId, onDone }: { approvalId: string; onDone: () => void }) {
  return (
    <>
      <NoteAction
        label="Approve…"
        className="small primary"
        placeholder="Note (optional) — the CEO reads it"
        confirmLabel="✓ Approve"
        onSubmit={async (note) => {
          await post(`/admin/approvals/${approvalId}/decide`, { approve: true, note });
          onDone();
        }}
      />
      <NoteAction
        label="Deny…"
        className="small danger"
        placeholder="Why? — the CEO reads this and replans"
        confirmLabel="✕ Deny"
        requireNote
        onSubmit={async (note) => {
          await post(`/admin/approvals/${approvalId}/decide`, { approve: false, note });
          onDone();
        }}
      />
    </>
  );
}

// ── CSV export: trust is letting the data leave ─────────────────────────────────

export function downloadCsv(
  filename: string,
  header: string[],
  rows: (string | number | null | undefined)[][],
) {
  const esc = (v: string | number | null | undefined) => {
    const s = v === null || v === undefined ? "" : String(v);
    return /[",\n]/.test(s) ? `"${s.replaceAll('"', '""')}"` : s;
  };
  const csv = [header, ...rows].map((r) => r.map(esc).join(",")).join("\n");
  const url = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export function ExportCsv({ onClick }: { onClick: () => void }) {
  return (
    <button className="small" style={{ marginLeft: "auto" }} onClick={onClick}>
      ↓ export CSV
    </button>
  );
}

/** A blocking confirm dialog for changes that land the moment you press the
    button. `window.confirm` is fine for "delete this row?"; it is not fine for
    a decision that needs the consequences laid out beside it, and it cannot
    show a table of what is about to change. Escape and the backdrop both
    cancel, so the safe exit is the reflexive one. */
export function ConfirmModal({
  title, confirmLabel, danger, busy, onConfirm, onCancel, children,
}: {
  title: string;
  confirmLabel: string;
  danger?: boolean;
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
  children: React.ReactNode;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCancel();
    };
    window.addEventListener("keydown", onKey);
    // The page behind a modal must not scroll under it.
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = previous;
    };
  }, [onCancel]);

  return (
    <div className="modal-back" onClick={onCancel}>
      <div
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onClick={(e) => e.stopPropagation()}
      >
        <h3>{title}</h3>
        <div className="modal-body">{children}</div>
        <div className="actions" style={{ marginBottom: 0 }}>
          <button className={danger ? "danger" : "primary"} disabled={busy} onClick={onConfirm}>
            {busy ? "Saving…" : confirmLabel}
          </button>
          <button className="small" disabled={busy} onClick={onCancel}>
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}
