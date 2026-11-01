import { ReactNode } from "react";
import { fmtUnit } from "../ui";
import { Icon } from "./Icon";
import { VERDICT_CHIP } from "./kit";
import "./signal.css";

/** The small vocabulary every screen states its facts in.

    The audit's finding was not that the cockpit lacked information but that it
    said each fact several ways: a chip, a banner, a sentence and a tile, each
    from its own source. These are the one way to draw each recurring fact —
    a project's state, where money stops, how far the outcomes are, and how far
    a result can be trusted — so that two screens showing the same thing show
    it the same way. Each always pairs its colour with a word or a shape. */

// ── state ───────────────────────────────────────────────────────────────────

/** The project's state in one chip: the server's verdict (yaaf.server.
    status_line), the same one its sentence states and `yaaf status` prints.
    The raw status is the fallback only while the desk row has not loaded. */
export function verdictBadge(p: { status: string; unpaid?: boolean; status_line?: { verdict: string } },
  needs = 0) {
  const chip = p.status_line ? VERDICT_CHIP[p.status_line.verdict] : undefined;
  if (!chip) return statusBadge(p.status, p.unpaid);
  const [word, cls] = chip;
  const you = p.status_line?.verdict === "needs_you";
  const label = you && needs > 0 ? `${word} · ${needs}` : word;
  return <span className={`badge ${cls}${you ? " you" : ""}`}>{label}</span>;
}

/** The raw lifecycle status, for the moment before the verdict arrives. A
    project that cannot pay for a run is not running, whatever its row says. */
export function statusBadge(status: string, unpaid = false) {
  const plain: Record<string, string> = {
    running: "Running", paused: "Paused", stopped: "Stopped", completed: "Delivered", draft: "Not started",
  };
  if (unpaid && (status === "running" || status === "draft")) {
    return <span className="badge warn" title="No billing credential: agents cannot run">
      {plain[status] ?? status} · no key</span>;
  }
  const cls = status === "running" ? "good" : status === "paused" ? "warn"
    : status === "stopped" ? "critical" : "accent";
  return <span className={`badge ${cls}`}>{plain[status] ?? status.replace(/_/g, " ")}</span>;
}

// ── money ───────────────────────────────────────────────────────────────────

/** Spent, the plan and the wall on one bar, drawn to the wall's scale. The
    plan is a tick and the wall is the end, both labelled, because the one
    money fact that was stated wrong was where spending stops. */
export function MoneyMeter({ spent, cap, wall, unit, warnRatio, compact = false }: {
  spent: number; cap: number; wall: number; unit: "usd" | "tokens"; warnRatio?: number; compact?: boolean;
}) {
  const scale = Math.max(wall, cap, spent, 1);
  const pct = (n: number) => `${Math.min(100, (n / scale) * 100)}%`;
  const tone = wall > 0 && spent >= wall ? "stop" : spent > cap ? "over"
    : warnRatio && cap > 0 && spent >= cap * warnRatio ? "near" : "";
  const said = `${fmtUnit(spent, unit)} spent of a ${fmtUnit(cap, unit)} plan; the project pauses at ${fmtUnit(wall, unit)}.`;
  return (
    <div className={`sg-meter${compact ? " compact" : ""}`} role="img" aria-label={said} title={said}>
      <div className="sg-bar">
        <i className={`sg-fill ${tone}`} style={{ width: pct(spent) }} />
        {cap > 0 && cap < scale && <b className="sg-plan" style={{ left: pct(cap) }} />}
        <b className="sg-wall" />
      </div>
      {compact ? (
        <div className="sg-cap"><b>{fmtUnit(spent, unit)}</b> of {fmtUnit(cap, unit)} plan</div>
      ) : (
        <div className="sg-labels">
          <span>{fmtUnit(spent, unit)}</span>
          {cap > 0 && cap < scale && <span className="sg-at" style={{ left: pct(cap) }}>plan {fmtUnit(cap, unit)}</span>}
          <span className="sg-end">pauses at {fmtUnit(wall, unit)}</span>
        </div>
      )}
    </div>
  );
}

// ── the path ────────────────────────────────────────────────────────────────

/** Where one outcome stands, in the five shapes the path draws.
    verified — the platform ran its check and it passed
    work     — accepted and being worked, or being checked
    you      — waiting on the chairman (proposed scope, a verdict only he gives)
    todo     — accepted, nothing started
    failed   — a bet that expired or was killed */
export type NodeState = "verified" | "work" | "you" | "todo" | "failed";

export interface PathNode { key: string; state: NodeState; title: string; stage?: number | null }

export const NODE_WORD: Record<NodeState, string> = {
  verified: "verified", work: "in work", you: "waits on you", todo: "not started", failed: "failed",
};

/** A requirement's status as a node. Cut requirements are not on the path. */
export function requirementNode(r: { key: string; status: string; statement?: string; name?: string;
  stage?: number | null }, needsVerdict = false): PathNode | null {
  if (r.status === "cut") return null;
  const state: NodeState = r.status === "met" ? "verified"
    : r.status === "proposed" || needsVerdict ? "you"
      : r.status === "in_progress" || r.status === "checking" ? "work" : "todo";
  return { key: r.key, state, title: r.name ?? r.statement ?? r.key, stage: r.stage ?? null };
}

/** The outcomes as a row of dots, one per outcome, grouped by stage. Counts
    outcomes, never tasks: many closed tasks are not progress toward done. */
