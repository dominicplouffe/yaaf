import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { OrgView, post } from "../../api";
import { useDesk } from "../../desk";
import { firstName, ProjectCtx } from "../../projectCtx";
import { HirePanel, gapsFor, needsOf, useRoleLibrary } from "../../roles";
import { fmtUnit } from "../../ui";
import { Avatar, Seg } from "../../ui/kit";
import { Icon } from "../../ui/Icon";
import { heldFor, Seat, SEAT_LABEL, SEAT_TONE, SeatState } from "../../ui/team";

/** The org chart you hired, and what each person on it is doing.

    "See the org chart" used to scroll to a grid of team cards with names in
    them. This is the literal hierarchy — you, the CEO, each team and its
    people — and every face answers the question a roster is opened for:
    what are you doing right now? Click anyone for their live run.

    The chain of command is drawn, not just described: you speak to the CEO,
    the CEO directs the teams. That is why opening anyone else offers "Ask the
    CEO about them" and never a direct message — the boardroom rule, kept by
    the shape of the screen rather than by a sentence asking you to keep it. */

const STATE_ORDER: SeatState[] = ["working", "next", "waiting", "blocked", "idle", "paused", "stood_down"];

export function SeatPill({ state }: { state: SeatState }) {
  const tone = SEAT_TONE[state];
  return (
    <span className={`badge${tone ? ` ${tone}` : ""} seat-${state}`}>
      <span className={`seat-dot ${state}`} />
      {SEAT_LABEL[state]}
    </span>
  );
}

/** One face on the chart. */
function SeatNode({ seat, onOpen, wide, extra }: {
  seat: Seat; onOpen: (id: string) => void; wide?: boolean; extra?: React.ReactNode;
}) {
  const t = seat.today;
  return (
    <button className={`onode st-${seat.state}${wide ? " wide" : ""}`} onClick={() => onOpen(seat.id)}>
      <span className="on-id">
        <span className="av-wrap">
          <Avatar name={seat.name} team={seat.ceo ? "leadership" : seat.team} size="lg" />
          <span className={`seat-dot ring ${seat.state}`} />
        </span>
        <span className="on-name">
          <b>{seat.name}{seat.lead && !seat.ceo && <span className="on-lead">lead</span>}</b>
          <span>{seat.title}</span>
        </span>
      </span>
      <span className="on-state">
        <SeatPill state={seat.state} />
        {seat.since && <span className="on-since">{heldFor(seat.since)}</span>}
      </span>
      <span className="on-doing">{seat.doing}</span>
      <span className="on-foot">
        <span>{t?.runs ?? 0} run{(t?.runs ?? 0) === 1 ? "" : "s"} today</span>
        {t && t.usd > 0 && <span>${t.usd.toFixed(2)}</span>}
        {t && t.tasks_completed > 0 && <span>{t.tasks_completed} closed</span>}
      </span>
      {extra}
    </button>
  );
}

/** Stand down / recall, with the reason typed in place. Both verbs take one:
    a roster change without a reason is unreadable six weeks later, and "we
    took the designer off after the template shipped" is a sentence somebody
    has to be able to find. This used to be a browser prompt. */
function StaffingAction({ team, onDone }: { team: OrgView["teams"][number]; onDone: () => void }) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const verb = team.running ? "stand_down" : "recall";
  const label = team.running ? "Stand down" : "Recall";
  if (!open) {
    return <button className="small" onClick={() => setOpen(true)}>{label}…</button>;
  }
  return (
    <div className="reason-form">
      <input
        autoFocus
        value={reason}
        onChange={(e) => setReason(e.target.value)}
        placeholder={team.running
          ? `Why is ${team.name}'s job finished? (history and memory are kept)`
          : `Why is ${team.name} coming back?`}
        onKeyDown={(e) => { if (e.key === "Escape") setOpen(false); }}
      />
      <div className="reason-acts">
        <button className={`small${team.running ? " danger" : " primary"}`}
          disabled={busy || reason.trim().length < 10}
          title={reason.trim().length < 10 ? "A reason of at least ten characters — the CEO reads it" : undefined}
          onClick={async () => {
            setBusy(true);
            setError(null);
            try {
              await post(`/admin/teams/${team.id}/${verb}`, { reason: reason.trim() });
              setOpen(false);
              setReason("");
              onDone();
            } catch (e) {
              setError((e as Error).message);
            } finally {
              setBusy(false);
            }
          }}>
          {label}
        </button>
        <button className="small" onClick={() => setOpen(false)}>Cancel</button>
      </div>
      {error && <div className="help" style={{ color: "var(--critical)" }}>{error}</div>}
    </div>
  );
}

