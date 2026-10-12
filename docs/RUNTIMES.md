# Runtimes — any harness, any provider, the same rules

**Status: proposed. Nothing here is built.** This is the design and the
tracking doc for running a seat on something other than the Claude Agent SDK:
the Claude CLI driven directly, the Anthropic API under yaaf's own loop, and
then Codex, Gemini, OpenCode and whatever comes after them. Update the
checkboxes as milestones land; if a decision here turns out wrong, change the
doc in the same PR that changes the code.

Adopting it means rewriting one line of [PLAN.md](PLAN.md) — *"Multi-provider
models. The runner interface leaves room; nothing more."* — in the PR that
lands M3, not before. M0 through M2 stay inside Anthropic and change no
decision on record.

---

## Why

Two reasons, both already in this repo's own words.

**The salvage path is a governance harness, and a harness that governs one
vendor's agents is a narrow one.** [PLAN.md](PLAN.md) names yaaf-as-governance
as what survives if the org layer does not. Every rule that makes that
harness worth having — platform-run tests, verified evidence, the commitment,
the brakes, the money — lives behind `/agent/*` and has never known which
model was on the other side of the token. The two exceptions are metering and
the per-run dollar cap, which today ride on the SDK's own accounting; moving
them into a loop yaaf owns is most of what this design is.

**Disagreement is the product, and two seats on one model share its blind
spots.** The README argues that "one agent checking its own work has the same
blind spots in both seats." A risk seat on a different vendor's model than
the research seat it can veto is the strongest version of that argument, and
today it cannot be hired.

## What this is not

- **Not "bring your own agent".** Every harness runs inside yaaf's sandbox, on
  yaaf's per-run token, under yaaf's metering. Nothing calls out to an agent
  running somewhere else and trusts what it reports: the container is part of
  the containment ([SECURITY.md](../SECURITY.md)), so an agent yaaf cannot put
  in one is an agent yaaf cannot run.
- **Not a model router.** No automatic fallback from one provider to another
  when a call fails. A seat runs on what it was hired on; a dead credential is
  a flag and a pause, exactly as now.
- **Not session continuity.** Runs stay stateless — fresh container, context
  pack in, result out. A harness that can resume a session uses it only to
  survive a transient error *inside* one run, as `sandbox_entry` does today.
- **Not a worker fleet.** Same box, same Docker, same `max_concurrent_runs`.

---

## The governing constraint

> **A harness is transport. It decides how a model is driven, never what the
> model may do.**

Authority stays where it is: in the 4xx answers of `/agent/*`. A harness may
make a run *cheaper* (fewer schemas sent), *more legible* (a better trace) or
*more robust* (resume after a dropped stream). It may never make anything
*more permitted*, and nothing a harness does is counted as enforcement. The
tool-surface rule already in `sandbox_entry.py` — "this list is not
enforcement and must never become it" — is this constraint, stated once for
one harness. This doc states it for all of them.

The one thing a harness does that *is* load-bearing is **metering**. A run
nobody can price is a run the budget cannot stop. So the second half of the
constraint:

> **A harness that cannot report what it spent, while it is spending it, is
> not admitted.**

---

## Three axes, one resolution

Today one word, `runtime`, means all of these at once, and every one of them is
hardwired to Anthropic. They come apart cleanly:

| Axis | What it decides | Examples |
|---|---|---|
| **Provider** | Who sells the tokens: credential kinds, how a credential is checked, prices, the plan meter. | `anthropic`, `openai`, `google`, `openrouter` |
| **Credential kind** | One way to pay a provider, and the unit it meters in. | `anthropic:api_key` (USD), `anthropic:subscription` (tokens), `openai:api_key` (USD), `openai:chatgpt` (tokens) |
| **Harness** | What runs the agent loop inside the sandbox, and what it can and cannot do. | `claude_agent_sdk`, `claude_cli`, `anthropic_api`, `codex_cli`, `gemini_cli`, `opencode` |
| **Model** | The weights, the price per token, and the tier "high stakes" lifts to. | `claude-opus-5-5`, `gpt-…`, `gemini-…` |

A seat resolves at lease time:

```
agent.model_override ?? role_version.model           → model    → provider
agent.harness_override ?? role_version.harness
    ?? provider.default_harness                      → harness
project credential for that provider                 → credential (kind)
check: harness serves provider, harness accepts kind, model is priced
       (if the kind meters in USD), harness honours the role's permissions
```

