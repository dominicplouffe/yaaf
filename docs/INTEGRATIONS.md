# Integrations — connecting your company to the world

Everything here is **optional** and configured per project from the dashboard.
Nothing below requires editing a role, redeploying, or touching the CLI, with
no exceptions.

The rule that governs all of it: **agents get capabilities, never credentials
they could exfiltrate.** Secrets are envelope-encrypted, scoped to roles, and
injected per-run into the sandbox environment. Platform-level tokens (Slack,
GitHub provisioning, webhook signing) are decrypted server-side only and never
enter a sandbox at all.

---

## Billing — how agents pay for Claude

**Settings → Money.** Required; nothing runs without it.

Two modes, and the credential must match:

| Mode | Credential | Budget unit |
|---|---|---|
| `api_key` | An Anthropic API key (`sk-ant-api…`) from the console | USD (`monthly_usd_cap`) |
| `subscription` | An OAuth token (`sk-ant-oat…`) from `claude setup-token` | Tokens (`monthly_token_cap`) |

A mismatched pair is **refused while you're still looking at the screen** —
otherwise every future run fails with a baffling "Not logged in". The health
checklist re-checks it, and there's a live billing check button.

> **Terms note:** Anthropic's consumer terms don't permit running fleets on a
> personal Claude subscription. Subscription mode exists for single-user
> tinkering; use an API key for anything real.

## Credentials and variables

**Settings → Data & credentials**, or `yaaf set-secret`.

A secret has a name, a value (encrypted at rest with `YAAF_MASTER_KEY`), a
`kind`, and a list of **role scopes**. Only agents whose role is in the scope
list get it injected, and only for the duration of a run.

- `kind: "service"` — role-grantable. WordPress passwords, API tokens, database
  URLs.
- `kind: "billing"` — **refuses role grants entirely.** The Anthropic
  credential is not something any agent should hold.

Deleting a secret that a data source depends on is refused, so you can't orphan
a source by tidying up.

```bash
yaaf set-secret wordpress-app-password --project my-slug --scopes writer,blog-writer
# value is prompted, never echoed or logged
```

## Data sources — external MCP servers

**Settings → Data & credentials → Data sources.** One form: **name, URL,
roles, Save.**

What happens on save:

1. The URL is **validated at the door** — a bare hostname or a mispasted token
   is rejected now, not hours later inside an agent run.
2. The URL (and optional bearer token) are stored as role-granted secrets.
3. The mapping is recorded in project config.
4. The granted roles' sandboxes mount the server as **real tools**.

**Check it.** The check button actually speaks MCP — `initialize`, then
`tools/list` — and tells you which tools came back. (A plain GET was useless:
a healthy MCP endpoint answers GET with 405.)

Saving under an existing name is the edit path. `bearer` omitted keeps the
current token, `""` removes it, anything else rotates it.

> Roles never own MCP servers. A server frozen into a role template follows
> every project that pins that role — including one that had never heard of it.
> Data sources are **project-owned** and assigned to roles.

```bash
curl -X POST "$YAAF_API/admin/projects/$PROJECT/mcp_servers" \
  -H "Authorization: Bearer $YAAF_ADMIN_TOKEN" \
  -d '{"name": "warehouse", "url": "https://mcp.example.com/sse",
       "bearer": "…", "roles": ["researcher", "engineer"],
       "access": "bulk"}'
```

**`access` decides what a result costs.** `inline` (the default) mounts the
source as `mcp__<name>__*` tools, so every result lands in the model's context
and is billed by the token — right for a source you ask small questions of.
`bulk` does not mount it at all: agents reach it through `data_fetch`, which
writes the full result to a workspace file and hands back a path and a preview,
and they aggregate that file with code. Mark anything that serves rows —
exports, listings, logs — as bulk. It is enforcement, not advice: for a bulk
source the expensive path does not exist, so no prompt has to talk an agent out
of taking it.

## Slack

**Settings → Integrations → Slack bridge.**

Two things at once: your company's channels **mirror out** to Slack, and you
can **talk to your CEO from Slack** — the same resident chat session as the
Boardroom, answering in seconds.

**Socket Mode is the recommended path** — it opens an outbound WebSocket to
Slack, so your self-hosted box needs no public URL. Provide an app-level token
(`xapp-…`) as a secret and name it in the config.

The config shape (`project.config["slack"]`, set from the dashboard or
`PATCH /admin/projects/{id}`):

```json
{
  "bot_token_secret":   "<secret name holding the xoxb- bot token>",
  "app_token_secret":   "<secret name holding the xapp- app token, for Socket Mode>",
  "signing_secret":     "<secret name holding the Slack signing secret, for the HTTP path>",
  "chairman_slack_user": "U…",
  "channel_map": {
    "boardroom":  "C…",
    "leadership": "C…",
    "team:<team_id>": "C…"
  }
}
```

