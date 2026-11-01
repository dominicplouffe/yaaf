// How a settings section talks: show first, summarise second, explain on demand.
//
// Every section leads with one line saying what it is for, and the reasoning
// behind it sits one click away under "Why?". Consequences a reader must not
// miss — money, irreversibility, permissions, secrets — stay on screen, short.
//
// The rulebook's own sentences are written server-side, beside the rules they
// describe, and some run to two hundred words. They are not rewritten here
// (that would be a second copy to drift); they are split: the first sentence
// leads, the rest folds under "Why?", and the raw names a reader was never
// meant to see are turned into the labels the schema already gives them.

import type { ReactNode } from "react";
import "./pages/settings-copy.css";

/** The one line under a heading: what this section is for. */
export function Lede({ children }: { children: ReactNode }) {
  return <p className="s-lede">{children}</p>;
}

/** The explanation, on demand — the one "Why?" fold, wherever a line has a
    reason behind it (Settings, the new-project screens). Renders nothing when
    there is nothing to say. `className` places it; it never restyles it. */
export function Why({ children, label = "Why?", className }: {
  children: ReactNode; label?: string; className?: string;
}) {
  if (children === null || children === undefined || children === false || children === "") return null;
  return (
    <details className={`s-why${className ? ` ${className}` : ""}`}>
      <summary>{label}</summary>
      <div className="s-why-body">{children}</div>
    </details>
  );
}

const wordCount = (text: string) => (text.match(/\S+/g) ?? []).length;

/** Sentences, split where a stop is followed by a space and a capital, digit or
    opening quote — so "$8.05", "1.0" and "~15s" stay whole. */
export function sentences(text: string): string[] {
  const out: string[] = [];
  const stop = /[.!?]["'”’)]*\s+(?=["'“‘(]?[A-Z0-9])/g;
  let from = 0;
  for (let m = stop.exec(text); m; m = stop.exec(text)) {
    out.push(text.slice(from, m.index + m[0].trimEnd().length).trim());
    from = m.index + m[0].length;
  }
  out.push(text.slice(from).trim());
  return out.filter(Boolean);
}

/** [what leads, what folds]. The lead is the first sentence, plus the next one
    when the first is a stub ("USD.", "The wall.") and the pair stays short.
    Text no longer than `whole` words is not worth a fold and leads entire. */
export function splitLead(text: string, enough = 8, most = 32, whole = 22): [string, string] {
  if (wordCount(text) <= whole) return [text.trim(), ""];
  const parts = sentences(text);
  let lead = parts[0] ?? "";
  let next = 1;
  while (next < parts.length && wordCount(lead) < enough
         && wordCount(`${lead} ${parts[next]}`) <= most) {
    lead = `${lead} ${parts[next]}`;
    next += 1;
  }
  return [lead, parts.slice(next).join(" ")];
}

/** A stored choice, said as a word: "ceo_judgment" → "CEO judgment". */
export function choiceLabel(value: string): string {
  const said = value.split("_").map((w) => (w === "ceo" ? "CEO" : w)).join(" ");
  return said.charAt(0).toUpperCase() + said.slice(1);
}

/** A rule's help with its machine names replaced: a backticked rule key becomes
    that rule's label, and a quoted choice of this rule becomes its word. */
export function humanizeHelp(
  text: string,
  choices: string[] | null | undefined,
  labelOf: (key: string) => string | undefined,
): string {
  let said = text.replace(/`([a-z0-9_]+)`/g, (whole, key: string) => {
    const label = labelOf(key);
    return label ? `“${label}”` : whole;
  });
  for (const choice of choices ?? []) {
    said = said.split(`'${choice}'`).join(`“${choiceLabel(choice)}”`);
  }
  return said;
}