`integrations.runtime` survives only as a project-wide force-override, and only
for `mock`: the fire drill and the goldens keep pointing every seat at the
scripted backend exactly as they do now.

---

## Where Claude is wired in today

Every place a provider assumption lives, so M0 can move each one behind a
registry. Twenty-odd sites; none is large.

| Site | Today | Becomes |
|---|---|---|
| `yaaf/runner/sandbox_entry.py:28`, `:1830` | `claude_agent_sdk` imports; `ClaudeAgentOptions`, `query()` drive the run | the `claude_agent_sdk` driver |
| `sandbox_entry.py:235–1516` | 59 tools declared with the SDK's `@tool` | the same tools as plain data; a renderer per harness |
| `sandbox_entry.py:1648`, `:1668` | `Read/Write/Bash/WebFetch…` — Claude Code's built-in names | manifest `builtins` map |
| `sandbox_entry.py:1745` | plan meter parsed from the SDK's `RateLimitEvent` | a normalized `plan` event; provider-declared |
| `sandbox_entry.py:1792`, `daemon.py:602` | Anthropic/Claude Code error strings | `error_kind` in `result.json`; markers per provider |
| `chat_entry.py:28`, `:410` | `ClaudeSDKClient` holds the boardroom conversation | driver `converse()` (M5) |
| `runner/backends/__init__.py` | `agent_sdk` \| `mock` | one sandbox backend + a harness registry |
| `runner/backends/agent_sdk.py:89` | `YAAF_MODEL`, `YAAF_EFFORT`, `YAAF_MAX_USD` env | a `RunSpec` file beside the context pack |
| `runner/daemon.py:191`, `chat.py:353`, `types.py:21`, `sandbox_entry.py:43`, `cli/spend.py:137`, `core/models.py:703`, `api/admin.py:172` | default `claude-sonnet-5-5`, seven copies | the model catalog's default |
| `runner/daemon.py:441` | `ANTHROPIC_API_KEY` / `CLAUDE_CODE_OAUTH_TOKEN` | `credential_kind.env(value)` |
| `runner/daemon.py:496` | `integrations.runtime` picks the backend | seat resolution (above) |
| `runner/daemon.py:538` | `subscription` ⇒ cost is None | the credential kind's meter |
| `core/models.py:1927` | `Secret.llm_mode`: `api_key` \| `subscription` | + `provider` column |
| `core/llm_credentials.py:8`, `api/settings.py:91`, `:250`, `web/src/llmCredentials.tsx:47` | two Anthropic modes, hardcoded in labels, validation and the form | derived from the provider registry |
| `api/admin.py:2180`, `core/platform.py:194` | `sk-ant-oat` / `sk-ant-api` shape checks | `credential_kind.shape_error` |
| `api/admin.py:2311` | billing check calls `api.anthropic.com/v1/models` | `provider.live_check` |
| `server/plan_probe.py:59` | "Check now" calls Anthropic's usage URL | `provider.plan_probe` (optional) |
| `api/admin.py:693–707` | `CRAFT_MODEL`, `_MODEL_RANK` — Claude only | catalog tiers, **within the seat's provider** |
| `runner/daemon.py:229` | the data-source paragraph names tools `mcp__<name>__*` | `manifest.tool_ref` renders the name the model actually sees |
| `deploy/sandbox/Dockerfile` | installs `@anthropic-ai/claude-code` and `claude-agent-sdk` | one install script per harness |
| `founder/session.py`, `core/platform.py:56` | the Founder runs on the CLI via the SDK | unchanged until M5 |

---

## The pattern

Four registries and one loop. Adding a provider should be data plus one small
file; everything else in the product reads the registries.

### 1. One tool catalog, three renderings

The yaaf tools are already harness-neutral in substance: each is a name, a
description, a JSON schema and a handler that calls `/agent/*` with the run
token. Only their *declaration* is SDK-shaped — 59 uses of the SDK's `@tool`.

**They stay where they are.** Fifteen test files reach into `sandbox_entry`,
several by patching its module globals (`_call`, `AGENT_KIND`, `WORKSPACE`,
`_data_source_specs`); move the handlers to another module and those patches
silently stop reaching them. So M0 swaps only the decorator — a local `tool()`
with the SDK's signature, producing plain data with the attribute names the
SDK's own tool objects carry:

