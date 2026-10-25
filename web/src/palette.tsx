import { useEffect, useMemo, useRef, useState } from "react";
import { NavigateFunction, useLocation, useNavigate } from "react-router-dom";
import { DeskProject, get } from "./api";
import { firstName, useProjectFrame } from "./projectCtx";
import { ACTIVITY_VIEWS, placeOf, PROJECT_DESTINATIONS, SETTINGS_SECTIONS } from "./nav";
import { placeAt, recentPlaces, usePins } from "./shortcuts";
import { Icon, IconName } from "./ui/Icon";
import { applyTheme, Avatar } from "./ui/kit";
import { SEAT_LABEL } from "./ui/team";

/** Search and jump, from anywhere, without knowing where anything lives.

    A product you are meant to learn by browsing still has to let you skip the
    browsing once you know what you want. ⌘K finds a page, a project or a
    person by name, and does the handful of things worth doing from anywhere.
    Nothing here is a new capability — every entry is a door to a screen that
    already exists, so the palette can never become a second way to decide
    something with fewer safeguards than the first.

    Inside a project it also reaches one level further than the sidebar does:
    every Settings section and Activity view by name, the places you were in
    last, the setup chores ("Add a secret"), and any deliverable by its title —
    so a keyboard reader is two keys and a word from anywhere. */

interface Command {
  group: string;
  label: string;
  hint?: string;
  icon?: IconName;
  avatar?: { name: string; team: string };
  /** Extra words that should find this entry. */
  words?: string;
  run: () => void;
}

