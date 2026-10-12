# Runtimes — any harness, any provider, the same rules

**Status: M0 and M1 are built — the seams, then the Claude CLI as a second
harness a seat can choose. M1's one unticked line is a week of a live seat on
it, which only a deployment can tick. M2 onward is proposed.** This is the design and the tracking doc for running a seat on something other than the Claude Agent SDK:
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

## Where Claude was wired in

Every place a provider assumption lived when this was written (line numbers as
of `43b315c`), so each can move behind a registry. Twenty-odd sites; none is
large. **M0** marks the ones that already have.

| Site | Today | Becomes |
|---|---|---|
| `yaaf/runner/sandbox_entry.py:28`, `:1830` | `claude_agent_sdk` imports; `ClaudeAgentOptions`, `query()` drive the run | the `claude_agent_sdk` driver |
| `sandbox_entry.py:235–1516` | 59 tools declared with the SDK's `@tool` | the same tools as plain data; a renderer per harness — **M0** |
| `sandbox_entry.py:1648`, `:1668` | `Read/Write/Bash/WebFetch…` — Claude Code's built-in names | manifest `builtins` map |
| `sandbox_entry.py:1745` | plan meter parsed from the SDK's `RateLimitEvent` | a normalized `plan` event; provider-declared |
| `sandbox_entry.py:1792`, `daemon.py:602` | Anthropic/Claude Code error strings | `error_kind` in `result.json`; markers per provider — **M0** |
| `chat_entry.py:28`, `:410` | `ClaudeSDKClient` holds the boardroom conversation | driver `converse()` (M6) |
| `runner/backends/__init__.py` | `agent_sdk` \| `mock` | a backend per harness name — **M0**; one sandbox backend — M1 |
| `runner/backends/agent_sdk.py:89` | `YAAF_MODEL`, `YAAF_EFFORT`, `YAAF_MAX_USD` env | a `RunSpec` file beside the context pack |
| `runner/daemon.py:191`, `chat.py:353`, `types.py:21`, `sandbox_entry.py:43`, `cli/spend.py:137`, `core/models.py:703`, `api/admin.py:172` | default `claude-sonnet-5-5`, seven copies | the model catalog's default; the sandbox keeps a literal a test holds to it — **M0** |
| `runner/daemon.py:441` | `ANTHROPIC_API_KEY` / `CLAUDE_CODE_OAUTH_TOKEN` | the credential kind's variable — **M0**, for agents and the Founder |
| `runner/daemon.py:496` | `integrations.runtime` picks the backend | seat resolution (above) — **M0** |
| `runner/daemon.py:538` | `subscription` ⇒ cost is None | the credential kind's meter |
| `core/models.py:1927` | `Secret.llm_mode`: `api_key` \| `subscription` | + `provider` column — **M0** |
| `core/llm_credentials.py:8`, `api/settings.py:91`, `:250`, `web/src/llmCredentials.tsx:47` | two Anthropic modes, hardcoded in labels, validation and the form | derived from the provider registry — **M0** for the server; the form in M3, when it has a second provider to show |
| `api/admin.py:2180`, `core/platform.py:194` | `sk-ant-oat` / `sk-ant-api` shape checks | `credential_kind.shape_error` — **M0** |
| `api/admin.py:2311` | billing check calls `api.anthropic.com/v1/models` | `provider.live_check` |
| `server/plan_probe.py:59` | "Check now" calls Anthropic's usage URL | `provider.plan_probe` (optional) |
| `api/admin.py:693–707` | `CRAFT_MODEL`, `_MODEL_RANK` — Claude only | catalog tiers, **within the seat's provider** — **M0** |
| `runner/daemon.py:229` | the data-source paragraph names tools `mcp__<name>__*` | `manifest.tool_ref` renders the name the model actually sees |
| `deploy/sandbox/Dockerfile` | installs `@anthropic-ai/claude-code` and `claude-agent-sdk` | one install script per harness |
| `founder/session.py`, `core/platform.py:56` | the Founder runs on the CLI via the SDK | unchanged until M6 |

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