```python
@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_schema: dict
    handler: Callable[[dict], Awaitable[dict]]
```

`sandbox_entry.py` becomes the catalog: tools, `run_tools()`, the HTTP plumbing,
no SDK import. What leaves it is `main()` and the `ClaudeAgentOptions` around
it, which become the `claude_agent_sdk` driver. Then three renderers, each a
few dozen lines:

| Rendering | For | How |
|---|---|---|
| **in-process SDK** | `claude_agent_sdk` | `create_sdk_mcp_server` over the catalog — today's behaviour, byte for byte |
| **stdio MCP server** | every CLI harness | `yaaf-tools`, a small server on the `mcp` package the image already carries, launched by the CLI from a generated MCP config with the run's env. Every CLI worth supporting speaks MCP. |
| **function calling** | every API harness | the catalog as the provider's tool schema; the loop calls the handler in-process |

The stdio server also **proxies data sources**: it mounts each granted
external MCP server, filters `tools/list` to the source's allowlist and
refuses `tools/call` outside it. That makes the allowlist harness-independent
instead of an SDK option — and it is the first half of the data-source gateway
(move the proxy server-side and the bearer never enters the sandbox).

### 2. One run loop, many drivers

`sandbox_entry.main()` is two things tangled together: what any run must do,
and how the SDK does it. The first half becomes `entry.py` and is written
once:

- read the `RunSpec`, the system prompt and the context pack;
- write the trace; checkpoint `result.json` after **every** event, so a kill is
  metered rather than free;
- the **governor** — sum usage, price it, and stop the driver at
  `max_usd_per_run` (a harness with a native budget uses it too; the governor
  is the backstop, never the only line on a harness that has one);
- the transient-error policy — classify, back off, resume in place if the
  harness can, rerun fresh if no session exists yet;
- write the final `result.json`.

A driver only translates. It turns a `RunSpec` into a running model and the
model's output into **normalized events**:

```python
Event = (
    Session(id)                       # resumable handle, if any
  | Text(text)
  | ToolCall(name, preview) | ToolResult(name, ok, preview)   # trace only
  | Usage(input, output, cache_read, cache_write,             # running totals
          cost_usd: float | None, model_served: str)          #   for this attempt
  | Plan(windows)                     # the provider's plan meter, if it reports one
  | Error(kind: "auth" | "quota" | "transient" | "fatal", text)
)

class Driver(Protocol):
    manifest: Manifest
    def prepare(self, spec: RunSpec, tools: list[Tool]) -> None: ...   # config files, skills, MCP config
    def run(self, spec: RunSpec, prompt: str, resume: str | None) -> AsyncIterator[Event]: ...
    def stop(self) -> None: ...                                        # the governor's lever
```

Two families cover almost everything, so almost no driver is written from
nothing:

- **`CliDriver`** — a subprocess, a JSON-lines parser, a generated config file,
  SIGTERM-then-SIGKILL for `stop()`. A concrete CLI driver supplies `argv()`,
  `env()`, `config_files()`, `parse(line) -> list[Event]` and its error
  markers. Claude CLI, Codex, Gemini CLI, OpenCode and Cursor's agent CLI are
  all this shape.
- **`LoopDriver(client)`** — yaaf's own agent loop for providers reached by
  plain API: send messages, run tool calls, repeat to `max_turns`. The loop owns
  the built-in tools (files, shell, fetch — the same grants Claude Code's
  built-ins answer to today) and the data-source MCP client `data_fetch`
  already has. A provider supplies a `ModelClient`: request/response mapping,
  usage extraction, error classes, and its server-side tools (web search)
  where it has them. Anthropic, OpenAI, Gemini, OpenRouter, Bedrock, Vertex
  and any OpenAI-compatible endpoint are all this shape.

`mock` is a driver too, in-process, so rehearsal and the goldens go through the
same seam everything else does.

### 3. The manifest — what a harness can do, as data

Every rule this design adds is derived from manifests rather than written per
harness. If a fact about a harness changes what the platform should allow, it
is a field here:

