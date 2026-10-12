# People — accounts, sessions, and who may decide what

**Status: A1 and A2 are built.** This is the design and the tracking doc.
Update the checkboxes as they land; if a decision here turns out wrong, change
the doc in the same PR that changes the code.

Until this milestone yaaf had exactly one human credential: `YAAF_ADMIN_TOKEN`,
a static bearer that authenticated a *client*, never a person. `admin_auth`
returned the string `"admin"`, so every write through the dashboard or the CLI
was recorded as the project's chairman whoever held the token, and "the chairman
— the one human in the building" was not a product position so much as the only
thing the auth could express.

This document replaces that with people: accounts with passwords, browser
sessions, personal tokens for the CLI, and per-project roles. The admin token is
retired.

---

## What this is not

- **Not multi-tenant orgs.** [PLAN.md](PLAN.md) keeps "Multi-tenant orgs and
  k8s" under *Deliberately not built*, and this does not reopen it. One box,
  several people, each project with its own chair. A project already plays the
  part an org plays elsewhere — its own CEO, roster, budget and constitution —
  so membership is per project and nothing sits above it.
- **Not an implicit local mode.** There is no "on loopback, everyone is the
  admin". Any process that can reach the port would be the admin, which is the
  shape of a DNS-rebinding bug, and the dashboard binding to `127.0.0.1` is a
  containment line, not a credential.
- **Not open sign-up.** Accounts are invited by an instance admin. A box that
  holds LLM credentials and a budget is not a sign-up page.
- **Not SSO, 2FA or email delivery.** Password, session, personal token, setup
  link. The schema leaves room for the rest; nothing pretends to have it.

---

## Principals

