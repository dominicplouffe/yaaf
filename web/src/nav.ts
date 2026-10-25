import { IconName } from "./ui/Icon";

/** A project's pages, each answering one question: Inbox (what does it need
    from me, and what should I know?), Team (who is doing my job, and what is
    each of them on?), Chat (talk to the CEO who runs them), Results (what has
    the team delivered, and who checked it?) and Details (how it works inside,
    for when you want to look). Ten pages in the sidebar made every one of
    them look equally important; one page holding both the decisions and the
    org chart fell apart the first time a project had thirteen teams. */
export const PROJECT_PAGES: { path: string; label: string; icon: IconName; key: string }[] = [
  { path: "", label: "Inbox", icon: "inbox", key: "o" },
  { path: "team", label: "Team", icon: "org", key: "t" },
  { path: "chat", label: "Chat", icon: "message", key: "c" },
  { path: "results", label: "Results", icon: "check", key: "r" },
  { path: "details", label: "Details", icon: "layout", key: "d" },
];

/** The tabs under Details, in the order a reader reaches for them. Every
    page the sidebar used to list is one of these, part of Team, or Settings. */
export const DETAIL_TABS: { path: string; label: string; icon: IconName; key: string; words: string }[] = [
  { path: "roadmap", label: "Roadmap", icon: "flag", key: "m", words: "plan requirements" },
  { path: "tasks", label: "Tasks", icon: "kanban", key: "k", words: "board work bets" },
  { path: "deliverables", label: "Deliverables", icon: "folder", key: "f", words: "files drafts working" },
  { path: "spend", label: "Spend", icon: "wallet", key: "s", words: "money budget cost" },
  { path: "reviews", label: "Reviews", icon: "meeting", key: "v", words: "review meeting" },
  { path: "activity", label: "Activity", icon: "history", key: "a",
    words: "records runs messages memory decisions evidence proof audit" },
];

/** A project's Settings: what the project is configured to be, not how its
    work is going. It was the last tab of Details, three clicks from anywhere
    and the page a new project needs most; it opens from the gear on the
    project in the sidebar now, and each section has an address of its own. */
export const SETTINGS_PAGE = { path: "settings", label: "Settings", icon: "gear" as IconName, key: "," };

// Eleven headings on one scrolling page is not navigation. Ordered by
// necessity, not by subsystem: the docs say the required path is brief →
// money → data sources, and everything after that is occasional.
//
// Data sources and variables were one page, "Data & credentials", with an
// add form open at the top of each half. They are two different objects with
// two different lifecycles — a connection with a health state, and a value —
// so they are two sections, beside the other things agents plug into.
export const SETTINGS_NAV = [
  { group: "The work" },
  { key: "brief", label: "Brief", words: "goal standing orders" },
  { key: "money", label: "Money", words: "budget billing key credential pay fallback" },
  { group: "Connections" },
  { key: "sources", label: "Data sources", words: "mcp database api connect" },
  { key: "secrets", label: "Secrets & variables", words: "credentials api key token env" },
  { key: "integrations", label: "Integrations", words: "slack github webhooks" },
  { group: "Governance" },
  { key: "people", label: "People", words: "members co-chair observer invite" },
  { key: "rules", label: "Rules in force", words: "constitution autonomy pace brakes" },
  { group: "Housekeeping" },
  { key: "duplicate", label: "Duplicate & export", words: "clone copy twin" },
  { key: "advanced", label: "Advanced", words: "runtime mock" },
] as const;

export const SETTINGS_SECTIONS = SETTINGS_NAV.filter(
  (i): i is Extract<(typeof SETTINGS_NAV)[number], { key: string }> => "key" in i);

// Every way of asking "what happened?", on one page. The five record views
// were a utility tab; they lead with the narrative now, because "what did I
// miss?" is the question this page is opened with, and the five tables are
// where that sentence's evidence lives.
export const ACTIVITY_VIEWS = [
  ["record", "Everything", "every line, filterable by person and kind, each saying on whose authority it happened"],
  ["summary", "What happened", "the briefing: what moved, what is worth your eye"],
  ["runs", "Runs", "every model call, why it woke, what it cost"],
  ["decisions", "Decisions", "everything you decided, and everything that decided itself"],
  // Evidence is a record and belongs with the records. On the Work tab it was
  // drawn as a queue with a verdict button on every row and a percentage of it
  // done, which is a demand rather than an account.
  ["evidence", "Proof", "every claim the agents made, and what the platform could check"],
  // Before Messages: the inbox answers "who has not read what", which is the
  // question the inbox banner raises and the channel log cannot answer.
  ["inbox", "Agent inbox", "each agent's unread messages"],
  ["messages", "Messages", "every channel you can read but not speak in"],
  ["memory", "Memory", "what the project chose to remember"],
] as const;

/** Every place inside a project the palette and the G-keys can go. */
export const PROJECT_DESTINATIONS = [
  ...PROJECT_PAGES.map((p) => ({ ...p, words: p.path === "" ? "home overview decisions needs you approvals questions"
    : p.path === "team" ? "org chart people agents who" : p.path === "chat" ? "boardroom ceo message"
      : p.path === "results" ? "published deliverables" : "" })),
  ...DETAIL_TABS.map((t) => ({ ...t, path: `details/${t.path}`, label: `Details › ${t.label}` })),
  { ...SETTINGS_PAGE, label: "Project settings", words: "configure gear" },
];

/** What a place inside a project is called and drawn as, from its address
    under /projects/<id>/ — for a pin, and for the palette's recent places.
    Null for an address that is not a place worth returning to by name. */
export function placeOf(rel: string): { label: string; icon: IconName } | null {
  const [path, query = ""] = rel.split("?");
  const q = new URLSearchParams(query);
  const parts = path.split("/").filter(Boolean);
  if (parts.length === 0) return { label: "Inbox", icon: "inbox" };
  const page = PROJECT_PAGES.find((p) => p.path === parts[0]);
  if (parts[0] === SETTINGS_PAGE.path) {
    const s = q.get("s") === "data" ? "secrets" : q.get("s");
    const section = SETTINGS_SECTIONS.find((x) => x.key === s);
    return section ? { label: section.label, icon: s === "secrets" ? "key" : s === "sources" ? "plug" : "gear" }
      : { label: "Settings", icon: "gear" };
  }
  if (parts[0] !== "details") return page && parts.length === 1 ? { label: page.label, icon: page.icon } : null;
  const tab = DETAIL_TABS.find((t) => t.path === parts[1]);
  if (!tab) return { label: "Details", icon: "layout" };
  if (tab.path === "activity" && parts[2]) {
    const view = ACTIVITY_VIEWS.find(([p]) => p === parts[2]);
    if (view && view[0] !== "record") return { label: view[1], icon: "history" };
  }
  return { label: tab.label, icon: tab.icon };
}
