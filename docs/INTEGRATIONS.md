# Integrations — connecting your project to the world

Everything here is **optional** and configured from the dashboard — most of it
per project, and the pieces that are the same for every project (the Slack
connection, the Founder's model key) once, app-wide, under the sidebar's
**Settings**. Nothing below requires editing a role, redeploying, or touching
the CLI, with no exceptions.

The rule that governs all of it: **agents get capabilities, never credentials
they could exfiltrate.** Secrets are envelope-encrypted, scoped to roles, and
injected per-run into the sandbox environment. Platform-level tokens (Slack,
GitHub provisioning, webhook signing) are decrypted server-side only and never
enter a sandbox at all.

---

## Billing — how agents pay for Claude

Two screens. An administrator creates **LLM credentials** once, under the
sidebar's **Settings → LLM credentials**; each project is then **assigned**
one, under its own **Settings → Money**. Required; nothing runs without it.

Two kinds of credential, and the kind decides the project's billing mode and
budget unit — there is no separate switch:

| Credential | Value | Budget unit |
|---|---|---|
| Anthropic API key | `sk-ant-api…` from the console | USD (`monthly_usd_cap`) |
| Claude subscription token | `sk-ant-oat…` from `claude setup-token` | Tokens (`monthly_token_cap`) |

A value of the wrong shape for its kind is **refused while you're still
looking at the screen** — otherwise every future run fails with a baffling
"Not logged in". The health checklist re-checks it, and **Test connection**
proves the assigned credential works now.

What being managed buys you:

- **One value, many projects.** Rotate a credential in Settings and every
  project assigned to it uses the new value on its next run. Rename it and
  every Overview follows. A credential a project is assigned to cannot be
  deleted until the project is reassigned or detached (clear the pick under
  the project's Settings → Money).
- **No key in a project, ever.** `POST /admin/secrets` refuses `kind: billing`,
  `yaaf set-secret --kind billing` is gone, a clone keeps the assignment, and a
  backup carries the credential's *name* and never its value — the form
  re-selects it on import when this install has one by that name.
- **Every founding path asks.** The New Project form has a picker, the
  Founder reads the list and asks you which when there is more than one, and
  the MCP `create_project` preview lists the choices when none was named.
  The Founder is told not to guess, and both previews show the choice
  before anything is created.
- **The plan meter.** A subscription credential shows what Anthropic says is
  left on the session and weekly windows — the same numbers as `/usage` in
  Claude Code — in Settings and on every project paying with it. Every project
  on the credential draws from the same windows; at 100% Anthropic refuses
  calls and runs fail until the window resets. It is **harvested, never fetched**: the Claude Agent
  SDK reports these windows to every agent run, on the credential doing the
  spending, so the platform reads them off its own runs. Nothing polls
  Anthropic — the poller this replaced earned a `429` with a `Retry-After` of
  tens of minutes and left the meter dark while the credential was perfectly
  healthy. The trade is stated on the screen: a credential nothing has run on
  yet has no reading, and the number carries the time a run last reported it. Shown and never
  enforced: the token cap is the platform's own rule. API keys have no plan
  meter.

> **Terms note:** Anthropic's consumer terms don't permit running fleets on a
> personal Claude subscription. Subscription mode exists for single-user
> tinkering; use an API key for anything real.

**Not to be confused with the Founder's credential.** The sidebar's **Settings
→ Founder model credential** holds a *separate* key that pays for
product-wide conversations (the Founder, which drafts a project before one
exists). It is decrypted server-side only and **never reaches a project's
agents** — there is no inheritance and no fallback. A project with no
credential assigned cannot run, and that is the intended answer.

## Credentials and variables

**Settings → Data & credentials**, or `yaaf set-secret`.

A secret has a name, a value (encrypted at rest with `YAAF_MASTER_KEY`), a
`kind`, and a list of **role scopes**. Only agents whose role is in the scope
list get it injected, and only for the duration of a run.

- `kind: "service"` — role-grantable. WordPress passwords, API tokens, database
  URLs. This is the only kind a project stores.
- The Anthropic credential agents pay with is not a project secret at all. It
  is a **managed LLM credential**, created in Settings (or with
  `yaaf create-llm-credential`) and *assigned* to projects, and it refuses role
  grants entirely — no agent should hold it. `yaaf set-secret --kind billing`
  is refused; see [Managed LLM credentials](FEATURES.md#7-money-that-cant-run-away).

Deleting a secret that a data source depends on is refused, so you can't orphan
a source by tidying up.

```bash
yaaf set-secret wordpress-app-password --project my-slug --scopes writer,blog-writer
# value is prompted, never echoed or logged
```

### Credentials that are files

Some credentials are not a value you can type — a Google service-account key, an
SSH deploy key, a kubeconfig, a `.p12` keystore. Store one with the **choose a
file** button beside the value box, or:

```bash
yaaf set-secret google-sa --project my-slug --scopes researcher \
  --from-file ~/Downloads/my-project-a1b2c3.json
```

What a granted role then receives is **the path**, not the contents:

```
GOOGLE_SA=/run/yaaf/credentials/my-project-a1b2c3.json
```

which is what every library that reads a key file actually wants — pass it to
`from_service_account_file`, to `ssh -i`, to `KUBECONFIG`. The platform writes
the file, at mode `0600`, on a tmpfs **outside the workspace**, and it dies with
the container. That last part is the point rather than a detail: the workspace
is a git clone agents commit and push from, so a key written there is one
`git add -A` from being published. No agent is ever asked to save a credential
itself, and the standing instruction — never write a credential to a file —
stays true for them.

Two refusals you may meet, both deliberate:

- Pasting a key file's *contents* into the value box is refused, and the message
  names the fix. Stored as a string it would arrive as a variable holding JSON
  where the library expects a path, and the error you'd eventually see names a
  missing file rather than the mistake.
- Whether a credential is a file is **fixed when you store it**, exactly as
  `kind` is. Rotating replaces the bytes; changing its shape means storing a new
  credential, because the old one's meaning is already wired into running roles.

A file credential is an ordinary `service` credential in every other respect: it
is granted per role, it clones, and an export carries its name and the fact that
it is a file — never its bytes.

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

### OAuth servers — Notion, Linear, Atlassian and friends

Hosted MCP servers usually hand out no pastable token: they are OAuth
resource servers, and the client is expected to walk the MCP authorization
spec — discovery, client registration, the authorization-code flow with PKCE,
refresh-token renewal. yaaf does all of that on the control plane. Pick
**OAuth — sign in through the provider** in the auth dropdown, save, and press
**Connect**: a tab opens on the provider's consent screen, and when you finish
there the source connects. The tokens are stored as encrypted project secrets
and **refreshed automatically** — before every health check and before every
run that mounts the source — so sandboxes keep receiving the same
`MCP_<NAME>_TOKEN` variable a pasted bearer would produce, always holding a
current access token. If renewal ever stops working (a revoked grant, a
provider reset), the source shows **needs re-auth** on the health page and the
fix is one **Reconnect** click.

Most providers register yaaf as a client automatically (RFC 7591). The few
that don't get an **Advanced** disclosure on the form: paste a client ID (and
secret, if issued) from the provider's developer console.

Two operational notes:

- Set `YAAF_PUBLIC_BASE_URL` to the browser-facing origin of your install
  (e.g. `https://yaaf.example.com`). The sign-in redirects back to
  `<that origin>/oauth/mcp/callback`, and providers generally refuse
  non-https redirect URIs — behind a TLS-terminating proxy the derived
  fallback reports plain http.
- OAuth tokens never travel: a **clone** carries the registered client but
  asks you to press Connect on the twin (refresh tokens rotate on use, so two
  projects sharing one would invalidate each other), and an **export** carries
  only the `auth: "oauth"` marker.

## Slack

**Settings → Slack connection** (app-wide), then **project → Settings →
Integrations → Slack bridge** (that project's channels).

Two things at once: your project's channels **mirror out** to Slack, and you
can **talk to your CEO from Slack** — the same resident chat session as the
Boardroom, answering in seconds.

### One workspace, every project

You reuse one Slack workspace and only ever change the *channels*, so that is
how it is stored: the tokens live once at platform scope and every project
inherits them. Connect Slack under the sidebar's **Settings**, and a new
project needs nothing but its channel map.

**yaaf makes those channels for you — the boardroom now, the rest on request.**
With a connection configured, creating a project provisions `{prefix}-boardroom`
— private by default, with your Slack user invited — and the "project created"
screen tells you the name. That is the one room every project has and the room
you talk to the CEO in; `{prefix}-leadership` and one room per team wait behind
**Create missing channels** on the project's Slack screen, because a project
that was created five minutes ago does not need six channels in your workspace
yet. The prefix defaults to the project's slug (the one name guaranteed not to
collide between two projects) and is configurable app-wide or per project. Turn
auto-creation off and the boardroom waits behind the same button as everything
else. Either way it is idempotent — a room already mapped is left alone, and a
name Slack already holds is *adopted*, not duplicated. Run it again after hiring
a team to get that team's room.

The bot needs `chat:write` plus `channels:manage` (public) or `groups:write`
(private) to create and invite.

### Overriding it for one project

Inheritance is **per field**. A project that names its own `bot_token_secret`
overrides only the bot token and still inherits the signing secret, the app
token, your Slack member ID and the naming convention — which is what a project
living in a *different* workspace actually needs. The channel map is always the
project's own, because that is the part that genuinely differs.

Three states, and the difference between the last two matters:

| `project.integrations["slack"]` | Meaning |
|---|---|
| absent or `null` | Inherit everything. The default a project is born in — harmless, because the channel map is empty until rooms exist. |
| `{ … }` | Inherit, with the named fields winning. |
| `{"enabled": false}` | Off for this project, however the platform is wired. |

The shape (set from the dashboard or `PATCH /admin/projects/{id}`; every field
optional):

```json
{
  "enabled": true,
  "bot_token_secret":   "<secret name holding the xoxb- bot token>",
  "app_token_secret":   "<secret name holding the xapp- app token, for Socket Mode>",
  "signing_secret":     "<secret name holding the Slack signing secret, for the HTTP path>",
  "chairman_slack_user": "U…",
  "channel_prefix":     "acme",
  "channel_visibility": "private",
  "channel_map": {
    "boardroom":  "C…",
    "leadership": "C…",
    "team:<team_id>": "C…"
  }
}
```

**Socket Mode is the recommended path** — it opens an outbound WebSocket to
Slack, so your self-hosted box needs no public URL. Set the app-level token
(`xapp-…`) once in the app-wide settings and every project's socket uses it.

### The trust model

The Postgres bus is always canonical; Slack is a viewport onto it plus exactly
**one** inbound path — a plain message from the configured chairman user in the
mapped boardroom channel, which becomes an ordinary chairman boardroom message.
Slack can never write anything you couldn't. Every Slack token keeps empty
`role_scopes` whether it is stored app-wide or per project, so **no agent
sandbox ever holds the bot token**: an agent can't speak to Slack except by
writing to the bus and letting the mirror carry it — comms policy enforced by
key custody. Sharing one workspace across projects does not widen that by an
inch; the mirror decrypts server-side, exactly as it did before.

Mirroring is individually best-effort. A failed mirror leaves the message
un-mirrored and retries next tick; one misconfigured project never stops
another. Channel provisioning is best-effort the same way: a Slack outage
during project creation costs you the channel map, never the project.

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