function useCommands({
  projects, currentProjectId, navigate, openCheatsheet, signOut,
}: {
  projects: DeskProject[];
  currentProjectId: string | null;
  navigate: NavigateFunction;
  openCheatsheet: () => void;
  signOut: () => void;
}): Command[] {
  const frame = useProjectFrame();
  const ctx = frame?.ctx;
  const { pinsFor } = usePins();
  const pins = useMemo(() => (currentProjectId ? pinsFor(currentProjectId) : []), [pinsFor, currentProjectId]);
  const { pathname, search } = useLocation();
  const here = currentProjectId ? placeAt(pathname, search, currentProjectId) : null;
  return useMemo(() => {
    // One list per group, joined in the order a reader scans: where you were
    // and what you pinned, then every place, then people and actions.
    const recent: Command[] = [];
    const pinned: Command[] = [];
    const goTo: Command[] = [
      { group: "Go to", label: "Home", icon: "home", hint: "G H", run: () => navigate("/home") },
      { group: "Go to", label: "Inbox", icon: "inbox", hint: "G I", words: "desk approvals questions decisions",
        run: () => navigate("/inbox") },
      { group: "Go to", label: "All projects", icon: "grid", hint: "G P", run: () => navigate("/projects") },
    ];
    const settings: Command[] = [];
    const others: Command[] = [];
    const people: Command[] = [];
    const actions: Command[] = [];
    if (currentProjectId) {
      const base = `/projects/${currentProjectId}`;
      const ceo = ctx ? firstName(ctx.ceoName) : "your CEO";
      // Where you were, newest first: the habits you have not pinned.
      for (const rel of recentPlaces(currentProjectId)) {
        const place = placeOf(rel);
        if (!place || rel === here) continue;
        recent.push({
          group: "Recent", label: place.label === "Inbox" ? "This project’s inbox" : place.label,
          icon: place.icon, words: rel.replace(/[/?=]/g, " "),
          run: () => navigate(rel ? `${base}/${rel}` : base),
        });
      }
      pins.forEach((pin, i) => pinned.push({
        group: "Pinned", label: pin.label, icon: placeOf(pin.to.split("?open=")[0])?.icon ?? "pin",
        hint: i < 5 ? `G ${i + 1}` : undefined, words: "pin pinned",
        run: () => navigate(`${base}/${pin.to}`),
      }));
      for (const p of PROJECT_DESTINATIONS) {
        goTo.push({
          group: "Go to",
          label: p.path === "chat" ? `Chat with ${ceo}` : p.label,
          icon: p.icon,
          hint: `G ${p.key.toUpperCase()}`,
          words: p.words,
          run: () => navigate(p.path ? `${base}/${p.path}` : base),
        });
      }
      for (const [view, label, means] of ACTIVITY_VIEWS) {
        goTo.push({
          group: "Go to", label: `Activity › ${label}`, icon: "history", words: `details record ${means}`,
          run: () => navigate(`${base}/details/activity/${view}`),
        });
      }
      for (const sec of SETTINGS_SECTIONS) {
        settings.push({
          group: "Project settings", label: sec.label, icon: sec.key === "secrets" ? "key"
            : sec.key === "sources" ? "plug" : "gear", words: `settings ${sec.words}`,
          run: () => navigate(`${base}/settings?s=${sec.key}`),
        });
      }
      // The chores a new project is made of, by what you want done rather
      // than where the form for it lives.
      const chores: [string, string, string, IconName][] = [
        ["Add a secret or variable", "secrets", "api key token credential env value", "key"],
        ["Connect a data source", "sources", "mcp database api", "plug"],
        ["Set the budget", "money", "spend limit monthly cap", "wallet"],
        ["Change the model key", "money", "billing credential pay provider", "key"],
        ["Invite someone to this project", "people", "co-chair observer member share", "users"],
        ["Mirror into Slack", "integrations", "slack channel", "message"],
        ["Edit the brief", "brief", "goal standing orders", "edit"],
      ];
      for (const [label, section, words, icon] of chores) {
        actions.push({ group: "Actions", label, icon, words, run: () => navigate(`${base}/settings?s=${section}`) });
      }
    }
    goTo.push(
      { group: "Go to", label: "Role library", icon: "users", words: "hire roles people", run: () => navigate("/roles") },
      { group: "Go to", label: currentProjectId ? "App settings" : "Settings", icon: "gear",
        words: "settings workspace providers models prices credentials account slack", run: () => navigate("/settings") },
    );
    for (const p of projects) {
      others.push({
        group: "Projects", label: p.name, avatar: { name: p.name, team: p.name },
        hint: p.status === "running" ? "Running" : p.status, words: `project ${p.goal}`,
        run: () => navigate(`/projects/${p.id}`),
      });
    }
    if (ctx) {
      for (const s of ctx.seats) {
        people.push({
          group: "People", label: `${s.name} — ${s.ceo ? "CEO" : s.team}`, avatar: { name: s.name, team: s.ceo ? "leadership" : s.team },
          hint: SEAT_LABEL[s.state], words: `${s.title} ${s.doing}`,
          run: () => ctx.openAgent(s.id),
        });
      }
      actions.push({
        group: "Actions", label: `Message ${firstName(ctx.ceoName)}…`, icon: "send", words: "ceo chat boardroom ask",
        run: () => navigate(`/projects/${ctx.projectId}/chat`),
      });
    }
    actions.push(
      { group: "Actions", label: "Start a new project", icon: "plus", words: "create", run: () => navigate("/projects/new") },
      { group: "Actions", label: "Use the light theme", icon: "sun", words: "theme", run: () => applyTheme("light") },
      { group: "Actions", label: "Use the dark theme", icon: "moon", words: "theme", run: () => applyTheme("dark") },
      { group: "Actions", label: "Follow the system theme", icon: "monitor", words: "theme auto", run: () => applyTheme("auto") },
      { group: "Actions", label: "Keyboard shortcuts", icon: "help", hint: "?", run: openCheatsheet },
      { group: "Actions", label: "Sign out", icon: "logout", run: signOut },
    );
    return [...recent, ...pinned, ...goTo, ...settings, ...others, ...people, ...actions];
  }, [projects, currentProjectId, navigate, openCheatsheet, signOut, ctx, pins, here]);
}

interface FileHit { id: string; name: string; dir: string; state: string }

/** Deliverables whose name matches what is typed, from the same index the
    Deliverables page reads — so "AVGO" finds the note without knowing where
    in the day folders it sits. Asked of the server once typing pauses. */
