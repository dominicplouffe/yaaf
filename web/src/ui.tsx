// Shared attention-UI pieces for the Desk and the project tabs: countdown
// formatting, inline note capture (this app never uses window.prompt), the
// approve/deny pair, and CSV export. Kept deliberately small — these are the
// primitives the redesign repeats everywhere so the pattern is learned once.

import { useState } from "react";
import { post } from "./api";

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

/** The approve/deny pair, identical wherever an approval appears (Desk, Today,
    Boardroom rail, Approvals tab) — a view of one decision, not a second system. */
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