| Principal | Credential | Reaches | Stamped as |
|---|---|---|---|
| **A person in a browser** | session cookie `yaaf_session` | `/admin`, `/observe`, `/auth`, the live socket — bounded by their roles | `user:<id>` |
| **A person at a terminal** | personal token `ypt_…` | the same as their browser, never more | `user:<id> via token:<name>` |
| **A model client** | MCP token `ymt_…` (unchanged) | `/mcp` only — its scopes, on the projects its person is a member of (never the instance admin's read-everything) | `user:<id> via mcp:<name>` |
| **An agent run** | per-run token `yrt_…` (unchanged) | `/agent/*` only, its own project | `agent:<id>` |
| **The platform** | none | — | `system:<name>` |

Every credential is a random value stored as a SHA-256 hash, the shape
`yaaf/core/tokens.py` already uses for run and MCP tokens. Nothing is a signed,
stateless token: each one can be revoked by a row update, and is checked against
that row on every request.

## Roles

**On the box:** `users.is_admin` — the *instance admin*. Invites people, issues
setup links, promotes and deactivates; owns the platform settings, the LLM
credentials, platform-level secrets and the role and crew libraries. Can read
every project, because whoever runs the box can read its database anyway and the
page should say so. **Decides nothing on a project it does not chair** — with one
exception, the break-glass: it may transfer a project's chairmanship, audited,
for the chairman who left.

**On a project:** a row in `project_members`.

| | Chairman (exactly one) | Co-chair (any number) | Observer (any number) |
|---|---|---|---|
| Read everything the project shows | yes | yes | yes |
| Decide: approvals, scope, verdicts, dates, reviews, audits, overruns, spend windows | yes | yes | no |
| Speak in the boardroom with authority the CEO may cite | yes | yes | no |
| Configure: amendments, teams, data sources, project secrets, lifecycle | yes | yes | no |
| Decide who decides: add or remove co-chairs and observers | yes | no | no |
| Hand the chair to someone else | yes | no | no |

The chairman is the one name accountable for the project, and `projects.chairman_user_id`
stays the canonical pointer; their `project_members` row says `chairman` and the
two are written together by one function. Co-chairs are peers on every decision
and are recorded as themselves. **Only the chairman decides who decides.**

A person who is neither a member nor the instance admin cannot tell a project
exists: its routes answer 404 in the same words as a project that does not. A
member whose role is too small gets a 403 that says which role the verb needs.

## The rules of this surface

Each names its enforcement, per [CONTRIBUTING.md](../CONTRIBUTING.md) house rule
1, and the invariant test that attacks it, per house rule 2.

| # | Rule | Enforced by | Attacked in |
|---|---|---|---|
| 1 | Every human route declares the authority it needs, and the declaration is the check. | A dependency per route (`authority.READ`, `STEER`, `CHAIR`, `SIGNED_IN`, `INSTANCE_ADMIN`) that resolves the project from the path and refuses before the handler runs. A route that cannot name its project in the path declares `IN_HANDLER` and calls `authority.require`. | `test_auth_surface.py` walks the live app and fails on any `/admin`, `/observe` or `/auth` route without exactly one declaration. |
| 2 | Nobody reaches a project they are not a member of, and a member reaches only what their role holds. | The same dependency, reading `project_members` on every request — so removing a co-chair takes effect on their next click, with nothing to revoke. | `test_auth_roles.py` |
| 3 | A write is recorded as the person who made it, through the door they used. | The actor is bound once per request from the credential that authenticated it; `decided_by_user_id`, `accepted_by_user_id`, `sender_user_id` and the audit `actor` all read it. Never `project.chairman_user_id`. | `test_auth_roles.py`, `test_mcp_attribution.py`, and the harness itself (below) |
| 4 | A credential cannot widen itself. | A personal token cannot mint a personal token or change a password; an MCP token is refused on `/admin`; a personal token or session is refused on `/mcp`. | `test_auth_tokens.py`, `test_mcp_auth.py` |
| 5 | The CEO may cite a boardroom order only from someone who may still give it. | `policy.check_directive_citation` checks the sender is the chairman or a co-chair *now* and their account is active. | `test_auth_roles.py` |
| 6 | The box always has an admin, and a live project always has a chairman. | Demoting or deactivating the last active admin is refused; deactivating a person who chairs a live project is refused until the chair is handed on. | `test_auth_people.py` |
| 7 | A browser's ambient credential cannot be spent by another origin. | Unsafe methods carrying only a session cookie need an `Origin` (or `Referer`) naming this install; the cookie is `HttpOnly` and `SameSite=Lax`; the live socket checks `Origin` too. | `test_auth_sessions.py` |
| 8 | A password is not guessable at speed. | Ten failures for one address in fifteen minutes and that address is refused with 429 until the window clears — the same for an address that has no account, so the throttle is not an oracle either. | `test_auth_sessions.py` |

## Credentials

**Sessions.** `POST /auth/login` checks the password (argon2id, as `login.py`
already did for the consent screen) and sets `yaaf_session`: `HttpOnly`,
`SameSite=Lax`, `Secure` when the install's public origin is https, `Path=/`.
The row lives fourteen days at most and dies after three idle; `POST
/auth/logout` revokes it, and so do a password change (every other session) and
deactivation (all of them). The dashboard keeps no credential in
`localStorage` any more, so a cross-site script that runs in it cannot carry one
away, and links that the browser follows itself — the evidence snapshot, the
live socket — authenticate without a token in the URL.

**Personal tokens.** `ypt_…`, for the CLI and for scripts. Named, always
expiring (1–365 days, default 30), revocable, listed with their last use. Minted
from the dashboard (`POST /auth/tokens`) or by `yaaf login`, which exchanges an
email and password for one at `POST /auth/token` and stores it with mode 0600.
A token is the person: it reaches exactly what their roles reach, and is stamped
`via token:<name>` so a script's writes do not read as clicks.

**Setup links.** A single-use, expiring link that sets a password: how an invite
is accepted, how a forgotten password is reset, and how the first admin signs in.
Issuing one for a person revokes their earlier unused ones. An instance admin
issues them from the dashboard; whoever has a shell on the box can issue one
with `yaaf users setup-link EMAIL`, which talks to the database directly — the
same trust as `yaaf spend-report`, because a shell on the box already is root on
the box. The box sends no mail: the link is shown to whoever issued it, to hand
on, and the API returns it as a path as well as a URL so the dashboard can
complete it with the origin it is actually being read on.

**The consent screen** for MCP clients keeps asking for the password even when a
dashboard session exists. Handing a machine a credential is the moment to prove
it is still you, and its 30-minute cookie stays scoped to `/oauth`.

## Retiring the admin token

`YAAF_ADMIN_TOKEN` is no longer read. A request bearing anything that is not a
session, a personal token, an MCP token or a run token is a 401 that says so, and
the server logs one warning at boot if the variable is still set.

**Upgrading an install:**

```bash
docker compose -f deploy/docker-compose.yml exec api \
  yaaf users bootstrap --email you@example.com --name "You"
```

`bootstrap` works only while the box has no admin. It promotes the account with
that email if one exists — the chairman you have been all along, with every
project you founded — or creates it, and prints a setup link. `deploy_single_server.sh`
and `update_single_server.sh` run it for you from `YAAF_ADMIN_EMAIL` in
`deploy/.env`, and use a ten-minute personal token for the library sync they
used to do with the admin token.

A project whose chairman was a stray address typed into the old New-project form
is still chaired by that address. The instance admin can read it and transfer it.

## Attribution

One contextvar, `yaaf.server.acting`, holds the actor for the work in progress:
bound per request from the credential, per MCP call from the grant, and per
agent call that reaches into an admin helper (a CEO hiring through `add_team`
used to be recorded as `user:admin`). An audit row written with nothing bound is
stamped `system:unattributed`, and the test harness fails any test that leaves
one behind — so a new door that forgets to say who walked through it fails the
suite instead of lying in the record.

## Known edges

Said here rather than waiting to be found:

- **A person can spend the box's model credentials.** Anyone with an account
  may found a project and bill it to any LLM credential the box holds. The
  instance admin owns those credentials and decides who has an account; per-person
  grants on a credential are not built.
- **Notices go to the chairman.** The email ration is one person's, so co-chairs
  read decisions in the app's Inbox rather than in their email (`yaaf.server.notify`).
- **Slack trusts one person.** Inbound Slack maps one Slack user per box to the
  chairman; a co-chair speaks through the dashboard, the CLI or a model client.
- **The export is a read, the backup is not.** Any member may export a project's
  design; `include_values=true` carries every credential decrypted and needs a
  chair's standing, the same as the eye on a single secret.

---

## Milestones

### A1 — people authenticate, the admin token goes

- [x] `user_sessions`, `user_tokens`, `user_setup_links`, `login_failures`;
      `users.deactivated_at`; emails normalised; a chairman row in
      `project_members` for every project that lacks one (migration 0046)
- [x] `/auth`: login, logout, me, setup, password, tokens, sessions
- [x] `person_auth` replaces `admin_auth`; `YAAF_ADMIN_TOKEN` removed
- [x] Origin check on cookie-authenticated writes; login throttle
- [x] The live socket authenticates by cookie and checks `Origin`
- [x] `yaaf users bootstrap | setup-link | token | has-admin | list`;
      `yaaf login | logout | whoami`
- [x] Rehearsal, evals and deploy scripts carry a person instead of the token
- [x] Dashboard: email + password sign-in, setup page, account page

### A2 — who may decide what

- [x] An authority declaration on every human route, and the test that walks them
- [x] Observer, co-chair and chairman enforced; membership routes; transfer
- [x] Cross-project rollups (projects, desk, briefing) list only what the person may see
- [x] Writes attributed to the person who made them, not to the chair
- [x] Directive citation honours co-chairs, and only current ones
- [x] MCP reach follows membership: read for any member, steer and configure for chairs
- [x] Instance admin: users page, invites, setup links, promote, deactivate
- [x] `yaaf members` and `yaaf people` — the dashboard's people screens, scriptable
- [x] The CEO, the boardroom, the dashboard and Slack name a co-chair's words as theirs
- [x] Dashboard: members panel, transfer, and the role on screen — a notice for
      observers and the instance admin, and no lifecycle buttons for them. Other
      decide buttons stay and show the server's refusal; hiding every one of them
      is a sweep across the app that has not been done.

### Later, if wanted

- Per-person Slack identity (today one `slack.chairman_user` per install maps to
  the chairman, so a co-chair speaks in the dashboard, the CLI or a model client).
- TOTP; OIDC sign-in; email delivery of invites and resets.
- Chairman notices to co-chairs as well, once someone needs them.