/** The condition in the words the boardroom uses, so the card and the message
    the chairman already read say the same thing. */
function conditionWords(when: NonNullable<OrgView["teams"][number]["stand_down_when"]>) {
  return when.kind === "requirement_met"
    ? `${(when.keys ?? []).join(" and ")} ${(when.keys ?? []).length === 1 ? "is" : "are"} met`
    : `everything up to stage ${when.stage} has settled`;
}

/** A team's heading on the chart: what it is, what it runs, what it owes. */
function TeamHead({ ctx, team, onRepinned }: {
  ctx: ProjectCtx; team: OrgView["teams"][number]; onRepinned: (msg: string) => void;
}) {
  const tasks = (ctx.tasks ?? []).filter((t) => t.team_id === team.id);
  const open = tasks.filter((t) => ["ready", "claimed", "checking", "review"].includes(t.status)).length;
  const stuck = tasks.filter((t) => t.status === "blocked").length;
  const behind = team.role_version !== null && team.role_latest_version !== null
    && team.role_version < team.role_latest_version;
  const steer = ctx.dash.can_steer !== false;
  return (
    <div className="team-h">
      <div className="team-t">
        <Avatar name={team.name} team={team.name} size="sm" />
        <b>{team.name}</b>
        {!team.running && <span className="badge">stood down</span>}
      </div>
      <div className="team-role">
        {team.role_name && (
          <span className="chip" title={team.role_slug ?? undefined}>{team.role_name} v{team.role_version}</span>
        )}
        {behind && steer && (
          <button className="small" title="A newer version of this role exists. Live teams never change silently — adopting is your call, and the CEO is told."
            onClick={async () => {
              const result = await post<{ changed: boolean; version: number }>(`/admin/teams/${team.id}/repin`, {});
              onRepinned(result.changed
                ? `${team.name} now runs ${team.role_name ?? team.role_slug} v${result.version}, from its next run.`
                : `${team.name} is already on the latest role version (v${result.version}).`);
              ctx.reload();
            }}>
            Adopt v{team.role_latest_version}
          </button>
        )}
      </div>
      <div className="team-sub">
        {tasks.length === 0 ? "No work on its board" : `${open} open${stuck ? ` · ${stuck} stuck` : ""}`}
        {team.stand_down_when && <> · stands down when {conditionWords(team.stand_down_when)}</>}
      </div>
      {steer && <StaffingAction team={team} onDone={ctx.reload} />}
    </div>
  );
}

/** Hiring onto a project that already exists. Here the chairman is not
    browsing — they know the symptom ("nothing gets checked"), so this opens on
    the gaps its own roster implies before it offers the library. */
