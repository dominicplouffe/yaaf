/** Undo for decisions: a few seconds before a decision is sent.

    A decision that has reached the server cannot honestly be taken back — a
    purchase was cleared, a dial raised, the CEO woken to act on it. So undo
    is the delay, not a reversal: Approve and Decline are held here for
    UNDO_SECONDS, the row leaves every list at once, and Undo puts it back
    untouched. Only when the window closes is the decision sent. A page that
    closes with one still held sends it on the way out, so leaving is never a
    silent "never mind". */
import { useEffect, useState, useSyncExternalStore } from "react";
import { post } from "./api";

export const UNDO_SECONDS = 8;
/** After sending, how long the row stays hidden while the next desk poll
    catches up, so it does not flash back for a moment. */
const SETTLE_MS = 20_000;

interface Held {
  key: string;
  label: string;
  path: string;
  body: unknown;
  after?: (result: unknown) => void;
  due: number;
  timer: number;
}

const held = new Map<string, Held>();
const settled = new Map<string, number>();
const failures: { key: string; text: string }[] = [];
const listeners = new Set<() => void>();
let snapshot = { keys: new Set<string>(), held: [] as Held[], failures: [] as typeof failures };

function emit() {
  const now = Date.now();
  for (const [key, until] of settled) if (until <= now) settled.delete(key);
  snapshot = {
    keys: new Set([...held.keys(), ...settled.keys()]),
    held: [...held.values()],
    failures: [...failures],
  };
  listeners.forEach((fn) => fn());
}

function subscribe(fn: () => void) {
  listeners.add(fn);
  return () => { listeners.delete(fn); };
}

async function send(key: string) {
  const entry = held.get(key);
  if (!entry) return;
  window.clearTimeout(entry.timer);
  held.delete(key);
  settled.set(key, Date.now() + SETTLE_MS);
  emit();
  try {
    const result = await post(entry.path, entry.body);
    entry.after?.(result);
  } catch (e) {
    settled.delete(key);
    failures.push({ key, text: `${entry.label} was not sent: ${(e as Error).message}` });
  }
  emit();
  window.setTimeout(emit, SETTLE_MS + 50);
}

/** Hold a decision for UNDO_SECONDS, then send it. */
export function holdDecision(d: { key: string; label: string; path: string; body: unknown;
  after?: (result: unknown) => void }) {
  if (held.has(d.key)) return;
  const timer = window.setTimeout(() => { void send(d.key); }, UNDO_SECONDS * 1000);
  held.set(d.key, { ...d, due: Date.now() + UNDO_SECONDS * 1000, timer });
  emit();
}

export function undo(key: string) {
  const entry = held.get(key);
  if (!entry) return;
  window.clearTimeout(entry.timer);
  held.delete(key);
  emit();
}

/** Keys of decisions held or just sent: every list leaves these out. */
export function useHeldKeys(): Set<string> {
  return useSyncExternalStore(subscribe, () => snapshot.keys);
}

// Leaving the page sends what is held; `keepalive` lets it outlive the page.
if (typeof window !== "undefined") {
  window.addEventListener("pagehide", () => {
    for (const entry of held.values()) {
      window.clearTimeout(entry.timer);
      void fetch(entry.path, {
        method: "POST", keepalive: true, credentials: "same-origin",
        headers: { "Content-Type": "application/json" }, body: JSON.stringify(entry.body),
      });
    }
    held.clear();
  });
}

/** The toasts: one per held decision, with its countdown and Undo. */
export function UndoHost() {
  const state = useSyncExternalStore(subscribe, () => snapshot);
  const [, tick] = useState(0);
  useEffect(() => {
    if (!state.held.length) return;
    const id = window.setInterval(() => tick((n) => n + 1), 500);
    return () => window.clearInterval(id);
  }, [state.held.length]);
  if (!state.held.length && !state.failures.length) return null;
  return (
    <div className="undo-host" role="status" aria-live="polite">
      {state.held.map((h) => (
        <div className="undo-toast" key={h.key}>
          <span className="grow">{h.label}</span>
          <span className="help">sending in {Math.max(0, Math.ceil((h.due - Date.now()) / 1000))}s</span>
          <button className="small" onClick={() => undo(h.key)}>Undo</button>
        </div>
      ))}
      {state.failures.map((f, i) => (
        <div className="undo-toast bad" key={`${f.key}-${i}`}>
          <span className="grow">{f.text}</span>
          <button className="small" onClick={() => { failures.splice(i, 1); emit(); }}>Dismiss</button>
        </div>
      ))}
    </div>
  );
}
