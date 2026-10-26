# The shared store — one credential, many projects

**Built.** Secrets, plain variables and data sources that live *above* every
project, stored once and handed to a project's teams on a permission basis.

An install that runs ten projects for one person tends to hold the same
`EMAIL_USERNAME` and `EMAIL_PASSWORD` ten times. Each copy is rotated
separately, or more likely is not, and the first anyone hears of a stale one
is an agent's 401 in the fourth project. The shared store keeps the value in
one place. A project does not copy it; it is given access, and its chairman
chooses which of their teams receive it.

## The two keys

Two people turn a key before a shared value reaches a sandbox. Both are
enforced on the server, not settings in the UI.

| Key | Held by | What it decides |
|---|---|---|
| **Availability** | the instance admin | which projects may use the item at all: *every project* or *only the projects listed* |
| **Grant** | that project's chairman or a co-chair | which of the project's teams (roles) receive it on their next run |

The split is the point. The admin owns the value, so the admin decides which
projects it may reach. A co-chair of one project must not be able to put the
admin's mail password into a sandbox the admin never approved, because an
agent can print anything it holds. The chairman knows which team needs it.
Making the admin pick teams in a project they do not run would be guesswork,
and it would leave a grant nobody on the project can change.

- **Available to every project** (`shared_with: "all"`): any project's
  chairman or co-chair may grant it to their teams. The grant creates the
  project's access row.
- **Available to listed projects** (`shared_with: "listed"`): the admin adds a
  project, which creates an access row granting nobody yet, and only then may
  that project's chairs grant it. A project the admin did not list gets a 404,
  the same answer a missing item gets.
- **Narrowing from "all" to "listed"** keeps the access rows that exist, so a
  project already using the item keeps it. Taking it away is a revoke, a
  separate act that is audited on the project's own record.

## What is shared

| Item | Stored as | What a granted team receives |
|---|---|---|
| **Secret** | a platform-scope `Secret` row, `shared_with` set, masked | an env var named like any project credential (`EMAIL_PASSWORD`) |
| **Plain variable** | the same, with `visibility: plain` | an env var. It is shown as-is to the admin, who could reveal it anyway |
| **File** | the same, with `filename` | the env var holds a path, exactly as a project file credential does |
| **Data source** | a `shared_data_sources` row. Its address and token are encrypted on the row | the source mounted as `mcp__<name>__*` (or reachable through `data_fetch` when it is `bulk`), the same as a project source. Read-only and write sources alike: a tool allowlist (`tools`) makes unlisted tools need the project chairman's approval per call, as on a project source |

Grants live in `shared_access`, one row per (project, item), holding the
project's role slugs. **A shared row's own `role_scopes` is always empty**, so
no path that reads `role_scopes` can inject a shared value box-wide. The one
platform-scope branch that used to read them (`_role_secret_env`) now reads
`shared_access` instead.

## One OAuth connection for every project

A shared data source can sign in with OAuth (Notion, Linear, Atlassian and the
like), and that one connection serves every project granted the source. The
admin presses **Connect** once in Settings → Shared store. The access token and
refresh token are stored once, encrypted on the store's row. No project holds a
copy.

One copy is what makes this safe. Providers rotate refresh tokens on use, so
two copies of one connection would invalidate each other on the first refresh.
Here the token is renewed in one place (`yaaf/server/shared_oauth.py`), under
a lock on the store's row. When two projects' runs start at the same moment,
the second finds the token the first one renewed instead of spending a refresh
token that was just rotated.

An edit that keeps the source's address keeps its sign-in. A moved address
needs a new one. A connection that expires shows on every granted project's
health page, with the admin named as the person who can fix it.

## One name, one value: the newer one replaces the older

A shared value and a project's own value under the same variable name would
leave one of them silently unread, and an agent told about both would guess
which it holds. So a name never has two values on a project, and the most
recent decision wins:

- **Granting a shared item replaces the project's own** credential under the
  same env var name, or its own data source of the same name (address, token
  and OAuth tokens together). The grant's answer, the audit row and the
  project's history say what was replaced.
- **Storing a project value under a name the project already receives** from
  the store takes the shared item back from this project's teams. Access is
  kept, so the chairs can switch it back on, which replaces their own again.
- **Saving a shared variable under another spelling** of a shared name
  (`email-password` for `EMAIL_PASSWORD`) updates that item rather than
  making a second one.

Two cases stay refused, because the clash is not a duplicate:

- a project value something else runs on, such as the Slack bridge's token, is
  never replaced by a shared one. Rename one of them;
- the CEO's `credential_scope` never removes a value the chairman stored. It
  can give a shared item to its teams, and where that would replace the
  project's own, the chairman decides.

`MCP_*` (the data sources' own variables), `YAAF_*`, `platform-*` and `github`
are still refused as shared names, since they belong to the platform.

## Who sees what

| Who | Sees |
|---|---|
| Instance admin | everything: values (by an audited reveal), every project's access and grants |
| A project's chairman and co-chairs | the items available to *their* project: names, kinds, when each was updated, and their own grants. Never a value, a hint, or another project's use of it |
| Observer | the same names and grants. Read-only |
| The CEO agent | `credentials_list` includes shared credentials the project uses, marked `shared`, with names and scopes only. Under `capability_authority: ceo` it may re-scope one across its own teams, the same as a project credential |
| Any other agent | the variable names announced in its prompt, as today |
| A model client (MCP) | the project's shared items, names and grants only. Values, reveals and the store itself are withheld (see `yaaf/server/mcp/surface.py`) |

## Deleting

A shared item cannot be deleted while any project still has access to it. The
refusal names those projects. This mirrors the rule for managed LLM
credentials: the admin revokes each project first, which each project's record
then shows, so a value never disappears from under a running project in one
click.

## Surfaces

| Surface | Where |
|---|---|
| Admin store | **Settings → Shared store**. `GET/POST /admin/shared/...` |
| Project grants | **Project → Settings → Secrets & variables** and **Data sources**: a *Shared store* section, and rows in the access map. `GET /admin/projects/{id}/shared`, `PUT /admin/projects/{id}/shared/{kind}/{item_id}` |
| Lease | `yaaf/runner/daemon.py` (`_role_secret_env`, `_merge_project_data_sources`) through `yaaf/core/shared_store.py` |
| Approved writes | `data_fetch` and approved tool calls resolve a shared source the project is granted |
| Health | the project health page checks shared data sources it grants, as it checks its own |
| Setup checklist | shared variables and sources the project uses count toward *Credentials* and *Data sources* |
| Export / clone | export lists the project's grants by name, with no values. Clone carries grants on items available to every project. Grants on *listed* items stay behind, because the admin listed the source project and not its twin, and the clone's response says which were left |
| CLI | `yaaf shared`, `yaaf set-shared`, `yaaf share`, `yaaf grant-shared` |
| MCP | `shared` (list, grant) on the configure scope. The store's own doors are withheld (`yaaf/server/mcp/surface.py`) |

## Deliberately not built

- **A shared default with per-project overrides held side by side.** A
  project's own value replaces the shared one instead (see "One name, one
  value"), so there is never a second value to choose between.
- **Importing a grant from a project file.** A setup file names a shared item
  it cannot prove the importer may use. Grants are made on the project after
  it exists.
