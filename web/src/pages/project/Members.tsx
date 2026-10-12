// Who may decide on this project, and who may only read it.
//
// A project is the unit that plays the part an organisation plays elsewhere —
// its own CEO, roster, budget and constitution — so the people who govern it
// are listed with it, not on a page above it. Three roles and no more: one
// chairman who answers for the project, any number of co-chairs who decide as
// the chairman's peers, any number of observers who read.
//
// Only the chairman decides who decides. Everybody else sees the same list with
// no verbs on it, because the server would refuse the verbs anyway, and a
// button that exists only to produce a 403 is a lie about who holds it.

import { Fragment, useState } from "react";
import {
  ChairHandover,
  Dashboard,
  del,
  get,
  MemberRow,
  patch,
  post,
  ProjectRole,
  ROLE_LABEL,
} from "../../api";
import { usePoll } from "../../hooks";
import { useMe } from "../../me";
import { ConfirmModal } from "../../ui";
import { Badge, Btn, Person } from "../../ui/kit";

/** One line each: what the role holds, in the verbs it holds. */
const ROLE_MEANS: { role: ProjectRole; means: string }[] = [
  { role: "chairman", means: "decides, and decides who decides. There is exactly one." },
  { role: "co_chair", means: "decides — approvals, scope, verdicts, dates, the rules — as the chairman's peer." },
  { role: "observer", means: "reads everything the project shows, and decides nothing." },
];

type Note = { ok: boolean; text: string } | null;

function RoleLegend() {
  return (
    <dl className="kv" style={{ margin: 0 }}>
      {ROLE_MEANS.map((r) => (
        <Fragment key={r.role}>
          <dt>{ROLE_LABEL[r.role]}</dt>
          <dd>{r.means}</dd>
        </Fragment>
      ))}
    </dl>
  );
}

function AddMember({ projectId, onAdded }: { projectId: string; onAdded: (said: string) => void }) {
  const [email, setEmail] = useState("");
  // Least first. Widening somebody later is one click; un-deciding something
  // they decided because they were added a rung too high is not.
  const [role, setRole] = useState<Exclude<ProjectRole, "chairman">>("observer");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await post(`/admin/projects/${projectId}/members`, { email: email.trim(), role });
      onAdded(`${email.trim()} is now ${role === "co_chair" ? "a co-chair" : "an observer"} here.`);
      setEmail("");
    } catch (err) {
      // "Nobody has an account with that email" is the common one, and the
      // server's sentence already says what to do about it.
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit}>
      <h3>Add somebody</h3>
      <div className="row" style={{ alignItems: "flex-end" }}>
        <div style={{ flex: 2 }}>
          <label className="field" htmlFor="member-email">Their email</label>
          <input
            id="member-email"
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
          />
        </div>
        <div>
          <label className="field" htmlFor="member-role">As</label>
          <select
            id="member-role"
            value={role}
            onChange={(e) => setRole(e.target.value as Exclude<ProjectRole, "chairman">)}
          >
            <option value="observer">Observer — reads</option>
            <option value="co_chair">Co-chair — decides</option>
          </select>
        </div>
        <div style={{ flex: "none" }}>
          <Btn kind="primary" type="submit" disabled={!email.trim()} busy={busy}>Add</Btn>
        </div>
      </div>
      <p className="help">
        They need an account on this box first. If they do not have one, whoever runs the box
        invites them from Settings → People.
      </p>
      {error && <div className="error">{error}</div>}
    </form>
  );
}

/** Handing the chair on, with what it costs said before the click. The
    outgoing chairman is not removed — they stay as a co-chair — but they lose
    the one verb nobody else holds, and nobody can give it back but the new
    chairman. That deserves a sentence and a second press. */