`wire_schema()` is the SDK's schema rule, owned rather than borrowed, and
`tests/fixtures/tool_catalog.json` — captured through the SDK's own conversion
before the change — holds it to what the model was actually sent. That much is
M0. The SDK driver stays in `sandbox_entry.py` beside the catalog, for the
reason the tools did: the tests that drive a run patch that module's globals.
Three renderers, each a few dozen lines:

| Rendering | For | How |
|---|---|---|
| **in-process SDK** | `claude_agent_sdk` | `render_sdk()` → `create_sdk_mcp_server` — today's behaviour, handed the same objects (M0) |
| **stdio MCP server** | every CLI harness | `yaaf_tools.py`, launched by the CLI from a generated MCP config with the run's env. It serves the **very server object** the SDK harness serves in process, so two harnesses cannot describe one tool two ways (M1). Every CLI worth supporting speaks MCP. |
| **function calling** | every API harness | the catalog as the provider's tool schema; the loop calls the handler in-process |

Data sources reach `claude_cli` exactly as they reach the SDK harness: the
same server configs in the MCP config, the same per-source allowlist as
`--allowedTools`. The **proxy** — the stdio server mounting each source itself,
filtering `tools/list` and refusing `tools/call` outside the allowlist — waits
for the first CLI that has no allowlist of its own (M4). Built now, it would be
a second enforcement of a rule Claude Code already enforces, on the one
harness that needs neither. It remains the first half of the data-source
gateway: move it server-side and the bearer never enters the sandbox.

### 2. One run loop, many drivers

`sandbox_entry.main()` was two things tangled together: what any run must do,
and how the SDK does it. M1 split them. The first half is
`sandbox_entry.run_harness`, written once:

- read the system prompt and the context pack;
- write the trace; checkpoint `result.json` after **every** event, so a kill is
  metered rather than free;
- the **governor** — the per-run cap held across resumes: each resumed attempt
  is handed only what is left of `max_usd_per_run`, and a run that has spent
  it is not resumed. Each resume is a fresh model process with a fresh budget
  of its own, so before M1 a cap handed to every attempt whole bounded a run
  at three times itself. Within an attempt the harness's own budget is the
  line where it has one — and both Claude harnesses do (`--max-budget-usd`).
  Pricing streamed usage to stop an attempt mid-flight arrives with prices,
  in M2;
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
    conversation=True,                    # can hold the boardroom chat (M6)
    skills="native",                      # native | inline | none
    plan_meter=True,
    efforts={"low": "low", "medium": "medium", "high": "high", "xhigh": "xhigh", "max": "max"},
    binary="claude", install="deploy/sandbox/harnesses/claude.sh",
)
```

The harness registry lives in `yaaf/runtimes/harnesses.py`, which imports
nothing from `yaaf` — a test holds that — so the control plane and the sandbox
can read **one** copy. M0 registers `claude_agent_sdk` and `mock` with the
fields something already reads; the rest of the manifest arrives with the
harness that first needs each field. `FILE_CRED_PREFIX`
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

One file, `yaaf/runtimes/catalog.py`, replaces the default-model strings and
`_MODEL_RANK`. Data, not logic — a Python module rather than JSON only so it
ships in the wheel with nothing to remember in `package-data`:

```python
Model("claude-opus-5-5", "anthropic", tier=5)
# from M2, when a harness needs it to stop a run:
#   price=Price(input=…, output=…, cache_read=…, cache_write=…, per="mtok", as_of="…")
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

M0 shipped the part something already reads: `RunJob.provider` and
`RunJob.harness`, those two columns, and `error_kind` end to end. The rest of
`result.json` arrives with the run loop in M1, because the loop is what fills
it in.

---

## The rules this adds

Each is a refusal or an automatic state change, enforced server-side, with an
attack in `tests/selftest_invariants/` — per [CONTRIBUTING.md](../CONTRIBUTING.md).
All of them read manifests and the catalog; none names a harness.

