# Code Factory: a fourth kind of project

Mockups: https://claude.ai/artifact/NhyCppdUFBnuZ4E6KTsZpR (21 screens, two worked examples).

## In one paragraph

Code Factory takes software through its whole life in one project: spec, design,
build, test, review, release, run, and years of upkeep. It starts from an empty
repo or from one you already have. Agents write the code. The server decides
what counts: it runs the tests, it alone merges, and it rolls back bad deploys.
Deliver ships once and stops. Operate repeats a play. Code Factory ships
releases to dates and then keeps the code healthy on a weekly rhythm, without
rebuilding the project in between.

## Why

Coding agents write good code quickly, but nobody can walk away from them. They
mark their own work done, weaken tests until they pass, pull in packages nobody
checked, and open changes too big to read. Industry data for 2025 shows the
result: about twice as many merged PRs, review time up 91%, PR size up 154%,
and delivery flat. yaaf already holds the fix for this, which is rules enforced
in the server. Code Factory applies those rules to a codebase.

## The ten rules

Each rule is a refusal or an automatic state change, enforced server-side, and
each ships with an attack test in `tests/selftest_invariants/` (house rules 1–2).

| # | Rule | How it is enforced |
|---|---|---|
| 1 | Only the platform merges. | Main is protected when the repo is provisioned. Agents' tokens can push branches only. The platform performs the merge through the API after every gate passes. |
| 2 | Green means the server ran it. | Tests, scans and builds run as platform `command` checks on the change's head commit. What an agent reports is ignored. |
| 3 | Nobody approves their own change. | A challenge seat must sign. Extends `check_task_review` (policy.py) to changes. |
| 4 | Hidden tests. | The test engineer's scenarios are stored outside the repo and never mounted in builders' sandboxes. The platform runs them and reports a count, never the text. |
| 5 | Tests can't get weaker quietly. | A diff that deletes or loosens assertions, or skips tests, is held until a challenge seat gives a signed reason. |
| 6 | Small changes only. | More than 400 changed lines (amendable) is refused. |
| 7 | New packages are checked. | Below the age or download floor, or outside the licence list: held for the security seat. A known advisory: refused. |
| 8 | You choose who releases. | Per project: **approve first** (you approve the first production release and every major; the release seat ships minor and patch after) or **fully autonomous** (the release seat ships everything; you are told after). Gates, canary and rollback apply in both. Protected paths still need a person. |
| 9 | Bad deploys undo themselves. | The canary guard reads health metrics. A breach rolls back and opens an incident, with nobody asked first. |
| 10 | Upkeep is a promise. | Standing requirements (as in Operate) are judged every cycle. Two misses in a row pause new feature work. |

Also fixed: text from people without write access (issues, comments) reaches
agents as quoted data, never as instructions. Only the release seat holds the
production deploy key.

## Two ways in

- **Build new.** yaaf creates the repo, protects main, and stage 1 is always the
  harness: CI, preview builds, hidden scenarios, scanners, a health check.
  Features come after. Releases are promised by date, as in Deliver.
- **Adopt a repo.** Day 0 is a read-only intake: tests, coverage, flaky tests,
  CI, packages, advisories, licences, leaked secrets, end-of-life runtimes, and
  90-day delivery speed. The tech lead proposes a charter (standing promises
  plus first work). Nothing is written to the repo until you accept it.

When the last build requirement is met, the project rolls into weekly upkeep on
its own. It is the same project, with the same history.

## What already exists (reused, not rebuilt)

- One GitHub repo per project, cloned into a persistent sandbox workspace: `yaaf/server/github.py` and `runner/daemon.py`.
- Five coding harnesses, with role-scoped `github` secrets.
- Platform-run `command` checks judged by exit code: `runner/sandbox.py` (`run_check_command`).
- Requirements with stages, a dependency graph and the R-00 plan gate; Deliver-style commitments; Operate-style cycles and standing requirements.
- Reviewer (challenge) seats, the independent-read rule, and the rule that a test's author can't be judged by it.
- Budgets that stop, approvals, side-effect records, and the signed generic webhook (for incident alerts).

## What is missing today

- Pull requests, CI status and merges are not records yaaf owns. The GitHub webhook ignores push, check, review and merge events.
- There is no merge gate. **An agent holding the role's GitHub token can push to main today.**
- Releases, deploys, rollbacks and incidents don't exist.
- The sandbox image has no scanners.
- There are no test-engineer, code-reviewer, security, architect, release or tech-lead roles.

## Build order

Every phase ships on its own, with its attack tests, and leaves the suite green.