```python
Manifest(
    name="claude_cli",
    family="cli",                         # cli | loop | sdk | mock
    label="Claude Code CLI (claude -p)",
    providers=("anthropic",),             # opencode would list several
    accepts=("anthropic:api_key", "anthropic:subscription"),
    tools_via="mcp_stdio",                # sdk_inprocess | mcp_stdio | function_calling
    tool_ref="mcp__{server}__{tool}",     # the name the model sees; prompts render through it
    builtins={"files": "native", "bash": "native", "web": "native"},
    can_withhold={"files": True, "bash": True, "web": True},
    usage="streaming",                    # admission requires it
    cost="reported",                      # reported | priced
    native_budget=False,
    resume=True,
    conversation=True,                    # can hold the boardroom chat (M5)
    skills="native",                      # native | inline | none
    plan_meter=True,
    efforts={"low": "low", "medium": "medium", "high": "high", "xhigh": "xhigh", "max": "max"},
    binary="claude", install="deploy/sandbox/harnesses/claude.sh",
)
```

The manifest lives in the sandbox package, which imports nothing from `yaaf`,
so the control plane and the sandbox read **one** copy. `FILE_CRED_PREFIX`
solves the same problem today with two copies and a test that they agree; this
solves it without the second copy.

### 4. Providers and credential kinds

```python
Provider(
    name="anthropic",
    default_harness="claude_agent_sdk",
    kinds=(
        CredentialKind("api_key", meter="usd",
                       env=lambda v: {"ANTHROPIC_API_KEY": v},
                       shape_error=..., label="Anthropic API key"),
        CredentialKind("subscription", meter="tokens",
                       env=lambda v: {"CLAUDE_CODE_OAUTH_TOKEN": v},
                       shape_error=..., label="Claude subscription token"),
    ),
    live_check=...,          # API key: list models. Subscription: shape only, as now.
    plan_probe=...,          # optional: today's "Check now"
    failure_markers=(...),   # moved out of daemon.py
)
```

A credential that has to be a *file* — Codex's ChatGPT login is an
`auth.json`, not a variable — uses the file-credential path that already
exists (`Secret.filename`, materialized on a tmpfs outside `/workspace`). No
new mechanism.

### 5. The model catalog

One file, `models.json`, replaces seven default strings and `_MODEL_RANK`:

```json
{"id": "claude-opus-5-5", "provider": "anthropic", "tier": 5,
 "efforts": ["low", "medium", "high", "xhigh", "max"],
 "price": {"input": …, "output": …, "cache_read": …, "cache_write": …,
           "per": "mtok", "as_of": "2026-10-12"}}
```

`tier` is what `stakes: high` lifts a seat to — the top tier **of the seat's
own provider**. A dial labelled "how much this matters" that silently moved a
Codex seat onto Opus would be two decisions wearing one label.

Prices are in the repo, dated, and overridable per install in Settings. Where a
harness reports its own cost, the loop prices the run anyway and records the
gap in the trace; a model whose gap stays wide raises a health line — the
price table is checked against the bills it is supposed to predict.

### 6. The run contract

`RunJob` gains `harness`, `provider`, `credential_kind` and the model's price
row. The env-var interface becomes one `spec-{run_id}.json` staged beside the
prompt and the pack, run-keyed for the reason those already are. `result.json`
gains:

| Field | Why |
|---|---|
| `harness`, `provider`, `model_served`, `harness_version` | `model_served` is what answered — a CLI can fall back — and the run is priced on it. The version makes a transcript reproducible. |
| `cost_source` | `reported` \| `priced`. Never "unknown": an unpriced run cannot start under a USD meter (rule R3). |
| `error_kind` | `auth` \| `quota` \| `transient` \| `fatal`. The daemon stops string-matching one vendor's error messages. |
| `session_id` | Trace only. |

`Run` gains `provider` and `harness` columns, pinned at lease time like `model`
already is, so a reaped run stays attributed to what actually ran.

---

## The rules this adds

Each is a refusal or an automatic state change, enforced server-side, with an
attack in `tests/selftest_invariants/` — per [CONTRIBUTING.md](../CONTRIBUTING.md).
All of them read manifests and the catalog; none names a harness.