| # | Rule | Where it fires |
|---|---|---|
| R1 | **A credential the seat's harness cannot accept is refused** — a subscription token on `anthropic_api`, a ChatGPT login on a Gemini seat. | creation, add-team, hire, repin, credential change, recall; lease backstop; health (built in M1) |
| R2 | **A harness that cannot withhold a tool the role does not grant is refused for that role.** Codex cannot run without a shell; a role without `bash` cannot be hired onto it. Widening a role's tools silently because the transport is clumsy is exactly what the role version exists to prevent. | the role version itself, and every door R1 is at (built in M1) |
| R3 | **Under a USD meter, a model with no price is refused.** A run nobody can price is a run the budget cannot stop. | hire, repin, override, lease |
| R4 | **One project, one meter.** All of a project's credentials meter in USD or all in tokens. Two units cannot share one cap, and inventing an exchange rate between them is inventing a number. (Open question 1.) | credential assignment |
| R5 | **A seat whose provider has no credential on the project cannot be hired, and a project with such a seat cannot start.** It fails at the door, not at 3am. | hire, start, health |
| R6 | **An effort the model does not support is refused**, with the list it does. Never mapped silently, in either direction. | hire, repin, override (from M4) |
| R7 | **A CEO seat holding the boardroom chat must be on a harness with `conversation`.** | hire, start (from M6) |

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
| Budget | `max_budget_usd` per attempt + the cap across resumes | `--max-budget-usd` per attempt + the cap across resumes | governor, exact per call |
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
| **API provider** | `clients/<name>.py` (a `ModelClient`); a `Provider` entry with its credential kinds; price rows in `catalog.py`; recorded response fixtures |
| **model** | rows in `catalog.py` |

Where the providers people ask for actually land:

| Provider | Road | Milestone |
|---|---|---|
| DeepSeek | OpenAI-compatible API → a `Provider` entry on the M3 client; no CLI needed. Open weights, so also reachable through OpenRouter or a local server. | M3 |
| OpenRouter, local models (Ollama, vLLM) | the same client | M3 |
| Codex | `codex_cli`, plus the `openai` provider with an API key or a ChatGPT login | M4 |
| Gemini | `gemini_cli`, or a Gemini `ModelClient` on the loop | M5 |
| Kimi, Qwen, Mistral, Grok | `Provider` entries on the M3 client where they speak the OpenAI shape; their own CLIs where those are better | M5 |

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

**The suite proves the plumbing, never the judgment.** A model that passes it
can be driven, metered, stopped and refused. Whether it is any good at the job
is a different question: yaaf's agents work by calling 59 tools correctly from
role prompts written and tuned against Claude. A weaker model is still bounded
by every rule — its bad calls are 403s and the refusal cap revokes its token —
but being bounded is not being useful. Neither the fire drill nor the contract
suite can see the difference, because both run scripted agents. So before a
model is recommended for a seat, it gets the paid smoke and
`test_planning_quality.py` pointed at it, and the result goes in the catalog
entry's notes.

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

### M0 — The seams, and nothing else moves ✅
- [x] The SDK's `@tool` swapped for a local decorator of the same signature;
      the 59 tools stay in `sandbox_entry.py`, and the fifteen test files that
      import it pass **unmodified** — the proof the change moved nothing
- [x] `wire_schema()` held to `tests/fixtures/tool_catalog.json`, captured
      through the SDK's own conversion before the change; `render_sdk()` hands
      the SDK the very objects it always had (`test_tool_catalog.py`)
- [x] `yaaf/runtimes/`: the provider registry (`anthropic`, its two credential
      kinds, their variables, shape sentences, labels and dead-credential
      markers), the model catalog, the harness registry (`claude_agent_sdk`,
      `mock`), and `seats.seat_for` — the one resolution the runner, the chat
      lane and the spend report now share
- [x] `Secret.provider`, `Run.provider`, `Run.harness`, `Run.error_kind`
      (migration `0041`); a mock project pins `mock`, the harness that actually
      ran, not the seat's default
- [x] A credential naming a provider the install does not have — a rollback
      from a release that added one — is refused for its own project, never
      raised in the lease, where one exception stalls every project's runs
- [x] The catalog replaces six control-plane copies of the default model and
      `_MODEL_RANK`; the sandbox's literal fallback is held to it by a test
- [x] `error_kind` in `result.json`; the daemon believes `auth` before it
      reads any vendor's prose, and still reads the prose when a run died
      before its harness could say anything
