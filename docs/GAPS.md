# Known gaps

Found while rewriting the docs on 2026-11-07. Each item says where the gap
is and a suggested fix. Delete an item once it is handled.

## 1. Setup and security

**1.1 Docker ignores most of `deploy/.env`.**
`deploy/docker-compose.yml` passes only a fixed list of variables into the
containers: the master key, public URL, SMTP, webhook and the four
second-stack variables. Anything else in `deploy/.env` (for example
`YAAF_MCP_REDIRECT_HOSTS`, the `YAAF_ARTIFACT_S3_*` group, `YAAF_WORKER_NAME`,
the poll timings) never reaches the app, and nothing warns you.
*Fix:* add `env_file: .env` to the `api` and `runner` services, or have the
deploy script warn about any `YAAF_` variable in `deploy/.env` that compose
does not pass through.

**1.2 MinIO is open to the network with a default password.**
The `s3` profile publishes ports `9000` and `9001` on every interface, and
`MINIO_ROOT_PASSWORD` defaults to `yaaf-minio-dev`. Docker bypasses `ufw`, so
on a server this is reachable from the internet.
*Fix:* bind to `127.0.0.1`, and have the deploy script generate the password
into `deploy/.env` the way it does the master key.

**1.3 Running the server by hand listens on every network.**
`YAAF_API_HOST` defaults to `0.0.0.0`. `yaaf up` and the Docker setup both
override it to stay local, but `yaaf-server` started directly does not.
*Fix:* default to `127.0.0.1`.

**1.4 The master key cannot be rotated.**
Changing `YAAF_MASTER_KEY` makes every stored secret unreadable, and there is
no command to re-encrypt them under a new key.
*Fix:* a `yaaf rotate-master-key` that decrypts with the old key and
re-encrypts with the new one, in one transaction.

**1.5 `/healthz` doesn't check the database.**
It returns 200 on a box whose schema never applied.
*Fix:* add a readiness endpoint that runs one query.

**1.6 Backups stay on the same disk.**
`update_single_server.sh` dumps to `backups/` beside the database volume.
Nothing copies backups off the server, and nothing backs up artifacts or the
master key.
*Fix:* a `yaaf backup` that bundles the database dump, artifacts and
`deploy/.env` into one archive, with an optional upload target.

**1.7 Every install builds from source.**
No published images or releases, so a first Docker install builds both images
(about 10 minutes) and needs the full checkout.
*Fix:* publish versioned images to a registry, and have the compose file use
them by default.

**1.8 Only Linux is covered.**
Native mode needs bubblewrap, which only exists on Linux. The Docker scripts
assume Linux paths (`/var/lib/yaaf`) and `sudo`. Nobody has checked macOS or
Windows with Docker Desktop.
*Fix:* test the Docker path on Docker Desktop and document the result, or
state "Linux only" as policy.

**1.9 Not verified end to end in this rewrite.**
The commands in INSTALL.md and DEPLOYMENT.md were checked against the scripts
and code, but `deploy_single_server.sh` and `install_desktop.sh` were not run
on a fresh machine for this change.
*Fix:* run both on a clean VM before the next release.

## 2. Docs that no longer exist

These docs were deleted on purpose. What remains is the README and four
docs: INSTALL, CONFIGURATION, DEPLOYMENT and ARCHITECTURE. These topics now
have no documentation:

| Topic | Was in |
|---|---|
| Creating a first project | `docs/CREATING_A_PROJECT.md` |
| The example projects (what each one is for) | `examples/*.md`, `examples/README.md` |
| Every rule and its default | `docs/GOVERNANCE.md` |
| The autonomy settings | `docs/AUTONOMY.md` |
| Accounts, roles and permissions | `docs/AUTH.md` |
| The MCP server: tools, scopes, safety rules | `docs/MCP_SERVER.md` |
| The `yaaf` command reference | `docs/CLI.md` |
| The agent API, and writing a role | `docs/AGENT_API.md` |
| Model providers, prices, web search | `docs/PROVIDERS.md` |
| Harnesses (Claude Code, Codex, Gemini, OpenCode) | `docs/RUNTIMES.md` |
| Slack, GitHub, data sources, webhooks | `docs/INTEGRATIONS.md` |
| The dashboard screens | `docs/DASHBOARD.md` |
| The feature list | `docs/FEATURES.md` |
| Roadmap and project thesis | `docs/PLAN.md` |
| Worked project write-ups and the UX study | `docs/projects/`, `docs/ux-redesign/` |

All of it is still in git history: `git show 6b8a711:docs/GOVERNANCE.md`.

## 3. References to deleted docs

**3.1 Messages users can see.** These strings name `docs/AUTH.md`:
- `yaaf/server/identity.py:375`: the "admin token was retired" error.
- `yaaf/server/app.py:144`: the startup warning about a leftover admin token.

*Fix:* drop the doc reference from both messages.

**3.2 Repository files still link to deleted docs.**
- `CONTRIBUTING.md:126-129`: links to PLAN, FEATURES and AGENT_API.
- `SECURITY.md:49, 59, 94`: names `docs/PROVIDERS.md` and `docs/PLAN.md`.
- `deploy/.env.example:10`: names `docs/AUTH.md`.

**3.3 Code comments and docstrings.** About 125 files under `yaaf/`, `web/src/`
and `deploy/`, and 44 test files, cite a deleted doc (for example
"see docs/RUNTIMES.md, M4"). Harmless at runtime, but misleading.
To list them:

```bash
git grep -lE '(AGENT_API|AUTH|AUTONOMY|CLI|CREATING_A_PROJECT|DASHBOARD|FEATURES|GOVERNANCE|INTEGRATIONS|MCP_SERVER|PLAN|PROVIDERS|RUNTIMES)\.md|ux-redesign|docs/projects'
```

**3.4 A safety test was removed with its doc.**
`test_every_rule_is_written_down_where_a_chairman_reads_about_them` checked
that `docs/GOVERNANCE.md` explained every project rule. It went with the doc.
New rules can now ship without any written explanation.
*Fix:* if rules get documented again, restore the test from git history and
point it at the new doc.

## 4. The website (`yaaf-website`)

The site still shows its last-synced copy of every deleted doc. The next
`npm run sync` will:

- **Remove** those doc pages. Site pages link into them about 40 times
  (`/docs/dashboard/`, `/docs/features/`, `/docs/plan/` and others), from
  `index.astro`, `features.astro`, `how-it-works.astro`, `manifesto.astro`,
  `runtimes.astro`, `changelog.astro`, `_compare.astro` and the footer
  (`Foot.astro`).
- **Empty `/examples/`.** Example pages are built from `examples/*.md`,
  which no longer exist. The `.json` specs remain.
- **Break the docs nav groups** in `src/site.config.mjs` and the blurbs in
  `src/pages/docs/index.astro`.
- **Stop syncing goal grammar to the site:** `sync-content.mjs` publishes
  `yaaf/founder/GOAL_GRAMMAR.md` only because the deleted
  `docs/GOAL_GRAMMAR.md` pointer file existed.

*Fix:* decide what the site's docs section should contain, update its links
and nav, then sync. The sync will also bring in unrelated changes to roles
and other content, which have built up since the last sync. Review those
separately.
