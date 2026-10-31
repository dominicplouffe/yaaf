import { RefObject, useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { DeskProject } from "./api";
import { useDesk } from "./desk";
import { Icon } from "./ui/Icon";
import { Avatar } from "./ui/kit";

/** The project switcher: the sidebar card's own dropdown, and nothing else.

    It is the control every workspace product puts in that spot — Slack's
    workspace menu, Linear's, Vercel's and Supabase's project pickers, GitHub's
    context switcher — and readers arrive knowing what it does: the card shows
    where you are, the ⇅ promises a list, and clicking opens that list right
    under it. Your projects, the one you are in ticked, a field to narrow them
    when there are many, and the two doors that belong with a list of projects:
    all of them, and a new one.

    It used to open the ⌘K palette with "project " typed in, which matched
    every Settings section, three actions and two pages before it reached a
    single project. Pages, people and actions are what ⌘K is for, and it sits
    directly above this card; mixing them in here is what made it a mess. */

export function statusWord(status: string): string {
  return status === "running" ? "Running" : status === "paused" ? "Paused"
    : status === "draft" ? "Not started" : status;
}

interface Place { top: number; left: number; width: number; maxHeight: number }

/** Fixed to the viewport rather than inside the sidebar, whose scroll box
    would clip it or make it scroll with the nav. */
function placeUnder(el: HTMLElement | null): Place | undefined {
  const r = el?.getBoundingClientRect();
  if (!r) return undefined;
  const width = Math.min(Math.max(r.width, 300), window.innerWidth - 16);
  const left = Math.max(8, Math.min(r.left, window.innerWidth - width - 8));
  const top = r.bottom + 4;
  return { top, left, width, maxHeight: Math.max(200, window.innerHeight - top - 12) };
}

export function ProjectSwitcher({
  anchor, trigger, projects, currentId, onClose, onNavigate,
}: {
  /** The row the menu hangs from; it opens beneath it, at least as wide. */
  anchor: RefObject<HTMLElement>;
  /** The button that toggles it, whose own click closes it. */
  trigger: RefObject<HTMLElement>;
  projects: DeskProject[];
  currentId: string;
  onClose: (refocus: boolean) => void;
  onNavigate: () => void;
}) {
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const { mine } = useDesk();
  const box = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const [query, setQuery] = useState("");
  const [sel, setSel] = useState(() => Math.max(0, projects.findIndex((p) => p.id === currentId)));
  const input = useRef<HTMLInputElement>(null);
  // Measured before the first paint, so the menu is never drawn hidden — a
  // hidden field cannot take focus, and typing would go nowhere.
  const [place, setPlace] = useState(() => placeUnder(anchor.current));

  const needs = useMemo(() => {
    const n = new Map<string, number>();
    for (const i of mine) n.set(i.project.id, (n.get(i.project.id) ?? 0) + 1);
    return n;
  }, [mine]);

  const q = query.trim().toLowerCase();
  const shown = q ? projects.filter((p) => p.name.toLowerCase().includes(q)) : projects;
  const pick = Math.min(sel, Math.max(0, shown.length - 1));

  useEffect(() => {
    input.current?.focus();
    const measure = () => setPlace(placeUnder(anchor.current));
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, [anchor]);

  // A press anywhere else closes it. The trigger is left to its own click,
  // which toggles — closing here too would have that click reopen it.
  useEffect(() => {
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node;
      if (box.current?.contains(t) || trigger.current?.contains(t)) return;
      onClose(false);
    };
    document.addEventListener("mousedown", onDown, true);
    return () => document.removeEventListener("mousedown", onDown, true);
  }, [trigger, onClose]);

  useEffect(() => {
    listRef.current?.querySelector<HTMLElement>(`[data-i="${pick}"]`)?.scrollIntoView({ block: "nearest" });
  }, [pick]);

  function go(to: string) {
    onClose(false);
    onNavigate();
    navigate(to);
  }

  function choose(p: DeskProject | undefined) {
    if (!p) return;
    const base = `/projects/${p.id}`;
    // Picking the project you are already in is a no-op, as in every switcher.
    if (pathname === base || pathname.startsWith(`${base}/`)) onClose(true);
    else go(base);
  }

  return (
    <div className="psw" ref={box} role="dialog" aria-label="Switch project"
      style={place}
      onKeyDown={(e) => {
        if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); onClose(true); }
        // ⌘K opens the palette over the page; this menu should not wait under it.
        else if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") onClose(false);
      }}>
      <div className="psw-find">
        <Icon name="search" size={14} />
        <input
          ref={input}
          role="combobox"
          aria-expanded="true"
          aria-controls="psw-list"
          aria-activedescendant={shown[pick] ? `psw-${shown[pick].id}` : undefined}
          aria-label="Find a project"
          placeholder="Find a project…"
          value={query}
          onChange={(e) => { setQuery(e.target.value); setSel(0); }}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") { e.preventDefault(); setSel(Math.min(pick + 1, shown.length - 1)); }
            else if (e.key === "ArrowUp") { e.preventDefault(); setSel(Math.max(pick - 1, 0)); }
            else if (e.key === "Enter") { e.preventDefault(); choose(shown[pick]); }
          }}
        />
      </div>
      <div className="psw-list" id="psw-list" role="listbox" aria-label="Projects" ref={listRef}>
        {shown.length === 0 && (
          <div className="psw-empty">{projects.length ? `No project is called “${query.trim()}”.` : "No projects yet."}</div>
        )}
        {shown.map((p, i) => {
          const n = needs.get(p.id) ?? 0;
          const current = p.id === currentId;
          return (
            <div key={p.id} id={`psw-${p.id}`} data-i={i} role="option" aria-selected={i === pick}
              aria-current={current ? "true" : undefined}
              className={`psw-it${i === pick ? " on" : ""}`}
              onMouseMove={() => setSel(i)} onClick={() => choose(p)}>
              <Avatar name={p.name} team={p.name} size="sm" />
              <span className="psw-t">
                <b>{p.name}</b>
                <span>
                  <i className={`dot ${p.status === "running" ? "run" : ""}`} />
                  {n > 0 ? <span className="warn">{n} need you</span> : statusWord(p.status)}
                </span>
              </span>
              {current && <Icon name="check" size={15} className="psw-check" />}
            </div>
          );
        })}
      </div>
      <div className="psw-foot">
        <button onClick={() => go("/projects")}>
          <Icon name="grid" size={15} />
          <span className="grow">All projects</span>
          <kbd>G P</kbd>
        </button>
        <button onClick={() => go("/projects/new")}>
          <Icon name="plus" size={15} />
          <span className="grow">New project</span>
        </button>
      </div>
    </div>
  );
}
