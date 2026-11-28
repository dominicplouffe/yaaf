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
//
// The list is the page. Adding somebody opens a drawer; a role changes on its
// row; handing the chair on — the one change its maker cannot take back — sits
// in the danger zone and asks for the new chairman's email typed out.

import { useState } from "react";
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
import { ConfirmModal, Drawer } from "../../ui";
import { Badge, Person } from "../../ui/kit";
import { DangerZone, Flash, SectionHead } from "../../settings/kit";

type Note = { ok: boolean; text: string } | null;

function AddMember({ projectId, onAdded, onClose }: {
  projectId: string; onAdded: (said: string) => void; onClose: () => void;
}) {
  const [email, setEmail] = useState("");
  // Least first. Widening somebody later is one click; un-deciding something
  // they decided because they were added a rung too high is not.
  const [role, setRole] = useState<Exclude<ProjectRole, "chairman">>("observer");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      await post(`/admin/projects/${projectId}/members`, { email: email.trim(), role });
      onAdded(`${email.trim()} is now ${role === "co_chair" ? "a co-chair" : "an observer"} here.`);
    } catch (err) {
      // "Nobody has an account with that email" is the common one, and the
      // server's sentence already says what to do about it.
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Drawer title="Add a person" sub="They need an account on this install first; whoever runs it invites them from workspace Settings → People."
      onClose={onClose}
      footer={<>
        <span className="grow" />
        <button type="button" onClick={onClose}>Cancel</button>
        <button type="button" className="primary" disabled={busy || !email.trim()} onClick={() => void submit()}>
          {busy ? "Adding…" : "Add"}</button>
      </>}>
      <label className="field" htmlFor="member-email">Their email</label>
      <input id="member-email" type="email" value={email} autoComplete="off"
        onChange={(e) => setEmail(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Enter" && email.trim()) void submit(); }} />
      <span className="field">As</span>
      <div className="keypick" role="radiogroup" aria-label="Role">
        {([["observer", "Reads everything the project shows, and decides nothing."],
           ["co_chair", "Decides as your peer: approvals, scope, verdicts, dates, the rules."]] as const).map(([r, means]) => (
          <label key={r} className={role === r ? "on" : ""}>
            <input type="radio" name="member-role" checked={role === r} onChange={() => setRole(r)} />
            <span><b>{ROLE_LABEL[r]}</b><span className="help" style={{ display: "block" }}>{means}</span></span>
            <span />
          </label>
        ))}
      </div>
      {error && <div className="error">{error}</div>}
    </Drawer>
  );
}

/** Handing the chair on, with what it costs said before the click. The
    outgoing chairman is not removed — they stay as a co-chair — but they lose
    the one verb nobody else holds, and nobody can give it back but the new
    chairman. That deserves a sentence and the email typed out. */
function HandTheChair({ projectId, dash, members, breakGlass, onHanded }: {
  projectId: string;
  dash: Dashboard;
  members: MemberRow[];
  /** The instance admin acting on a project they do not chair. */
  breakGlass: boolean;
  onHanded: (out: ChairHandover) => void;
}) {
  const [open, setOpen] = useState(false);
  const [email, setEmail] = useState("");
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const target = email.trim().toLowerCase();
  const already = members.find((m) => m.email.toLowerCase() === target);
  const outgoing = dash.chairman?.display_name ?? "The current chairman";
  const ready = !!target && already?.role !== "chairman" && typed.trim().toLowerCase() === target;

  async function hand() {
    setBusy(true);
    setError(null);
    try {
      const out = await post<ChairHandover>(`/admin/projects/${projectId}/chairman`, { email: email.trim() });
      setOpen(false);
      setEmail("");
      setTyped("");
      onHanded(out);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <DangerZone>
      <div className="set-row">
        <span className="set-row-lab">Hand the chair to someone else
          <span className="set-row-hint">Only the new chairman can give it back</span></span>
        <div className="set-row-val">
          {breakGlass
            ? <span className="warn-ink">You see this because you run the install. Use it only when the chairman has left or can&apos;t be reached; it is recorded under your name.</span>
            : "You stay on as a co-chair. You lose the one thing nobody else holds: deciding who decides."}
        </div>
        <div className="set-row-act">
          <button type="button" className="small danger" onClick={() => setOpen(true)}>Hand the chair…</button>
        </div>
      </div>
      {open && (
        <ConfirmModal title="Hand the chair on?" confirmLabel="Hand the chair" danger busy={busy} confirmDisabled={!ready}
          onConfirm={() => { if (ready) void hand(); }} onCancel={() => { setOpen(false); setError(null); }}>
          <ul style={{ marginTop: 0, paddingLeft: 18 }}>
            <li>They become chairman: the one name accountable for {dash.name}, and the only member who may
              add or remove people, or hand the chair on again.</li>
            <li>{breakGlass ? `${outgoing} stays on as a co-chair. Nobody loses access.` : "You stay on as a co-chair."}</li>
            <li>Only they can give the chair back.</li>
          </ul>
          <label className="field" htmlFor="chair-email">New chairman&apos;s email</label>
          <input id="chair-email" type="email" list="chair-candidates" value={email} autoComplete="off"
            onChange={(e) => setEmail(e.target.value)} />
          <datalist id="chair-candidates">
            {members.filter((m) => m.role !== "chairman" && m.active).map((m) => (
              <option key={m.user_id} value={m.email}>{m.display_name}</option>
            ))}
          </datalist>
          {already?.role === "chairman" && <p className="help">That is already the chairman.</p>}
          {target && !already && <p className="help">They are not a member yet; they join as chairman.</p>}
          <label className="field" htmlFor="chair-typed">Type their email again to confirm</label>
          <input id="chair-typed" type="email" value={typed} autoComplete="off" onChange={(e) => setTyped(e.target.value)} />
          {error && <div className="error">{error}</div>}
        </ConfirmModal>
      )}
    </DangerZone>
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
  const [adding, setAdding] = useState(false);
  const [removing, setRemoving] = useState<MemberRow | null>(null);
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

  return (
    <>
      <SectionHead title="People" lede="Who may decide on this project, and who may only read it."
        actions={chairs ? <button type="button" className="primary" onClick={() => setAdding(true)}>Add a person</button> : undefined}>
        {members && <span className="help">{members.length} {members.length === 1 ? "person" : "people"}</span>}
      </SectionHead>

      <Flash note={note} />
      {error && <div className="error">{error}</div>}

      <div className="set-card">
        {members === null ? (
          <p className="help" style={{ margin: 0, padding: "14px 18px" }}>Loading…</p>
        ) : (
          <div className="table-wrap">
            <table className="pset-table">
              <thead><tr><th>Person</th><th>Role</th><th /></tr></thead>
              <tbody>
                {members.map((m) => {
                  const isChair = m.role === "chairman";
                  const busy = busyId === m.user_id;
                  return (
                    <tr key={m.user_id} className={m.active ? undefined : "muted"}>
                      <td>
                        <span className="pset-who">
                          <Person name={m.display_name} role={m.email} size="sm" />
                          {m.user_id === me?.id && <Badge tone="accent">you</Badge>}
                          {!m.active && <Badge tone="serious">deactivated</Badge>}
                        </span>
                      </td>
                      <td>
                        {chairs && !isChair ? (
                          <select value={m.role} disabled={busy} aria-label={`${m.display_name}'s role`}
                            style={{ maxWidth: 180 }}
                            onChange={(e) => void changeRole(m, e.target.value as ProjectRole)}>
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
                          <button type="button" className="small quiet-danger" disabled={busy}
                            onClick={() => setRemoving(m)}>Remove</button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        <div className="pset-foot help">
          <span><b>Chairman</b> decides, and decides who decides. <b>Co-chair</b> decides as the
            chairman&apos;s peer: approvals, scope, verdicts, dates, the rules. <b>Observer</b> reads
            everything and decides nothing.</span>
        </div>
      </div>

      {!chairs && !breakGlass && (
        <p className="help">
          {dash.chairman
            ? `${dash.chairman.display_name} chairs this project, and decides who else may decide on it.`
            : "The chairman decides who else may decide on this project."}{" "}
          Ask them if somebody should be added.
        </p>
      )}

      {(chairs || breakGlass) && members && (
        <HandTheChair projectId={projectId} dash={dash} members={members} breakGlass={breakGlass}
          onHanded={(out) => {
            setNote({ ok: true, text: `${out.chairman.display_name} chairs this project now.` });
            reloadMembers();
            reload();
          }} />
      )}

      {adding && (
        <AddMember projectId={projectId} onClose={() => setAdding(false)}
          onAdded={(said) => { setAdding(false); setNote({ ok: true, text: said }); reloadMembers(); }} />
      )}
      {removing && (
        <ConfirmModal title={`Remove ${removing.display_name}?`} confirmLabel="Remove" danger
          busy={busyId === removing.user_id}
          onConfirm={() => {
            const m = removing;
            setRemoving(null);
            void act(m.user_id, () => del(`/admin/projects/${projectId}/members/${m.user_id}`),
                     `${m.display_name} is no longer on this project.`);
          }}
          onCancel={() => setRemoving(null)}>
          <p style={{ marginTop: 0 }}>From their next click they can&apos;t see this project at all.
            Anything they decided stays decided.</p>
        </ConfirmModal>
      )}
    </>
  );
}