function AddTeam({ ctx }: { ctx: ProjectCtx }) {
  const { projectId, org } = ctx;
  const { roles, crews, loaded, error: libraryError } = useRoleLibrary();
  const [open, setOpen] = useState(false);
  const [picked, setPicked] = useState<string[]>([]);
  const [teamName, setTeamName] = useState("");
  const [employees, setEmployees] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const roleSlug = picked[0] ?? "";
  const role = roles.find((r) => r.slug === roleSlug);
  // Which roles this project already runs, and under which team name — shown
  // on the row so hiring a second Data Analyst is a choice, not an accident.
  const alreadyOn: Record<string, string> = {};
  for (const team of org?.teams ?? []) {
    if (team.role_slug) alreadyOn[team.role_slug] = team.name;
  }
  const roster = roles.filter((r) => r.slug in alreadyOn);
  const gaps = gapsFor(roster).filter((gap) => gap.suggest.some((slug) => !(slug in alreadyOn)));

  function toggle(slug: string) {
    setPicked((prev) => (prev[0] === slug ? [] : [slug]));
    const next = roles.find((r) => r.slug === slug);
    if (next && !teamName.trim()) setTeamName(next.name.split(" / ")[0]);
  }

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      await post(`/admin/projects/${projectId}/teams`, {
        role_slug: roleSlug,
        name: teamName.trim(),
        employees: employees.split(",").map((s) => s.trim()).filter(Boolean),
      });
      setTeamName("");
      setEmployees("");
      setPicked([]);
      ctx.reload();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="org-branch add">
      <div className="team-h">
        <div className="team-t"><b>Add a team</b></div>
        <div className="team-sub">From the role library</div>
      </div>
      <div className="onode addnode">
        {!role && (
          <>
            <Icon name="plus" size={20} />
            <span>{gaps.length > 0 ? gaps[0].sentence : "Hire a role this project is missing."}</span>
          </>
        )}
        {error && <div className="error">{error}</div>}
        <button className="small" onClick={() => setOpen(true)}>
          {role ? `Role: ${role.name} — change` : "Choose a role…"}
        </button>
        {role && (
          <div className="addform">
            <label className="field">Team name</label>
            <input value={teamName} onChange={(e) => setTeamName(e.target.value)} placeholder="Content" />
            <label className="field">Who is on it (comma-separated; the first leads)</label>
            <input value={employees} onChange={(e) => setEmployees(e.target.value)} placeholder="Ada, Bea" />
            {needsOf(role.secret_scopes ?? []).length > 0 && (
              <p className="help">
                {role.name} needs {needsOf(role.secret_scopes ?? []).map((n) => n.words).join(" and ")} —
                add it in Settings → Data &amp; credentials, scoped to this role, or its runs will
                fail asking for it.
              </p>
            )}
            <button className="primary small"
              disabled={busy || !roleSlug || !teamName.trim() || !employees.trim()} onClick={submit}>
              Add team
            </button>
            <span className="help">The CEO sees the new team in its next plan.</span>
          </div>
        )}
      </div>
      {open && (
        <HirePanel
          roles={roles} crews={crews} loaded={loaded} error={libraryError}
          selected={picked} onToggle={toggle} onClose={() => setOpen(false)}
          alreadyOn={alreadyOn} gaps={gaps} title="Who's missing?"
        />
      )}
    </div>
  );
}

function OrgChart({ ctx }: { ctx: ProjectCtx }) {
  const { org, seats, dash } = ctx;
  const { mine } = useDesk();
  const [repinMsg, setRepinMsg] = useState<string | null>(null);
  const ceo = seats.find((s) => s.ceo);
  const waiting = mine.filter((i) => i.project.id === ctx.projectId).length;
  return (
    <>
      {repinMsg && <div className="notice-box">{repinMsg}</div>}
      <div className="org-scroll">
        <div className="org">
          <div className="org-level">
            <div className="onode chair">
              <span className="on-id">
                <Avatar name={dash.chairman?.display_name ?? "You"} team="chair" size="lg" />
                <span className="on-name">
                  <b>{dash.chairman?.display_name ?? "The chairman"}</b>
                  <span>Chairman{dash.my_role === "chairman" ? " · you" : ""}</span>
                </span>
              </span>
              <span className="on-doing">
                Decides money, legal questions, credentials, and whatever the autonomy dial leaves
                with the chair.
              </span>
              <Link className="btnlink small" to="/inbox">
                <Icon name="inbox" size={14} />
                {waiting > 0 ? `${waiting} waiting in the Inbox` : "Nothing waiting on the chair"}
              </Link>
            </div>
          </div>
          <div className="org-v" />
          {ceo && (
            <div className="org-level">
              <SeatNode seat={ceo} onOpen={ctx.openAgent} wide
                extra={<span className="on-ceo"><Icon name="message" size={13} /> The only agent you talk to</span>} />
            </div>
          )}
          <div className="org-v" />
          <div className="org-branches">
            {(org?.teams ?? []).map((team) => (
              <div className="org-branch" key={team.id}>
                <TeamHead ctx={ctx} team={team} onRepinned={setRepinMsg} />
                <div className="team-members">
                  {seats.filter((s) => s.teamId === team.id).map((s) => (
                    <SeatNode key={s.id} seat={s} onOpen={ctx.openAgent} />
                  ))}
                </div>
              </div>
            ))}
            {dash.can_steer !== false && <AddTeam ctx={ctx} />}
          </div>
        </div>
      </div>
    </>
  );
}

