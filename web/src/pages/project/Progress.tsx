import { NavLink, Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { get, ReviewsView } from "../../api";
import { usePoll } from "../../hooks";
import { Btn, Seg } from "../../ui/kit";
import { Icon } from "../../ui/Icon";
import type { ProjectCtx } from "../Project";
import Plan from "./Plan";
import Work from "./Work";
import Review from "./Review";
import Evidence from "./Evidence";

/** Will this land, and is any of it real?
 *
 * Three tabs used to ask that: Plan held what has to be true, Work held who is
 * doing it, Review held the meeting about both. The chain is one chain — a
 * requirement, the tasks serving it, the evidence that settles it — and
 * splitting it across three destinations meant the reader carried the join in
 * their own head, and had to guess which of the three a given question lived
 * under. It is one place with three lenses now.
 *
 * Review came in with them because a review is a *moment on the plan*, not a
 * location. When one is open it should be impossible to miss, which is what
 * the banner below does; when none is, a tab that only ever explains its own
 * emptiness teaches people to stop clicking it. */

const LENSES = [
  { key: "plan", label: "The plan" },
  { key: "board", label: "Who is doing it" },
  { key: "proof", label: "What proves it" },
] as const;

type Lens = (typeof LENSES)[number]["key"];

function lensOf(pathname: string): Lens {
  if (pathname.includes("/progress/board")) return "board";
  if (pathname.includes("/progress/proof")) return "proof";
  return "plan";
}

/** A review opens on a schedule, costs nothing to run, and is the one meeting
    the platform holds whether or not the board has anything on it. It gets a
    banner rather than a tab so that "there is a meeting waiting for you" is
    visible from the screen you were already on. */
function ReviewBanner({ projectId, number }: { projectId: string; number: number }) {
  return (
    <div className="strip" style={{ marginTop: 0 }}>
      <Icon name="message" size={18} />
      <div className="grow">
        <strong>Your standing review with the CEO is open.</strong>
        <div className="sb">
          It has the platform's own numbers frozen at the moment it opened, the CEO's account of
          why they are what they are, and the six things you can do about it.
        </div>
      </div>
      <NavLink to={`/projects/${projectId}/progress/review`}>
        <Btn kind="primary">Open review #{number}</Btn>
      </NavLink>
    </div>
  );
}

export default function Progress({ ctx }: { ctx: ProjectCtx }) {
  const { projectId } = ctx;
  const location = useLocation();
  const navigate = useNavigate();
  const { data: reviews } = usePoll(
    () => get<ReviewsView>(`/observe/projects/${projectId}/reviews`),
    15000,
  );

  const onReview = location.pathname.includes("/progress/review");
  const lens = lensOf(location.pathname);
  const counts: Record<Lens, number | undefined> = {
    plan: ctx.plan?.requirements?.length,
    board: ctx.dash.open_tasks,
    proof: undefined,
  };

  return (
    <>
      {reviews?.open && !onReview && (
        <ReviewBanner projectId={projectId} number={reviews.open.number} />
      )}

      {onReview ? (
        <div style={{ margin: "var(--s4) 0" }}>
          <NavLink to={`/projects/${projectId}/progress`} style={{ fontSize: "var(--t-small)", fontWeight: 600 }}>
            ← Back to the plan
          </NavLink>
        </div>
      ) : (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: "var(--s4)",
            margin: "var(--s4) 0",
            flexWrap: "wrap",
          }}
        >
          <Seg
            value={lens}
            onPick={(k) =>
              navigate(k === "plan" ? `/projects/${projectId}/progress` : `/projects/${projectId}/progress/${k}`)
            }
            options={LENSES.map((l) => ({ key: l.key, label: l.label, count: counts[l.key] }))}
          />
          <span style={{ fontSize: "var(--t-small)", color: "var(--muted)", flex: 1, minWidth: 260 }}>
            What has to be true, who is doing it, and what proves it — one chain, three ways of
            looking at it.
          </span>
          {reviews && reviews.history.length > 0 && !reviews.open && (
            <NavLink
              to={`/projects/${projectId}/progress/review`}
              style={{ fontSize: "var(--t-small)", fontWeight: 600 }}
            >
              Past reviews
            </NavLink>
          )}
        </div>
      )}

      <Routes>
        <Route index element={<Plan ctx={ctx} />} />
        <Route path="board" element={<Work ctx={ctx} />} />
        <Route path="proof" element={<Evidence projectId={projectId} />} />
        <Route path="review" element={<Review ctx={ctx} />} />
        <Route path="*" element={<Navigate to={`/projects/${projectId}/progress`} replace />} />
      </Routes>
    </>
  );
}
