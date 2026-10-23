import { ReactNode, useEffect, useLayoutEffect, useState } from "react";
import { Link, NavLink, useLocation, useNavigate } from "react-router-dom";
import { DeskProject } from "./api";
import { useDesk } from "./desk";
import { useMe } from "./me";
import { CommandPalette, Cheatsheet, useShortcuts } from "./palette";
import { firstName, useProjectFrame } from "./projectCtx";
import { PROJECT_PAGES } from "./nav";
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
  to, icon, label, end, aside, onClick,
}: {
  to: string; icon: IconName; label: ReactNode; end?: boolean; aside?: ReactNode; onClick?: () => void;
}) {
  return (
    <NavLink to={to} end={end} onClick={onClick}
      className={({ isActive }) => "sb-item" + (isActive ? " on" : "")}>
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
  project, onSwitch, onNavigate,
}: {
  project: { id: string; name: string; status: string };
  onSwitch: () => void;
  onNavigate: () => void;
}) {
  const frame = useProjectFrame();
  const ctx = frame?.ctx?.dash.id === project.id ? frame.ctx : null;
  const { mine } = useDesk();
  const needs = mine.filter((i) => i.project.id === project.id).length;

  // Each page carries the one number that says whether it is worth opening.
  const aside: Record<string, ReactNode> = {};
  if (ctx) {
    const { dash, plan, seats, reviews } = ctx;
    if (plan?.burnup) aside.roadmap = `${plan.burnup.settled}/${plan.burnup.scope_total}`;
    aside.tasks = `${dash.open_tasks} open`;
    const people = seats.filter((s) => s.state !== "stood_down");
    const busy = people.filter((s) => s.state === "working").length;
    aside.team = people.length ? `${busy}/${people.length}` : undefined;
    const cap = dash.billing_mode === "api_key" ? dash.caps.monthly_usd_cap : dash.caps.monthly_token_cap;
    const spent = dash.billing_mode === "api_key"
      ? dash.month_to_date.usd
      : dash.month_to_date.tokens_in + dash.month_to_date.tokens_out
        + dash.month_to_date.cache_creation + dash.month_to_date.cache_read;
    if (cap > 0) aside.spend = `${Math.round((spent / cap) * 100)}%`;
    if (reviews?.open) aside.reviews = <span className="sb-pill">open</span>;
  }
  const unread = ctx?.dash.boardroom_unread ?? 0;
  const explore = ctx?.dash.mode === "explore";
  const ceo = ctx ? firstName(ctx.ceoName) : null;
  const base = `/projects/${project.id}`;

  return (
    <>
      <div className="sb-head">Project</div>
      <button className="sb-switch" onClick={onSwitch} title="Switch project (⌘K)">
        <Avatar name={project.name} team={project.name} size="sm" />
        <span className="sb-switch-t">
          <b>{project.name}</b>
          <span>
            <i className={`dot ${project.status === "running" ? "run" : ""}`} />
            {project.status === "running" ? "Running" : project.status === "paused" ? "Paused"
              : project.status === "draft" ? "Not started" : project.status}
            {needs > 0 && <> · <span className="warn">{needs} need you</span></>}
          </span>
        </span>
        <Icon name="updown" size={14} />
      </button>
      {PROJECT_PAGES.filter((p) => !(explore && p.path === "roadmap")).map((p) => (
        <NavItem
          key={p.path}
          to={p.path ? `${base}/${p.path}` : base}
          end={p.path === ""}
          icon={p.icon}
          label={p.path === "chat" && ceo ? `Chat with ${ceo}` : p.label}
          aside={p.path === "chat" && unread > 0
            ? <span className="sb-badge" title={`${unread} new in the boardroom since you last read it`}>
                {unread > 99 ? "99+" : unread}
              </span>
            : aside[p.path] !== undefined ? <span className="sb-aside">{aside[p.path]}</span>
            : p.path === "chat" ? <span className="sb-aside">CEO</span> : undefined}
          onClick={onNavigate}
        />
      ))}
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
      parts.push({ label: page.path === "chat" ? `Chat with ${firstName(frame.ctx.ceoName)}` : page.label });
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
  const [palette, setPalette] = useState<{ open: boolean; query: string }>({ open: false, query: "" });
  const [sheet, setSheet] = useState(false);
  const [navOpen, setNavOpen] = useState(false);
  const projects = data?.projects ?? [];
  const current = useCurrentProject(projects);

  useShortcuts({
    projectId: current?.id ?? null,
    openPalette: () => setPalette({ open: true, query: "" }),
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
  useLayoutEffect(() => window.scrollTo(0, 0), [location.pathname]);

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
        <button className="sb-search" onClick={() => setPalette({ open: true, query: "" })}>
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
            <ProjectNav project={current} onNavigate={close}
              onSwitch={() => setPalette({ open: true, query: "project " })} />
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
          <button className="sb-icon tb-search" onClick={() => setPalette({ open: true, query: "" })}
            aria-label="Search">
            <Icon name="search" size={16} />
          </button>
        </header>
        <main className="main">{children}</main>
      </div>
      {palette.open && (
        <CommandPalette
          initialQuery={palette.query}
          projects={projects}
          currentProjectId={current?.id ?? null}
          onClose={() => setPalette({ open: false, query: "" })}
          openCheatsheet={() => setSheet(true)}
          signOut={() => void signOut()}
        />
      )}
      {sheet && <Cheatsheet onClose={() => setSheet(false)} />}
    </div>
  );
}