function HandTheChair({
  projectId,
  dash,
  members,
  breakGlass,
  onHanded,
}: {
  projectId: string;
  dash: Dashboard;
  members: MemberRow[];
  /** The instance admin acting on a project they do not chair. */
  breakGlass: boolean;
  onHanded: (out: ChairHandover) => void;
}) {
  const [email, setEmail] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const target = email.trim().toLowerCase();
  const already = members.find((m) => m.email.toLowerCase() === target);
  const outgoing = dash.chairman?.display_name ?? "The current chairman";

  async function hand() {
    setBusy(true);
    setError(null);
    try {
      const out = await post<ChairHandover>(`/admin/projects/${projectId}/chairman`, { email: email.trim() });
      setConfirming(false);
      setEmail("");
      onHanded(out);
    } catch (err) {
      setConfirming(false);
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <h3>Hand the chair to somebody else</h3>
      {breakGlass && (
        <p className="help" style={{ marginTop: 0 }}>
          You are seeing this because you run the box, not because you chair this project.
          It is the way out for a project whose chairman has left or cannot be reached — use
          it for that, and nothing else. It is recorded under your name.
        </p>
      )}
      <div className="row" style={{ alignItems: "flex-end" }}>
        <div style={{ flex: 2 }}>
          <label className="field" htmlFor="chair-email">The new chairman&apos;s email</label>
          <input
            id="chair-email"
            type="email"
            list="chair-candidates"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
          <datalist id="chair-candidates">
            {members.filter((m) => m.role !== "chairman" && m.active).map((m) => (
              <option key={m.user_id} value={m.email}>{m.display_name}</option>
            ))}
          </datalist>
        </div>
        <div style={{ flex: "none" }}>
          <Btn
            disabled={!target || already?.role === "chairman"}
            onClick={() => setConfirming(true)}
          >
            Hand the chair to…
          </Btn>
        </div>
      </div>
      {already?.role === "chairman" && <p className="help">That is already the chairman.</p>}
      {error && <div className="error">{error}</div>}
      {confirming && (
        <ConfirmModal
          title="Hand the chair on?"
          confirmLabel="Hand it on"
          danger
          busy={busy}
          onConfirm={() => void hand()}
          onCancel={() => setConfirming(false)}
        >
          <p style={{ marginTop: 0 }}>
            <b>{already?.display_name ?? email.trim()}</b> becomes this project&apos;s chairman: the
            one name accountable for it, and the only member who may add or remove people, or
            hand the chair on again.
            {!already && " They are not a member yet; they join as chairman."}
          </p>
          <p>
            {!breakGlass
              ? "You stay on as a co-chair. You can still decide everything a co-chair decides; who decides is theirs from now on, and handing it back is their call."
              : `${outgoing} stays on as a co-chair. Nobody loses access, and nothing they decided is undone.`}
          </p>
        </ConfirmModal>
      )}
    </>
  );
}

export function MembersPanel({
  projectId,
  dash,
  reload,
}: {
  projectId: string;
  dash: Dashboard;
  /** The project payload carries the reader's own role; a handover changes it. */
  reload: () => void;
}) {
  const { me } = useMe();
  const { data: members, error, reload: reloadMembers } = usePoll(
    () => get<MemberRow[]>(`/admin/projects/${projectId}/members`),
    30000,
    [projectId],
  );
  const [note, setNote] = useState<Note>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const chairs = Boolean(dash.can_chair);
  // The break-glass: an instance admin may move the chair of a project they do
  // not chair, and of nothing else here.
  const breakGlass = !chairs && Boolean(me?.is_admin);

  async function act(id: string, fn: () => Promise<unknown>, said: string) {
    setBusyId(id);
    setNote(null);
    try {
      await fn();
      setNote({ ok: true, text: said });
      reloadMembers();
    } catch (err) {
      setNote({ ok: false, text: (err as Error).message });
    } finally {
      setBusyId(null);
    }
  }

  const changeRole = (m: MemberRow, role: ProjectRole) =>
    act(m.user_id, () => patch(`/admin/projects/${projectId}/members/${m.user_id}`, { role }),
        `${m.display_name} is now ${role === "co_chair" ? "a co-chair" : "an observer"}. It takes effect on their next click.`);

  const remove = (m: MemberRow) => {
    if (!confirm(`Remove ${m.display_name} from this project? From their next click they cannot see it at all.`)) return;
    void act(m.user_id, () => del(`/admin/projects/${projectId}/members/${m.user_id}`),
             `${m.display_name} is no longer on this project.`);
  };

  return (
    <>
      <div className="card" style={{ marginBottom: "var(--s4)" }}>
        <RoleLegend />
      </div>

      {note && <div className={note.ok ? "notice-box" : "error"}>{note.text}</div>}
      {error && <div className="error">{error}</div>}

      <div className="card">
        {members === null ? (
          <p className="help" style={{ margin: 0 }}>Loading…</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Person</th>
                <th>Role</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {members.map((m) => {
                const isChair = m.role === "chairman";
                const busy = busyId === m.user_id;
                return (
                  <tr key={m.user_id} style={m.active ? undefined : { color: "var(--muted)" }}>
                    <td>
                      <span style={{ display: "inline-flex", alignItems: "center", gap: "var(--s2)", flexWrap: "wrap" }}>
                        <Person name={m.display_name} role={m.email} size="sm" />
                        {m.user_id === me?.id && <Badge tone="accent">you</Badge>}
                        {!m.active && <Badge tone="serious">deactivated</Badge>}
                      </span>
                    </td>
                    <td>
                      {chairs && !isChair ? (
                        <select
                          value={m.role}
                          disabled={busy}
                          aria-label={`${m.display_name}'s role`}
                          style={{ maxWidth: 180 }}
                          onChange={(e) => void changeRole(m, e.target.value as ProjectRole)}
                        >
                          <option value="co_chair">Co-chair</option>
                          <option value="observer">Observer</option>
                        </select>
                      ) : (
                        <Badge tone={isChair ? "accent" : m.role === "co_chair" ? "good" : undefined}>
                          {ROLE_LABEL[m.role]}
                        </Badge>
                      )}
                    </td>
                    <td style={{ textAlign: "right" }}>
                      {chairs && !isChair && (
                        <Btn small kind="danger" disabled={busy} onClick={() => remove(m)}>Remove</Btn>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}

        {chairs && (
          <AddMember
            projectId={projectId}
            onAdded={(said) => {
              setNote({ ok: true, text: said });
              reloadMembers();
            }}
          />
        )}

        {(chairs || breakGlass) && members && (
          <HandTheChair
            projectId={projectId}
            dash={dash}
            members={members}
            breakGlass={breakGlass}
            onHanded={(out) => {
              setNote({ ok: true, text: `${out.chairman.display_name} chairs this project now.` });
              reloadMembers();
              reload();
            }}
          />
        )}

        {!chairs && !breakGlass && (
          <p className="help" style={{ marginBottom: 0 }}>
            {dash.chairman
              ? `${dash.chairman.display_name} chairs this project, and decides who else may decide on it.`
              : "The chairman decides who else may decide on this project."}{" "}
            Ask them if somebody should be added.
          </p>
        )}
      </div>
    </>
  );
}
