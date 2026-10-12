import { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { ApiError, get, post, SetupInfo } from "../api";
import { useMe } from "../me";
import { Btn } from "../ui/kit";

/** The server's rule, said before it is broken rather than after. */
const MIN_PASSWORD = 12;

/** What the page is for depends on why the link was made. The same form sets a
    first password, replaces a forgotten one, and lets the box's first admin in,
    and a page that greeted all three with "Welcome" would be wrong twice. */
function heading(info: SetupInfo): { title: string; lede: string } {
  if (info.purpose === "bootstrap") {
    return {
      title: "You are this box's first admin",
      lede: `Choose a password for ${info.email}. Once you are in, Settings → People is where you invite everyone else.`,
    };
  }
  if (info.purpose === "reset") {
    return {
      title: "Choose a new password",
      lede: `For ${info.email}. Your old password stops working the moment you save this, and every browser signed in as you is signed out.`,
    };
  }
  return {
    title: `Welcome, ${info.display_name}`,
    lede: `Choose a password for ${info.email}. You will sign in with this address and that password from now on.`,
  };
}

/** A dead link is the common case, not an edge: links work once, run out, and
    are replaced whenever a new one is issued. The person holding one needs to
    know who can make another — and the one person nobody can ask, the box's
    own admin, needs the command that makes one. */
function DeadLink() {
  return (
    <>
      <h2 style={{ marginTop: 0 }}>This link no longer works</h2>
      <p className="help">
        It has been used already, it ran out, or a newer one was made for you — each link
        works once, and only the latest one does.
      </p>
      <p className="help">
        Ask whoever runs this box for a new one. If that is you, run{" "}
        <code>yaaf users setup-link EMAIL</code> on the box itself, with your own address.
      </p>
      <div className="actions">
        <Link to="/login">Go to sign in</Link>
      </div>
    </>
  );
}

export default function Setup() {
  const [params] = useSearchParams();
  const token = params.get("token") ?? "";
  // undefined while asking, null when the link is dead.
  const [info, setInfo] = useState<SetupInfo | null | undefined>(token ? undefined : null);
  const [problem, setProblem] = useState<string | null>(null);
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const navigate = useNavigate();
  const { refresh } = useMe();

  useEffect(() => {
    if (!token) return;
    // Reading the link spends nothing; only choosing the password does.
    get<SetupInfo>(`/auth/setup?token=${encodeURIComponent(token)}`)
      .then(setInfo)
      .catch((e) => {
        if (e instanceof ApiError && e.status === 404) setInfo(null);
        else setProblem((e as Error).message);
      });
  }, [token]);

  const tooShort = password.length > 0 && password.length < MIN_PASSWORD;
  const mismatch = confirm.length > 0 && confirm !== password;
  const ready = password.length >= MIN_PASSWORD && confirm === password;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!ready) return;
    setBusy(true);
    setError(null);
    try {
      await post("/auth/setup", { token, password });
      await refresh();
      navigate("/desk");
    } catch (err) {
      // The link can die between reading it and using it — another tab, a newer
      // link issued in the meantime. Say so in the same place it would have
      // been said on arrival, not as a stray error under the form.
      if (err instanceof ApiError && err.status === 404) setInfo(null);
      else setError((err as Error).message);
      setBusy(false);
    }
  }

  const head = info ? heading(info) : null;

  return (
    <div className="center-page">
      <form className="card login-card" onSubmit={submit}>
        <h1>
          y<span style={{ color: "var(--accent)" }}>aa</span>f
        </h1>
        {problem ? (
          <>
            <div className="error">{problem}</div>
            <p className="help">
              The box did not answer, so this link has not been checked or spent. Try it
              again in a moment.
            </p>
          </>
        ) : info === undefined ? (
          <p className="subtitle">Checking your link…</p>
        ) : info === null || !head ? (
          <DeadLink />
        ) : (
          <>
            <h2 style={{ marginTop: "var(--s3)" }}>{head.title}</h2>
            <p className="help" style={{ marginBottom: "var(--s4)" }}>{head.lede}</p>
            {/* The address rides along hidden so a password manager saves the
                new password against the right account, not against nothing. */}
            <input
              type="email"
              value={info.email}
              autoComplete="username"
              readOnly
              hidden
            />
            <label className="field" htmlFor="new-password">
              Password
            </label>
            <input
              id="new-password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="new-password"
              autoFocus
              required
            />
            <p className="help" style={{ margin: "var(--s1) 0 0" }}>
              At least {MIN_PASSWORD} characters. A few unrelated words is easier to remember
              than a short string of symbols, and harder to guess.
              {tooShort && ` ${MIN_PASSWORD - password.length} to go.`}
            </p>
            <label className="field" htmlFor="confirm-password">
              The same again
            </label>
            <input
              id="confirm-password"
              type="password"
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              autoComplete="new-password"
              required
            />
            {mismatch && <p className="help" style={{ color: "var(--critical)" }}>The two do not match yet.</p>}
            {error && <div className="error">{error}</div>}
            <div className="actions">
              <Btn kind="primary" type="submit" disabled={!ready} busy={busy}>
                {info.purpose === "reset" ? "Save and sign in" : "Choose it and sign in"}
              </Btn>
            </div>
          </>
        )}
      </form>
    </div>
  );
}
