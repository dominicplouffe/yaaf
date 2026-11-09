// The Settings kit: the few patterns every section of app-wide Settings is
// built from, so a ninth section inherits the page's behaviour instead of
// inventing its own.
//
// The page used to be eight forms built one at a time: four ways to save, six
// browser confirm() dialogs, a green "connected" for a token that was only
// saved, and instructions living inside field labels. Each pattern here
// replaces one of those:
//
// - SectionHead: a title, one line on what the section is for, the state, and
//   at most one primary action. No page-wide subtitle above it.
// - Seals: a badge says what yaaf will do; a seal says how we know. Answered,
//   refused, or not yet, with when. Never green without an answer on record.
// - SecretRow: "Saved · Replace", never an empty box that means "keep".
// - SaveBar: a form saves once, and says when there is nothing to save.
// - Toggle: a switch that saves at once (its caller holds it for Undo).
// - NeedRow: one thing that needs you, its consequence, and its fix.

import { ReactNode, useEffect, useState } from "react";
import { fmtAgo } from "../ui";
import { Icon, IconName } from "../ui/Icon";
import type { Evidence } from "../readiness";
import "../ui/signal.css";
import "./settings.css";

export function SectionHead({ title, lede, actions, children }: {
  title: string;
  lede?: ReactNode;
  actions?: ReactNode;
  /** The state line, under the lede. */
  children?: ReactNode;
}) {
  return (
    <header className="set-head">
      <div className="set-head-row">
        <div className="set-head-text">
          <h2>{title}</h2>
          {lede && <p className="set-lede">{lede}</p>}
        </div>
        {actions && <div className="set-head-acts">{actions}</div>}
      </div>
      {children && <div className="set-state">{children}</div>}
    </header>
  );
}

/** One rung of a section's ladder: what the saved settings make possible, in
    order, each one working or not yet — the state before the form. */
export function Step({ done, n, title, children }: {
  done: boolean; n: number; title: string; children: React.ReactNode;
}) {
  return <div className="ladder-step">
    <span className={`ladder-node ${done ? "ok" : "todo"}`} aria-hidden="true">
      {done ? <Icon name="check" size={13} strokeWidth={2.6} /> : n}</span>
    <span className="t">{title}<span className="sr-only">{done ? " — working" : " — not yet"}</span></span>
    <div className="d">{children}</div>
  </div>;
}

/** A labelled group inside a section. */
export function Group({ title, count, children, aside, chip }: {
  title: string; count?: ReactNode; children: ReactNode; aside?: ReactNode;
  /** The filled count of things that are yours to fix, beside the title. */
  chip?: number;
}) {
  return (
    <section className="set-group" aria-label={title}>
      <h3 className="set-group-h">{title}{count !== undefined && <span className="c"> · {count}</span>}
        {chip ? <span className="badge you-count" aria-label={`${chip} need you`}>{chip}</span> : null}
        {aside && <span className="set-group-aside">{aside}</span>}</h3>
      {children}
    </section>
  );
}

const VIA: Record<string, string> = {
  test: "a Test", model_list: "its model list", check: "a graded check", run: "a run",
};

/** How we know a key works: the newest answer from the provider, a refusal of
    the key itself, or nothing yet. `long` adds what answered. */
export function EvidenceSeal({ evidence, long }: { evidence?: Evidence | null; long?: boolean }) {
  if (!evidence || evidence.state === "never") {
    return <span className="sg-seal word" title="Nothing has answered on this key since it was last changed">Never answered</span>;
  }
  const when = evidence.at ? fmtAgo(evidence.at) : "";
  const how = evidence.via === "run" && evidence.project ? `a run on ${evidence.project}`
    : VIA[evidence.via ?? ""] ?? "";
  if (evidence.state === "refused") {
    return <span className="sg-seal bad" title={evidence.detail ?? undefined}>
      <Icon name="x" size={12} strokeWidth={2.4} />Refused the key · {when}
      {long && how && <span className="sg-seal-x"> · {how}</span>}</span>;
  }
  return <span className="sg-seal verified" title={evidence.detail ?? undefined}>
    <Icon name="check" size={12} strokeWidth={2.4} />Answered {when}
    {long && how && <span className="sg-seal-x"> · {how}</span>}</span>;
}

export interface LastTest { ok: boolean; at: string; detail: string }

/** The last test of a connection that is not an LLM key (Slack, web search). */
export function TestSeal({ test }: { test?: LastTest | null }) {
  if (!test) return <span className="sg-seal word">Not tested</span>;
  return test.ok
    ? <span className="sg-seal verified" title={test.detail}><Icon name="check" size={12} strokeWidth={2.4} />Tested {fmtAgo(test.at)}</span>
    : <span className="sg-seal bad" title={test.detail}><Icon name="x" size={12} strokeWidth={2.4} />Test failed · {fmtAgo(test.at)}</span>;
}

