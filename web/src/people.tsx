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

import { useState } from "react";
import { IssuedLink, patch, post, setupLink, UserRow } from "./api";
import { fmtWhen } from "./hooks";
import { useMe } from "./me";
import { CopyField } from "./ui";
import { Badge, Btn, Person } from "./ui/kit";

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
      Send this to them yourself — this box sends no email. It works once, for{" "}
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

function InviteForm({ onInvited }: { onInvited: (link: FreshLink | null, note: string | null) => void }) {
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
    <form className="card" onSubmit={submit}>
      <h3 style={{ marginTop: 0 }}>Invite somebody</h3>
      <div className="row">
        <div>
          <label className="field" htmlFor="invite-email">Their email</label>
          <input
            id="invite-email"
            type="email"
            value={email}
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
        They help run this box, too
      </label>
      <p className="help" style={{ marginTop: 0 }}>
        An admin invites people, holds the platform&apos;s settings and credentials, and can
        read every project — but decides nothing on a project they do not chair. Most people
        are not admins: they found their own projects, or are added to someone else&apos;s.
      </p>
      {error && <div className="error">{error}</div>}
      <div className="actions" style={{ marginTop: "var(--s2)" }}>
        <Btn kind="primary" type="submit" disabled={!email.trim()} busy={busy}>
          Invite and make a link
        </Btn>
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
  const { me } = useMe();

  /** Every refusal here is the server keeping the box governable — the last
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
        ? `${u.display_name} can now help run this box.`
        : `${u.display_name} is no longer an admin. Their projects are untouched.`;
    });

  const setActive = (u: UserRow, active: boolean) => {
    if (!active && !confirm(
      `Deactivate ${u.display_name}? They are signed out everywhere at once, and every token ` +
      "they hold stops working. Nothing they did is deleted, and you can reactivate them later.",
    )) return;
    void act(u, async () => {
      await patch(`/admin/users/${u.id}`, { active });
      return active
        ? `${u.display_name} can sign in again. Their old tokens stay revoked; they make new ones.`
        : `${u.display_name} is deactivated and signed out everywhere.`;
    });
  };

  const waiting = (users ?? []).filter((u) => u.active && !u.has_password).length;

  return (
    <>
      <p className="help" style={{ marginTop: 0 }}>
        Everyone with an account on this box. Inviting somebody makes their account and a
        one-time link for them to choose a password; adding them to a project is that
        project&apos;s chairman&apos;s decision, made in the project&apos;s Settings → People.
        {waiting > 0 && ` ${waiting} ${waiting === 1 ? "person has" : "people have"} not followed their link yet.`}
      </p>

      {link && <LinkBox link={link} onDone={() => setLink(null)} />}
      {note && <div className={note.ok ? "notice-box" : "error"}>{note.text}</div>}
      {loadError && <div className="error">{loadError}</div>}

      <div className="card" style={{ marginBottom: "var(--s4)" }}>
        {users === null ? (
          <p className="help" style={{ margin: 0 }}>Loading…</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Person</th>
                <th>Standing</th>
                <th>Since</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {users.map((u) => {
                const self = u.id === me?.id;
                const busy = busyId === u.id;
                return (
                  <tr key={u.id} style={u.active ? undefined : { color: "var(--muted)" }}>
                    <td><Person name={u.display_name} role={u.email} size="sm" /></td>
                    <td>
                      <span style={{ display: "inline-flex", gap: "var(--s1)", flexWrap: "wrap" }}>
                        {self && <Badge tone="accent">you</Badge>}
                        {u.is_admin && <Badge tone="good">admin</Badge>}
                        {!u.active && <Badge tone="serious">deactivated {u.deactivated_at ? fmtWhen(u.deactivated_at) : ""}</Badge>}
                        {u.active && !u.has_password && <Badge tone="warn">no password yet</Badge>}
                      </span>
                    </td>
                    <td>{fmtWhen(u.created_at)}</td>
                    <td>
                      {/* Your own row offers nothing that would lock you out of
                          the page you are standing on. Another admin can do
                          it, which is also who should. */}
                      <span style={{ display: "inline-flex", gap: "var(--s1)", flexWrap: "wrap", justifyContent: "flex-end", width: "100%" }}>
                        {u.active && (
                          <Btn small disabled={busy} onClick={() => void newLink(u)}
                               title={u.has_password ? "A link to choose a new password; their current one keeps working until they use it" : "A fresh invitation; any earlier link stops working"}>
                            New setup link
                          </Btn>
                        )}
                        {!self && u.active && (
                          <Btn small disabled={busy} onClick={() => void setAdmin(u, !u.is_admin)}>
                            {u.is_admin ? "Remove admin" : "Make admin"}
                          </Btn>
                        )}
                        {!self && (
                          <Btn small kind={u.active ? "danger" : "secondary"} disabled={busy}
                               onClick={() => setActive(u, !u.active)}>
                            {u.active ? "Deactivate" : "Reactivate"}
                          </Btn>
                        )}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>

      <InviteForm
        onInvited={(issued, said) => {
          setLink(issued);
          setNote(said ? { ok: true, text: said } : null);
          reload();
        }}
      />
    </>
  );
}
