import { ReactNode, useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { Link, NavLink, useLocation, useNavigate } from "react-router-dom";
import { DeskProject } from "./api";
import { useDesk } from "./desk";
import { useMe } from "./me";
import { CommandPalette, Cheatsheet, useShortcuts } from "./palette";
import { OfferHost } from "./precedent";
import { ProjectSwitcher, statusWord as statusWordOf } from "./projectSwitcher";
import { UndoHost } from "./undo";
import { firstName, useProjectFrame } from "./projectCtx";
import { DETAIL_TABS, PROJECT_PAGES, placeOf, SETTINGS_PAGE } from "./nav";
import { lastDetailsTab, placeAt, usePins, useRememberPlaces } from "./shortcuts";
import { Icon, IconName } from "./ui/Icon";
import { Avatar, Person, ThemeToggle } from "./ui/kit";
import { SEAT_LABEL } from "./ui/team";

/** The app's frame: a sidebar you can learn the product from, a top bar that
    says where you are, and the page.

    The old sidebar held four places and a create button, and everything else
    lived behind a project's tabs — so the only way to discover the product was
    to open a project and click along a row of words. The sidebar now holds the
    whole map: the places that span projects, the current project's pages with
    a number beside each, who is working right now, and the workspace. A
    person who has never been told what a "boardroom" is can find it by
    reading the left edge of the screen. */

const LAST_PROJECT = "yaaf_last_project";

function rememberProject(id: string) {
  try {
    localStorage.setItem(LAST_PROJECT, id);
  } catch {
    /* a browser that refuses storage just forgets which project was last */
  }
}

function lastProject(): string | null {
  try {
    return localStorage.getItem(LAST_PROJECT);
  } catch {
    return null;
  }
}

function NavItem({
  to, icon, label, end, aside, onClick, match,
}: {
  to: string; icon: IconName; label: ReactNode; end?: boolean; aside?: ReactNode; onClick?: () => void;
  /** Lit for everything under this path, when `to` goes somewhere deeper. */
  match?: string;
}) {
  const { pathname } = useLocation();
  const under = match !== undefined && (pathname === match || pathname.startsWith(`${match}/`));
  return (
    <NavLink to={to} end={end} onClick={onClick}
      className={({ isActive }) => "sb-item" + (isActive || under ? " on" : "")}>
      <Icon name={icon} size={16} />
      <span className="sb-label">{label}</span>
      {aside}
    </NavLink>
  );
}

/** Which project the project half of the sidebar is about: the one you are
    in, else the one you were last in — so leaving for the Inbox and coming
    back is one click, not a trip through Projects. */
function useCurrentProject(projects: DeskProject[]): { id: string; name: string; status: string } | null {
  const frame = useProjectFrame();
  const inside = frame?.ctx?.dash;
  useEffect(() => {
    if (inside) rememberProject(inside.id);
  }, [inside]);
  if (inside) return { id: inside.id, name: inside.name, status: inside.status };
  const last = lastProject();
  const known = projects.find((p) => p.id === last) ?? null;
  return known ? { id: known.id, name: known.name, status: known.status } : null;
}

/** The project half of the sidebar: who it is, how it is, and its pages. */
function ProjectNav({
  project, projects, onNavigate,
}: {
  project: { id: string; name: string; status: string };
  projects: DeskProject[];
  onNavigate: () => void;
}) {
  const frame = useProjectFrame();
  const ctx = frame?.ctx?.dash.id === project.id ? frame.ctx : null;
  const { mine } = useDesk();
  const needs = mine.filter((i) => i.project.id === project.id).length;

  // Each page carries the one number that says whether it is worth opening.
  const aside: Record<string, ReactNode> = {};
  if (ctx) {
    const { seats, reviews } = ctx;
    const people = seats.filter((s) => s.state !== "stood_down");
    const busy = people.filter((s) => s.state === "working").length;
    // Team carries who is at it; Details only flags the one thing inside it
    // that is an occasion rather than a reference — an open review.
    aside.team = people.length ? `${busy}/${people.length}` : undefined;
    // The project's Inbox carries the same count as the Inbox above it, for
    // this project only — the one number on the sidebar that asks for you.
    if (needs > 0) {
      aside[""] = <span className="sb-badge" title="Decisions on this project only you can make">{needs}</span>;
    }
    if (reviews?.open) aside.details = <span className="sb-pill">review open</span>;
  }
  const unread = ctx?.dash.boardroom_unread ?? 0;
  const ceo = ctx ? firstName(ctx.ceoName) : null;
  const base = `/projects/${project.id}`;

  const { pinsFor, toggle } = usePins();
  const pins = pinsFor(project.id);
  const location = useLocation();
  const here = placeAt(location.pathname, location.search, project.id);
  // Details opens on the tab you were last on: a reader who lives in Runs
  // should not pass through the Roadmap every time to get back to them.
  const lastTab = lastDetailsTab();
  const lastLabel = lastTab ? placeOf(`details/${lastTab}`)?.label : null;
  const setup = ctx?.setup?.open ? ctx.setup : null;
  const settingsOn = location.pathname.startsWith(`${base}/settings`);
  const statusWord = statusWordOf(project.status);
  const row = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const [switching, setSwitching] = useState(false);
  const closeSwitcher = useCallback((refocus: boolean) => {
    setSwitching(false);
    if (refocus) trigger.current?.focus();
  }, []);

  return (
    <>
      <div className="sb-head">Project</div>
      <div className="sb-switch-row" ref={row}>
        <button className={`sb-switch${switching ? " open" : ""}`} ref={trigger}
          onClick={() => setSwitching((v) => !v)}
          aria-haspopup="dialog" aria-expanded={switching}
          title={`${project.name} · ${statusWord}${needs > 0 ? ` · ${needs} need you` : ""}. Switch project`}>
          <Avatar name={project.name} team={project.name} size="sm" />
          <span className="sb-switch-t">
            <b>{project.name}</b>
            <span>
              <i className={`dot ${project.status === "running" ? "run" : ""}`} />
              {/* The card shares its row with the gear, so one fact fits:
                  what needs you when anything does, the state otherwise —
                  the dot still says running or not. */}
              {needs > 0 ? <span className="warn">{needs} need you</span>
                : ctx && !ctx.dash.billing_secret_id && ctx.dash.integrations?.runtime !== "mock"
                  ? <>{statusWord} · <span className="warn">no key</span></>
                  : statusWord}
            </span>
          </span>
          <Icon name="updown" size={14} />
        </button>
        {/* Settings is what the project is configured to be, so it hangs off
            the project itself, the way a gear sits on the thing it configures —
            one click from anywhere, and no row of its own. */}
        <Link to={`${base}/${SETTINGS_PAGE.path}`} onClick={onNavigate}
          className={`sb-gear${settingsOn ? " on" : ""}`} title="Project settings (G then ,)"
          aria-label={`${project.name} settings`} aria-current={settingsOn ? "page" : undefined}>
          <Icon name="gear" size={15} />
        </Link>
      </div>
      {switching && (
        <ProjectSwitcher anchor={row} trigger={trigger} projects={projects} currentId={project.id}
          onClose={closeSwitcher} onNavigate={onNavigate} />
      )}
      {PROJECT_PAGES.map((p) => (
        <NavItem
          key={p.path}
          to={p.path === "details" && lastTab ? `${base}/details/${lastTab}` : p.path ? `${base}/${p.path}` : base}
          match={p.path === "details" ? `${base}/details` : undefined}
          end={p.path === ""}
          icon={p.icon}
          label={p.path === "chat" && ceo ? `Chat with ${ceo}` : p.label}
          aside={p.path === "chat" && unread > 0
            ? <span className="sb-badge" title={`${unread} new in the boardroom since you last read it`}>
                {unread > 99 ? "99+" : unread}
              </span>
            : aside[p.path] !== undefined ? <span className="sb-aside">{aside[p.path]}</span>
            : p.path === "chat" ? <span className="sb-aside">CEO</span>
            : p.path === "details" && lastLabel && lastTab !== "roadmap"
              ? <span className="sb-aside" title="Details opens where you left it">{lastLabel}</span>
            : undefined}
          onClick={onNavigate}
        />
      ))}
      {(setup || pins.length > 0) && <div className="sb-rule" role="separator" />}
      {/* While the project is being set up, one row goes straight to the next
          thing it needs, and goes away when nothing is left. */}
      {setup && setup.next && (
        <Link to={`${base}/settings?s=${setup.next.section}`} onClick={onNavigate}
          className={`sb-item sb-setup${here === `settings?s=${setup.next.section}` ? " on" : ""}`}
          title={`Next: ${setup.next.label}`}>
          <Icon name="check" size={16} />
          <span className="sb-label">Finish setup</span>
          <span className="sb-aside">{setup.settled} of {setup.total}</span>
        </Link>
      )}
      {pins.map((pin, i) => {
        const place = placeOf(pin.to.split("?open=")[0]);
        const on = here === pin.to;
        return (
          <div key={`${pin.project_id ?? ""}:${pin.to}`} className={`sb-item sb-pin${on ? " on" : ""}`}>
            <Link to={`${base}/${pin.to}`} onClick={onNavigate} className="sb-pin-a"
              title={`${pin.label}${i < 5 ? ` (G then ${i + 1})` : ""}`} aria-current={on ? "page" : undefined}>
              <Icon name={pin.to.includes("?open=") ? "file" : place?.icon ?? "pin"} size={16} />
              <span className="sb-label">{pin.label}</span>
            </Link>
            <button className="sb-unpin" onClick={() => void toggle(pin)}
              title={`Unpin ${pin.label}`} aria-label={`Unpin ${pin.label}`}>
              <Icon name="x" size={13} />
            </button>
          </div>
        );
      })}
    </>
  );
}

/** Who is working right now, one click from what they are doing. A team
    working while you are not looking is the thing you bought; it belongs in
    the frame, not at the foot of one page. */
function LiveNow() {
  const frame = useProjectFrame();
  const ctx = frame?.ctx;
  if (!ctx) return null;
  const live = ctx.seats.filter((s) => s.state === "working" || s.state === "waiting" || s.state === "next");
  const working = live.filter((s) => s.state === "working").length;
  return (
    <>
      <div className="sb-head">
        Live now
        <span className={`sb-live${working ? " on" : ""}`}><i />{working}</span>
      </div>
      {live.length === 0 && <div className="sb-quiet">Nobody is working on this right now.</div>}
      {live.slice(0, 6).map((s) => (
        <button key={s.id} className="sb-item sb-person" onClick={() => ctx.openAgent(s.id)}
          title={`${s.name} · ${SEAT_LABEL[s.state]} · ${s.doing}`}>
          <span className={`seat-dot ${s.state}`} />
          <span className="sb-label"><b>{s.name}</b> <span>{s.doing}</span></span>
        </button>
      ))}
      {live.length > 6 && (
        <Link className="sb-more" to={`/projects/${ctx.projectId}/team`}>and {live.length - 6} more</Link>
      )}
    </>
  );
}

/** "Projects › Semis Coverage Desk › Team" — so a reader dropped onto any
    screen by a link knows where they landed. */
function Crumbs() {
  const location = useLocation();
  const frame = useProjectFrame();
  const path = location.pathname;
  const parts: { label: string; to?: string }[] = [];
  const project = path.match(/^\/projects\/([^/]+)(?:\/([^/]+))?/);
  if (project && project[1] !== "new" && frame?.ctx) {
    const page = PROJECT_PAGES.find((p) => p.path === (project[2] ?? ""));
    parts.push({ label: "Projects", to: "/projects" });
    parts.push({ label: frame.ctx.dash.name, to: `/projects/${project[1]}` });
    if (page && page.path) {
      const tab = page.path === "details"
        ? DETAIL_TABS.find((t) => path.startsWith(`/projects/${project[1]}/details/${t.path}`))
        : undefined;
      // One person's page sits under Team, and the crumb goes back there.
      const person = page.path === "team" && path.split("/").length > 4;
      parts.push({
        label: page.path === "chat" ? `Chat with ${firstName(frame.ctx.ceoName)}` : page.label,
        to: tab ? `/projects/${project[1]}/details` : person ? `/projects/${project[1]}/team` : undefined,
      });
      if (tab) parts.push({ label: tab.label });
      if (person) {
        const seat = frame.ctx.seats.find((s) => s.id === path.split("/")[4]);
        parts.push({ label: seat?.name ?? "Person" });
      }
    } else if (project[2] === SETTINGS_PAGE.path) {
      const section = placeOf(`settings${location.search}`);
      parts.push({ label: "Settings", to: `/projects/${project[1]}/settings` });
      if (section && section.label !== "Settings") parts.push({ label: section.label });
    }
  } else if (path.startsWith("/projects/new")) {
    parts.push({ label: "Projects", to: "/projects" }, { label: "New project" });
  } else {
    const top: Record<string, string> = {
      "/home": "Home", "/inbox": "Inbox", "/projects": "Projects",
      "/roles": "Role library", "/settings": "Settings",
    };
    parts.push({ label: top[path] ?? "" });
  }
  return (
    <nav className="tb-crumbs" aria-label="Where you are">
      {parts.map((p, i) => (
        <span key={i} className="tb-crumb">
          {i > 0 && <Icon name="right" size={13} />}
          {p.to && i < parts.length - 1 ? <Link to={p.to}>{p.label}</Link> : <b>{p.label}</b>}
        </span>
      ))}
    </nav>
  );
}

/** How fresh the screen is. Every number here polls itself; this says when the
    last one landed, so nobody has to wonder whether to press refresh. */
function Freshness() {
  const frame = useProjectFrame();
  const { at, error } = useDesk();
  const when = frame?.ctx?.asOf ?? at;
  const failed = frame?.ctx?.error ?? error;
  if (!when) return null;
  return (
    <span className={`tb-live${failed ? " stale" : ""}`}
      title={failed ? `Not refreshing: ${failed}` : "Every screen updates itself — there is no refresh button."}>
      <i />
      {failed ? "Not refreshing" : "Live"} · {when.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })}
    </span>
  );
}

