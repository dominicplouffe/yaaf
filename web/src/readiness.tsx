// Is each provider this install pays ready to run seats — and if not, what
// fixes it, one press away.
//
// The server reads every refusal a provider could meet (a class with no model,
// a model with no price, a browsing role on a provider that cannot search, a
// model the key no longer lists, a model that failed its check) into one list
// per provider, with the fix beside each (yaaf.core.readiness). Four screens
// show that list — the Providers cards, the connect flow, Home's checklist and
// the New Project money step — and they show it the same way, with the same
// buttons, from here. A chairman who meets the list before the refusal never
// meets the refusal.

import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { get, post } from "./api";
import { forgetModels } from "./providers";

export interface IssueFix {
  action: "import_prices" | "set_price" | "web_search" | "set_lineup" | "check";
  models?: string[];
  class?: string;
}

export interface ReadinessIssue {
  code: string;
  severity: "block" | "warn";
  text: string;
  fix: IssueFix | null;
}

export interface TierCheck { status: string; summary: string; at: string | null }

export interface ReadinessTier {
  class: string;
  label: string;
  model: string | null;
  source: "install" | "shipped" | null;
  priced: boolean;
  price: { input: number; output: number } | null;
  states_cost: boolean;
  /** Whether the key's own model list (as last read) has it; null until read. */
  served: boolean | null;
  context_window: number | null;
  check: TierCheck | null;
}

export interface ProviderReadiness {
  provider: string;
  label: string;
  local: boolean;
  is_default: boolean;
  search: "own" | "platform" | "none";
  states_cost: boolean;
  credentials: { id: string; name: string; mode: string; kind: string }[];
  tiers: ReadinessTier[];
  served: { at: string; count: number } | null;
  ceiling: number | null;
  issues: ReadinessIssue[];
  ready: boolean;
}

export interface Readiness {
  default_provider: string;
  providers: ProviderReadiness[];
  install: {
    connected: boolean;
    ready: boolean;
    default_connected: boolean;
    founder: boolean;
    web_search: boolean;
    upkeep: boolean;
    roles: number;
  };
}

export const READINESS = "/admin/settings/providers/readiness";

/** The readiness report, re-read whenever `nonce` moves. `error` is set when it
    cannot be read — including for anyone who is not the instance admin, which
    the screens that borrow it treat as "draw nothing". */
export function useReadiness(nonce = 0) {
  const [data, setData] = useState<Readiness | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let alive = true;
    get<Readiness>(READINESS)
      .then((r) => { if (alive) { setData(r); setError(null); } })
      .catch((e) => { if (alive) setError((e as Error).message); });
    return () => { alive = false; };
  }, [nonce, tick]);
  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { data, error, reload };
}

/** Ready, ready with something to look at, or refusing seats — said in a word
    as well as a colour. */
export function providerStatus(p: ProviderReadiness): { label: string; tone: "good" | "warn" | "critical" } {
  if (!p.ready) return { label: "Blocked", tone: "critical" };
  if (p.issues.length) return { label: "Needs attention", tone: "warn" };
  return { label: "Ready", tone: "good" };
}

export function StatusBadge({ p }: { p: ProviderReadiness }) {
  const s = providerStatus(p);
  return <span className={`badge ${s.tone}`}>{s.label}</span>;
}

/** Where each fix that is a place, rather than a press, lives. */
export const FIX_LINK = {
  prices: (model?: string) => `/settings?section=prices${model ? `&model=${encodeURIComponent(model)}` : ""}`,
  search: "/settings?section=search",
  provider: (name: string) => `/settings?section=providers&provider=${encodeURIComponent(name)}`,
};

interface ImportAnswer { source: string; applied: string[] }

/** Price models from the snapshot shipped with this release. The server chose
    these models because the snapshot has them, so this works offline. */
export async function importPrices(models: string[]): Promise<ImportAnswer> {
  const said = await post<ImportAnswer>("/admin/settings/model-prices/import",
    { source: "snapshot", apply: true, models });
  // Every picker's "no price yet" reads the catalog; it is stale now.
  forgetModels();
  return said;
}

/** The issues, each with the one press or link that fixes it. `onSetLineup`
    and `onCheck` are given where the screen can do those in place (a provider
    card, the connect flow); elsewhere they are links to the provider's card.
    `newTab` keeps a half-filled form behind the link alive. */