**1. The mode (M).**
- Add `ProjectMode.CODE_FACTORY = "code_factory"`. It fits `String(16)`, so no migration is needed.
- Open it in the three validators: the `admin.py` pattern, the founder `PROPOSE_SCHEMA`, and MCP create.
- The code never switches over modes. It asks three yes/no questions, so each must be answered for the new mode:
  - *has scope?* Yes. This comes free from `requires_requirements`.
  - *can commit to a date?* Yes, per release.
  - *runs on cycles?* Yes, after v1.0.
- Grep every `== DELIVER`, `== OPERATE` and `in ("deliver","operate")` site and say yes or no for Code Factory at each. Known sites:
  - janitor cycle roll and commitment voiding
  - `staffing.py` last-challenge-seat protection
  - founder stakes, tempo and roster checks
  - rehearsal `scope_project`
  - `review_record.headline` and `review_record.board`
  - `observe.py` health rows
  - `tempo_for`
  - `complete`
- Add a `test_modes.py` section and extend the `test_tempo.py` parametrize.

**2. Changes and the merge gate (L).**
- Add a `Change` model with these fields: project, task, branch, number, head sha, lines, gate results, review verdict, status and merged sha.
- Extend `webhooks.py` to handle `pull_request`, `pull_request_review`, `check_run`, `workflow_run`, `push` and `issues`.
- Branch protection at provision and attach. The platform performs the merge.
- Agent verbs: `change_open` and `change_status`. Reuse `side_effect_begin` for idempotency.
- Attacks: push to main, merge without signature, self-sign, forged webhook, change serving no requirement.

**3. Quality gates (L).**
- Hidden scenarios: a new store, visible only to challenge seats and the platform.
- Test-weakening detector (diff analysis), size cap, changed-line coverage.
- Scanners in a `yaaf-sandbox:code` image: Semgrep, gitleaks, OSV-Scanner, Syft.
- Package floor and licence allowlist.
- New constitution fields in a "Code Factory" group (`constitution.py`), so the settings UI generates itself.

**4. Releases (L).**
- Add a `Release` model with: version, commit, changelog, SBOM artifact, provenance, stage and rollback target.
- Environments are a deploy command plus a health URL, with the key scoped to the release seat.
- Canary guard on metric requirements (`metrics.py`), automatic rollback, and an `Incident` record opened by the guard or the generic webhook.
- Approval kind `release`, never granted by silence. A new constitution field `release_authority` (`approve_first` | `autonomous`), set on step 3 and amendable.

**5. Upkeep (M).**
- Reuse Operate's cycle roll, standing checks and review-at-the-roll.
- Add the standing promise checkers: advisories, main green, issue answer time, flaky tests and package lag.
- Issue triage verbs. Batched dependency changes.

**6. Adopt a repo (M).**
- The read-only intake run and its report artifact.
- The proposed charter as requirements plus standing promises, accepted like a plan.
- Protected paths are read from CODEOWNERS.

**7. Crew and docs (S).**
- Six roles: tech-lead, architect, test-engineer, code-reviewer, security-engineer, release-engineer.
- Three crews: build a product, keep a repo healthy, solo engineer.
- The kind picks the lead and the crew. A factory is always led by tech-lead (the server fills it and refuses any other, and refuses tech-lead on any other kind). The form fills in build a product for a new build or keep a repo healthy for an adopted repo, and offers only the factory crews.
- Two `examples/` bundles.
- Founder doctrine for the shape call.
- README "Four kinds of project", CLI, MCP read surface, golden evals re-recorded and explained.

**First run, checked end to end.** What a default-settings factory needs before its first merge, each fixed where it used to fail silently:
- Evidence: a merged change and a live release are platform-attested evidence; a factory's outward-work floor defaults to 0 and its drought to 72h, and a drought warns but never refuses a factory's tasks (refusing them deadlocked it).
- The form asks for the repo and creates it (or checks an empty one) right after Create, with main protected. A repo attached after the first run is still cloned.
- GitHub is read every minute for each open change's head, its checks and main, so a box at localhost works with no webhook; with a public https address the hook is created on attach.
- Git in the sandbox: safe.directory, an identity, and a credential helper reading $GITHUB. The runner's clone is shared-writable.
- Settings → Integrations → GitHub holds the platform key, the repo and the agents' push key (checked for push access, granted to every pushing role). The checklist lists repo, push key and deploy target.
- A green change wakes one challenge seat to read it (the code reviewer if there is one); accepted scope with no hidden scenario wakes one (the test engineer). A change waiting only on the chairman tells him, once per commit.
- Each seat works in a checkout of its own (`<workspaces_root>/.seats/<slug>/<agent>`), a local clone of the project's tree started from the remote's latest default branch and kept between that seat's runs. Platform staging (`.yaaf/`) is excluded from git.