/** A stored secret, said as its state: saved, or not set, and the one verb. */
export function SecretRow({ label, hint, saved, unlocks, value, onChange, open, setOpen, placeholder,
                            disabled, id }: {
  label: string;
  hint?: ReactNode;
  saved: boolean;
  /** What setting it would make possible, said while it is not set. */
  unlocks?: string;
  value: string;
  onChange: (v: string) => void;
  open: boolean;
  setOpen: (open: boolean) => void;
  placeholder?: string;
  disabled?: boolean;
  id: string;
}) {
  return (
    <div className={`set-row${open ? " editing" : ""}`}>
      <label className="set-row-lab" htmlFor={id}>{label}{hint && <span className="set-row-hint">{hint}</span>}</label>
      <div className="set-row-val">
        {open
          ? <input id={id} type="password" autoComplete="off" value={value} disabled={disabled}
              placeholder={placeholder} onChange={(e) => onChange(e.target.value)} autoFocus />
          : saved
            ? <span className="set-row-state"><span className="good-ink"><Icon name="check" size={14} strokeWidth={2.2} /></span>Saved · encrypted, never shown again</span>
            : <span className="set-row-state"><span className="dot hollow" />Not set{unlocks ? ` · ${unlocks}` : ""}</span>}
      </div>
      <div className="set-row-act">
        {open
          ? <button type="button" className="small" disabled={disabled}
              onClick={() => { onChange(""); setOpen(false); }}>Cancel</button>
          : <button type="button" className={`small${saved ? "" : " primary"}`} disabled={disabled}
              onClick={() => setOpen(true)}>{saved ? "Replace" : "Add"}</button>}
      </div>
    </div>
  );
}

/** A plain setting on a row: its label, its current value said as a sentence,
    and Change — which opens the editor in place. */
export function DefRow({ label, value, editing, onEdit, editor, actions }: {
  label: string;
  value: ReactNode;
  editing: boolean;
  onEdit: () => void;
  editor: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className={`set-row${editing ? " editing" : ""}`}>
      <span className="set-row-lab">{label}</span>
      <div className="set-row-val">{editing ? editor : value}</div>
      <div className="set-row-act">{editing ? actions
        : <button type="button" className="small" onClick={onEdit}>Change</button>}</div>
    </div>
  );
}

/** A switch. It says what it is on its own line, and what it means under it. */
export function Toggle({ checked, onChange, label, sub, disabled, id }: {
  checked: boolean;
  onChange: (next: boolean) => void;
  label: string;
  sub?: ReactNode;
  disabled?: boolean;
  id: string;
}) {
  return (
    <div className="set-toggle">
      <button type="button" role="switch" id={id} aria-checked={checked} disabled={disabled}
        className={`switch${checked ? " on" : ""}`} onClick={() => onChange(!checked)}>
        <span className="sr-only">{label}</span>
      </button>
      <label htmlFor={id} className="set-toggle-text">
        <span className="set-toggle-label">{label}</span>
        {sub && <span className="set-toggle-sub">{sub}</span>}
      </label>
    </div>
  );
}

/** The one way a form in Settings saves: one button, there when something
    changed, and the quiet way out (Disconnect, Remove) at the far end. */
export function SaveBar({ dirty, busy, onSave, label = "Save changes", danger }: {
  dirty: boolean;
  busy?: boolean;
  onSave: () => void;
  label?: string;
  danger?: ReactNode;
}) {
  return (
    <div className="set-savebar">
      <button type="button" className="primary" disabled={!dirty || busy} onClick={onSave}>
        {busy ? "Saving…" : label}</button>
      {!dirty && !busy && <span className="help">Nothing changed</span>}
      {danger && <span className="set-savebar-danger">{danger}</span>}
    </div>
  );
}

/** One thing that needs you: what it is, what happens if you leave it, and
    the button that fixes it. */
export function NeedRow({ tone, icon, title, children, facts, actions }: {
  tone: "critical" | "warn" | "neutral";
  icon: IconName;
  title: ReactNode;
  children?: ReactNode;
  facts?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className={`set-need t-${tone}`}>
      <span className={`set-need-ico ${tone}`}><Icon name={icon} size={16} strokeWidth={2} /></span>
      <div className="set-need-body">
        <div className="set-need-t">{title}</div>
        {children && <div className="set-need-d">{children}</div>}
        {facts && <div className="sg-facts">{facts}</div>}
      </div>
      {actions && <div className="set-need-acts">{actions}</div>}
    </div>
  );
}

/** The answer to the button you just pressed. */
export function Flash({ note }: { note: { ok: boolean; text: string } | null }) {
  if (!note) return null;
  return <p className={note.ok ? "set-flash" : "error"} role={note.ok ? "status" : "alert"}>{note.text}</p>;
}

/** True while `query` matches: the phone layout reads it. */
export function useMedia(query: string): boolean {
  const [matches, setMatches] = useState(() =>
    typeof window !== "undefined" && window.matchMedia ? window.matchMedia(query).matches : false);
  useEffect(() => {
    if (!window.matchMedia) return;
    const list = window.matchMedia(query);
    const on = () => setMatches(list.matches);
    on();
    list.addEventListener("change", on);
    return () => list.removeEventListener("change", on);
  }, [query]);
  return matches;
}
