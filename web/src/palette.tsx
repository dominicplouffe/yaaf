import { useEffect, useMemo, useRef, useState } from "react";
import { NavigateFunction, useNavigate } from "react-router-dom";
import { DeskProject } from "./api";
import { firstName, useProjectFrame } from "./projectCtx";
import { PROJECT_DESTINATIONS } from "./nav";
import { Icon, IconName } from "./ui/Icon";
import { applyTheme, Avatar } from "./ui/kit";
import { SEAT_LABEL } from "./ui/team";

/** Search and jump, from anywhere, without knowing where anything lives.

    A product you are meant to learn by browsing still has to let you skip the
    browsing once you know what you want. ⌘K finds a page, a project or a
    person by name, and does the handful of things worth doing from anywhere.
    Nothing here is a new capability — every entry is a door to a screen that
    already exists, so the palette can never become a second way to decide
    something with fewer safeguards than the first. */

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
  return useMemo(() => {
    const out: Command[] = [
      { group: "Go to", label: "Home", icon: "home", hint: "G H", run: () => navigate("/home") },
      { group: "Go to", label: "Inbox", icon: "inbox", hint: "G I", words: "desk approvals questions decisions",
        run: () => navigate("/inbox") },
      { group: "Go to", label: "All projects", icon: "grid", hint: "G P", run: () => navigate("/projects") },
    ];
    if (currentProjectId) {
      const base = `/projects/${currentProjectId}`;
      const ceo = ctx ? firstName(ctx.ceoName) : "your CEO";
      for (const p of PROJECT_DESTINATIONS) {
        out.push({
          group: "Go to",
          label: p.path === "chat" ? `Chat with ${ceo}` : p.label,
          icon: p.icon,
          hint: p.key === "," ? undefined : `G ${p.key.toUpperCase()}`,
          words: p.words,
          run: () => navigate(p.path ? `${base}/${p.path}` : base),
        });
      }
    }
    out.push(
      { group: "Go to", label: "Role library", icon: "users", words: "hire roles people", run: () => navigate("/roles") },
      { group: "Go to", label: "Settings", icon: "gear", words: "credentials account slack", run: () => navigate("/settings") },
    );
    for (const p of projects) {
      out.push({
        group: "Projects", label: p.name, avatar: { name: p.name, team: p.name },
        hint: p.status === "running" ? "Running" : p.status, words: `project ${p.goal}`,
        run: () => navigate(`/projects/${p.id}`),
      });
    }
    if (ctx) {
      for (const s of ctx.seats) {
        out.push({
          group: "People", label: `${s.name} — ${s.ceo ? "CEO" : s.team}`, avatar: { name: s.name, team: s.ceo ? "leadership" : s.team },
          hint: SEAT_LABEL[s.state], words: `${s.title} ${s.doing}`,
          run: () => ctx.openAgent(s.id),
        });
      }
      out.push({
        group: "Actions", label: `Message ${firstName(ctx.ceoName)}…`, icon: "send", words: "ceo chat boardroom ask",
        run: () => navigate(`/projects/${ctx.projectId}/chat`),
      });
    }
    out.push(
      { group: "Actions", label: "Start a new project", icon: "plus", words: "create", run: () => navigate("/projects/new") },
      { group: "Actions", label: "Use the light theme", icon: "sun", words: "theme", run: () => applyTheme("light") },
      { group: "Actions", label: "Use the dark theme", icon: "moon", words: "theme", run: () => applyTheme("dark") },
      { group: "Actions", label: "Follow the system theme", icon: "monitor", words: "theme auto", run: () => applyTheme("auto") },
      { group: "Actions", label: "Keyboard shortcuts", icon: "help", hint: "?", run: openCheatsheet },
      { group: "Actions", label: "Sign out", icon: "logout", run: signOut },
    );
    return out;
  }, [projects, currentProjectId, navigate, openCheatsheet, signOut, ctx]);
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
  const shown = commands.filter((c) => {
    const hay = `${c.group} ${c.label} ${c.words ?? ""}`.toLowerCase();
    return words.every((w) => hay.includes(w));
  });
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
              <div key={`${c.group}:${c.label}`}>
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
  const state = useRef({ projectId, openPalette, openCheatsheet, navigate });
  state.current = { projectId, openPalette, openCheatsheet, navigate };

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