| # | Rule | Where it fires |
|---|---|---|
| R1 | **A credential the seat's harness cannot accept is refused** — a subscription token on `anthropic_api`, a ChatGPT login on a Gemini seat. | credential assignment, hire, repin, override |
| R2 | **A harness that cannot withhold a tool the role does not grant is refused for that role.** Codex cannot run without a shell; a role without `bash` cannot be hired onto it. Widening a role's tools silently because the transport is clumsy is exactly what the role version exists to prevent. | hire, repin, override |
| R3 | **Under a USD meter, a model with no price is refused.** A run nobody can price is a run the budget cannot stop. | hire, repin, override, lease |
| R4 | **One project, one meter.** All of a project's credentials meter in USD or all in tokens. Two units cannot share one cap, and inventing an exchange rate between them is inventing a number. (Open question 1.) | credential assignment |
| R5 | **A seat whose provider has no credential on the project cannot be hired, and a project with such a seat cannot start.** It fails at the door, not at 3am. | hire, start, health |
| R6 | **An effort the model does not support is refused**, with the list it does. Never mapped silently, in either direction. | hire, repin, override |
| R7 | **A CEO seat holding the boardroom chat must be on a harness with `conversation`.** | hire, start (from M5) |

Every one of them also shows in the health checklist and the fire drill's
"where it stopped", because both read the same functions.

## Admission — what a harness must do to be registered

A harness that fails any of these is not merged, whatever else it does well:

1. **Streams usage.** Running totals per model call, not one number at the end.
   Without that the governor has nothing to stop on.
2. **Runs non-interactive with its own approval prompts off.** The container is
   the containment; a harness waiting on a human it cannot see is a run that
   hangs to the timeout and bills for it.
3. **Takes yaaf's tools over MCP (CLI) or function calling (loop).**
4. **Classifies a dead credential as `auth`.** The daemon's three-strikes pause
   depends on it.
5. **Has its telemetry and self-update switched off** in the install script.
6. **Pins a version**, recorded on every run.
7. **Passes the contract suite** below, offline, at $0.

---

## The three Claude harnesses

All three take the same models. They differ in what pays, what runs, and what
they are the reference for.

