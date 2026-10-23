import { Navigate, NavLink, Route, Routes } from "react-router-dom";
import { DETAIL_TABS } from "../../nav";
import { firstName, ProjectCtx } from "../../projectCtx";
import { PageHead } from "../../ui/kit";
import Plan from "./Plan";
import Tasks from "./Tasks";
import Deliverables from "./Deliverables";
import Spend from "./Spend";
import Review from "./Review";
import Activity from "./Activity";
import Settings from "./Settings";

/** One sentence per tab, only where the tab does not already say it. */
function tabLede(path: string, ctx: ProjectCtx): string | null {
  const ceo = firstName(ctx.ceoName);
  switch (path) {
    case "roadmap":
      return `What has to be true for this project to be done, in the order it can happen. ${ceo} `
        + "proposes each requirement; only the platform can mark one met.";
    case "deliverables":
      return "Every file the team has written, drafts and working files included. What was published "
        + "is also on Results, with who made it and who checked it.";
    case "spend":
      return "Every run is metered by the platform. The budget is the plan; no agent can talk "
        + "its way past the hard stop, where the project pauses itself.";
    case "activity":
      return `Everything that happened on this project. You can read every channel; you speak only to ${ceo}.`;
    default:
      return null;
  }
}

/** How the project works inside — the roadmap, the task board, every file,
    the money, the reviews, the record and the rules. Nothing here needs the
    chairman; anything that does is on Team and in the Inbox. Seven pages that
    each used to claim a line of the sidebar are tabs of this one, so the
    sidebar can say what the project is: a team, a CEO, and what they made. */
export default function Details({ ctx }: { ctx: ProjectCtx }) {
  const { projectId, dash } = ctx;
  const base = `/projects/${projectId}/details`;
  // An Explore project has no roadmap to show — it runs on bets, which live
  // on the task board — so it opens on Tasks instead.
  const tabs = DETAIL_TABS.filter((t) => !(dash.mode === "explore" && t.path === "roadmap"));
  return (
    <>
      <PageHead title="Details"
        lede="How the project works inside. Nothing here needs you; open it when you want to look." />
      <nav className="tabs" aria-label="Details">
        {tabs.map((t) => (
          <NavLink key={t.path} to={`${base}/${t.path}`}
            className={({ isActive }) => `tab${isActive ? " active" : ""}`}>
            {t.label}
            {t.path === "reviews" && ctx.reviews?.open && <span className="badge accent" style={{ marginLeft: 6 }}>open</span>}
          </NavLink>
        ))}
      </nav>
      <Routes>
        <Route index element={<Navigate to={tabs[0].path} replace />} />
        {tabs.map((t) => {
          const lede = tabLede(t.path, ctx);
          return (
            <Route key={t.path} path={t.path === "activity" ? "activity/*" : t.path} element={
              <>
                {lede && <p className="help details-lede">{lede}</p>}
                {t.path === "roadmap" ? <Plan ctx={ctx} />
                  : t.path === "tasks" ? <Tasks ctx={ctx} />
                  : t.path === "deliverables" ? <Deliverables projectId={projectId} />
                  : t.path === "spend" ? <Spend ctx={ctx} />
                  : t.path === "reviews" ? <Review ctx={ctx} />
                  : t.path === "activity" ? <Activity ctx={ctx} />
                  : <Settings projectId={projectId} dash={dash} org={ctx.org} reload={ctx.reload} />}
              </>
            } />
          );
        })}
        <Route path="*" element={<Navigate to={tabs[0].path} replace />} />
      </Routes>
    </>
  );
}
