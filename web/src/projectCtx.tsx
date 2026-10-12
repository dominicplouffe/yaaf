import { createContext, ReactNode, useCallback, useContext, useMemo, useState } from "react";
import {
  ActivityView,
  AgendaView,
  ApprovalRow,
  AskRow,
  Dashboard,
  FlagsView,
  get,
  HypothesisRow,
  MessageRow,
  OrgView,
  OverviewView,
  PlanView,
  ProjectHealth,
  ReviewsView,
  RunRow,
  SpendReport,
  TaskRow,
} from "./api";
import { usePoll } from "./hooks";
import { Seat, seatsOf } from "./ui/team";
import { useProjectLive } from "./ws";

/** Everything a project screen needs, read once for the whole frame.

    The project used to poll inside its page, under the tabs — so the sidebar
    could not say who was working, and four tabs each polled /org on their own
    schedule and disagreed with each other. The frame then owned the polls, but
    still eleven of them on six clocks, plus five more on the Overview: the seat
    list could be drawn from one moment and the runs beside it from another.
    It is one request now, `/overview`, read in one pass on the server, so
    every part of the screen describes the same moment, give or take the
    milliseconds between two queries. (The Overview's newest deliverables are
    the exception, and the server says why.) */
export interface ProjectCtx {
  projectId: string;
  dash: Dashboard;
  /** Read here, not by the pages: the delivery tile needs the commitment and
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
  agenda: AgendaView | null;
  flags: FlagsView | null;
  /** This month's spend by team — the rows add up to the budget meter. */
  spendByTeam: SpendReport | null;
  /** The boardroom's last few messages, for the CEO's latest word. */
  boardroom: MessageRow[];
  /** The join of all of the above: every person and what they are doing. */
  seats: Seat[];
  ceoName: string;
  asOf: Date | null;
  error: string | null;
  /** Ask again now — after anything the reader just did. One read, so there is
      nothing to choose between: whatever changed, every section is redrawn. */
  reload: () => void;
  /** The agent drawer is the frame's, so any screen can open it on anyone. */
  agentOpen: string | null;
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
  const { data, error, at, reload } = usePoll(
    () => get<OverviewView>(`/observe/projects/${projectId}/overview`), 6000, [projectId]);
  // Latency hint over the 6s poll: a change stamp on the socket pulls the
  // reload forward. The poll stays the source of truth.
  useProjectLive(projectId, reload);

  const [agentOpen, setAgentOpen] = useState<string | null>(null);
  const openAgent = useCallback((id: string | null) => setAgentOpen(id), []);

  const seats = useMemo(
    () => (data ? seatsOf({
      org: data.org, dash: data.dashboard, tasks: data.board, runs: data.runs,
      approvals: data.approvals, seats: data.today.seats,
    }) : []),
    [data],
  );

  const frame = useMemo<Frame>(() => {
    if (!data) return { ctx: null, loading: !error, error };
    return {
      loading: false,
      error,
      ctx: {
        projectId,
        dash: data.dashboard,
        plan: data.plan,
        approvals: data.approvals,
        asks: data.asks,
        hypotheses: data.hypotheses,
        org: data.org,
        health: data.health,
        tasks: data.board,
        runs: data.runs,
        today: data.today,
        reviews: data.reviews,
        agenda: data.agenda,
        flags: data.flags,
        spendByTeam: data.spend_by_team,
        boardroom: data.boardroom,
        seats,
        ceoName: data.org.ceo?.name ?? "your CEO",
        asOf: at,
        error,
        reload,
        agentOpen, openAgent,
      },
    };
  }, [projectId, data, seats, at, error, reload, agentOpen, openAgent]);

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