function useFileSearch(projectId: string | null, query: string, navigate: NavigateFunction): Command[] {
  const [hits, setHits] = useState<FileHit[]>([]);
  const q = query.trim();
  useEffect(() => {
    if (!projectId || q.length < 2) { setHits([]); return; }
    let live = true;
    const timer = window.setTimeout(() => {
      get<{ rows: FileHit[] }>(`/observe/projects/${projectId}/files/browse/rows?q=${encodeURIComponent(q)}&limit=6&lean=true`)
        .then((r) => { if (live) setHits(r.rows); })
        .catch(() => { if (live) setHits([]); });
    }, 180);
    return () => { live = false; window.clearTimeout(timer); };
  }, [projectId, q]);
  return hits.map((f) => ({
    group: "Deliverables", label: f.name, icon: "file" as IconName,
    hint: f.state === "published" ? "published" : f.dir || f.state,
    run: () => navigate(`/projects/${projectId}/details/deliverables?open=${encodeURIComponent(f.id)}`),
  }));
}

export function CommandPalette({
  initialQuery, projects, currentProjectId, onClose, openCheatsheet, signOut,
}: {
  initialQuery: string;
  projects: DeskProject[];
  currentProjectId: string | null;
  onClose: () => void;
  openCheatsheet: () => void;
  signOut: () => void;
}) {
  const navigate = useNavigate();
  const [query, setQuery] = useState(initialQuery);
  const [sel, setSel] = useState(0);
  const listRef = useRef<HTMLDivElement>(null);
  const commands = useCommands({ projects, currentProjectId, navigate, openCheatsheet, signOut });

  const words = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const files = useFileSearch(currentProjectId, query, navigate);
  const matched = commands.filter((c) => {
    const hay = `${c.group} ${c.label} ${c.words ?? ""}`.toLowerCase();
    return words.every((w) => hay.includes(w));
  });
  // Files sit with the places, before projects, people and actions.
  const cut = matched.findIndex((c) => c.group === "Projects" || c.group === "People" || c.group === "Actions");
  const shown = cut < 0 ? [...matched, ...files] : [...matched.slice(0, cut), ...files, ...matched.slice(cut)];
  const pick = Math.min(sel, Math.max(0, shown.length - 1));

  useEffect(() => {
    const el = listRef.current?.querySelector<HTMLElement>(`[data-i="${pick}"]`);
    el?.scrollIntoView({ block: "nearest" });
  }, [pick]);

  function run(c: Command | undefined) {
    if (!c) return;
    onClose();
    c.run();
  }

  let group = "";
  return (
    <div className="cmdk-back" onClick={onClose}>
      <div className="cmdk" role="dialog" aria-modal="true" aria-label="Search and jump"
        onClick={(e) => e.stopPropagation()}>
        <div className="cmdk-in">
          <Icon name="search" size={16} />
          <input
            autoFocus
            value={query}
            placeholder="Search pages, projects, people, or an action…"
            onChange={(e) => { setQuery(e.target.value); setSel(0); }}
            onKeyDown={(e) => {
              if (e.key === "Escape") onClose();
              else if (e.key === "ArrowDown") { e.preventDefault(); setSel(Math.min(pick + 1, shown.length - 1)); }
              else if (e.key === "ArrowUp") { e.preventDefault(); setSel(Math.max(pick - 1, 0)); }
              else if (e.key === "Enter") { e.preventDefault(); run(shown[pick]); }
            }}
          />
          <kbd>esc</kbd>
        </div>
        <div className="cmdk-list" ref={listRef}>
          {shown.length === 0 && (
            <div className="cmdk-empty">Nothing matches. Try a project, a person, or “inbox”.</div>
          )}
          {shown.map((c, i) => {
            const head = c.group !== group ? (group = c.group) : null;
            return (
              <div key={`${c.group}:${c.label}:${i}`}>
                {head && <div className="cmdk-g">{head}</div>}
                <button className={`cmdk-it${i === pick ? " on" : ""}`} data-i={i}
                  onMouseMove={() => setSel(i)} onClick={() => run(c)}>
                  {c.avatar ? <Avatar name={c.avatar.name} team={c.avatar.team} size="sm" />
                    : <span className="cmdk-ic"><Icon name={c.icon ?? "arrow"} size={15} /></span>}
                  <span className="grow">{c.label}</span>
                  {c.hint && <span className="cmdk-hint">{c.hint}</span>}
                </button>
              </div>
            );
          })}
        </div>
        <div className="cmdk-foot">
          <span><kbd>↑</kbd><kbd>↓</kbd> move</span>
          <span><kbd>↵</kbd> open</span>
          <span><kbd>?</kbd> every shortcut</span>
        </div>
      </div>
    </div>
  );
}

