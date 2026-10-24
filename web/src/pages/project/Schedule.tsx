import { get, ScheduleView } from "../../api";
import { usePoll } from "../../hooks";
import type { ProjectCtx } from "../../projectCtx";

/** Who works when — one row per person across the day around now.

    A team that works while you are not looking is the thing you bought, and
    the one question it raises is "when do they actually work?". The answer
    was a list of rhythms on the agenda ("every 6h", "daily at 07:00") that
    nobody could turn into a picture of the day. This is the picture: the
    last twelve hours as the runs that happened, a line at now, and the next
    twelve as the wakes the platform will fire. Everything else on the team
    wakes when handed work, and the row says so rather than drawing nothing. */
export default function ScheduleStrip({ ctx }: { ctx: ProjectCtx }) {
  const { data } = usePoll(
    () => get<ScheduleView>(`/observe/projects/${ctx.projectId}/schedule`), 60000, [ctx.projectId]);
  if (!data || data.rows.length === 0) return null;
  // On a large team most rows would be empty: people who wake only when
  // handed work and have not run. Those are counted, not drawn.
  const QUIET_AFTER = 10;
  const active = data.rows.filter((r) => r.ran.length || r.next.length);
  const rows = data.rows.length > QUIET_AFTER ? active : data.rows;
  const quiet = data.rows.length - rows.length;
  const from = new Date(data.from).getTime();
  const span = new Date(data.to).getTime() - from;
  const pos = (iso: string) => Math.min(100, Math.max(0, ((new Date(iso).getTime() - from) / span) * 100));
  const ticks = [-12, -6, 0, 6, 12];
  const hour = (h: number) => {
    const at = new Date(new Date(data.now).getTime() + h * 3_600_000);
    return h === 0 ? "now" : at.toLocaleTimeString([], { hour: "numeric" });
  };
  return (
    <section className="card sched" aria-label="Who works when">
      <div className="card-h">
        <h3>Who works when</h3>
        <span className="help">the last 12 hours as they happened · the next 12 as scheduled</span>
      </div>
      {!data.live && <p className="help">This project is not running, so nothing ahead is scheduled.</p>}
      <div className="sched-grid">
        <div className="sched-axis" aria-hidden="true">
          <span />
          <span className="sched-track">
            {ticks.map((h) => (
              <i key={h} style={{ left: `${(h + 12) / 24 * 100}%` }}>{hour(h)}</i>
            ))}
          </span>
        </div>
        {rows.map((r) => (
          <div className={`sched-row${r.paused ? " paused" : ""}`} key={r.agent_id}>
            <button type="button" className="sched-who linkish" onClick={() => ctx.openAgent(r.agent_id)}
              title={`${r.name} wakes ${r.wakes}`}>
              <b>{r.name}</b>{r.team !== r.name && <span className="help"> {r.team}</span>}
            </button>
            <span className="sched-track" role="img"
              aria-label={`${r.name}: ${r.ran.length} run${r.ran.length === 1 ? "" : "s"} in the last 12 hours, `
                + `${r.next.length} scheduled wake${r.next.length === 1 ? "" : "s"} in the next 12; wakes ${r.wakes}`}>
              <i className="sched-now" style={{ left: "50%" }} />
              {r.ran.map((x) => (
                <i key={x.run_id} className={`sched-run st-${x.status}`}
                  style={{ left: `${pos(x.at)}%`, width: `max(3px, ${pos(x.until) - pos(x.at)}%)` }}
                  title={`${new Date(x.at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })} · ${x.status}`} />
              ))}
              {r.next.map((x, i) => (
                <i key={i} className="sched-next" style={{ left: `${pos(x.at)}%` }}
                  title={`${x.label} · ${new Date(x.at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}`} />
              ))}
            </span>
          </div>
        ))}
      </div>
      {quiet > 0 && (
        <p className="help sched-quiet">
          {quiet} {quiet === 1 ? "other person" : "others"} did not run in the last 12 hours and
          {quiet === 1 ? " has" : " have"} no scheduled wake: they work when handed work or a message.
        </p>
      )}
      <p className="help sched-key">
        <span className="sched-swatch run" /> ran <span className="sched-swatch next" /> scheduled wake ·
        anyone without a scheduled wake works when handed work or a message.
      </p>
    </section>
  );
}