- [x] Registry completeness tests (`test_runtime_registries.py`)
- [x] Full suite on SQLite and on Postgres 16 (CLAUDE.md tier 3)

**Done when:** the goldens are byte-identical, the full suite is green on both
databases, and a deploy shows no difference anywhere a chairman can look.

Two items the first draft put here moved to M1, on the rule
[PLAN.md](PLAN.md) applies to virtual cards — *a seam nothing uses is
scaffolding*:

- **The run loop and the `Driver` protocol.** An interface with one
  implementation is a guess about the second. M1 brings the second, and the
  interface gets decided against two real cases instead of one imagined one.
- **`RoleVersion.harness` and `Agent.harness_override`.** A column nobody can
  write is a column nobody can test. They arrive with the first harness there
  is a choice of.

### M1 — `claude_cli`: the CLI family's reference ✅ (but the live week)
- [x] `run_harness` + the driver shape (`attempt`, `budget`, `error_kind`,
      `drain`), split out of `sandbox_entry.main()` with `SdkDriver` as the
      first driver; every existing test that drives a run passes unmodified. One
      `SandboxBackend`, one entry script per harness
- [x] `RoleVersion.harness`, `Agent.harness_override` (migration `0042`); the
      role editor's picker, fed by `GET /admin/harnesses`; overrides through
      project create, add-team, export, clone and the New Project form
- [x] `yaaf_tools.py` — the catalog over stdio MCP, serving the SDK harness's
      own server object
- [x] `CliDriver`; `claude_cli` on it — flags read off the SDK's own argv
      builder and held to it by a test
- [x] The governor: the per-run cap across resumes, on both harnesses
- [x] Contract suite (`test_harness_contract.py`): two **recorded** `claude -p`
      streams and one synthetic, each replayed through **both** harnesses,
      which must land the same result.json; plus a real tool call through the
      stdio server to a stub agent API
- [x] R1 and R2, at every door a seat comes through — creation, add-team,
      the CEO's hire, repin, a credential change, a recall — with a lease-time
      backstop and a health-checklist line (`test_seat_harness.py`)
- [ ] **One seat of a live project on `claude_cli` beside an SDK seat for a
      week**, and the two Runs pages indistinguishable in shape. Needs a
      deployment; nothing here can tick it

What M1 found on the way, both now fixed:

- **A dead API key never paused a project.** Claude Code 2.1.296 says
  "Invalid API key · Fix external API key", and no marker matched it, so the
  three-strikes pause never fired for a revoked key. Both harnesses now read
  the fields beside the prose — `error: authentication_failed`,
  `api_error_status: 401` — and the marker list learned the sentence too.
- **The per-run cap was per attempt** — see the governor above.

And what was checked and is fine: with `--setting-sources=` empty, an
`.mcp.json` an agent plants in the workspace is not loaded on the next run, on
either harness.

Two things moved on:

- **R6** (an effort the model does not support) to M4. Both Claude harnesses
  pass every effort yaaf has; the first harness whose levels differ is Codex.
- **R1 and R2 cannot fire on a real harness yet** — both Claude harnesses take
  every Anthropic credential and can withhold every grant. They are built and
  attacked against registered harnesses that cannot, because the rules read
  the registry rather than names; `anthropic_api` (M2, API keys only) is the
  first real one R1 refuses.

**The boardroom chat stays on the SDK** whatever the CEO's seat names, and its
runs are pinned as `claude_agent_sdk` so the record says so — until M6.

### M2 — `anthropic_api`: the loop family's reference
- [ ] `LoopDriver`; the loop's built-ins (files, shell, fetch) under the same
      grants; web search as the API's server tool
- [ ] `ModelClient` for Messages; prompt caching
- [ ] The governor prices streamed usage and stops an attempt mid-flight
- [ ] Prices in `catalog.py`; the reported-vs-priced gap check against the
      SDK on the same models
- [ ] R3 with an attack (an unpriced model hired under USD)

### M3 — The first other providers: everything OpenAI-compatible
One `ModelClient` for the OpenAI-compatible chat API reaches more providers
than any other single piece of work, which is why it comes before any second
CLI. Each named host is a `Provider` entry — base URL, credential kinds,
prices — and nothing more.
- [ ] `openai_compatible` `ModelClient` on the M2 loop: per-call usage, the
      provider's error classes, tool calls in the OpenAI shape
