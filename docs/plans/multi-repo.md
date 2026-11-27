# Code Factory: more than one repo

Status: plan. Nothing here is built yet.

## In one paragraph

A Code Factory holds one GitHub repo today (`Project.github_repo`, one
`owner/name`). Real products are often split: a web front end in one repo and
an API in another, sometimes a third for infrastructure or a mobile app. This
plan lets one factory hold several repos, each created new or adopted, under
the same crew, budget, requirements and release dial. Every one of the ten
rules keeps working. Each one simply runs once per repo, and nothing becomes
weaker because the work is split across repos.

## Why not two factories, or a monorepo

Both still work, and the docs should say so. But:

- **Two factories** means two crews, two budgets and two plans for one
  product. A feature that needs an API field and the screen that shows it
  becomes two requirements in two projects that cannot see each other. Nobody
  owns the join, and no release can say "front end and back end, together".
- **A monorepo** is a migration the chairman may not want or be allowed to do.
  Adopting a repo is supposed to take the code as it is.

## What a project with several repos looks like

- The project has a list of **repos**. Each has a short **name** the crew and
  the screens use (`web`, `api`), a GitHub `owner/name`, its own default
  branch, and how it started (`new` or `adopted`).
- One repo is **primary**. It is the default when a verb names no repo, and its
  name goes on the project home. A one-repo project is just a list of one, and
  behaves exactly as it does today.
- Every **change** belongs to exactly one repo. A feature that touches two
  repos is two changes, **linked** under one task (see "Linked changes").
- Every **release** names one commit for each repo it ships.
- **Upkeep** promises are measured for each repo. A promise is kept only when
  it is kept in every repo.

## Where the single repo is assumed today

`git grep -n github_repo -- yaaf runner web/src` finds about 70 reads in 28
files. These are the ones that change behaviour, not just a label:

| Where | Assumption | What it becomes |
|---|---|---|
| `models.Project.github_repo` | one repo | `ProjectRepo` rows |
| `changes._repo`, `open_change` | the change's repo is the project's | `repo` argument, required when there is more than one |
| `changes.by_number` and the webhook handlers | a PR number names a change | `(repo, number)` names a change. **Numbers collide across repos: `web#12` and `api#12` both exist.** |
| `api/webhooks.github_webhook` | the payload is from the project's repo | route by `repository.full_name`; refuse a repo the project does not hold (today the repo is never checked against the project) |
| `changes` merge loop, `suites_running`, `branch_head` | one main | one main per repo |
| `upkeep.main_head`, `on_push`, `on_check_run` | one `main_head` signal | signal key carries the repo |
| `upkeep` package lag | reads `package.json` from one repo | reads each repo |
| `runner/daemon` workspace, `_seat_workspace`, `_refresh_shared_tree` | the seat's cwd **is** the clone | the seat's cwd holds one clone per repo |
| `runner/daemon` scan, scenario, deploy, main-health jobs | one `fetch_url` | the change's repo; scenarios and deploys may need all of them |
| `releases`, `provenance` | one `sha`, one tag, one subject | one sha per repo, a tag in each, one subject per repo |
| `repo_guidance.read` | one AGENTS.md / CLAUDE.md | each repo's, labelled by repo |
| `policy.intake_owed` | one intake report | an intake for every adopted repo |
| `github.attach`, `create_wanted_repo`, `protect_main` | one repo made or attached | per repo |
| `checklist` "repo" step, push-key check (`admin.py` ~7330) | one repo | every repo |
| `admin` create, clone (`twin`), export; `founder/tools`, `founder/quick` | `github_repo` in a spec | `repos` list; an old `github_repo` key reads as a list of one |
| `observe.py`, `factory_view`, `web/src` | one repo | repo shown on each change and release, numbers per repo |

## Design

### The record

```text
ProjectRepo
  id, project_id
  name           short, unique in the project: [a-z0-9-]{1,32} (it is a directory name)
  full_name      owner/name, unique in the project
  default_branch read from GitHub at attach, as integrations["default_branch"] is today
  start          new | adopted
  primary        exactly one per project
  attached_at, detached_at
```

- `Change.repo_id` (not null after backfill). The unique key for a pull
  request becomes `(repo_id, number)`.
- `Release.shas`: `{repo name: sha}`. `Release.sha` stays, for one-repo
  projects and old rows, and is the primary repo's sha.
- `FactorySignal.key` gets a repo prefix for repo facts (`main_head`,
  `main_check`, `package_lag`, `advisory`). `data.repo` carries the name.
