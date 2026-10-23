import { createContext, ReactNode, useCallback, useContext, useMemo } from "react";
import {
  ActivityView,
  ApprovalRow,
  AskRow,
  Dashboard,
  get,
  HypothesisRow,
  OrgView,
  PlanView,
  ProjectHealth,
  ReviewsView,
  RunRow,
  TaskRow,
} from "./api";
import { useNavigate } from "react-router-dom";
import { usePoll } from "./hooks";
import { Seat, seatsOf } from "./ui/team";
import { useProjectLive } from "./ws";

/** Everything a project screen needs, polled once for the whole frame.

    The project used to poll inside its page, under the tabs — so the sidebar
    could not say who was working, and four tabs each polled /org on their own
    schedule and disagreed with each other. The frame now owns the polls and
    hands them to the sidebar, the page and the agent drawer alike: one answer
    to "who is awake", on one clock. */
export interface ProjectCtx {
  projectId: string;
  dash: Dashboard;
  /** Polled here, not by the pages: the delivery tile needs the commitment and
      the Roadmap needs all of it — two polls on two clocks is two answers. */
  plan: PlanView | null;
  approvals: ApprovalRow[];
  asks: AskRow[];
  hypotheses: HypothesisRow[];
  org: OrgView | null;
  health: ProjectHealth | null;
  tasks: TaskRow[] | null;
  runs: RunRow[] | null;
  /** Each seat's day, in the project's own timezone. */
  today: ActivityView | null;
  reviews: ReviewsView | null;
  /** The join of all of the above: every person and what they are doing. */
  seats: Seat[];
  ceoName: string;
  asOf: Date | null;
  error: string | null;
  reload: () => void;
  reloadApprovals: () => void;
  reloadOrg: () => void;
  reloadHealth: () => void;
  reloadPlan: () => void;
  reloadTasks: () => void;
  /** Open one person's page — any screen can, on anyone. */
  openAgent: (agentId: string | null) => void;
}

interface Frame {
  ctx: ProjectCtx | null;
  /** Set while the first answer is still in flight or after a failure that
      left nothing to show. */
  loading: boolean;
  error: string | null;
}

const ProjectContext = createContext<Frame | null>(null);

export function ProjectProvider({ projectId, children }: { projectId: string; children: ReactNode }) {
  const { data: dash, error, at, reload } = usePoll(
    () => get<Dashboard>(`/observe/projects/${projectId}`), 6000, [projectId]);
  // Latency hint over the 6s poll: a change stamp on the socket pulls the
  // reload forward. The poll stays the source of truth.
  useProjectLive(projectId, reload);

  const base = `/observe/projects/${projectId}`;
  const { data: approvals, reload: reloadApprovals } = usePoll(
    () => get<ApprovalRow[]>(`${base}/approvals`), 6000, [projectId]);
  const { data: asks } = usePoll(() => get<AskRow[]>(`${base}/asks`), 6000, [projectId]);
  const { data: hypotheses } = usePoll(
    () => get<HypothesisRow[]>(`${base}/hypotheses`), 15000, [projectId]);
  const { data: org, reload: reloadOrg } = usePoll(
    () => get<OrgView>(`${base}/org`), 15000, [projectId]);
  const { data: health, reload: reloadHealth } = usePoll(
    () => get<ProjectHealth>(`${base}/health`), 10000, [projectId]);
  const { data: plan, reload: reloadPlan } = usePoll(
    () => get<PlanView>(`${base}/plan`), 8000, [projectId]);
  const { data: tasks, reload: reloadTasks } = usePoll(
    () => get<TaskRow[]>(`${base}/board`), 10000, [projectId]);
  const { data: runs } = usePoll(() => get<RunRow[]>(`${base}/runs?limit=200`), 10000, [projectId]);
  const { data: today } = usePoll(
    () => get<ActivityView>(`${base}/activity?since=today`), 15000, [projectId]);
  const { data: reviews } = usePoll(() => get<ReviewsView>(`${base}/reviews`), 30000, [projectId]);

  // A person on the team is a page of their own now, not a drawer, so
  // "open" is a navigation — and a link to them is something you can send.
  const navigate = useNavigate();
  const openAgent = useCallback((id: string | null) => {
    if (id) navigate(`/projects/${projectId}/team/${id}`);
  }, [navigate, projectId]);

  const seats = useMemo(
    () => (dash ? seatsOf({ org, dash, tasks, runs, approvals: approvals ?? [], seats: today?.seats }) : []),
    [org, dash, tasks, runs, approvals, today],
  );

  const frame = useMemo<Frame>(() => {
    if (!dash) return { ctx: null, loading: !error, error };
    return {
      loading: false,
      error,
      ctx: {
        projectId, dash, plan, org, health, tasks, runs, today, reviews, seats,
        approvals: approvals ?? [],
        asks: asks ?? [],
        hypotheses: hypotheses ?? [],
        ceoName: org?.ceo?.name ?? "your CEO",
        asOf: at,
        error,
        reload, reloadApprovals, reloadOrg, reloadHealth, reloadPlan, reloadTasks,
        openAgent,
      },
    };
  }, [projectId, dash, plan, org, health, tasks, runs, today, reviews, seats, approvals, asks,
    hypotheses, at, error, reload, reloadApprovals, reloadOrg, reloadHealth, reloadPlan,
    reloadTasks, openAgent]);

  return <ProjectContext.Provider value={frame}>{children}</ProjectContext.Provider>;
}

/** The project the reader is inside, or null on a screen that is not one. */
export function useProjectFrame(): Frame | null {
  return useContext(ProjectContext);
}

/** For a component that only ever renders inside a loaded project. */
export function useProject(): ProjectCtx {
  const frame = useContext(ProjectContext);
  if (!frame?.ctx) throw new Error("useProject() outside a loaded project");
  return frame.ctx;
}

/** A first name for a sentence: "Chat with Iris", not "Chat with Iris Vale". */
export function firstName(name: string): string {
  return name.split(" ")[0] || name;
}
