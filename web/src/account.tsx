// Your own account: the password, the tokens your scripts use, and the browsers
// you are signed in on.
//
// Everything here is about the person reading it and nobody else, which is why
// it is open to everyone — a co-chair who has never been near the box's
// settings still has a password to change and a laptop they left signed in.
// What an instance admin does to other people's accounts is People, next door.

import { useState } from "react";
import {
  ApiError,
  del,
  get,
  MintedToken,
  PersonalToken,
  post,
  SignedInSession,
} from "./api";
import { fmtWhen, usePoll } from "./hooks";
import { useMe } from "./me";
import { clip, CopyField, fmtAgo, fmtLeft } from "./ui";
import { Badge, Btn } from "./ui/kit";

/** The server's rule, said before it is broken rather than after. */
const MIN_PASSWORD = 12;

type Note = { ok: boolean; text: string } | null;

/** One flash line under a card: the answer to the button you just pressed. */
function Flash({ note }: { note: Note }) {
  if (!note) return null;
  return <p className={note.ok ? "help" : "error"}>{note.text}</p>;
}

/* ── password ────────────────────────────────────────────────────────────── */

export function PasswordCard() {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [again, setAgain] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<Note>(null);
  const { me } = useMe();

  const ready = current.length > 0 && next.length >= MIN_PASSWORD && next === again;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!ready) return;
    setBusy(true);
    setNote(null);
    try {
      await post("/auth/password", { current, new: next });
      setCurrent("");
      setNext("");
      setAgain("");
      setNote({
        ok: true,
        text: "Password changed. Every other browser signed in as you has been signed out; this one stays.",
      });
    } catch (err) {
      setNote({
        ok: false,
        text: err instanceof ApiError && err.status === 401
          ? "That is not your current password. Nothing was changed."
          : (err as Error).message,
      });
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="card" onSubmit={submit}>
      <p className="help" style={{ marginTop: 0 }}>
        Changing it signs out every other browser that is signed in as you — which is what
        you want after leaving a laptop somewhere. Your personal tokens keep working; revoke
        those below if they are the worry.
      </p>
      {/* The address rides along hidden so a password manager updates the
          right entry instead of saving a new one against nothing. */}
      <input type="email" value={me?.email ?? ""} autoComplete="username" readOnly hidden />
      <div className="row">
        <div>
          <label className="field" htmlFor="pw-current">Current password</label>
          <input
            id="pw-current"
            type="password"
            value={current}
            onChange={(e) => setCurrent(e.target.value)}
            autoComplete="current-password"
          />
        </div>
        <div>
          <label className="field" htmlFor="pw-new">New password</label>
          <input
            id="pw-new"
            type="password"
            value={next}
            onChange={(e) => setNext(e.target.value)}
            autoComplete="new-password"
          />
        </div>
        <div>
          <label className="field" htmlFor="pw-again">The new one again</label>
          <input
            id="pw-again"
            type="password"
            value={again}
            onChange={(e) => setAgain(e.target.value)}
            autoComplete="new-password"
          />
        </div>
      </div>
      <p className="help">
        At least {MIN_PASSWORD} characters.
        {next.length > 0 && next.length < MIN_PASSWORD && ` ${MIN_PASSWORD - next.length} to go.`}
        {again.length > 0 && again !== next && " The two new ones do not match yet."}
      </p>
      <div className="actions" style={{ marginTop: "var(--s2)" }}>
        <Btn kind="primary" type="submit" disabled={!ready} busy={busy}>
          Change password
        </Btn>
      </div>
      <Flash note={note} />
    </form>
  );
}

/* ── personal tokens ─────────────────────────────────────────────────────── */

function tokenState(t: PersonalToken): { live: boolean; badge: React.ReactNode } {
  if (t.revoked_at) return { live: false, badge: <Badge>revoked {fmtWhen(t.revoked_at)}</Badge> };
  if (new Date(t.expires_at).getTime() <= Date.now()) {
    return { live: false, badge: <Badge>expired {fmtWhen(t.expires_at)}</Badge> };
  }
  const soon = new Date(t.expires_at).getTime() - Date.now() < 3 * 86_400_000;
  return { live: true, badge: <Badge tone={soon ? "warn" : "good"}>{fmtLeft(t.expires_at)}</Badge> };
}

