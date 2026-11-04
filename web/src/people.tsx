// People: who has an account on this box. Instance admins only.
//
// There is no sign-up page, on purpose — a box that holds LLM credentials and a
// budget is not somewhere strangers should be able to make themselves an
// account. Everybody arrives through here: an admin invites them, gets a link,
// and hands it on. The box sends no email, so the link *is* the invitation,
// and this screen's job is to make handing it on impossible to get wrong.
//
// Membership of a project is not here. Who may decide on a project is that
// project's chairman's call, made in the project's own Settings → People; this
// page only says who exists.

import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { IssuedLink, patch, post, setupLink, UserRow } from "./api";
import { useMe } from "./me";
import { ConfirmModal, CopyField, MenuItem, RowMenu } from "./ui";
import { Badge, Btn, Person } from "./ui/kit";
import { Icon } from "./ui/Icon";
import { Why } from "./settingsCopy";
import { SectionHead } from "./settings/kit";

/** "Nov 4", or "Nov 4, 2025" outside this year: when somebody joined, not the minute. */
function day(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric",
    ...(d.getFullYear() === new Date().getFullYear() ? {} : { year: "numeric" }) });
}

/** A link just made, and who it is for. */
interface FreshLink {
  who: UserRow;
  url: string;
  hours: number;
  purpose: "invite" | "reset";
}

function LinkBox({ link, onDone }: { link: FreshLink; onDone: () => void }) {
  return (
    <div className="notice-box">
      <b>
        {link.purpose === "reset"
          ? `A link for ${link.who.display_name} to choose a new password.`
          : `A link for ${link.who.display_name} to set up their account.`}
      </b>{" "}
      Send this to them yourself: yaaf sends no email. It works once, for{" "}
      {link.hours} hours, and making another one for them stops this one working.
      <div style={{ marginTop: "var(--s2)" }}>
        <CopyField
          value={link.url}
          note={`Anyone holding it can choose ${link.who.email}'s password, so send it the way you would send a password: to them, not to a channel.`}
        />
      </div>
      <div className="actions" style={{ marginTop: "var(--s2)" }}>
        <Btn small onClick={onDone}>Done</Btn>
      </div>
    </div>
  );
}

function fresh(issued: IssuedLink, fallback: "invite" | "reset"): FreshLink | null {
  const url = setupLink(issued);
  if (!url) return null;
  return {
    who: issued,
    url,
    hours: issued.setup_expires_in_hours ?? 0,
    purpose: issued.purpose ?? fallback,
  };
}

function InviteForm({ onInvited, onCancel }: {
  onInvited: (link: FreshLink | null, note: string | null) => void;
  onCancel: () => void;
}) {
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [admin, setAdmin] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const issued = await post<IssuedLink>("/admin/users", {
        email: email.trim(),
        display_name: name.trim() || email.trim(),
        is_admin: admin,
      });
      setEmail("");
      setName("");
      setAdmin(false);
      if (issued.existing) {
        // Inviting somebody twice is not an error, and it is not a second
        // account either: the address is the person.
        onInvited(null,
          `${issued.email} already has an account, so nothing new was made. If they need a link, use "New setup link" on their row.`);
      } else {
        onInvited(fresh(issued, "invite"), null);
      }
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="card" onSubmit={submit} style={{ marginBottom: "var(--s4)" }}>
      <h3 style={{ marginTop: 0 }}>Invite someone</h3>
      <div className="row">
        <div>
          <label className="field" htmlFor="invite-email">Their email</label>
          <input
            id="invite-email"
            type="email"
            value={email}
            autoFocus
            onChange={(e) => setEmail(e.target.value)}
            required
          />
        </div>
        <div>
          <label className="field" htmlFor="invite-name">Their name, as others will see it</label>
          <input id="invite-name" value={name} maxLength={120} onChange={(e) => setName(e.target.value)} />
        </div>
      </div>
      <label className="field" style={{ display: "flex", gap: 6, alignItems: "center" }}>
        <input
          type="checkbox"
          checked={admin}
          style={{ width: "auto", flex: "none", minHeight: 0 }}
          onChange={(e) => setAdmin(e.target.checked)}
        />
        Make them an admin
      </label>
      <p className="help" style={{ marginTop: 0 }}>
        An admin holds this install&apos;s settings and keys and reads every project, but decides
        nothing on a project they do not chair.
      </p>
      <Why>
        An admin also invites people. Most people are not admins: they found their own
        projects, or are added to someone else&apos;s.
      </Why>
      {error && <div className="error">{error}</div>}
      <div className="actions" style={{ marginTop: "var(--s2)" }}>
        <Btn kind="primary" type="submit" disabled={!email.trim()} busy={busy}>
          Invite and make a link
        </Btn>
        <Btn onClick={onCancel}>Cancel</Btn>
      </div>
    </form>
  );
}

/** The list arrives from the page, which already holds it for the rail's
    "is anything waiting here?" line; one list, so the two never disagree. */
