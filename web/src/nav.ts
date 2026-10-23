import { IconName } from "./ui/Icon";

/** A project is a team at work, so it has four pages, each answering one
    question: Team (who is doing my job, how is it going, what do they need
    from me?), Chat (talk to the CEO who runs them), Results (what has the team
    delivered, and who checked it?) and Details (how it works inside, for when
    you want to look). Ten pages in the sidebar made every one of them look
    equally important, and the team, the thing a chairman actually bought, was
    the sixth. */
export const PROJECT_PAGES: { path: string; label: string; icon: IconName; key: string }[] = [
  { path: "", label: "Team", icon: "org", key: "t" },
  { path: "chat", label: "Chat", icon: "message", key: "c" },
  { path: "results", label: "Results", icon: "check", key: "r" },
  { path: "details", label: "Details", icon: "layout", key: "d" },
];

/** The tabs under Details, in the order a reader reaches for them. Every
    page the sidebar used to list is one of these or part of Team. */
export const DETAIL_TABS: { path: string; label: string; icon: IconName; key: string; words: string }[] = [
  { path: "roadmap", label: "Roadmap", icon: "flag", key: "m", words: "plan requirements" },
  { path: "tasks", label: "Tasks", icon: "kanban", key: "k", words: "board work bets" },
  { path: "deliverables", label: "Deliverables", icon: "folder", key: "f", words: "files drafts working" },
  { path: "spend", label: "Spend", icon: "wallet", key: "s", words: "money budget cost" },
  { path: "reviews", label: "Reviews", icon: "meeting", key: "v", words: "review meeting" },
  { path: "activity", label: "Activity", icon: "history", key: "a",
    words: "records runs messages memory decisions evidence proof audit" },
  { path: "settings", label: "Settings", icon: "sliders", key: ",", words: "rules brief people money" },
];

/** Every place inside a project the palette and the G-keys can go. */
export const PROJECT_DESTINATIONS = [
  ...PROJECT_PAGES.map((p) => ({ ...p, words: p.path === "" ? "org chart people agents home overview"
    : p.path === "chat" ? "boardroom ceo message" : p.path === "results" ? "published deliverables" : "" })),
  ...DETAIL_TABS.map((t) => ({ ...t, path: `details/${t.path}`, label: `Details › ${t.label}` })),
];
