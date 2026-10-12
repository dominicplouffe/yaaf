import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError, post } from "../api";
import { useMe } from "../me";
import { Btn } from "../ui/kit";

/** The first thing anyone sees.
 *
 * It used to ask for "your access key": one value for the whole box, pasted
 * into a box of its own, which signed in a *client* and never a person — so
 * whoever held it was recorded as the chairman of everything they touched.
 * It is an email and a password now, the way every other site a person uses
 * asks, and the browser keeps the session in a cookie this page cannot read.
 *
 * There is no "forgot your password" link because there is nothing for it to
 * do: this box sends no email. A reset is a fresh setup link from whoever runs
 * the box, and the page says so rather than offering a button that pretends. */
export default function Login() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const navigate = useNavigate();
  const { refresh } = useMe();

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await post("/auth/login", { email: email.trim(), password });
      await refresh();
      navigate("/desk");
    } catch (err) {
      // A wrong password and an address with no account get the same sentence,
      // because the server gives them the same answer: the form is not a way
      // to find out who has an account here. A throttle says how long, in the
      // server's own words.
      setError(
        err instanceof ApiError && err.status === 401
          ? "That email and password did not match."
          : (err as Error).message,
      );
      setBusy(false);
    }
  }

  return (
    <div className="center-page">
      <form className="card login-card" onSubmit={submit}>
        <h1>
          y<span style={{ color: "var(--accent)" }}>aa</span>f
        </h1>
        <p className="subtitle" style={{ marginBottom: "var(--s5)" }}>
          Hire a team of agents, give them a budget and a deadline, and read what they did
          while you were out.
        </p>
        <label className="field" htmlFor="email">
          Email
        </label>
        <input
          id="email"
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          autoFocus
          autoComplete="username"
          required
        />
        <label className="field" htmlFor="password">
          Password
        </label>
        <input
          id="password"
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoComplete="current-password"
          required
        />
        {error && <div className="error">{error}</div>}
        <div className="actions">
          <Btn kind="primary" type="submit" disabled={!email.trim() || !password} busy={busy}>
            Sign in
          </Btn>
        </div>
        <p className="help" style={{ marginBottom: 0 }}>
          Forgotten your password, or never chose one? Ask whoever runs this box for a setup
          link — it lets you pick a new one.
        </p>
      </form>
    </div>
  );
}
