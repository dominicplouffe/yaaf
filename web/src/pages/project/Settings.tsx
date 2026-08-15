import { useState } from "react";
import { Dashboard, patch, post } from "../../api";

/** One decision, one button: pick how this project pays for models, paste the
    credential, Connect does everything (store encrypted secret + attach it +
    set the billing mode). The status card always reflects the server. */
export default function Settings({
  projectId,
  dash,
  reload,
}: {
  projectId: string;
  dash: Dashboard;
  reload: () => void;
}) {
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [billingChoice, setBillingChoice] = useState<"api_key" | "subscription">(
    dash.billing_mode === "subscription" ? "subscription" : "api_key",
  );
  const [credential, setCredential] = useState("");
  const [busy, setBusy] = useState(false);

  const [runtime, setRuntime] = useState(String(dash.config["runtime"] ?? "agent_sdk"));
  const [heartbeatHours, setHeartbeatHours] = useState(
    String(dash.config["ceo_heartbeat_hours"] ?? 6),
  );

  const [amendReason, setAmendReason] = useState("");
  const [amendChanges, setAmendChanges] = useState('{\n  "queue_ceiling": 8\n}');

  async function act(fn: () => Promise<unknown>, okMessage: string) {
    setError(null);
    setMessage(null);
    setBusy(true);
    try {
      await fn();
      setMessage(okMessage);
      reload();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function connectBilling() {
    const name = billingChoice === "subscription" ? "claude-subscription-token" : "anthropic-api-key";
    await act(async () => {
      const secret = await post<{ id: string }>("/admin/secrets", {
        project_id: projectId,
        name,
        value: credential.trim(),
        role_scopes: [],
      });
      await patch(`/admin/projects/${projectId}`, {
        billing_mode: billingChoice,
        billing_secret_id: secret.id,
      });
      setCredential("");
    }, `Billing connected: ${billingChoice} using "${name}". New runs use it immediately.`);
  }

  const billingReady = dash.billing_secret_id !== null;
  const runtimeNow = String(dash.config["runtime"] ?? "agent_sdk");

  return (
    <>
      {message && <div className="notice-box">{message}</div>}
      {error && <div className="error">{error}</div>}

      <h2 style={{ marginTop: 0 }}>Current state</h2>
      <div className="card">
        <dl className="kv">
          <dt>runtime</dt>
          <dd>
            <span className={`badge ${runtimeNow === "mock" ? "warn" : "accent"}`}>
              {runtimeNow}
            </span>{" "}
            {runtimeNow === "mock" && <span className="help">scripted — no real model runs</span>}
          </dd>
          <dt>billing</dt>
          <dd>
            <span className={`badge ${billingReady ? "good" : "critical"}`}>
              {billingReady
                ? `${dash.billing_mode} · ${dash.billing_secret_name}`
                : "not connected"}
            </span>{" "}
            {!billingReady && runtimeNow === "agent_sdk" && (
              <span className="help">real runs will fail until you connect billing below</span>
            )}
          </dd>
          <dt>budget unit</dt>
          <dd className="help">
            {dash.billing_mode === "subscription"
              ? `tokens (cap ${Number(dash.constitution["monthly_token_cap"]).toLocaleString()}/month) — subscription runs book tokens, never dollars`
              : `USD (cap $${Number(dash.constitution["monthly_usd_cap"]).toLocaleString()}/month)`}
          </dd>
          <dt>CEO heartbeat</dt>
          <dd className="help">every {String(dash.config["ceo_heartbeat_hours"] ?? 6)}h — each wake costs a real run</dd>
        </dl>
      </div>

      <h2>Connect billing</h2>
      <div className="card">
        <label className="field">How should this project pay for models?</label>
        <div className="pill-row" style={{ marginBottom: 8 }}>
          <label style={{ display: "flex", gap: 6, alignItems: "center" }}>
            <input
              type="radio"
              style={{ width: "auto" }}
              checked={billingChoice === "api_key"}
              onChange={() => setBillingChoice("api_key")}
            />
            Anthropic API key — metered, USD budget
          </label>
          <label style={{ display: "flex", gap: 6, alignItems: "center" }}>
            <input
              type="radio"
              style={{ width: "auto" }}
              checked={billingChoice === "subscription"}
              onChange={() => setBillingChoice("subscription")}
            />
            Claude subscription — token budget
          </label>
        </div>
        <label className="field">
          {billingChoice === "subscription"
            ? "Subscription token — run `claude setup-token` in a terminal and paste the result (sk-ant-oat…)"
            : "API key from console.anthropic.com (sk-ant-api…)"}
        </label>
        <input
          type="password"
          value={credential}
          onChange={(e) => setCredential(e.target.value)}
          placeholder={billingChoice === "subscription" ? "sk-ant-oat01-…" : "sk-ant-api03-…"}
        />
        <div className="actions">
          <button className="primary" disabled={busy || credential.trim().length < 10} onClick={connectBilling}>
            {busy ? "Connecting…" : "Connect"}
          </button>
          <span className="help">
            Stored encrypted, write-only. One click does all of it: store → attach → set mode.
          </span>
        </div>
        {billingChoice === "subscription" && (
          <p className="help">
            Subscription runs share your personal Claude rate limits and are for personal projects;
            use an API key for anything beyond that.
          </p>
        )}
      </div>

      <h2>Runtime & cadence</h2>
      <div className="card">
        <div className="row">
          <div>
            <label className="field">Runtime</label>
            <select value={runtime} onChange={(e) => setRuntime(e.target.value)}>
              <option value="agent_sdk">agent_sdk — real model in the sandbox</option>
              <option value="mock">mock — scripted, $0</option>
            </select>
          </div>
          <div>
            <label className="field">CEO planning heartbeat (hours)</label>
            <input
              type="number"
              min={0.1}
              step={0.5}
              value={heartbeatHours}
              onChange={(e) => setHeartbeatHours(e.target.value)}
            />
          </div>
        </div>
        <div className="actions">
          <button
            disabled={busy}
            onClick={() =>
              act(
                () =>
                  patch(`/admin/projects/${projectId}`, {
                    config: {
                      runtime,
                      ceo_heartbeat_hours: Number(heartbeatHours) || 6,
                    },
                  }),
                "Runtime configuration applied.",
              )
            }
          >
            Apply
          </button>
        </div>
      </div>

      <h2>Constitution amendment</h2>
      <div className="card">
        <p className="help">
          Amendments take effect after a 72-hour cooling-off and are disclosed to the CEO. A forcing
          function you can edit instantly is just a default — the delay is the point.
        </p>
        <label className="field">Reason (recorded, shown to the CEO)</label>
        <input value={amendReason} onChange={(e) => setAmendReason(e.target.value)} />
        <label className="field">Changes (JSON overlay onto the current constitution)</label>
        <textarea
          className="mono"
          value={amendChanges}
          onChange={(e) => setAmendChanges(e.target.value)}
        />
        <div className="actions">
          <button
            disabled={busy || amendReason.trim().length < 10}
            onClick={() =>
              act(async () => {
                const changes = JSON.parse(amendChanges);
                await post(`/admin/projects/${projectId}/amendments`, {
                  reason: amendReason,
                  changes,
                });
              }, "Amendment proposed — in force after the cooling-off, then disclosed to the CEO.")
            }
          >
            Propose amendment
          </button>
        </div>
      </div>
    </>
  );
}