export function Shell({ children }: { children: ReactNode }) {
  const { me, signOut } = useMe();
  const { data, mine } = useDesk();
  const navigate = useNavigate();
  const location = useLocation();
  const [palette, setPalette] = useState(false);
  const [sheet, setSheet] = useState(false);
  const [navOpen, setNavOpen] = useState(false);
  const projects = data?.projects ?? [];
  const current = useCurrentProject(projects);
  const frameNow = useProjectFrame();
  useRememberPlaces(frameNow?.ctx?.projectId ?? null);

  useShortcuts({
    projectId: current?.id ?? null,
    openPalette: () => setPalette(true),
    openCheatsheet: () => setSheet(true),
    navigate,
  });

  // A phone's drawer closes itself once you have gone somewhere.
  useEffect(() => setNavOpen(false), [location.pathname]);

  // A new page starts at its top. The window is the scroller and HashRouter
  // restores nothing, so without this every navigation kept the last page's
  // offset — and the project tabs share one frame that is already loaded, so
  // the new tab drew at full height and you landed as far down it as you had
  // been on the old one: on a shorter page, the bottom. Keyed on the path, not
  // the query, so a filter or a selection inside a page keeps your place.
  // A layout effect, so it runs before a page's own mount effect that scrolls
  // to something on purpose (NewProject's `?start=file`).
  //
  // A block body, never `() => window.scrollTo(0, 0)`: whatever an effect
  // returns, React keeps as its cleanup and calls on the next navigation.
  // Browsers return undefined, but an extension that replaces scrollTo may
  // not, and then every link threw "destroy is not a function".
  useLayoutEffect(() => {
    window.scrollTo(0, 0);
  }, [location.pathname]);

  const close = () => setNavOpen(false);
  return (
    <div className={`app${navOpen ? " nav-open" : ""}`}>
      <aside className="side" aria-label="Main navigation">
        <div className="sb-top">
          <Link to="/home" className="brand" title={`Build ${__BUILD_TIME__}`}>y<span>aa</span>f</Link>
          <span className="grow" />
          <Link to="/projects/new" className="sb-icon" title="New project" aria-label="New project">
            <Icon name="plus" size={16} strokeWidth={2.2} />
          </Link>
          <button className="sb-icon" onClick={() => setSheet(true)} title="Keyboard shortcuts (?)"
            aria-label="Keyboard shortcuts">
            <Icon name="help" size={16} />
          </button>
        </div>
        <button className="sb-search" onClick={() => setPalette(true)}>
          <Icon name="search" size={15} />
          <span className="grow">Search or jump to…</span>
          <kbd>⌘K</kbd>
        </button>
        <div className="sb-scroll">
          <NavItem to="/home" icon="home" label="Home" onClick={close} />
          <NavItem to="/inbox" icon="inbox" label="Inbox" onClick={close}
            aside={mine.length > 0 ? (
              <span className="sb-badge" title="Decisions only you can make. Each one decides itself if you leave it.">
                {mine.length}
              </span>
            ) : undefined} />
          <NavItem to="/projects" icon="grid" label="Projects" end onClick={close}
            aside={projects.length ? <span className="sb-aside">{projects.length}</span> : undefined} />
          {current && (
            <ProjectNav project={current} projects={projects} onNavigate={close} />
          )}
          <LiveNow />
          <div className="sb-head">Workspace</div>
          <NavItem to="/roles" icon="users" label="Role library" onClick={close} />
          <NavItem to="/settings" icon="gear" label="Settings" onClick={close} />
        </div>
        <div className="sb-foot">
          {me && (
            <Link to="/settings?section=account" className="whoami" title={`${me.email} — your account`}>
              <Person name={me.display_name} role={me.email} size="sm" />
            </Link>
          )}
          <div className="sb-foot-row">
            <ThemeToggle />
            <button className="sb-icon" onClick={() => void signOut()} title="Sign out" aria-label="Sign out">
              <Icon name="logout" size={15} />
            </button>
          </div>
        </div>
      </aside>
      <div className="sb-scrim" onClick={close} />
      <div className="col">
        <header className="topbar">
          <button className="sb-icon tb-menu" onClick={() => setNavOpen(true)} aria-label="Open navigation">
            <Icon name="panel" size={17} />
          </button>
          <Crumbs />
          <span className="grow" />
          <Freshness />
          <button className="sb-icon tb-search" onClick={() => setPalette(true)}
            aria-label="Search">
            <Icon name="search" size={16} />
          </button>
        </header>
        <main className="main">{children}</main>
        <OfferHost />
        <UndoHost />
      </div>
      {palette && (
        <CommandPalette
          projects={projects}
          currentProjectId={current?.id ?? null}
          onClose={() => setPalette(false)}
          openCheatsheet={() => setSheet(true)}
          signOut={() => void signOut()}
        />
      )}
      {sheet && <Cheatsheet onClose={() => setSheet(false)} />}
    </div>
  );
}
