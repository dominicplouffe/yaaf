import { useEffect, useState } from "react";
import { Dashboard, get, patch, post } from "../../api";

interface SecretRow {
  id: string;
  name: string;
  project_id: string | null;
  role_scopes: string[];
}

export default function Settings({ projectId, dash }: { projectId: string; dash: Dashboard }) {
  const [secrets, setSecrets] = useState<SecretRow[]>([]);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // New secret form
  const [secretName, setSecretName] = useState("anthropic-api-key");
  const [secretValue, setSecretValue] = useState("");

  // Config form
  const [runtime, setRuntime] = useState("");
  const [billingSecret, setBillingSecret] = useState("");
  const [heartbeatHours, setHeartbeatHours] = useState("");

  // Amendment form
  const [amendReason, setAmendReason] = useState("");
  const [amendChanges, setAmendChanges] = useState('{\n  "queue_ceiling": 8\n}');

  function refreshSecrets() {
    get<SecretRow[]>(`/admin/secrets?project_id=${projectId}`)
      .then(setSecrets)
      .catch((e: Error) => setError(e.message));
  }
  useEffect(refreshSecrets, [projectId]);

  async function act(fn: () => Promise<unknown>, okMessage: string) {
    setError(null);
    setMessage(null);
    try {
      await fn();
      setMessage(okMessage);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  return (
    <>
      {message && <div className="notice-box">{message}</div>}
      {error && <div className="error">{error}</div>}

      <h2 style={{ marginTop: 0 }}>Secrets</h2>
      <div className="card">
        <p className="help">
          Stored Fernet-encrypted; injected into sandboxes only for roles whose scopes name them.
          The value is write-only — it is never shown again.
        </p>
        <div className="row">
          <div>
            <label className="field">Name</label>
            <input value={secretName} onChange={(e) => setSecretName(e.target.value)} />
          </div>
          <div>
            <label className="field">Value</label>
            <input
              type="password"
              value={secretValue}
              onChange={(e) => setSecretValue(e.target.value)}
              placeholder="sk-ant-…"
            />
          </div>
        </div>
        <div className="actions">
          <button
            className="primary"
            disabled={!secretName.trim() || !secretValue}
            onClick={() =>
              act(async () => {
                await post("/admin/secrets", {
                  project_id: projectId,
                  name: secretName.trim(),
                  value: secretValue,
                  role_scopes: [],
                });
                setSecretValue("");
                refreshSecrets();
              }, "Secret stored (encrypted).")
            }
          >
            Store secret
          </button>
        </div>
        {secrets.length > 0 && (
          <table style={{ marginTop: 12 }}>
            <thead>
              <tr>
                <th>Name</th>
                <th>Id</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {secrets.map((secret) => (
                <tr key={secret.id}>
                  <td>{secret.name}</td>
                  <td className="mono">{secret.id}</td>
                  <td>
                    <button
                      className="small"
                      onClick={() =>
                        act(
                          () => patch(`/admin/projects/${projectId}`, { billing_secret_id: secret.id }),
                          `Billing now uses "${secret.name}".`,
                        )
                      }
                    >
                      use for billing
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <h2>Runtime & billing</h2>
      <div className="card">
        <div className="row">
          <div>
            <label className="field">Runtime</label>
            <select value={runtime} onChange={(e) => setRuntime(e.target.value)}>
              <option value="">(unchanged)</option>
              <option value="agent_sdk">agent_sdk — real model</option>
              <option value="mock">mock — scripted, $0</option>
            </select>
          </div>
          <div>
            <label className="field">Billing mode</label>
            <select value={billingSecret} onChange={(e) => setBillingSecret(e.target.value)}>
              <option value="">(unchanged)</option>
              <option value="api_key">api_key — USD budget</option>
              <option value="subscription">subscription — token budget</option>
            </select>
          </div>
          <div>
            <label className="field">CEO planning heartbeat (hours)</label>
            <input
              type="number"
              min={0.1}
              step={0.5}
              value={heartbeatHours}
              placeholder="6"
              onChange={(e) => setHeartbeatHours(e.target.value)}
            />
          </div>
        </div>
        <div className="actions">
          <button
            className="primary"
            onClick={() =>
              act(async () => {
                const config: Record<string, unknown> = {};
                if (runtime) config["runtime"] = runtime;
                if (heartbeatHours) config["ceo_heartbeat_hours"] = Number(heartbeatHours);
                const body: Record<string, unknown> = {};
                if (Object.keys(config).length) body["config"] = config;
                if (billingSecret) body["billing_mode"] = billingSecret;
                if (!Object.keys(body).length) throw new Error("nothing to change");
                await patch(`/admin/projects/${projectId}`, body);
              }, "Configuration updated (mutable config only — the constitution is untouched).")
            }
          >
            Apply
          </button>
          <span className="help">
            Currently: runtime <code>{String(dash.config["runtime"] ?? "agent_sdk")}</code>, billing{" "}
            <code>{dash.billing_mode}</code>, heartbeat every{" "}
            <code>{String(dash.config["ceo_heartbeat_hours"] ?? 6)}h</code>
          </span>
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
            onClick={() =>
              act(async () => {
                const changes = JSON.parse(amendChanges);
                await post(`/admin/projects/${projectId}/amendments`, {
                  reason: amendReason,
                  changes,
                });
              }, "Amendment proposed — in force after the cooling-off.")
            }
            disabled={amendReason.trim().length < 10}
          >
            Propose amendment
          </button>
        </div>
      </div>
    </>
  );
}