export function PathDots({ nodes, size = "sm" }: { nodes: PathNode[]; size?: "sm" | "md" }) {
  if (!nodes.length) return <span className="help">—</span>;
  const out: ReactNode[] = [];
  nodes.forEach((n, i) => {
    if (i > 0 && n.stage != null && nodes[i - 1].stage != null && n.stage !== nodes[i - 1].stage) {
      out.push(<span key={`gap-${i}`} className="sg-gap" aria-hidden />);
    }
    out.push(<i key={n.key} className={`sg-node ${n.state} ${size}`} title={`${n.key} · ${n.title} — ${NODE_WORD[n.state]}`} />);
  });
  const verified = nodes.filter((n) => n.state === "verified").length;
  return (
    <span className="sg-dots" role="img" aria-label={`${verified} of ${nodes.length} outcomes verified`}>{out}</span>
  );
}

// ── trust ───────────────────────────────────────────────────────────────────

export type SealLevel = "verified" | "judged" | "word";

const SEAL: Record<SealLevel, [string, "shield" | "check" | "user"]> = {
  verified: ["Verified", "shield"],
  judged: ["Judged", "check"],
  word: ["Agent's word", "user"],
};

/** How far a result can be trusted, in three levels that are never merged into
    a score: the platform ran the check, a person or reviewer signed it, or
    nothing outside the agent looked. */
export function Seal({ level, children }: { level: SealLevel; children?: ReactNode }) {
  const [word, icon] = SEAL[level];
  return (
    <span className={`sg-seal ${level}`}>
      <Icon name={icon} size={13} />
      {word}{children != null && children !== false && children !== "" && <span className="sg-seal-x"> · {children}</span>}
    </span>
  );
}

/** A result's check, as the seal it earns. */
export function sealOf(checked: string): SealLevel {
  return checked === "platform" ? "verified" : checked === "reviewer" || checked === "judged" ? "judged" : "word";
}

// ── consequences ────────────────────────────────────────────────────────────

/** One consequence of a decision, as a chip: the amount, what it does to the
    plan, whether it can be undone, what silence does. A warning chip is for
    what must not be missed — never a footnote. */
export function Fact({ children, warn = false }: { children: ReactNode; warn?: boolean }) {
  return <span className={`sg-fact${warn ? " warn" : ""}`}>{children}</span>;
}

export function Facts({ children }: { children: ReactNode }) {
  return <span className="sg-facts">{children}</span>;
}

/** The CEO's recommendation, set apart from the case it argues. */
export function Recommendation({ who, children }: { who: string; children: ReactNode }) {
  return (
    <div className="sg-rec">
      <Icon name="message" size={14} />
      <span><b>{who} recommends:</b> {children}</span>
    </div>
  );
}

// ── a desk row, drawn ───────────────────────────────────────────────────────

type RowLike = {
  mode?: string;
  path: { key: string; status: string; stage?: number; name?: string; kind: string }[];
  next_milestone:
    | { kind: "promise"; at: string; status: string; projected: string | null }
    | { kind: "bet"; at: string; id: string }
    | { kind: "window"; at: string }
    | null;
  awaiting_you: { plan: number; verdicts: { key: string }[] };
};

const day = (iso: string) => new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short" });

/** A desk row's outcomes as path nodes. A bet past its kill date waits on
    the chairman (only he extends it); a live one is in work. */
export function rowPath(p: RowLike): PathNode[] {
  const verdicts = new Set(p.awaiting_you.verdicts.map((v) => v.key));
  // Proposed scope waits on the chairman only where the dial left the plan
  // with him; on an autonomous project the CEO accepts its own, so a
  // proposed outcome is simply not started yet.
  const scopeIsYours = p.awaiting_you.plan > 0;
  return p.path.flatMap((n): PathNode[] => {
    if (n.kind === "bet") {
      return [{ key: n.key, state: n.status === "expired" ? "you" : "work", title: "a bet", stage: null }];
    }
    const status = n.status === "proposed" && !scopeIsYours ? "pending" : n.status;
    const node = requirementNode({ key: n.key, status, name: n.name, stage: n.stage ?? null },
      verdicts.has(n.key));
    return node ? [node] : [];
  });
}

/** "2 of 5 verified" / "3 bets in play": the path in words, for the line under it. */
export function pathWords(p: RowLike): string {
  const nodes = rowPath(p);
  if (!nodes.length) return "no outcomes yet";
  if (p.path[0]?.kind === "bet") return `${nodes.length} bet${nodes.length === 1 ? "" : "s"} in play`;
  return `${nodes.filter((n) => n.state === "verified").length} of ${nodes.length} verified`;
}

/** The next date the platform holds, in words, and whether it is in trouble.
    Never a guessed date: no milestone reads "no date yet". */
export function milestoneLine(p: RowLike): { what: string; when: string; warn: boolean } {
  const m = p.next_milestone;
  if (!m) return { what: "No date yet", when: p.mode === "explore" ? "no bet is running" : "nothing promised", warn: false };
  if (m.kind === "promise") {
    if (m.status === "met") return { what: "Delivered", when: `promised ${day(m.at)} · kept`, warn: false };
    const signed = m.status !== "proposed";
    const warn = m.status === "at_risk" || m.status === "breached";
    const when = m.status === "breached" ? `promised ${day(m.at)} · passed`
      : `${signed ? "promised" : "proposed"} ${day(m.at)}${m.projected ? ` · ${warn ? "projected" : "on course"} ${day(m.projected)}` : ""}`;
    return { what: signed ? "Delivery" : "Delivery date to sign", when, warn };
  }
  if (m.kind === "bet") return { what: "A bet's deadline", when: `expires ${day(m.at)}`, warn: false };
  return { what: "Window closes", when: day(m.at), warn: false };
}