function SeatTable({ ctx }: { ctx: ProjectCtx }) {
  // The project's own unit: a subscription project is metered in tokens, and a
  // dollar column of dashes would read as "nobody spent anything".
  const usdMode = ctx.dash.billing_mode === "api_key";
  const spent = (t: Seat["today"]) => !t ? 0
    : usdMode ? t.usd : t.tokens_in + t.tokens_out + t.cache_creation + t.cache_read;
  const rows = [...ctx.seats].sort(
    (a, b) => STATE_ORDER.indexOf(a.state) - STATE_ORDER.indexOf(b.state));
  return (
    <div className="card" style={{ padding: 0, overflowX: "auto" }}>
      <table className="seat-table">
        <thead>
          <tr><th>Person</th><th>Team</th><th>Status</th><th>Doing now</th><th className="num">Runs today</th><th className="num">Spent today</th></tr>
        </thead>
        <tbody>
          {rows.map((s) => (
            <tr key={s.id} onClick={() => ctx.openAgent(s.id)} className="clickable">
              <td><span className="person-cell"><Avatar name={s.name} team={s.ceo ? "leadership" : s.team} size="sm" />
                <span><b>{s.name}</b><span className="help">{s.title}</span></span></span></td>
              <td>{s.ceo ? "CEO" : s.team}{s.lead && !s.ceo ? " · lead" : ""}</td>
              <td><SeatPill state={s.state} /></td>
              <td className="doing-cell">{s.doing}</td>
              <td className="num">{s.today?.runs ?? 0}</td>
              <td className="num">{spent(s.today) ? fmtUnit(spent(s.today), usdMode ? "usd" : "tokens") : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function Team({ ctx }: { ctx: ProjectCtx }) {
  const [params, setParams] = useSearchParams();
  const view = params.get("view") === "list" ? "list" : "chart";
  const ceo = firstName(ctx.ceoName);
  const counts = new Map<SeatState, number>();
  for (const s of ctx.seats) counts.set(s.state, (counts.get(s.state) ?? 0) + 1);
  return (
    <>
      <div className="relay">
        <Icon name="message" size={16} />
        <span>
          You direct the team through {ceo}. To change what someone is working on, tell {ceo} and
          the plan changes with it.
        </span>
        <Link to={`/projects/${ctx.projectId}/chat`} className="btnlink small">Message {ceo}</Link>
      </div>
      <div className="viewbar">
        <Seg<"chart" | "list">
          value={view}
          onPick={(v) => setParams(v === "list" ? { view: "list" } : {}, { replace: true })}
          options={[{ key: "chart", label: "Org chart" }, { key: "list", label: "List" }]}
        />
        <span className="grow" />
        <span className="legend">
          {STATE_ORDER.filter((s) => counts.get(s)).map((s) => (
            <span key={s} className="legend-i"><span className={`seat-dot ${s}`} />{counts.get(s)} {SEAT_LABEL[s].toLowerCase()}</span>
          ))}
        </span>
      </div>
      {!ctx.org ? <p className="subtitle">Loading…</p> : view === "chart" ? <OrgChart ctx={ctx} /> : <SeatTable ctx={ctx} />}
    </>
  );
}
