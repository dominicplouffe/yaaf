// The short ways to the places a person keeps going back to.
//
// A project's sidebar holds five pages and no more: ten made every one of them
// look equally important. That put the pages under them — Deliverables, every
// Settings section — two and three clicks away, which is the right depth for a
// page you open once a month and the wrong one for a page you open every
// morning. Which pages those are differs by person, so the shortcuts are
// personal: pins you choose (kept by the server, so they follow you between
// browsers), the places you visited last (kept by this browser, for ⌘K), and
// the Details tab you were on (so Details reopens where you left it).
//
// Research on menus found people prefer the ones they arrange themselves to
// ones that rearrange themselves, and also that most people never arrange
// anything. So nothing here moves on its own: a sensible default (Deliverables
// is pinned), and one offer to pin a page you keep opening, made once.

import { createContext, ReactNode, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { useLocation } from "react-router-dom";
import { get, Pin, PinsView, put } from "./api";
import { DETAIL_TABS, placeOf } from "./nav";
import { Icon } from "./ui/Icon";

interface PinsState {
  view: PinsView | null;
  /** This project's pins, in order: the ones for every project and the ones
      made in this one. */
  pinsFor: (projectId: string) => Pin[];
  isPinned: (to: string, projectId: string) => boolean;
  /** Pin or unpin. `project_id` is set for a place that only exists in one
      project, like a file. */
  toggle: (pin: Pin) => Promise<void>;
  error: string | null;
}

const PinsContext = createContext<PinsState | null>(null);

const samePin = (a: Pin, b: Pin) => a.to === b.to && (a.project_id ?? null) === (b.project_id ?? null);

export function PinsProvider({ children }: { children: ReactNode }) {
  const [view, setView] = useState<PinsView | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    get<PinsView>("/me/pins").then(setView).catch((e: Error) => setError(e.message));
  }, []);

  const pinsFor = useCallback((projectId: string) =>
    (view?.pins ?? []).filter((p) => !p.project_id || p.project_id === projectId), [view]);

  const isPinned = useCallback((to: string, projectId: string) =>
    pinsFor(projectId).some((p) => p.to === to), [pinsFor]);

  const toggle = useCallback(async (pin: Pin) => {
    if (!view) return;
    const had = view.pins.some((p) => samePin(p, pin));
    const next = had ? view.pins.filter((p) => !samePin(p, pin)) : [...view.pins, pin];
    if (next.length > view.max) return;
    // Drawn at once and confirmed by the server's answer: a pin that waited
    // on the round trip would look like a button that did nothing.
    setView({ ...view, pins: next, default: false });
    try {
      setView(await put<PinsView>("/me/pins", { pins: next }));
      setError(null);
    } catch (e) {
      setView(view);
      setError((e as Error).message);
    }
  }, [view]);

  const value = useMemo(() => ({ view, pinsFor, isPinned, toggle, error }),
    [view, pinsFor, isPinned, toggle, error]);
  return <PinsContext.Provider value={value}>{children}</PinsContext.Provider>;
}

export function usePins(): PinsState {
  const state = useContext(PinsContext);
  if (!state) throw new Error("usePins outside PinsProvider");
  return state;
}

/** "Pin to sidebar" for the place it sits on. Says when the sidebar is full
    rather than doing nothing. */
export function PinButton({ projectId, to, label, only }: {
  projectId: string;
  /** The place, relative to the project: "details/deliverables". */
  to: string;
  label: string;
  /** Pin it in this project only — for a place that exists only here. */
  only?: boolean;
}) {
  const { view, isPinned, toggle } = usePins();
  if (!view) return null;
  const pinned = isPinned(to, projectId);
  const full = !pinned && view.pins.length >= view.max;
  return (
    <button type="button" className={`pin-btn${pinned ? " on" : ""}`} aria-pressed={pinned} disabled={full}
      title={full ? `The sidebar holds ${view.max} pins. Unpin one to pin this.`
        : pinned ? `Unpin ${label} from the sidebar` : `Pin ${label} to the sidebar`}
      onClick={() => void toggle({ to, label, project_id: only ? projectId : null })}>
      <Icon name="pin" size={14} />
      {pinned ? "Pinned" : full ? "Sidebar full" : "Pin to sidebar"}
    </button>
  );
}

// ── where you have been ──────────────────────────────────────────────────────

