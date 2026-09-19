import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { get, setToken } from "../api";
import { Btn } from "../ui/kit";

/** The first thing anyone sees.
 *
 * It used to say "The chairman's cockpit. Paste your admin token to enter",
 * and when the token was wrong it told the reader the value "lives in
 * deploy/.env". Both are true and neither is a sign-in screen: the first is
 * our word for the product, and the second is a path on a server the person
 * signing in may never have touched. */
export default function Login() {
  const [token, setTokenInput] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const navigate = useNavigate();

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setToken(token.trim());
    try {
      await get("/observe/projects");
      navigate("/desk");
    } catch {
      setError(
        "That key was not accepted. It is the one printed at the end of your install — " +
          "whoever set this up for you can read it out again.",
      );
    } finally {
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
        <label className="field" htmlFor="token">
          Your access key
        </label>
        <input
          id="token"
          type="password"
          value={token}
          onChange={(e) => setTokenInput(e.target.value)}
          placeholder="Paste it here"
          autoFocus
          autoComplete="current-password"
        />
        {error && <div className="error">{error}</div>}
        <div className="actions">
          <Btn kind="primary" type="submit" disabled={!token.trim()} busy={busy}>
            Sign in
          </Btn>
        </div>
      </form>
    </div>
  );
}