**The trust model:** the Postgres bus is always canonical; Slack is a viewport
onto it plus exactly **one** inbound path — a plain message from the configured
chairman user in the mapped boardroom channel, which becomes an ordinary
chairman boardroom message. Slack can never write anything you couldn't. Both
Slack tokens keep empty `role_scopes`, so **no agent sandbox ever holds the bot
token**: an agent can't speak to Slack except by writing to the bus and letting
the mirror carry it — comms policy enforced by key custody.

Mirroring is individually best-effort. A failed mirror leaves the message
un-mirrored and retries next tick; one misconfigured project never stops
another.

## GitHub

**Settings → Integrations → GitHub workspace.**

Install a **platform-level** secret named `github` (no project) holding a token
that can create repos. Then attach a repo per project:

- a bare name (`widget`) creates a private repo under the token's account
- `owner/repo` attaches an existing one after verifying access

That repo becomes the project's workspace: the runner clones it once, and
agents work in it. The platform token is decrypted in exactly two places
(provisioning and that first clone) and **never enters a sandbox** — agents
push with their own role-scoped `github` secret.

Repo events arriving on the signed webhook become **platform-attested
evidence**, which skips verification because agents can't forge it.

## Webhooks — evidence agents can't fabricate

**Settings → Advanced → Webhook secrets.** Provisioning returns two signing
secrets, **shown once**:

```bash
curl -X POST "$YAAF_API/admin/projects/$PROJECT/webhook_secrets" \
  -H "Authorization: Bearer $YAAF_ADMIN_TOKEN"
# → {"github_webhook_secret": "…", "webhook_secret": "…"}
```

They live only in project config, never in a role scope, so **no run ever holds
them** — which is exactly what makes webhook evidence unforgeable.

| Endpoint | Sender | Signature header |
|---|---|---|
| `POST /webhooks/github/{project_id}` | GitHub | `X-Hub-Signature-256` |
| `POST /webhooks/slack/{project_id}` | Slack (HTTP path) | Slack signing |
| `POST /webhooks/generic/{project_id}` | Anything you wire up | `X-Yaaf-Signature` |

The generic connector takes a described observation:

```json
{"description": "Stripe: first paid subscription, plan pro",
 "source_url": "https://dashboard.stripe.com/…",
 "occurred_at": "2026-08-16T14:02:00Z"}
```

`X-Yaaf-Signature` is the HMAC-SHA256 hex of the **raw body** under
`webhook_secret`. Signature is checked before parsing, so an unsigned caller
learns nothing about the schema. GitHub deliveries are deduped, so a redelivery
doesn't double-count.

## Notifications to you

Process-level environment, set once for the deployment (see
[DEPLOYMENT.md](DEPLOYMENT.md)):

- **Email** — `YAAF_SMTP_HOST`, `YAAF_SMTP_PORT`, `YAAF_SMTP_USERNAME`,
  `YAAF_SMTP_PASSWORD`, `YAAF_SMTP_FROM`
- **Webhook** — `YAAF_CHAIRMAN_WEBHOOK_URL`, a generic JSON POST with a
  Slack-compatible `text` field

Unset means log-only. You'll be notified for budget auto-pauses and approval
nags.

## Artifact storage

Local disk by default (`YAAF_ARTIFACTS_ROOT`). For anything
S3-compatible — MinIO, Cloudflare R2, AWS — set `YAAF_ARTIFACT_S3_ENDPOINT`
(plus bucket and keys) on **both** the `api` and `runner` services and install
the extra:

```bash
pip install 'yaaf[s3]'
docker compose --profile s3 up      # bundled MinIO for local use
```

The bucket is created on first use. The base install stays boto3-free — it's
imported lazily, only when the endpoint is set.

## Waking on the world

A signed generic webhook may carry `wake`: `"ceo"` or an agent's exact name.
The named agent gets an urgent notice and wakes on the next dispatcher tick —
under every wake-rate brake, so the world can knock but never stampede. This
is how a customer email, an order, or a monitoring alert reaches an agent
before its next heartbeat.

## Revenue

A signed generic webhook whose JSON body carries `revenue_usd` books a
`revenue` ledger row alongside its evidence. This is the ONLY path that writes
revenue — no agent verb exists for it — so income can be reported without ever
being inventable, and the CEO can build a P&L on ledger rows instead of
claims. Point your payment processor's webhook (via a small signing relay) at
`/webhooks/generic/{project_id}`.

## Network

The sandbox network is open — no allowlist, no per-role proxy. The chairman's
decision, on the record in docs/AUTONOMY.md: capability should never be a
firewall ticket. What contains an agent is the fresh container, the
role-scoped secrets it was or wasn't granted, the budgets, the loop brakes and
the audit trail.
