// The marketplace, and any other library source this install reads, inside the
// dashboard: search, what a listing can do, and Adopt with consent. The rules
// live on the server (yaaf.server.library); this file only says them plainly.

import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { ApiError, get, post } from "./api";

export interface LibraryResult {
  id: string;
  kind: "role" | "project";
  name: string;
  summary: string;
  category: string;
  domains: string[];
  tier: "official" | "community";
  status: string;
  latest: number;
  installs: number;
  can_do: string[];
  needs_credentials: string[];
  required_credentials: string[];
  data_sources: string[];
  inputs: string[];
  roles: string[];
  has_receipt: boolean;
  details: { what_you_get?: string; what_it_costs?: string; what_you_need?: string };
  published_at: string | null;
  source: string;
  installed_as: string | null;
}

export interface LibrarySearch {
  results: LibraryResult[];
  count: number;
  sources: { url: string; ok: boolean; name?: string; error?: string }[];
}

export interface LibraryUpdate {
  slug: string;
  name: string;
  source: string;
  installed_version: number | null;
  latest_version: number | null;
  status: string | null;
  reason: string | null;
  update: boolean;
}

/** What a role may do, in a chairman's words. Only running commands is
    coloured: a warning on everything is a warning nobody reads. */
export const CAN_DO: Record<string, string> = {
  web: "reads the web",
  files: "reads and writes files",
  bash: "runs commands",
};

export function canDoWords(tools: string[]): string {
  return tools.length ? tools.map((t) => CAN_DO[t] ?? t).join(", ") : "replies only";
}

export function installHref(source: string, id: string, version?: number | null): string {
  const q = new URLSearchParams({ source, id, ...(version ? { v: String(version) } : {}) });
  return `/library/install?${q.toString()}`;
}

/** Adopt a team's newest role version. A role from a library source whose new
    version can do more is refused until the chairman says yes to exactly
    that; this returns what it would grant instead of throwing. */
export async function adoptRole(teamId: string, consent = false):
  Promise<{ changed: boolean; version: number } | { grows: string[]; text: string }> {
  try {
    return await post<{ changed: boolean; version: number }>(
      `/admin/teams/${teamId}/repin`, consent ? { consent: true } : {});
  } catch (e) {
    const detail = (e instanceof ApiError
      ? (e.data as { detail?: { code?: string; grows?: string[]; text?: string } } | null)?.detail
      : null);
    if (e instanceof ApiError && e.status === 409 && detail?.code === "consent_required") {
      return { grows: detail.grows ?? [], text: detail.text ?? e.message };
    }
    throw e;
  }
}

/** An Adopt button that asks before granting more. */
export function AdoptButton({ teamId, label, onDone }: {
  teamId: string;
  label: string;
  onDone: (message: string) => void;
}) {
  const [asking, setAsking] = useState<{ grows: string[]; text: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  async function run(consent: boolean) {
    setError(null);
    try {
      const result = await adoptRole(teamId, consent);
      if ("grows" in result) {
        setAsking(result);
        return;
      }
      setAsking(null);
      onDone(result.changed ? `Now on v${result.version}, from its next run.`
        : `Already on the latest version (v${result.version}).`);
    } catch (e) {
      setError(String((e as Error).message));
    }
  }
  if (asking) {
    return (
      <span className="adopt-ask">
        <span className="warn-text">{asking.text}</span>{" "}
        <button className="small" onClick={() => run(true)}>Adopt and allow {asking.grows.join(", ")}</button>{" "}
        <button className="small" onClick={() => setAsking(null)}>Not now</button>
      </span>
    );
  }
  return (
    <>
      <button className="small" onClick={() => run(false)}
        title="A newer version of this role exists. Live teams never change silently — adopting is your call, and the CEO is told.">
        {label}
      </button>
      {error && <span className="error">{error}</span>}
    </>
  );
}

/** Where a role came from, and what its source says of it now. */
export function SourceNote({ source, status }: {
  source?: string | null;
  status?: { status: string; reason: string | null } | null;
}) {
  if (!source) return null;
  if (status?.status === "removed") {
    return (
      <span className="badge danger" title={status.reason ?? undefined}>
        removed from {source}{status.reason ? `: ${status.reason}` : ""}
      </span>
    );
  }
  return <span className="badge" title="Installed from a library source">{source}</span>;
}

/** Search the marketplace: roles or whole projects, each linking to the page
    that shows everything it can do before anything is installed. */
export function Marketplace({ kind, title }: { kind?: "role" | "project"; title?: string }) {
  const [query, setQuery] = useState("");
  const [found, setFound] = useState<LibrarySearch | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    const t = setTimeout(() => {
      const q = new URLSearchParams({ ...(query ? { q: query } : {}), ...(kind ? { kind } : {}), limit: "40" });
      get<LibrarySearch>(`/admin/library/search?${q.toString()}`)
        .then((r) => { setFound(r); setError(null); })
        .catch((e) => setError(String((e as Error).message)));
    }, 250);
    return () => clearTimeout(t);
  }, [query, kind]);

  if (found && found.sources.length === 0) {
    return (
      <p className="help">
        No library source is configured, so only this install&apos;s own library is offered. Set{" "}
        <code>YAAF_LIBRARY_SOURCES</code> to the marketplace&apos;s index to hire from it.
      </p>
    );
  }
  return (
    <div className="market">
      {title && <h2>{title}</h2>}
      <input type="search" value={query} onChange={(e) => setQuery(e.target.value)}
        placeholder={kind === "project" ? "Search projects — what do you want done?" : "Search the marketplace"}
        aria-label="Search the marketplace" />
      {error && <div className="error">{error}</div>}
      {found?.sources.filter((s) => !s.ok).map((s) => (
        <p key={s.url} className="help">Could not reach {s.url}: {s.error}</p>
      ))}
      {found && found.results.length === 0 && <p className="help">Nothing matches that.</p>}
      <div className="market-list">
        {found?.results.map((r) => (
          <Link key={`${r.source}${r.id}`} className="market-row" to={installHref(r.source, r.id)}>
            <div className="market-top">
              <b>{r.name}</b>
              <span className="mono help">{r.id}</span>
              {r.tier === "official" && <span className="badge accent">official</span>}
              {r.installed_as && <span className="badge">installed as {r.installed_as}</span>}
              {r.has_receipt && <span className="badge ok">ran for real</span>}
            </div>
            <div>{r.summary}</div>
            <div className="help">
              {r.kind === "project" ? `${r.roles.length} roles · ` : ""}
              {canDoWords(r.can_do)}
              {r.needs_credentials.length ? ` · needs ${r.needs_credentials.join(", ")}` : ""}
              {r.inputs.length ? ` · asks ${r.inputs.length} question${r.inputs.length === 1 ? "" : "s"}` : ""}
              {r.installs ? ` · ${r.installs} installs` : ""}
            </div>
          </Link>
        ))}
      </div>
    </div>
  );
}