const SHEET: [string, string][] = [
  ["⌘ K  or  /", "Search and jump anywhere"],
  ["G then H", "Home"],
  ["G then I", "Inbox"],
  ["G then P", "All projects"],
  ["G then O", "This project’s inbox (its home)"],
  ["G then T", "This project’s team"],
  ["G then C", "Chat with the CEO"],
  ["G then R", "Results"],
  ["G then D", "Details"],
  ["G then M", "Details › Roadmap"],
  ["G then K", "Details › Tasks"],
  ["G then F", "Details › Deliverables"],
  ["G then S", "Details › Spend"],
  ["G then V", "Details › Reviews"],
  ["G then A", "Details › Activity"],
  ["G then ,", "This project’s settings"],
  ["G then 1 – 5", "Your pinned places, in sidebar order"],
  ["J / K", "Next / previous item in the Inbox"],
  ["Esc", "Close a panel"],
  ["?", "This list"],
];

export function Cheatsheet({ onClose }: { onClose: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="cmdk-back" onClick={onClose}>
      <div className="cmdk sheet" role="dialog" aria-modal="true" aria-label="Keyboard shortcuts"
        onClick={(e) => e.stopPropagation()}>
        <div className="cmdk-in">
          <Icon name="help" size={16} />
          <b className="grow">Keyboard shortcuts</b>
          <button className="small" onClick={onClose}>Close</button>
        </div>
        <dl className="sheet-list">
          {SHEET.map(([k, v]) => (
            <div key={k}><dt><kbd>{k}</kbd></dt><dd>{v}</dd></div>
          ))}
        </dl>
      </div>
    </div>
  );
}

function typing(): boolean {
  const el = document.activeElement as HTMLElement | null;
  if (!el) return false;
  return /^(input|textarea|select)$/i.test(el.tagName) || el.isContentEditable;
}

/** The global keys. Navigation only: no single key ever approves, denies or
    spends anything — a decision that costs money is worth two clicks. */
export function useShortcuts({
  projectId, openPalette, openCheatsheet, navigate,
}: {
  projectId: string | null;
  openPalette: () => void;
  openCheatsheet: () => void;
  navigate: NavigateFunction;
}) {
  const go = useRef(false);
  const { pinsFor } = usePins();
  const pins = projectId ? pinsFor(projectId) : [];
  const state = useRef({ projectId, openPalette, openCheatsheet, navigate, pins });
  state.current = { projectId, openPalette, openCheatsheet, navigate, pins };

  useEffect(() => {
    let timer: number | undefined;
    const onKey = (e: KeyboardEvent) => {
      const s = state.current;
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        s.openPalette();
        return;
      }
      // A page that binds a key itself wins: "/" on Deliverables is its own
      // search box, and opening the palette on top of that would be two answers.
      if (e.defaultPrevented || e.metaKey || e.ctrlKey || e.altKey || typing()) return;
      if (document.querySelector(".cmdk-back, .modal-back")) return;
      if (e.key === "/") { e.preventDefault(); s.openPalette(); return; }
      if (e.key === "?") { e.preventDefault(); s.openCheatsheet(); return; }
      if (go.current) {
        go.current = false;
        const k = e.key.toLowerCase();
        const top: Record<string, string> = { h: "/home", i: "/inbox", p: "/projects" };
        if (top[k]) { s.navigate(top[k]); return; }
        const pin = /^[1-5]$/.test(k) ? s.pins[Number(k) - 1] : undefined;
        if (pin && s.projectId) { s.navigate(`/projects/${s.projectId}/${pin.to}`); return; }
        const page = PROJECT_DESTINATIONS.find((p) => p.key === k);
        if (page && s.projectId) {
          s.navigate(page.path ? `/projects/${s.projectId}/${page.path}` : `/projects/${s.projectId}`);
        }
        return;
      }
      if (e.key === "g") {
        go.current = true;
        window.clearTimeout(timer);
        timer = window.setTimeout(() => { go.current = false; }, 1200);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => { window.removeEventListener("keydown", onKey); window.clearTimeout(timer); };
  }, []);
}