export function IssueList({ provider, issues, onChanged, onSetLineup, onCheck, newTab, empty }: {
  provider: string;
  issues: ReadinessIssue[];
  onChanged: () => void;
  onSetLineup?: (cls: string) => void;
  onCheck?: (models: string[]) => void;
  newTab?: boolean;
  /** Said when the list is empty; nothing when absent. */
  empty?: string;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);
  const target = newTab ? { target: "_blank", rel: "noreferrer" } : {};
  const arrow = newTab ? " ↗" : "";

  async function doImport(issue: ReadinessIssue) {
    const models = issue.fix?.models ?? [];
    setBusy(issue.code); setNote(null);
    try {
      const said = await importPrices(models);
      setNote(said.applied.length
        ? { ok: true, text: `Priced ${said.applied.join(", ")} from ${said.source}.` }
        : { ok: false, text: "Nothing was imported — those models already carry a price of "
            + "their own. Set or check it under Model prices." });
      onChanged();
    } catch (e) {
      setNote({ ok: false, text: (e as Error).message });
    } finally { setBusy(null); }
  }

  // One model on two classes is one finding, not two rows saying it twice.
  issues = issues.filter((issue, i) => issues.findIndex((o) => o.text === issue.text) === i);
  if (!issues.length) return empty ? <p className="help issue-empty">✓ {empty}</p> : null;
  return <div className="issues">
    <ul className="issue-list">
      {issues.map((issue, i) => {
        const fix = issue.fix;
        let action: React.ReactNode = null;
        if (fix?.action === "import_prices") {
          action = <button type="button" className="small primary" disabled={busy !== null}
            onClick={() => doImport(issue)}>
            {busy === issue.code ? "Importing…" : `Import ${fix.models?.length === 1 ? "its price" : "prices"}`}
          </button>;
        } else if (fix?.action === "set_price") {
          action = <Link className="btnlink small" to={FIX_LINK.prices(fix.models?.[0])} {...target}>
            Set a price{arrow}</Link>;
        } else if (fix?.action === "web_search") {
          action = <Link className="btnlink small" to={FIX_LINK.search} {...target}>
            Set up web search{arrow}</Link>;
        } else if (fix?.action === "set_lineup") {
          action = onSetLineup
            ? <button type="button" className="small" onClick={() => onSetLineup(fix.class ?? "")}>
                Choose a model</button>
            : <Link className="btnlink small" to={FIX_LINK.provider(provider)} {...target}>
                Choose a model{arrow}</Link>;
        } else if (fix?.action === "check") {
          action = onCheck
            ? <button type="button" className="small" onClick={() => onCheck(fix.models ?? [])}>
                Check again</button>
            : <Link className="btnlink small" to={FIX_LINK.provider(provider)} {...target}>
                Check again{arrow}</Link>;
        }
        return <li key={`${issue.code}-${i}`} className={`issue sev-${issue.severity}`}>
          <span className={`chip ${issue.severity === "block" ? "critical" : "warn"}`}>
            {issue.severity === "block" ? "blocks seats" : "worth a look"}</span>
          <span className="issue-text">{issue.text}</span>
          {action && <span className="issue-fix">{action}</span>}
        </li>;
      })}
    </ul>
    {note && <p className={note.ok ? "help ok-line" : "error"} role={note.ok ? "status" : "alert"}>
      {note.ok ? "✓ " : ""}{note.text}</p>}
  </div>;
}

/** One provider's readiness, read on its own — for a screen that only cares
    about the provider it is about to pay (New Project's money step). Draws
    nothing for someone who cannot read the report, or for a provider the
    report does not hold (a hidden credential's). */
export function ProviderReadinessInline({ provider, onChanged }: {
  provider: string; onChanged?: () => void;
}) {
  const [nonce, setNonce] = useState(0);
  const { data } = useReadiness(nonce);
  const p = data?.providers.find((row) => row.provider === provider);
  if (!p) return null;
  const changed = () => { setNonce((n) => n + 1); onChanged?.(); };
  return <div className={`readiness-inline tone-${providerStatus(p).tone}`}>
    <div className="ri-head">
      <StatusBadge p={p} />
      <span>{p.ready && !p.issues.length
        ? `${p.label} is ready: every class has a priced model.`
        : p.ready ? `${p.label} can run seats; one thing is worth a look first.`
        : `${p.label} would refuse some seats as things stand. Fix it here, before the project meets the refusal:`}</span>
    </div>
    <IssueList provider={p.provider} issues={p.issues} onChanged={changed} newTab />
  </div>;
}