export function People({
  users,
  loadError,
  reload,
}: {
  users: UserRow[] | null;
  loadError: string | null;
  reload: () => void;
}) {
  const [link, setLink] = useState<FreshLink | null>(null);
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [leaving, setLeaving] = useState<UserRow | null>(null);
  const [params, setParams] = useSearchParams();
  const [inviting, setInviting] = useState(params.has("invite"));
  useEffect(() => { if (params.has("invite")) setInviting(true); }, [params]);
  const { me } = useMe();

  /** Every refusal here is the server keeping the install governable — the last
      admin, somebody who still chairs a live project — and it says exactly
      why, so the sentence is shown as it arrives. */
  async function act(user: UserRow, fn: () => Promise<string | null>) {
    setBusyId(user.id);
    setNote(null);
    try {
      const said = await fn();
      if (said) setNote({ ok: true, text: said });
      reload();
    } catch (err) {
      setNote({ ok: false, text: (err as Error).message });
    } finally {
      setBusyId(null);
    }
  }

  const newLink = (u: UserRow) =>
    act(u, async () => {
      const issued = await post<IssuedLink>(`/admin/users/${u.id}/setup-link`);
      setLink(fresh(issued, u.has_password ? "reset" : "invite"));
      return null;
    });

  const setAdmin = (u: UserRow, is_admin: boolean) =>
    act(u, async () => {
      await patch(`/admin/users/${u.id}`, { is_admin });
      return is_admin
        ? `${u.display_name} is now an admin of this install.`
        : `${u.display_name} is no longer an admin. Their projects are untouched.`;
    });

  const setActive = (u: UserRow, active: boolean) =>
    act(u, async () => {
      await patch(`/admin/users/${u.id}`, { active });
      return active
        ? `${u.display_name} can sign in again. Their old tokens stay revoked; they make new ones.`
        : `${u.display_name} is deactivated and signed out everywhere.`;
    });

  const closeInvite = () => {
    setInviting(false);
    if (params.has("invite")) { const next = new URLSearchParams(params); next.delete("invite"); setParams(next, { replace: true }); }
  };
  const active = (users ?? []).filter((u) => u.active);
  const waiting = active.filter((u) => !u.has_password).length;
  const admins = active.filter((u) => u.is_admin).length;

  return (
    <>
      <SectionHead title="People"
        lede="Everyone with an account on this install. Who decides on a project is set in that project."
        actions={!inviting && <button type="button" className="primary" onClick={() => setInviting(true)}>
          <Icon name="plus" size={15} />Invite someone</button>}>
        {users && <>
          <span><b>{active.length}</b> {active.length === 1 ? "person" : "people"}</span>
          <span><b>{admins}</b> admin{admins === 1 ? "" : "s"}</span>
          {waiting > 0 && <span className="warn-ink">{waiting} haven&apos;t signed in yet</span>}
        </>}
      </SectionHead>
      <Why>
        Inviting somebody makes their account and a one-time link for them to choose a
        password; adding them to a project is that project&apos;s chairman&apos;s decision,
        made in the project&apos;s Settings → People.
      </Why>

      {inviting && <InviteForm onCancel={closeInvite}
        onInvited={(issued, said) => {
          setLink(issued);
          setNote(said ? { ok: true, text: said } : null);
          if (issued) closeInvite();
          reload();
        }} />}
      {link && <LinkBox link={link} onDone={() => setLink(null)} />}
      {note && <div className={note.ok ? "notice-box" : "error"}>{note.text}</div>}
      {loadError && <div className="error">{loadError}</div>}

      <div className="set-card" style={{ overflow: "hidden" }}>
        {users === null ? (
          <p className="help" style={{ margin: 0, padding: 16 }}>Loading…</p>
        ) : (
          <div className="table-scroll"><table className="people-table">
            <thead>
              <tr>
                <th>Person</th>
                <th>Role</th>
                <th>Status</th>
                <th>Joined</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {users.map((u) => {
                const self = u.id === me?.id;
                const busy = busyId === u.id;
                const invited = u.active && !u.has_password;
                // Your own row offers nothing that would lock you out of the
                // page you are standing on. Another admin can do it, which is
                // also who should.
                const items: MenuItem[] = [];
                if (u.active && u.has_password) items.push({ label: "New password link", icon: "key",
                  note: "Their current password keeps working until they use it", onClick: () => void newLink(u) });
                if (!self && u.active) items.push({ label: u.is_admin ? "Remove admin" : "Make admin",
                  icon: "shield", onClick: () => void setAdmin(u, !u.is_admin) });
                if (!self) items.push(u.active
                  ? { label: "Deactivate…", icon: "x", danger: true, onClick: () => setLeaving(u) }
                  : { label: "Reactivate", icon: "refresh", onClick: () => void setActive(u, true) });
                return (
                  <tr key={u.id} style={u.active ? undefined : { color: "var(--muted)" }}>
                    <td><div className="ppl-who"><Person name={u.display_name} role={u.email} size="sm" />
                      {self && <Badge tone="accent">you</Badge>}</div></td>
                    <td>{u.is_admin ? "Admin" : "Member"}</td>
                    <td>
                      {!u.active ? <Badge tone="serious">Deactivated {u.deactivated_at ? day(u.deactivated_at) : ""}</Badge>
                        : invited && u.invite?.expired ? <Badge tone="warn">Link ran out</Badge>
                        : invited ? <><Badge tone="warn">Invited</Badge>{u.invite && <span className="help">
                            {" "}link works until {day(u.invite.expires_at)}</span>}</>
                        : <Badge tone="good">Active</Badge>}
                    </td>
                    <td>{day(u.created_at)}</td>
                    <td>
                      <span className="acts">
                        {invited && <Btn small disabled={busy} onClick={() => void newLink(u)}
                          title="A fresh invitation; any earlier link stops working">New link</Btn>}
                        {items.length > 0 && <RowMenu label={`More for ${u.display_name}`} items={items} />}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table></div>
        )}
      </div>

      {leaving && <ConfirmModal title={`Deactivate ${leaving.display_name}?`} confirmLabel="Deactivate" danger
        busy={busyId === leaving.id} onCancel={() => setLeaving(null)}
        onConfirm={() => { const u = leaving; setLeaving(null); void setActive(u, false); }}>
        <p>They are signed out everywhere at once, and every token they hold stops working. Nothing
          they did is deleted, and you can reactivate them later.</p>
      </ConfirmModal>}
    </>
  );
}