- [ ] Providers: `deepseek`, `openrouter` (hundreds of models behind one
      credential), and a custom endpoint whose base URL lives in the credential
      — a local Ollama or vLLM server, priced at whatever the chairman says,
      including zero
- [ ] Project credentials per provider (a `project_llm_credentials` table;
      `billing_secret_id` migrates into it), R4, R5
- [ ] Settings, New Project, Founder and MCP pick credentials by provider
- [ ] Open question 6 (web search) decided, because most of these hosts have
      no search of their own
- [ ] PLAN.md's multi-provider line rewritten

**Done when:** a project hires a Claude research seat and a DeepSeek critic,
both metered against one cap, and the receipt says which spent what.

### M4 — Codex
- [ ] `openai` provider: `api_key` (USD), `chatgpt` (tokens, a file credential)
- [ ] `codex_cli` on `CliDriver` — the first CLI that is not Claude's, and the
      test of whether M1's pattern was general or merely Claude-shaped
- [ ] The data-source proxy in the stdio server, if Codex has no per-tool
      allowlist of its own
- [ ] R6, with the harness's own effort levels in its registry entry

### M5 — The long tail
- [ ] Gemini, by either road: `gemini_cli` on `CliDriver`, or a Gemini
      `ModelClient` on the loop
- [ ] `opencode` (several providers through one harness), `cursor` CLI
- [ ] Kimi, Qwen, Mistral, Grok and the rest: `Provider` entries on the M3
      client wherever they speak the OpenAI shape
- [ ] Each one is the "Adding a provider" table and nothing else. If one is
      not, the pattern is wrong and gets fixed before the next one lands.

### M6 — The conversations
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
6. **Web search on loop providers.** Under yaaf's loop a provider gets files,
   shell and fetch from yaaf, and search only where the provider runs one
   server-side — Anthropic, OpenAI and Gemini do; DeepSeek and most
   OpenAI-compatible hosts do not. Three answers:
   - **refuse** — a role granting `web` cannot be hired onto a harness that
     cannot search: R2's reasoning applied to a grant the harness cannot
     honour, rather than one it cannot withhold. Honest, and the default
     until something better is built;
   - **yaaf supplies search** — one platform built-in backed by a search API
     credential, so every loop provider gets the same tool. Each query costs
     money, and that cost must be booked against the external envelope like any
     other spend, or a run's price is understated;
   - **search as a data source** — the chairman connects a search MCP server
     and grants it to roles. Works today, but `web` stops meaning one thing.

   Proposed: refuse now; build the second when the first loop provider without
   search is actually wanted on a seat that needs it. Decided in M3.

## Appendix — invocation notes, to verify against pinned versions

Starting points, not facts: every CLI moves fast, and each line below gets
checked against the version the install script pins before its driver is
written.

- **Claude CLI — verified against 2.1.296 (M1).** `claude -p` with the flags
  `SdkDriver`'s options produce (read off the SDK's own argv builder, held to
  it by `test_the_cli_is_started_with_the_flags_the_sdk_itself_builds`), with
  `--system-prompt-file` and an MCP config *file* where the SDK passes strings,
  and the prompt on stdin. The stream carries per-message usage, the final
  cost and the rate-limit events the plan meter reads. Two facts that cost a
  run to learn: it refuses `bypassPermissions` as root — the sandbox runs as an
  unprivileged user, as it does for the SDK — and it ends a failed run with an
  `is_error` result and a non-zero exit, which is the rule both harnesses now
  fail a run by.
- **Codex CLI.** `codex exec --json`; MCP servers in its TOML config;
  `codex exec resume <id>`; reasoning effort by config; its own sandbox and
  approvals off (the container is the sandbox); API key by env, ChatGPT login
  as an `auth.json` file credential.
- **Gemini CLI.** Non-interactive prompt mode with JSON streaming output; MCP
  servers in its settings file; approvals off; API key by env.
- **OpenCode.** `opencode run` with JSON output; providers and MCP servers in
  its config.