const RECENT_KEY = "yaaf.recent";
const VISITS_KEY = "yaaf.visits";
const OFFERED_KEY = "yaaf.pin-offered";
const DETAIL_KEY = "yaaf.details-tab";
const RECENT_MAX = 5;
const WEEK_MS = 7 * 24 * 3600 * 1000;
/** How many visits in a week make a page worth offering to pin. */
const OFFER_AFTER = 3;

function read<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
}

function write(key: string, value: unknown): void {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // A private window or a full store: shortcuts are a convenience, and a
    // page that broke over one would be the opposite.
  }
}

/** A place in a project, as the address under /projects/<id>/ that a pin and
    a recent entry store: the path and, for Settings, its section. Other query
    parameters are a page's own filters, not a different place. */
export function placeAt(pathname: string, search: string, projectId: string): string | null {
  const prefix = `/projects/${projectId}`;
  if (!pathname.startsWith(prefix)) return null;
  const path = pathname.slice(prefix.length).replace(/^\/|\/$/g, "");
  const s = new URLSearchParams(search).get("s");
  return path === "settings" && s ? `settings?s=${s === "data" ? "secrets" : s}` : path;
}

/** Remember where this reader goes inside a project: the last few places for
    ⌘K, a week of visits for the one-time offer to pin, and the Details tab
    they were on. Called once, by the shell. */
export function useRememberPlaces(projectId: string | null) {
  const location = useLocation();
  useEffect(() => {
    if (!projectId) return;
    const rel = placeAt(location.pathname, location.search, projectId);
    if (rel === null || !placeOf(rel)) return;
    const recent = read<Record<string, string[]>>(RECENT_KEY, {});
    recent[projectId] = [rel, ...(recent[projectId] ?? []).filter((r) => r !== rel)].slice(0, RECENT_MAX);
    write(RECENT_KEY, recent);
    const now = Date.now();
    const visits = read<Record<string, number[]>>(VISITS_KEY, {});
    for (const k of Object.keys(visits)) {
      visits[k] = visits[k].filter((t) => now - t < WEEK_MS);
      if (!visits[k].length) delete visits[k];
    }
    visits[rel] = [...(visits[rel] ?? []), now];
    write(VISITS_KEY, visits);
    // Only a real tab: an old address such as details/settings passes
    // through here on its way to where it redirects.
    const tab = rel.startsWith("details/") ? rel.slice("details/".length) : null;
    if (tab && DETAIL_TABS.some((t) => tab === t.path || tab.startsWith(`${t.path}/`))) write(DETAIL_KEY, tab);
  }, [projectId, location.pathname, location.search]);
}

export function recentPlaces(projectId: string): string[] {
  return read<Record<string, string[]>>(RECENT_KEY, {})[projectId] ?? [];
}

/** The Details tab this reader was last on — "deliverables", "activity/runs"
    — so the sidebar's Details opens there rather than on the Roadmap. */
export function lastDetailsTab(): string | null {
  return read<string | null>(DETAIL_KEY, null);
}

/** The one offer to pin a page this reader keeps opening. Shown at most once
    per page: dismissing it, or pinning, puts it away for good. */
export function PinSuggestion({ projectId }: { projectId: string }) {
  const location = useLocation();
  const { view, isPinned, toggle } = usePins();
  const [, redraw] = useState(0);
  const rel = placeAt(location.pathname, location.search, projectId);
  if (!view || rel === null || rel === "" || !rel.includes("/") && !rel.startsWith("settings?")) return null;
  const place = placeOf(rel);
  if (!place || isPinned(rel, projectId) || view.pins.length >= view.max) return null;
  const offered = read<string[]>(OFFERED_KEY, []);
  const seen = (read<Record<string, number[]>>(VISITS_KEY, {})[rel] ?? []).length;
  if (offered.includes(rel) || seen < OFFER_AFTER) return null;
  const done = () => { write(OFFERED_KEY, [...offered, rel]); redraw((n) => n + 1); };
  return (
    <div className="pin-offer" role="status">
      <Icon name="pin" size={14} />
      <span>You have opened <b>{place.label}</b> {seen} times this week. Pin it to the sidebar?</span>
      <button className="small primary" onClick={() => { done(); void toggle({ to: rel, label: place.label, project_id: null }); }}>
        Pin it
      </button>
      <button className="small" onClick={done}>Not now</button>
    </div>
  );
}