**UI**, alongside phases 1–6:
- New-project kind card and the "start from" choice.
- A Code section in the project nav: Pipeline, Changes, Releases, Health, Issues.
- The line (lifecycle rail) and factory vitals on the home page.
- The intake report, plus Settings → Code Factory (GitHub and Environments).
- `npm run build` must pass.

## Sibling footprint (CLAUDE.md rule)

Before calling each phase done, diff its footprint against its sibling:
- `Change` against `Task`
- `Release` against `Commitment`
- `Incident` against `Flag`
- Code Factory against Operate

Each concept must reach:
- daily brief and agenda
- change stamp
- context pack
- MCP reader and CLI
- rehearsal
- receipt and review record
- docs

Every surface either gets wired, or gets a sentence explaining why not.

## Measures shown to you

All are computed by the platform, never by the crew:
- Merged changes, and time from branch to merge.
- Share sent back by review, and signed-then-failed (reviewer escapes).
- Cost per merged change.
- Hidden-scenario pass rate.
- The four DORA measures against the day-0 baseline: deploys a week, commit to production, share needing a fix, and time to recover.

## Decisions

1. **GitHub only at launch.** Decided. GitLab and Bitbucket later.
2. **Deploys are a command plus a health URL.** Decided. No per-cloud adapters in v1; teams with a pipeline keep it.
3. **Release button.** Decided: a choice per project, "approve first" or "fully autonomous".
4. **Upkeep rhythm.** Decided: weekly by default. The crew works every day; the scorecard and review close every Monday. Daily and monthly are offered too.

## Verification

- Each phase passes tier 1 for its area, then `pytest -q -n auto` once, and Postgres (tier 3) for phases 2 and 4, because they add JSON columns and timestamps.
- A rehearsal on mock models and a fake GitHub walks both examples end to end for $0. It shows: a size refusal, a review return, a hidden-scenario refusal, a merge, a release, a forced canary breach with automatic rollback, an incident, and a cycle roll.
- One paid run builds a small real app (the Slotwise pilot spec, cut down) to prove the loop with real harnesses.

## Status (built on this branch)

Everything in this plan is built. Attack tests: `test_code_factory.py`,
`test_merge_gate.py`, `test_quality_gates.py`, `test_releases.py`,
`test_upkeep.py`, `test_repo_guidance.py`, `test_factory_view.py`.

| Piece | Where |
|---|---|
| The mode, build → upkeep | `yaaf/core/shape.py`, `FactoryPhase`, hand-over in `admin.complete_delivery` |
| Merge gate | `yaaf/server/changes.py`, GitHub webhook, `github.protect_main` |
| Diff gates: secrets, weakened tests, new packages, protected paths, CI config | `yaaf/server/diffscan.py`, `changes.gates_for` |
| Platform scan (osv-scanner, gitleaks), coverage floor, per-change preview | `change_scan` and `deploy` runs, `changes.scan_gate` / `coverage_gate` / `preview_gate` |
| Hidden scenarios | `Scenario`, `scenario_check` runs |
| Releases: staging first, approval per `release_authority`, canary, rollback, incidents | `yaaf/server/releases.py` |
| SBOM and signed provenance | syft in the deploy job; `yaaf/server/provenance.py`; public key at `/observe/provenance_key` |
| Upkeep promises, measured by the platform | `yaaf/server/upkeep.py`, `FactorySignal`, judged at the roll |
| Adopted repos read first | `policy.intake_owed` (plan holds only after `intake-report.md`) |
| The repo's AGENTS.md / CLAUDE.md, from main | `yaaf/server/repo_guidance.py` |
| Code-health ratchet (lizard + ruff on touched files, before vs after) and the "main no harder to maintain" promise | `yaaf/runner/health.py`, `changes.health_gate`, `upkeep.queue_main_health` |
| Forward pressure: a rollback opens the fix; a gate failing on 3 changes in 14 days opens work on the cause | `yaaf/server/pressure.py` |
| Preview screenshots for the reviewer | `daemon._screenshot`, `/observe/changes/{id}/preview.png` |
| Crew | six roles, three crews, examples 13 and 14 |
| Screens | New Project card, home-page line and numbers, Changes and Releases tabs, Settings → Code Factory |

Known limits, said plainly:
- The sandbox image's scanner layer is written and its download URLs checked
  for x86-64, but the image was not built here. arm64 names follow the same
  release pattern and were not checked. A runner without the scanners fails
  the scan gate closed; turn `platform_scans` off to ship without them.
- Upkeep reads Dependabot alerts for advisories: enable them on the repo.
- GitHub only. Deploys are a command plus a health URL; no cloud adapters.
