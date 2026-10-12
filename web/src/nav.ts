import { IconName } from "./ui/Icon";

/** The pages inside a project, in the order a chairman reaches for them. */
export const PROJECT_PAGES: { path: string; label: string; icon: IconName; key: string }[] = [
  { path: "", label: "Overview", icon: "layout", key: "o" },
  { path: "chat", label: "Chat", icon: "message", key: "c" },
  { path: "roadmap", label: "Roadmap", icon: "flag", key: "r" },
  { path: "tasks", label: "Tasks", icon: "kanban", key: "k" },
  { path: "deliverables", label: "Deliverables", icon: "folder", key: "d" },
  { path: "team", label: "Team", icon: "org", key: "t" },
  { path: "spend", label: "Spend", icon: "wallet", key: "s" },
  { path: "reviews", label: "Reviews", icon: "meeting", key: "v" },
  { path: "activity", label: "Activity", icon: "history", key: "a" },
  { path: "settings", label: "Settings", icon: "sliders", key: "," },
];