| | `claude_agent_sdk` | `claude_cli` | `anthropic_api` |
|---|---|---|---|
| What runs | the Python SDK, driving the CLI it wraps | `claude -p`, stream-json in and out | yaaf's own loop on the Messages API |
| Credentials | API key, subscription | API key, subscription | **API key only** — a subscription token is a CLI credential, not a Messages API one (`founder/session.py` says so for the Founder already) |
| yaaf tools | in-process SDK MCP | `yaaf-tools` over stdio | function calling |
| Built-in tools | Claude Code's | Claude Code's | the loop's own: files, shell, fetch; web search as the API's server tool |
| Budget | SDK's `max_budget_usd` + governor | governor | governor, exact per call |
| Cost | reported | reported | priced |
| Plan meter | yes | yes | no — API keys have none, as now |
| Image needs | Node, CLI, SDK | Node, CLI | nothing beyond the base image |
| It is the reference for | — (today's behaviour) | **every CLI harness** | **every API provider** |

**Why `claude_cli` when the SDK already drives the same CLI.** Because it is
the CLI family's reference implementation, built against the CLI this platform
already runs in production. Same model, same credential, same tools, same
names (`mcp__yaaf__board` either way): any difference between an SDK
transcript and a CLI transcript is the pattern's fault, not a vendor's — which
is the only condition under which the pattern can be debugged before it meets
Codex. It also takes one moving part out of the path: `sandbox_entry` already
duck-types the SDK's rate-limit event because an older SDK does not define the
class — version drift paid for a layer the CLI does not need.

**Why `anthropic_api`.** Because it is the loop family's reference, and the
loop is how most providers will arrive. Owning the loop also buys what no CLI
offers: exact per-call usage for the governor, control of cache breakpoints,
and a run with no CLI underneath it at all. The cost is that yaaf now owns the
built-in tools, which is why it waits until M2.

---

## Adding a provider, once M3 is done

The promise is that this list is the whole job:

| A new… | Files |
|---|---|
| **CLI harness** | `drivers/<name>.py` (`argv`, `env`, `config_files`, `parse`, markers — a `CliDriver` subclass); a manifest entry; `deploy/sandbox/harnesses/<name>.sh`; recorded stream fixtures |
| **API provider** | `clients/<name>.py` (a `ModelClient`); a `Provider` entry with its credential kinds; price rows in `models.json`; recorded response fixtures |
| **model** | rows in `models.json` |

Everything else — the credential form, the role editor's pickers, the health
checklist, the fire drill, the spend report, the receipt, the MCP `catalog`,
export and clone, the craft lift — **reads the registries and does not change.**
That is the design's answer to [CLAUDE.md](../CLAUDE.md)'s warning that the
expensive mistake is a right design wired into half the places it belongs: for
this concept, the places do not get edited per provider at all.

And forgetting one still fails a test, because the registries are tested for
completeness:

- every model names a registered provider and at least one harness that serves
  it, and has a price unless every credential kind of its provider meters in
  tokens;
- every credential kind is accepted by at least one harness, and has a shape
  check;
- every harness has an install script, fixtures, a pinned version, and passes
  the contract suite.

### The contract suite

`tests/selftest_invariants/test_harness_contract.py`, parametrized over every
registered harness. CLI harnesses run against a fake binary that replays their
recorded stream; loop providers against a fake HTTP server replaying recorded
responses. Offline, $0, part of the default run:

1. Usage parses to the fixture's known totals, cache split included.
2. The governor stops a run whose priced cost crosses `max_usd`, and what was
   spent up to the stop is metered.
3. A kill mid-run leaves a checkpointed, metered partial result.
4. A dead-credential fixture yields `error_kind: auth`.
5. A transient fixture resumes in place where the harness can, fresh where it
   cannot.
6. The tools rendered for a CEO and for an employee equal the catalog's split
   — the same assertion `test_tool_surface.py` makes for the SDK today.
7. A role that does not grant `bash` produces a config with no shell, or the
   manifest says `can_withhold.bash: false` and R2 refuses the hire.
8. An unlisted skill stays dark.

Plus one `-m paid` smoke per harness, like `test_planning_quality.py`: a real
credential, one trivial run, usage non-zero and priced. Opt-in, never in CI.

---

## The footprint

Per the sibling rule in [CLAUDE.md](../CLAUDE.md), every surface the concept
touches, and whether a new provider edits it:

| Surface | Change | Per provider? |
|---|---|---|
| Settings → LLM credentials, New Project picker, Founder's list, MCP `catalog`/`create_project` | provider + kind from the registry, via `llm_credentials.choices` | no |
| Role editor, `roles/*.json`, `seed-roles` | `harness` beside `model`; pickers and validation from manifests and the catalog | no |
| Roster / agent overrides | `harness_override` beside `model_override` | no |
| Project Settings → runtime | per seat; project-wide only for `mock` | no |
| Health checklist, fire drill | R1–R7 as findings | no |
| Lease (`daemon.py`, `chat.py`) | seat resolution; `RunSpec` | no |
| Runs page, `spend-report`, receipt, attribution | group by provider / harness / model | no |
| Plan meter, "Check now" | provider capability | no |
| Billing check | `provider.live_check` | no |
| Credential-failure pause | `error_kind`, provider markers | no |
| Craft lift (`stakes: high`) | catalog tier, within the provider | no |
| Export / clone | role version `harness`; credentials by name per provider | no |
| Context pack, data-source paragraph | tool names through `manifest.tool_ref` | no |
| Sandbox image | one install script per harness | **yes — one file** |
| Goldens | unchanged: they run `mock` | no |
| Docs | INTEGRATIONS (billing), AGENT_API (writing a role), DEPLOYMENT (image), SECURITY (each harness's own approvals off), GOVERNANCE (`max_usd_per_run` is the governor), PLAN (the multi-provider line) | once |

---

## Milestones

### M0 — The seams, and nothing else moves
- [ ] The SDK's `@tool` swapped for a local decorator of the same signature;
      the 59 tools stay in `sandbox_entry.py`, and the fifteen test files that
      import it pass **unmodified** — they are the proof the change moved
      nothing
- [ ] A byte-equality test: the SDK rendering of the catalog equals today's
      schemas, tool for tool
- [ ] `entry.py` run loop + `Driver` protocol; `claude_agent_sdk` and `mock`
      registered as drivers; one sandbox backend
- [ ] Provider registry (`anthropic`), credential kinds, `Secret.provider`;
      `Run.provider`, `Run.harness`; `RoleVersion.harness`,
      `Agent.harness_override` (nullable — null means the provider's default)
- [ ] `models.json` replaces the seven defaults and `_MODEL_RANK`
- [ ] `error_kind` in `result.json`; the daemon reads it before its markers
- [ ] Migration run on Postgres (CLAUDE.md tier 3)

**Done when:** the goldens are byte-identical, the full suite is green on both
databases, and a deploy shows no difference anywhere a chairman can look.

### M1 — `claude_cli`: the CLI family's reference
- [ ] `yaaf-tools` stdio MCP server rendering the catalog, plus the
      data-source proxy with the allowlist enforced in it
- [ ] `CliDriver` base; `claude_cli` on it
- [ ] The governor (here first — this harness has no SDK budget behind it)
- [ ] Contract suite, with recorded `claude -p` streams as fixtures
- [ ] R1, R2, R6 with attacks

**Done when:** one seat of a live project runs on `claude_cli` beside an SDK
seat for a week, and the two Runs pages are indistinguishable in shape.

### M2 — `anthropic_api`: the loop family's reference
- [ ] `LoopDriver`; the loop's built-ins (files, shell, fetch) under the same
      grants; web search as the API's server tool
- [ ] `ModelClient` for Messages; prompt caching
- [ ] Pricing from `models.json`; the reported-vs-priced gap check against the
      SDK on the same models
- [ ] R3 with an attack (an unpriced model hired under USD)

### M3 — The first other provider
- [ ] `openai` provider: `api_key` (USD), `chatgpt` (tokens, a file credential)
- [ ] `codex_cli` on `CliDriver`
- [ ] Project credentials per provider (a `project_llm_credentials` table;
      `billing_secret_id` migrates into it), R4, R5
- [ ] Settings, New Project, Founder and MCP pick credentials by provider
- [ ] PLAN.md's multi-provider line rewritten

**Done when:** a project hires a Claude research seat and a Codex critic, both
metered against one cap, and the receipt says which spent what.

### M4 — The long tail
- [ ] `gemini_cli`, `opencode` (several providers through one harness — the
      cheapest way to reach OpenRouter's catalog), `cursor` CLI
- [ ] `ModelClient`s: OpenAI, Gemini, any OpenAI-compatible endpoint (local
      models)
- [ ] Each one is the "Adding a provider" table and nothing else. If one is
      not, the pattern is wrong and gets fixed before the next one lands.

### M5 — The conversations
- [ ] The boardroom chat on any harness with `conversation` (R7)
- [ ] The Founder and the goal grader on any of them, still on the platform
      key and never a project's

---

## Open questions

1. **Mixed meters.** R4 refuses a project paying one provider by API key and
   another by subscription. Two caps would work; one cap across two units
   would not. Worth building only when somebody asks.
2. **Concurrency per provider.** `max_concurrent_runs` protects "the provider
   rate limit every project on the box shares" — with two providers that is two
   limits. Probably a per-provider ceiling beside the global one.
3. **One image or one per harness.** One image is simpler and gets large.
   Per-harness images are smaller and multiply builds. Start with one; split
   when the size hurts.
4. **Can the CEO pick a seat's harness when it hires?** Proposed: no. The CEO
   hires roles; a role carries its harness; changing it is a role edit or a
   chairman override. Hiring stays free because it buys no new *kind* of spend.
5. **A served model weaker than the hired one.** Price on the served model, as
   designed. Should a downgrade also raise a flag?

## Appendix — invocation notes, to verify against pinned versions

Starting points, not facts: every CLI moves fast, and each line below gets
checked against the version the install script pins before its driver is
written.

- **Claude CLI.** `claude -p` with `--output-format stream-json --verbose`;
  `--mcp-config` + `--strict-mcp-config` for `yaaf-tools`; `--allowedTools`;
  `--model`; `--max-turns`; system prompt by flag; `--resume <session>`;
  permission prompts off. The SDK builds its CLI argv from `ClaudeAgentOptions`,
  so the flags for every option `sandbox_entry` sets today can be read off the
  SDK's own subprocess transport rather than guessed. The stream carries
  per-message usage, the final cost and the rate-limit events the plan meter
  already reads, because the SDK is a parser over it.
- **Codex CLI.** `codex exec --json`; MCP servers in its TOML config;
  `codex exec resume <id>`; reasoning effort by config; its own sandbox and
  approvals off (the container is the sandbox); API key by env, ChatGPT login
  as an `auth.json` file credential.
- **Gemini CLI.** Non-interactive prompt mode with JSON streaming output; MCP
  servers in its settings file; approvals off; API key by env.
- **OpenCode.** `opencode run` with JSON output; providers and MCP servers in
  its config.