export function TokensCard() {
  const { data: tokens, error, reload } = usePoll(() => get<PersonalToken[]>("/auth/tokens"), 60000);
  const [name, setName] = useState("");
  const [days, setDays] = useState(30);
  const [minted, setMinted] = useState<MintedToken | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<Note>(null);

  async function mint(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setNote(null);
    try {
      setMinted(await post<MintedToken>("/auth/tokens", { name: name.trim(), days }));
      setName("");
      reload();
    } catch (err) {
      setNote({ ok: false, text: (err as Error).message });
    } finally {
      setBusy(false);
    }
  }

  async function revoke(t: PersonalToken) {
    if (!confirm(`Revoke "${t.name}"? Anything still using it is refused from its next request.`)) return;
    setNote(null);
    try {
      await del(`/auth/tokens/${t.id}`);
      if (minted?.id === t.id) setMinted(null);
      setNote({ ok: true, text: `"${t.name}" is revoked.` });
      reload();
    } catch (err) {
      setNote({ ok: false, text: (err as Error).message });
    }
  }

  // Live first, newest first; the dead ones stay listed because "did I revoke
  // the one on the old server?" is a question this list is opened to answer.
  const rows = [...(tokens ?? [])].sort(
    (a, b) => Number(tokenState(b).live) - Number(tokenState(a).live),
  );

  return (
    <div className="card">
      <p className="help" style={{ marginTop: 0 }}>
        For the <code>yaaf</code> command line and your own scripts. A token can do exactly
        what you can, no more, until it expires or you revoke it — and what it does is
        recorded as you, marked with the token&apos;s name, so a script&apos;s work never
        reads as your clicks. You rarely need to make one here: <code>yaaf login</code> asks
        for your password and makes one for you. This is for a script or a machine where you
        would rather not type it.
      </p>

      {minted && (
        <div className="notice-box">
          <b>Your new token, &ldquo;{minted.name}&rdquo;.</b> Copy it now — it is not shown
          again, not here and not anywhere. Lose it and you make another.
          <div style={{ marginTop: "var(--s2)" }}>
            <CopyField
              value={minted.token}
              note={<>Expires {fmtWhen(minted.expires_at)}. Use it as <code>YAAF_TOKEN</code>, or as a bearer token on requests.</>}
            />
          </div>
          <div className="actions" style={{ marginTop: "var(--s2)" }}>
            <Btn small onClick={() => setMinted(null)}>I have copied it</Btn>
          </div>
        </div>
      )}

      {error && <div className="error">{error}</div>}
      {tokens === null ? (
        <p className="help">Loading…</p>
      ) : rows.length === 0 ? (
        <p className="help">You have no tokens. That is the safest number to have.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Name</th>
              <th>Made</th>
              <th>Last used</th>
              <th>Expires</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {rows.map((t) => {
              const state = tokenState(t);
              return (
                <tr key={t.id} style={state.live ? undefined : { color: "var(--muted)" }}>
                  <td><b>{t.name}</b></td>
                  <td>{fmtWhen(t.created_at)}</td>
                  <td title={t.last_used_at ? fmtWhen(t.last_used_at) : undefined}>
                    {t.last_used_at ? fmtAgo(t.last_used_at) : "never"}
                  </td>
                  <td>{state.badge}</td>
                  <td style={{ textAlign: "right" }}>
                    {state.live && <Btn small kind="danger" onClick={() => void revoke(t)}>Revoke</Btn>}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}

      <form onSubmit={mint}>
        <div className="row" style={{ marginTop: "var(--s3)", alignItems: "flex-end" }}>
          <div style={{ flex: 2 }}>
            <label className="field" htmlFor="token-name">Name it after where it will live</label>
            <input
              id="token-name"
              value={name}
              maxLength={80}
              placeholder="nightly-export on the build box"
              onChange={(e) => setName(e.target.value)}
            />
          </div>
          <div>
            <label className="field" htmlFor="token-days">Works for (days)</label>
            <input
              id="token-days"
              type="number"
              min={1}
              max={365}
              value={days}
              onChange={(e) => setDays(Math.round(+e.target.value))}
            />
          </div>
          <div style={{ flex: "none" }}>
            <Btn
              kind="primary"
              type="submit"
              disabled={!name.trim() || !(days >= 1 && days <= 365)}
              busy={busy}
            >
              Make a token
            </Btn>
          </div>
        </div>
        <p className="help">
          Between 1 and 365 days. Every token expires; the shorter it lives, the less a
          leaked one is worth.
        </p>
      </form>
      <Flash note={note} />
    </div>
  );
}

/* ── signed-in browsers ──────────────────────────────────────────────────── */

/** "Firefox on macOS", from a user-agent string nobody can read. Loose on
    purpose: it only has to tell your laptop from your phone, and when it
    cannot it says the raw string rather than guessing. */
function browserOf(ua: string | null): string {
  if (!ua) return "A browser that did not say what it was";
  const browser = /Edg\//.test(ua) ? "Edge"
    : /OPR\//.test(ua) ? "Opera"
    : /Firefox\//.test(ua) ? "Firefox"
    : /Chrome\//.test(ua) ? "Chrome"
    : /Safari\//.test(ua) ? "Safari"
    : null;
  // iOS first: an iPhone's string also claims to be "like Mac OS X".
  const os = /iPhone|iPad/.test(ua) ? "iOS"
    : /Android/.test(ua) ? "Android"
    : /Mac OS X|Macintosh/.test(ua) ? "macOS"
    : /Windows/.test(ua) ? "Windows"
    : /Linux/.test(ua) ? "Linux"
    : null;
  if (browser && os) return `${browser} on ${os}`;
  return browser ?? os ?? clip(ua, 60);
}

export function SessionsCard() {
  const { data: sessions, error, reload } = usePoll(() => get<SignedInSession[]>("/auth/sessions"), 60000);
  const [note, setNote] = useState<Note>(null);
  const { signOut } = useMe();

  async function revoke(s: SignedInSession) {
    const what = s.current ? "this browser" : browserOf(s.user_agent);
    if (!confirm(`Sign out ${what}?`)) return;
    setNote(null);
    try {
      const out = await del<{ revoked: boolean; current: boolean }>(`/auth/sessions/${s.id}`);
      // Signing out the browser you are reading this in leaves nothing to
      // read it with; go where a signed-out browser goes.
      if (out.current) {
        await signOut();
        return;
      }
      setNote({ ok: true, text: `${what} is signed out.` });
      reload();
    } catch (err) {
      setNote({ ok: false, text: (err as Error).message });
    }
  }

  return (
    <div className="card">
      <p className="help" style={{ marginTop: 0 }}>
        Every browser signed in as you. A session ends on its own after three days without
        use, and after fourteen whatever happens. One you do not recognise is worth signing
        out — and then worth changing your password, which signs out all of them.
      </p>
      {error && <div className="error">{error}</div>}
      {sessions === null ? (
        <p className="help">Loading…</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Browser</th>
              <th>Signed in</th>
              <th>Last seen</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {sessions.map((s) => (
              <tr key={s.id}>
                <td title={s.user_agent ?? undefined}>
                  <b>{browserOf(s.user_agent)}</b>{" "}
                  {s.current && <Badge tone="accent">this browser</Badge>}
                </td>
                <td>{fmtWhen(s.created_at)}</td>
                <td title={fmtWhen(s.last_seen_at)}>{s.current ? "now" : fmtAgo(s.last_seen_at)}</td>
                <td style={{ textAlign: "right" }}>
                  <Btn small kind={s.current ? "secondary" : "danger"} onClick={() => void revoke(s)}>
                    {s.current ? "Sign out" : "Sign it out"}
                  </Btn>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <Flash note={note} />
    </div>
  );
}
