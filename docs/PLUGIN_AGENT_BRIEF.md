# Brief: build a yaaf factory plugin

**For the agent.** You are building a *factory plugin* for yaaf. A factory
plugin is a new kind of project, and it lives in its own Git repo. This page
is your whole brief. Follow the procedure in order. The work is done when the
[definition of done](#definition-of-done) holds, and not before.

**For the human sending it.** Send this page along with a short description of
the domain, using the [request template](#request-template) at the end. For
the background, read [PLUGINS.md](PLUGINS.md).

---

## What you are building

A plugin is a set of **pure functions** (hooks) that yaaf calls in a sandbox
with no network, no secrets, no processes and no writable disk except a
scratch directory. A hook is handed a read-only snapshot of a project and
returns a verdict: accept or refuse an artifact, list what is still owed,
render pages, fill a view. **yaaf performs every side effect.** Your code
never stores, sends, fetches or runs anything.

A plugin can only make yaaf **stricter**. It cannot loosen any rule of yaaf's.

## Ground truth

The contract is machine-readable. Read it before you write code, and again
whenever you are unsure:

```bash
python -m yaaf_plugin_api spec          # every hook, schema, limit, fixture format and requirement, as JSON
```

The JSON Schemas are in `yaaf_plugin_api/schema/manifest.schema.json` and
`view.schema.json`. When this page and `spec` disagree, `spec` is right.

Getting the API:

- **Inside a yaaf checkout:** it is already importable, and `yaaf plugin ...`
  runs the same commands as `python -m yaaf_plugin_api ...`.
- **Anywhere else** (Python 3.11 or newer):

  ```bash
  pip install "git+https://github.com/dominicplouffe/yaaf#subdirectory=packages/yaaf_plugin_api" pytest
  ```

## Hard rules

Each of these rules is enforced by a check that fails the install. None of
them is a matter of style.

1. **Hooks are pure.** No network, sockets, subprocesses, `ctypes`,
   environment writes, or file access outside the plugin's own files and the
   scratch directory. Import nothing that does these things at import time.
   Some libraries do (numpy calls `putenv`, for example). Prefer the
   standard library. (MIN-04, MIN-12)
2. **Hooks are deterministic.** The same snapshot must give the same answer.
   Use `ctx.today`, never the clock. Sort anything whose order would
   otherwise vary. (MIN-11)
3. **Never raise.** Bad input gets a `Refuse`, not an exception. The suite
   sends every hook a fuzz corpus of garbage. Catch `ValueError`,
   `UnicodeDecodeError` and `RecursionError` around every parse. (MIN-09)
4. **Every refusal says what to fix.** Each reason is non-empty, at most 500
   characters, written for the agent that will retry, and never a traceback.
   Give every reason at once, not just the first. (MIN-17)
5. **The manifest and the code match exactly.** Each declared capability
   needs its hook, with the signature in `spec`. A hook with no declaration
   is an error too. (MIN-05)
6. **Every gate has an accept fixture, a refuse fixture and an attack test.**
   An attack test is a test in `tests/` that submits something the gate must
   refuse and asserts that it is refused, with the expected words. (MIN-13,
   MIN-16)
7. **State only through `Accept(state_patch=...)`.** That is a JSON merge
   patch on `kind_state`. If you ever write state, declare `state = true` and
   implement `migrate_state`. (MIN-15)
8. **Pin every dependency.** If you need one, list it in `requirements.lock`
   with `==` and `--hash=sha256:...`, including its transitive dependencies.
   With no dependencies, ship no lock file. (MIN-03)
9. **Do not reimplement what yaaf provides.** Never check in your own code
   that a review names the latest version, that its author is not the
   publisher, or that re-derived numbers match. yaaf does all three before
   your `check_review` hook runs. Your review hook holds only the rubric.
10. **No UI code.** A view is data in `ui/view.json`, built from yaaf's
    components (`status`, `metric`, `markdown`, `link`, `artifact`,
    `checklist`, `table`, `coverage`).
11. **Never weaken a check to make it pass.** If a fixture or a test fails,
    fix the hook, or fix a fixture that is genuinely wrong, and say so in your
    report. Do not delete a refuse case. Do not widen an `expect.json`. Do
    not catch-and-accept.

## Procedure

### 1. Map the domain onto capabilities

Before you write any code, write down one line for each of these. Each one
becomes a capability in the manifest.

| Question | Capability |
|---|---|
| What documents do seats publish, and what makes one acceptable? | `artifacts = [...]`, plus a `check_artifact` hook for each |
| Does one need an independent review before the project completes? Which of its numbers are load-bearing? | `review = { of, artifact, rederive_tolerance }` |
| What human-readable pages should yaaf render from an accepted artifact? | `platform_pages = [...]` |
| What must exist before a plan holds? Before the project completes? | `plan_gate`, `completion_gate` |
| What should every seat know about this kind? | `brief` (at most 8,000 characters) |
| Which data-source tools does it use, and which areas can those tools cover? | `coverage`, `receipts` |
| Is there a pull-and-compute that the platform should run, not an agent? | `[capabilities.data_actions.<name>]` |
| What must the chairman set up first? | `setup_steps` |
| What should the project's home page show? | `view` |
| What should New Project ask? | `create_fields` |
| Does it need a fixed lead role? | `kind.lead`, plus `roles/<id>-lead.json` |

Declare only what the domain needs. An undeclared capability has no
requirements, so it costs nothing.

### 2. Scaffold

Pass the flags that match your mapping:

```bash
python -m yaaf_plugin_api new <dir> --id <plugin-id> \
  [--artifact NAME.json ...] [--review] [--pages] [--brief] [--plan-gate] \
  [--completion-gate] [--view] [--lead] [--create-fields] [--receipts] \
  [--action NAME --action-tool TOOL] [--setup-step KEY]
python -m yaaf_plugin_api check <dir> --full
```

The scaffold passes the full suite before your first edit. **Keep it that
way after every step that follows.** Run `check --full` after each change, so
any failure points at the change you just made.

The scaffold's conventions: the package is the id with `-` changed to `_`, the
review artifact is `<stem>-review.json`, the lead is `<id>-lead`, and a page
is `<stem>.md`. Keep them unless you have a reason to change them.

### 3. Replace every `Replace:`

```bash
grep -rn "Replace" .
```

Each hit is a placeholder for a real decision:

- **`check_artifact`**: parse defensively. Validate the shape and each domain
  rule. Collect every problem, then return `Refuse(problems)` or `Accept()`.
  If the artifact has a review, return
  `Accept(load_bearing={claim_id: number_or_None, ...})`. Those are the
  numbers the review will be compared with.
- **`check_review`**: only the rubric (every criterion judged, no `pass`
  with a failed criterion, and so on). The review envelope that yaaf reads is
  `{"subject_sha256", "verdict": "pass"|"return", "rederived": [{"claim", "value"} | {"claim", "matches": true}]}`.
  You may add fields of your own.
- **Gates**: return `[Owed(stable_key, text)]`. Read `ctx.latest(name)` and
  `ctx.read_artifact(name)`. An empty list means nothing is owed.
- **`render`**: return only the declared page names, with allowed media
  types. Mark a draft when `reviewed` is false; the draft and reviewed renders
  may differ only in that mark.
- **`fields_of_read`**: return the fields a tool result really holds, or
  `ReadFields.unknown()`. Never return an empty tuple. A read with unknown
  fields backs no field citation.
- **`data_action`**: call `gateway.call(tool, args)` only for the tools the
  manifest lists. Catch `ToolError` and return an `ActionResult` that says
  what failed. Store only the declared outputs.
- **`skills/*.md`**: the playbook for the seats, which is the domain's
  method. Name the real artifact files and tools. A seat gets the plugin's
  helper files through the `plugin_file` tool.
- **`roles/`, `crews/`**: yaaf's library JSON shapes. The lead must not also
  be one of the crews.

### 4. Fixtures from the domain

For each gate, write at least one realistic accept case and one refuse case
for each rule that matters. Name each refuse case after the rule it breaks,
and give it an `.expect.json` with `{"contains": ["words from your refusal"]}`.
Use `fixtures/states/*.json` (and `"state"` in an expect file) when a verdict
depends on the project's state. Record real tool results in
`fixtures/sources/<source>/` for data actions and receipts. Run `spec` and
read `fixtures_format` for the exact layout.

### 5. Tests

In `tests/test_*.py`, write at least one attack test for each gate (rule 6),
plus a test for each piece of non-trivial computation. Build snapshots with
`ProjectSnapshot(project_id, kind, mode, plugin_version, today, ...)` and call
the hooks directly. There is no yaaf server in these tests, and none is
needed.

### 6. Verify

```bash
python -m pytest tests -q
python -m yaaf_plugin_api check . --full
python -m yaaf_plugin_api check . --full --json    # the same report, as data
```

## Definition of done

The work is done when all of these hold:

- [ ] `check . --full` shows **no `fail`**, and the only `pending` lines are
      **MIN-02, MIN-03 and MIN-07**. Only a real install can decide those
      three.
- [ ] `grep -rn "Replace" .` returns nothing.
- [ ] Each rule in the domain description is enforced by a hook, has a refuse
      fixture, and is named by a test.
- [ ] The README says what the kind delivers, which capabilities it declares,
      and how to run the checks.
- [ ] Your report (below) is written.

## Reading a failure

Each failed line names a requirement and gives reasons (`↳ ...`). Fix the
cause the reason names. Do not fix the symptom.

| Failure | Cause | Fix |
|---|---|---|
| MIN-05 missing / extra hook | The manifest and the decorators disagree | Add the hook or the declaration. The names must match exactly |
| MIN-08 / MIN-13 wrong verdict | The hook disagrees with a fixture | Decide which one is wrong, using the domain description. Fix that one and say so |
| MIN-09 raised | An unguarded parse or index | Guard it and `Refuse` |
| MIN-10 too slow | Heavy work inside a hook | Precompute in a data action, or simplify |
| MIN-11 nondeterministic | Clock, randomness, or unordered iteration | Use `ctx.today`, sort, or remove it |
| MIN-12 guard tripped | Your code or an import touched the network, the environment or the disk | Remove the call or the library |
| MIN-16 | Tests failed, or a gate has no attack test | Fix them, or add the test |
| MIN-17 | A refusal is empty, too long or holds a traceback | Rewrite it as an instruction |
| EXT-* | A declared capability broke its extended requirement | Read the requirement's text in `spec` |

## Your report

When you finish, report:

1. The capabilities you declared, and one line each on why.
2. The domain rules, each mapped to the hook, fixture and test that enforce
   it.
3. Any fixture you changed after it first failed, and why the fixture (not
   the hook) was wrong.
4. The final `check --full` summary (the counts and the pending ids).
5. Anything in the domain description you could not enforce, and why.

## Request template

The human fills this in and sends it with this page:

```markdown
Build a yaaf factory plugin. Follow docs/PLUGIN_AGENT_BRIEF.md.

- Plugin id: <e.g. grant-writer>
- What a project of this kind delivers: <one sentence>
- Artifacts the seats publish, and the rules for each: <list>
- Needs an independent review? Which numbers are load-bearing?: <yes/no, list>
- Pages to render for the client: <list, formats>
- Data sources and tools (if any): <names; recorded examples attached?>
- What New Project should ask: <fields>
- Repo to push to: <https://github.com/owner/repo>
```