- `Scenario.repo_id`, nullable: a scenario for one repo, or for the whole
  product (null) when it drives the front end against the API.

`Project.github_repo` is removed at the end (phase 7). Until then it is kept
equal to the primary repo by the one module that writes repos, so nothing
reads a stale value.

### One accessor, enforced

`yaaf/server/repos.py` is the only code that reads or writes repo rows:
`repos.all(session, project)`, `repos.primary(...)`, `repos.named(...)`,
`repos.for_change(...)`, `repos.by_full_name(...)`. A new check in
`test_suite_hygiene.py` refuses `.github_repo` anywhere else in `yaaf/`.
That check is what stops this from shipping wired into half the places
(CLAUDE.md, "Match its sibling's footprint").

### Linked changes

One task, one change, stays the rule **per repo**. A task may hold one live
change in each repo. Changes under the same task are linked:

- Each one passes its own gates: size, review, tests, scans and hidden
  scenarios, all on its own head.
- **None merges until all are ready.** The merge loop treats a task's live
  changes as one unit.
- They merge in repo order (the order the chairman lists the repos: API before
  web, by default), each through the API at its signed head.
- GitHub cannot merge two repos atomically. If a later merge fails after an
  earlier one landed, the platform does not force anything. It marks the task
  **half-merged**, tells the seat and the chairman, and opens a fix task on the
  repo that failed. It also blocks any release that would ship the half-merged
  state. Review reads each change on its own, so the reviewer is told that
  changes merge one at a time and the API must stay backward compatible for
  one release.
- A push to any linked change clears that change's review only, as today.

### Workspaces

One layout for every factory, one repo or many:

```text
<workspaces_root>/<slug>/<repo name>/                     shared tree, one clone per repo
<workspaces_root>/.seats/<slug>/<agent>/<repo name>/      a seat's checkouts
```

The seat runs with its cwd at `.seats/<slug>/<agent>/`, so it sees every repo
side by side and can run the front end against the API it just changed. On the
first run after upgrade, existing checkouts are moved under the primary repo's
name (a rename, not a re-clone). A seat that holds uncommitted work is left
where it is and keeps working there until it is clean, the same rule
`_refresh_shared_tree` already applies.

Platform jobs:
- **scan, main health:** the one repo concerned.
- **hidden scenarios and previews:** every repo is fetched. Each linked change
  is at its head and every other repo is at main. A scenario tied to one repo
  runs in that repo's directory.
- **deploy:** see Releases.

### Releases

The decision we recommend (see Decisions, 2): **one release per project**,
because the chairman approves the product, not a repo.

- A release lists a commit for each repo that changed since the last live
  release. Repos that did not change ship nothing.
- One version, tagged `vX.Y.Z` in each repo it ships.
- An environment gives a deploy command per repo
  (`environments.production.deploy: {api: "...", web: "..."}`). A single
  string still means "the primary repo" for old configs. The deploys run in
  repo order. One health URL, or one per repo, is watched by the canary guard.
- Rollback returns every repo in the release to the previous live release's
  commit, in reverse order.
- Provenance has one subject per repo. The SBOM is one per repo, all kept with
  the release.

### Intake and guidance

- Every adopted repo is read before it is changed. `intake_owed` holds the
  plan until the intake report has a section for each adopted repo.
- A repo attached to a running factory starts in intake. No change may be
  opened against it until its section exists, while work in the other repos
  carries on.
- `repo_guidance` reads AGENTS.md / CLAUDE.md from each repo's main and labels
  each part by repo. The total stays under the same limit, split fairly
  between repos.

### Adding and removing repos

- Settings → Code Factory → Repositories: add (create or adopt), rename the
  short name, reorder, mark primary, detach.
- Attaching protects main, creates the webhook, checks that the agents' push
  key can push there, and adds the repo to every pushing role's grant.
- Detaching is refused while that repo has a live change or an in-flight
  release. History stays: the changes keep their `repo_id`, and the row gets
  `detached_at`.
- At most 8 repos per project, amendable. Every repo multiplies polling, CI
  and seat disk.

### What the crew sees

- `change_open` takes `repo`. It is refused when the project has more than one
  repo and none is named, and refused for a name the project does not hold.
  `change_status` and every change shown to a seat say the repo: `api#12`.
- The context pack lists the repos, their names and their roles in one line
  each.

## Build order

Every phase ships on its own and leaves the suite green.

