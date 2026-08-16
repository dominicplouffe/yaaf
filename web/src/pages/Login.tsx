import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { get, setToken } from "../api";

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
    } catch (err) {
      setError(
        "That token was rejected. It is the YAAF_ADMIN_TOKEN value — on a compose deployment it lives in deploy/.env.",
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
        <p className="subtitle">The chairman's cockpit. Paste your admin token to enter.</p>
        <label className="field" htmlFor="token">
          Admin token
        </label>
        <input
          id="token"
          type="password"
          value={token}
          onChange={(e) => setTokenInput(e.target.value)}
          placeholder="yat_…"
          autoFocus
        />
        {error && <div className="error">{error}</div>}
        <div className="actions">
          <button className="primary" disabled={busy || !token.trim()}>
            {busy ? "Checking…" : "Enter"}
          </button>
        </div>
      </form>
    </div>
  );
}