**0. One accessor (S). No behaviour change.**
- `yaaf/server/repos.py`, backed for now by `Project.github_repo`.
- Move every read (about 70) to it. Add the hygiene check.

**1. The record (M). Postgres run.**
- `ProjectRepo`, `Change.repo_id`, `Release.shas`, `Scenario.repo_id`.
- Migration 0087 backfills one repo per factory from `github_repo` and
  `integrations.default_branch`/`factory_start`, and sets `repo_id` on every
  change.
- `repos.py` switches to the table.

**2. Webhooks and polling per repo (M).**
- Route by `repository.full_name`, refuse an unknown repo, and look changes
  up by `(repo, number)`.
- Poll each repo's open changes and main. Per-repo `main_head` signals.
- Attacks: a webhook from a repo the project does not hold, PR #12 in two
  repos, a push to main in the second repo.

**3. Attach, detach and the push key (M).**
- API, Settings screen, CLI (`yaaf repos P`, `yaaf attach-repo P NAME owner/name`,
  `yaaf detach-repo`), checklist step per repo.
- Attacks: detach with a live change, a push key that cannot push to one of
  the repos, two repos with the same name.

**4. Workspaces and platform jobs (M).**
- The layout above, the one-time move, and the shared tree per repo.
- Scenario and preview jobs that fetch every repo.

**5. Changes across repos (L).**
- `change_open(repo=)`, one live change per repo per task, the linked merge
  unit, ordered merge, half-merged handling.
- Attacks: one linked change green and the other red (neither merges); the
  second merge fails (task is half-merged, release blocked, fix task opened);
  a change naming no repo in a two-repo project.

**6. Releases, upkeep and intake per repo (L). Postgres run.**
- Multi-repo releases, per-repo deploys, ordered rollback, provenance.
- Upkeep promises per repo, judged across all of them.
- Intake section per adopted repo, intake for a repo attached later.

**7. The form, the spec, and the rest (M).**
- New Project: a list of repos, each "create" or "adopt", with a name.
- Founder spec `repos: [{name, full_name?, start}]`. `github_repo` still reads
  as a list of one, and `DESIGN_DOCTRINE.md` is updated.
- Export and clone carry the list (`test_project_export`, `test_project_clone`).
- Rehearsal: the fake GitHub holds two repos, and a drill opens a linked pair.
- Factory home, Changes and Releases tabs show the repo. Delivery numbers per
  repo and in total.
- MCP reader, observe payloads, brief and agenda: `repo` on every change and
  release.
- Docs: `docs/CLI.md`, the Code Factory plan's "one repo" lines, README.
- Drop `Project.github_repo` (migration 0088).

## Sibling footprint (CLAUDE.md rule)

The sibling is `github_repo` itself. Before phase 7 closes:

```bash
git grep -l github_repo -- yaaf web/src docs | sort > /tmp/sibling
git grep -l -e ProjectRepo -e 'repos\.' -- yaaf web/src docs | sort > /tmp/new
comm -23 /tmp/sibling /tmp/new
```

The result must be empty, apart from migrations, or each leftover gets a
sentence here saying why it stays.

## Decisions for the chairman

1. **Linked changes merge in order, not atomically.** Recommended. The
   alternative is a merge queue that holds one repo's merge until the others
   can merge too. That is still not atomic, and it costs far more.
2. **One release per project, across repos.** Recommended. The alternative is
   releases per repo, each with its own version and approval. That fits teams
   that ship the front end ten times a day and the API once a week. It could
   come later as a per-repo "ships alone" switch.
3. **Cap of 8 repos.** Amendable in the constitution.
4. **One crew for all repos.** Seats are not tied to a repo. A role's grant
   covers every repo. Per-repo roles (a front-end engineer who cannot push to
   the API) are left for later.

## Verification

- Each phase: tier 1 for the areas it touches (`test_code_factory.py`,
  `test_merge_gate.py`, `test_releases.py`, `test_upkeep.py`, `test_github.py`,
  `test_repo_guidance.py`, plus a new `test_multi_repo.py` holding the attacks
  above). CI runs the full suite and Postgres.
- Every existing factory test keeps passing unchanged against a one-repo
  project. None of their assertions are widened. If one needs to change, the
  reason goes in the commit message.
- A rehearsal on mock models and the fake GitHub walks a two-repo build: a
  linked pair merging in order, a forced second-merge failure, a two-repo
  release, and a rollback, for $0.
