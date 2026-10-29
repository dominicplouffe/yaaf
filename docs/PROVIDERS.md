# Providers — any model, from the first screen

**Status: M7 is built** — every section below, with the attacks listed at the
end; what remains is `yaaf smoke` on a deployment with real keys, which nothing
offline can do. [RUNTIMES.md](RUNTIMES.md) made a
seat *able* to run on another vendor: the registries, the harnesses, the rules
R1–R7. This document is what it takes for a chairman who has never held an
Anthropic key to install yaaf, paste an OpenAI, Gemini, Mistral or Bedrock
credential, and get a working project out of the first screen — with nothing
in the product quietly assuming Claude underneath.

It is written the way the work was approached: *if yaaf had supported every
model from its first commit, what would the design be?* — and then, for each
place today's code differs, how it moves there. Where the answer was "the
existing code is already right", it says so and why.

---

## What was still Claude-shaped

RUNTIMES.md's registries were complete; the *content* above them was not.

| Assumption | Where it lived | What it cost |
|---|---|---|
| A role names a vendor's model | all 38 shipped roles pin `claude-*` | every crew is an Anthropic crew; a project paid by an OpenAI key refuses every seat (R5) until each is overridden by hand |
| Effort is Anthropic's five-rung ladder | R6 refuses an effort a model does not list | 10 shipped roles run at `xhigh`; Gemini takes `low`/`high`, so a Google project refuses them all |
| The loop's wire is Anthropic's or OpenAI-chat | two loop harnesses, one per wire | OpenAI and Gemini keys reached only through their CLIs: no native search, no reasoning dial, Codex cannot withhold file edits |
| Only Anthropic models have prices | R3 refuses an unpriced model under a dollar meter | every other API-key seat refused until an admin types prices in by hand |
| Search is the provider's server tool | R2's honour half | 35 of 38 roles grant `web`; every OpenAI-compatible provider and OpenCode refuse them |
| The Founder's model is Claude's | `DEFAULT_FOUNDER_MODEL = "claude-opus-5-5"` | a platform OpenAI key needs its model set by hand; "Regrade goal" ignored it entirely |
| The screens say "Claude" | role editor, Founding key form, project Settings copy, credential fallbacks | the UI cannot express what the server can |

None of these is a missing registry row. Each is a place where a *decision*
was written in one vendor's vocabulary. The fix for each is the same move:
name the decision in yaaf's vocabulary and let the provider translate.

---

## The design

### 1. Each seat names its model; the app supplies the default

> **A seat runs the model it names, on that model's provider. The app's
> default fills in for a seat that names none.**

The app's default provider is the platform credential — the key the Founder,
the goal grader and the provider checks run on, named in Settings → Providers.
Settings → Models names the app's default model and harness, which a seat
runs on when neither it nor its role names one. A project pays whichever
providers its seats run on: its billing credential, plus one credential per
other provider — attached from the seat's own Change dialog, in the same press
that moves the seat (or under Settings → Money). The app's key pays a
provider the project does not, and never becomes the project's billing
credential. A role says how hard its seat thinks — the effort — and may pin a
model; the seat can override model, effort and harness.

A model reference (`RoleVersion.model`, `Agent.model_override`, a draft's
`model_overrides`, the Founder's model) is one of:

| Reference | Means |
|---|---|
| `claude-sonnet-5-5`, `gpt-6.1-sol` | that model — pinned, paid by the project's credential for its provider |
| `openrouter/qwen/qwen3-coder`, `custom/llama3.1:8b` | a model the catalog does not list, named with its provider |
| `@balanced` | the balanced model of the project's billing provider |
| `openai/@balanced` | OpenAI's balanced model, wherever it is paid |

**The class references above are the legacy of the one-provider-per-project
era** and are no longer offered anywhere; a role carrying the shipped default
class has no opinion, and the app's default decides. Classes still resolve —
against the install's default provider, through each provider's **lineup**
(`{frontier, balanced, fast}` → model, overridable in Settings → Models) — so
an install that has run since before this rule behaves exactly as it always
has. A pinned model does not move: pinning is the chairman saying *this*
model, and the doors then ask for its provider to be paid.

`stakes: high` lifts a producing seat to `@frontier` of the provider it is
already on — the class rank replaces the old per-provider `craft_model`, and
the lift stays within the provider exactly as before.

**Resolved once, at lease and at every door, by one function**
(`yaaf.runtimes.seats.seat_for`, given a `ModelContext` from
`yaaf.core.model_policy`). The run row pins the concrete model it resolved to,
so history never shows `@balanced`.

**Why a sigil in the existing field, not a new column.** Every surface that
carries a model — export bundles, MCP drafts, the CLI's setup files, the API —
already carries `model` as a string. A reference that is still a string flows
through all of them untouched, and a reader that forgets to resolve gets a
string that is obviously not a model (`@balanced`) rather than a silent wrong
answer. A test holds that nothing outside the resolver hands a reference to a
provider.

### 2. Effort is yaaf's vocabulary; each model publishes its ladder

yaaf's five efforts — `low`, `medium`, `high`, `xhigh`, `max` — are how much a
role is worth thinking about. Models expose that dial differently: Claude
takes all five, GPT takes most, Gemini 3 takes `low` and `high`, Gemini 2.5
and most open-weights models have no dial at all.

**R6, restated — two cases, because they are two different statements.**

- **A pinned model is held to its own list, exactly as before.** Pinning is
  the chairman naming *this* model, so an effort it does not take is a mistake
  worth a refusal at the door, with the list it does take. The original rule's
  attacks stand unchanged.
- **A class seat runs at its model's published rung.** The role was written
  for no model in particular, so its effort is a statement of intent in yaaf's
  vocabulary, and the seat runs at **the lowest rung at or above the one it was
  hired at** — at the model's top rung when it asked for more than the model
  has. It never runs *below* what it was hired at while a higher rung exists,
  which was the point of the original rule ("`max` sent as `high` is a seat run
  cheaper than it was hired at"). A model with one rung, or none (Gemini 2.5,
  most open-weights models), is sent no effort at all.

Either way the rung a seat runs at is what the harness is handed, and
`runs.effort` keeps what was asked for, so a report reads in yaaf's
vocabulary. An effort outside yaaf's vocabulary is refused at every door.

The original rule refused instead of rounding because every model in the
catalog then shared Anthropic's ladder, so a mismatch meant a typo. For a
class seat a mismatch means "this role was written once and hired onto a
different vendor", which is the whole feature; for a pinned one it still means
a typo.

### 3. One loop, many wires

> **The loop is yaaf's; the wire is the provider's.**

`anthropic_api` and `openai_compatible` were the same loop with a different
client. In a design that started multi-provider they are one harness —
**`yaaf_loop`** — and the provider registry says which wire each provider
speaks:

| Protocol | Providers | Native search | Effort |
|---|---|---|---|
| `anthropic` | Anthropic | `web_search` server tool | `output_config.effort` |
| `openai_responses` | OpenAI, Azure OpenAI | `web_search` tool | `reasoning.effort` |
| `gemini` | Google, Vertex AI | a call of its own grounded in Google Search | `thinkingConfig.thinkingLevel` |
| `bedrock` | AWS Bedrock | — | — |
| `openai_chat` | DeepSeek, OpenRouter, Mistral, xAI, Groq, Together, Fireworks, Cerebras, Perplexity, Cohere, NVIDIA, Hugging Face, Z.ai, Moonshot, Qwen, Ollama, LM Studio, any custom server | — (platform search, §6) | `reasoning_effort` where the model takes it |

The wire adapters live in **`yaaf/runtimes/wire.py`**, which imports nothing
from `yaaf` — the `harnesses.py` discipline — so the *same file* runs in the
sandbox (copied into the image), in the Founder's process, in the credential
live check, in model discovery and in `yaaf smoke`. One copy of "how to talk to
Gemini", readable from both sides.

The Anthropic wire keeps the official SDK inside the sandbox: it is exact
about cache breakpoints and mid-stream errors, the governor's
abandon-at-prompt depends on `message_start`, and none of that is worth
re-deriving. Every other wire is plain `httpx`, because each is a request and
a stream of JSON and an SDK per vendor is a pin per vendor.

**Which harness a seat runs on by default is a fact about the credential, not
the provider.** An OpenAI API key runs on `yaaf_loop` (exact metering, every
grant withholdable, native search); a ChatGPT login can only pay the Codex CLI,
so it defaults there. `CredentialKind.default_harness` replaces
`Provider.default_harness`. Anthropic's kinds keep the Agent SDK as their
default — it is the harness this product has run in production longest.

CLIs remain first-class choices — a seat can still be put on Codex, the Gemini
CLI or OpenCode — they are simply no longer the only road to a vendor.

**Every wire was checked against its vendor's own definition**, since no
provider could be called from where this was built: request bodies were
validated by machine against OpenAI's OpenAPI spec, the `anthropic` and
`google-genai` SDK types, the Gemini and Vertex discovery documents and
botocore's Bedrock models, and the old bodies were shown to fail the same
check. What that caught — Responses tools without `strict`, OpenAI-compatible
hosts that stream tool calls off-spec, Gemini schema fields Gemini refuses,
finish reasons that read as "done", single-page model lists, DeepSeek's
thinking mode refusing a tool loop that drops `reasoning_content` — is fixed
and pinned by `test_wire_protocols.py`. What a spec cannot say (a vendor's
exact wording for a too-long prompt) is matched conservatively and named as
such there.

**A run outgrowing its model's window makes room rather than dying.** Claude
Code compacts its own history; a provider's API does not. yaaf's loop knows
the model's input window (imported with its price, typed beside it, or read
from the shipped snapshot; `model_prices.context_window`) and, past 80% of it,
first replaces old tool output with a line saying what it was — the call and
its pairing stay, since every provider refuses a result without its call —
then, if that is not enough, has the model summarise the history onto the
original prompt, billed as the call it is. A provider's own refusal for length
(the wire's `context` kind) triggers the same, so a model with no known
window is covered too. A history that will not fit even summarised ends the
run, naming the reason, rather than looping.

### 4. Providers as data, including how to authenticate

A provider row now carries everything every screen and every wire needs:

```python
Provider(
    name="azure_openai", label="Azure OpenAI",
    protocol="openai_responses", auth="api-key",          # header style
    credential_base_url=True,                              # the resource's own address
    base_url_hint="https://<resource>.openai.azure.com/openai/v1",
    kinds=(CredentialKind("api_key", meter="usd", default_harness="yaaf_loop",
                          label="API key", placeholder="…", help="Keys and Endpoint …"),),
    lineup=None, search=False, plan_meter=False, …)
```

| Field | Why it is a field |
|---|---|
| `protocol` | picks the wire (§3) for the loop, the Founder, the live check and discovery |
| `auth` | `bearer`, `x-api-key`, `api-key`, `none` (a local server), `google_service_account` (Vertex mints a short-lived token from the key file) |
| `credential_base_url`, `base_url_hint` | an address that is the chairman's to give: a custom server, Azure's resource, Bedrock's region |
| `lineup` | §1 |
| `console_url`, kind `placeholder`/`help` | the credential form explains where a key comes from, per provider, from the registry |
| `plan_meter` | only a provider that reports a plan meter shows one (Anthropic's subscription, today) |
| `local` | a server on the chairman's network that needs no key (Ollama, LM Studio) |

Providers added in M7, each a row: **Azure OpenAI, AWS Bedrock, Google Vertex
AI, Groq, Together AI, Fireworks AI, Cerebras, Perplexity, Cohere, NVIDIA NIM,
Hugging Face, Z.ai, Ollama, LM Studio** — beside Anthropic, OpenAI, Google,
DeepSeek, OpenRouter, Mistral, xAI, Moonshot, Qwen and the custom endpoint.

### 5. Prices are data that moves; yaaf ships the mechanism

The rule stands: **yaaf ships only prices it checked against the vendor's own
published list**, because a budget trusts the number it is given. That is still
only Anthropic's from where this was built — and third-party price lists were
again found contradicting one another.

What changes is how an install gets the rest. **Settings → Model prices →
Import** reads a price feed (the LiteLLM `model_prices_and_context_window.json`
format; the address is a setting, so an install can point it at its own
mirror), maps the feed's entries onto yaaf's model names, and shows a preview:
every model it would price, at what, from which source, and every hand-set
price it would *not* touch. Nothing is written until an administrator applies
it. Imported rows carry their source and date (`model_prices.source`), so the
prices table says where each number came from, and the existing gap check
still compares every reported bill against the price it was metered at.

A dated snapshot of the feed's rows for the catalog's models ships in the
repo (`yaaf/runtimes/price_snapshot.json`) for installs that cannot reach the
internet — imported the same way, labelled as a snapshot, never applied on its
own. `yaaf prices import` does the same from a shell.

### 6. Search is a platform capability, metered like a model call

R2's honour half refused a `web` role on any harness that could not search.
Open question 6 in RUNTIMES.md named the alternative — *yaaf supplies search* —
and deferred it "until the first loop provider without search that is actually
wanted". With provider-neutral roles that is every provider without a server
tool.

An install may configure one **search backend** (Settings → Web search):
Brave Search, Tavily, Exa, Serper, or a self-hosted SearXNG, with its key and
its price per query. Then:

- `yaaf_loop` searches on the provider's own wire where it has a server tool
  (Anthropic, OpenAI, Azure), answers `WebSearch` with a call grounded in
  Google Search on Gemini — which does not take a server search tool beside
  function declarations on every model — and offers it from the platform
  backend everywhere else;
- CLI harnesses with no search of their own (OpenCode) get it as a yaaf tool
  over the same stdio MCP server that carries the rest;
- every query is billed into the run at the configured price, so search spend
  is in the run's cost and under its cap (the governor stops a run whose
  searches cross it), exactly like Anthropic's per-search charge;
- the backend's key never reaches the agent's shell, like the model's.

R2 now refuses `web` only where nothing can search: no server tool, no
harness search, and no platform backend.

### 7. The Founder runs on whatever pays the platform

`DEFAULT_FOUNDER_MODEL` becomes `@frontier`, resolved against the platform
credential's provider — so pasting a Gemini key is enough. The Founder's own
conversation and the goal grader run on the Claude Code CLI for Anthropic (a
subscription token is a CLI credential) and on `wire.py` for every provider
with a protocol — Gemini natively, Bedrock, Azure, Vertex included — rather
than only on the OpenAI-compatible chat shape. Its credential menu, its prompt
and its draft checks read providers and lineups; none of them counts "the
Claude credentials" any more.

### 8. Operations: knowing it works before 3am

- **Readiness, before the first refusal.** Every half-set-up provider was
  already refused somewhere — a class with no model (R8), a model with no price
  (R3), a browsing role where nothing searches (R2), a model the key no longer
  lists, a model that failed its check. `GET /admin/settings/providers/readiness`
  (yaaf/core/readiness.py) reads them all at once, per provider the install
  pays, each with its fix — usually one click: import these prices, connect a
  search backend, pick a model for this class. Settings → Providers, the
  connect flow, Home's first-run checklist and the New Project form all read it,
  so a chairman sees the list before any door says no. It refuses nothing; the
  doors still do.
- **The provider check: can this model do yaaf's work?** A smoke call proves a
  key answers; it cannot say whether the model behind it can drive a seat. The
  check (yaaf/core/provider_check.py, "Run check" on a credential, `yaaf
  check-provider`) runs two batteries through the seat's own wire, graded
  mechanically — never by another model:
  *probes* (an instruction followed exactly, a nested tool schema filled, a
  tool result used, parallel calls, the effort dial, search) and *five small
  agent tasks* over an in-memory workspace (aggregate a paginated tool, find a
  fact across files, edit a config, recover from a refused write, follow an
  output format to the letter). Nothing a model writes is executed; every
  grader reads state. It runs in the background, writes each step as it
  finishes, stops on a dead key rather than spending eleven calls on it, says
  what it will cost first, and is kept per model — Settings → Models compares
  every model the install has checked, which is the evidence a lineup is
  chosen from.
- **Keeping models and prices current** (yaaf/core/model_upkeep.py). Opt-in,
  because it reaches out daily: each key's model list is re-read and
  remembered, and prices that came from the feed are refreshed — never one an
  administrator typed, never the catalog's checked ones, each change audited
  with what it was. A seat whose model its key no longer lists is named on its
  project's health checklist before its next run fails; a list older than two
  weeks is not held against anybody.
- **Live check per wire.** "Check" on a credential calls that provider's own
  model list through `wire.py` — the same address and header a run will use.
- **Model discovery.** `GET /admin/providers/{name}/models?credential=…` lists
  what the provider actually serves for that key; the lineup editor and the
  model pickers offer it, so a Groq lineup is picked from Groq's list, not typed.
- **`yaaf smoke`.** One real, trivial call per credential (or one named): it
  sends a one-line prompt and a tool, checks the reply, the usage and the
  price, and prints what a run on that credential would cost per thousand
  tokens. RUNTIMES.md's unticked "a live seat on …" lines are each one
  `yaaf smoke` on a deployment.
- **Concurrency per provider.** `max_concurrent_runs` protected "the provider
  rate limit every project on the box shares"; with many providers that is many
  limits. Settings → Models takes an optional ceiling per provider, and the
  lease leaves a run queued while its provider is at its ceiling (RUNTIMES.md
  open question 2).

### 9. Failover: an outage moves class seats, it does not stop the project

A provider outage used to stop every project on it: each run failed
transiently, was retried, failed again, and the work stood still until a
person re-pointed billing by hand. A project can now name a **fallback
credential** — another provider's — and the runs
themselves are the signal (yaaf/core/breakers.py):

- three transient failures in a row on a credential open its breaker;
- while it is open, every project it bills that holds a fallback resolves its
  bare classes on the fallback's provider — through
  `model_policy.context`, which every reader resolves through, so the lease,
  the seat doors, the boardroom and the health checklist all follow at once;
- after a cooling-off (five minutes, doubling to an hour) the provider is
  tried again: a run that works closes it, one that fails opens it for longer.

What never moves: a seat pinned to a model, or a class named with its
provider (`openai/@fast`) — somebody chose that provider on purpose. A dead
key is not an outage (`auth` stays the three-strikes pause's). The fallback
door refuses a fallback on the same provider (it fails with it) and one any
class seat could not run on — resolved, priced,
able to do what its role grants — because a fallback found wanting during an
outage is no fallback; the health checklist re-asks that question every day.
The chairman is told after the fact (a record, or an alarm when there was
nothing to move to); the move itself already happened, by state.

The fallback pays only while seats are moved onto it — held always, it would
also pay any seat pinned to its provider, which nobody named it for — and a
project pays each provider with one credential, so the fallback and a
"pays this provider too" credential cannot disagree about who pays. It
travels wherever its siblings do: a new project's body, a setup
file (by name), a clone, the MCP `create_project` and `project_configure`
tools, the CLI's import and the Founder's drafts.

The breaker is written in a savepoint and never raised: a breaker row that
cannot be written is a missed signal, never a run's booking rolled back.

---

## Migration — from what exists to the design

Nothing here rewrites history. Run rows keep the model, harness and provider
they ran on; role versions stay immutable.

| Change | How existing installs move |
|---|---|
| Roles name classes | the shipped `roles/*.json` move to `@frontier`/`@balanced`/`@fast` (Anthropic's lineup resolves them to exactly the models they named). `deploy/update_single_server.sh` moves an install's existing library roles onto their class (`POST /admin/roles/{slug}/adopt-class`): a new version identical but for its model, only where the role still names the model its class means on Anthropic — one moved by hand is left alone, and an administrator's prompt edits ride along. Teams stay pinned, and nothing is offered where adopting would change nothing a team runs (`yaaf.core.role_versions`): on a project paying Anthropic, or on a team whose every seat names its own model |
| `anthropic_api`, `openai_compatible` → `yaaf_loop` | migration `0055` rewrites `role_versions.harness` and `agents.harness_override`; the old names stay readable as aliases (export bundles, old clients, run history labels) |
| `Provider.default_harness` → `CredentialKind.default_harness` | in code for every new seat: an OpenAI or Google API key now defaults to `yaaf_loop` (exact metering, full withholding). An existing seat that ran on the Codex or Gemini CLI that way keeps it — migration `0055` pins it as the seat's harness override — so no running project changes engine on deploy; the chairman moves it when they choose. An upgrade rehearsal (projects built on `main`, the database upgraded) leased every existing seat on the same model, harness, effort and price, `anthropic_api` renamed `yaaf_loop` aside |
| `craft_model` → lineup `frontier` | in code |
| `DEFAULT_MODEL`, `DEFAULT_FOUNDER_MODEL` → classes | an install that set a Founder model keeps it; one that never did resolves `@frontier` on its credential's provider, which is `claude-opus-5-5` on Anthropic — unchanged |
| R6 translates a class seat's effort | in code; no seat that ran before runs differently — a pinned seat is held exactly as it was, and the shipped roles' classes resolve on Anthropic to models that take every effort |
| `model_prices.source`, `model_prices.context_window` | migration `0055`; existing rows read as hand-set, with no window (the snapshot's figure is used where it has one) |
| `provider_checks`, `credential_breakers`, `projects.fallback_secret_id` | migration `0055`; empty — no check run, no breaker open, no fallback named |
| lineups, default provider, search backend, provider ceilings | platform settings; absent means the shipped defaults |

**What deliberately did not move: `Project.billing_secret_id`.** A
from-scratch design still has a primary credential — the one that
sets the project's meter (R4) and names its primary provider (§1) — and that is
what this column is. It is also carried by that name through MCP drafts,
export bundles and setup files. Renaming it would change three hundred
lines and every client for no behaviour a chairman could see; the semantics,
which are what was Claude-shaped, did move.

---

## The rules, as M7 leaves them

R1–R7 are RUNTIMES.md's. M7 changes two and adds one.

| # | Rule | Change |
|---|---|---|
| R2 | A grant the harness cannot withhold or honour is refused | `web` is honoured by the platform search backend where the harness and the provider cannot search |
| R6 | A pinned model's efforts are held to its list; a class seat runs at its model's published rung — never lower while a higher rung exists | was: refuse any effort the model does not list, pinned or not |
| R8 | **A class seat on a provider with no model for that class is refused** — at every door R5 is at, and at the lease — naming the class, the provider and Settings → Models | new |

Each ships with its attack in `tests/selftest_invariants/`.

---

## Where it lives, and what attacks it

| Piece | Code | Attacked in |
|---|---|---|
| Classes, lineups, R8, R6 rungs, the stakes lift, provider ceilings | `yaaf/runtimes/catalog.py`, `seats.py`, `yaaf/core/model_policy.py`, `daemon.lease_and_prepare` | `test_model_classes.py` |
| Every wire through the loop: usage, tools, replay, errors, search, token refresh | `yaaf/runtimes/wire.py`, `yaaf/runner/loop_entry.py`, `loop_harness.py` | `test_wire_protocols.py`, `test_loop_harness.py`, `test_openai_compatible_harness.py` |
| The provider registry, the harness registry, the image | `yaaf/runtimes/providers.py`, `harnesses.py`, `deploy/sandbox/Dockerfile` | `test_runtime_registries.py`, `test_harness_contract.py` |
| Live checks, discovery, the credential door, Vertex minting, search settings and R2 | `yaaf/server/api/admin.py` (`live_check`), `settings.py`, `yaaf/core/model_tokens.py`, `web_search.py` | `test_provider_operations.py` |
| OpenCode's search through the tool server | `yaaf/runner/yaaf_tools.py`, `opencode_cli_entry.py`, `sandbox_entry.SearchLedger` | `test_opencode_harness.py` |
| The price feed, the snapshot, the smoke call | `yaaf/core/price_feed.py`, `yaaf/runtimes/price_snapshot.json`, `settings.smoke_credential` | `test_price_feed.py` |
| The Founder and the grader on any provider | `yaaf/founder/loop.py`, `tools.py`, `yaaf/core/platform.py` | `test_founder_providers.py`, `test_conversations.py` |
| Compaction against the model's window | `yaaf/runner/loop_harness.py`, `yaaf/core/prices.context_window` | `test_loop_harness.py`, `test_price_feed.py` |
| The provider check | `yaaf/core/provider_check.py`, `provider_check_runs.py` | `test_provider_check.py` |
| Readiness | `yaaf/core/readiness.py` | `test_readiness.py` |
| Keeping models and prices current | `yaaf/core/model_upkeep.py`, `janitor_all` | `test_model_upkeep.py` |
| Failover | `yaaf/core/breakers.py`, `model_policy.failover`, `daemon._record_breaker`, `admin.set_fallback_credential` | `test_failover.py` |

## Not built, and why

- **Claude on Bedrock or Vertex through the Claude CLI.** Claude Code can run
  on either cloud, which would let the Agent SDK serve `bedrock/` and
  `vertex/` Claude models. It needs those clouds' model ids priced and the CLI's
  cloud switches verified against a live account; until then a Claude model on
  Bedrock runs on `yaaf_loop` over Converse like any other Bedrock model.
- **AWS access keys (SigV4).** Bedrock is paid with a Bedrock API key, which
  AWS issues for exactly this. Signing requests with an IAM access key is a
  second credential kind on the same provider, and waits for an install that
  cannot use the first.
- **A plan meter for a ChatGPT plan.** Codex reads its rate-limit windows off
  the Responses API's headers, but the streams this repo records were made
  against a stand-in API that sends none — a meter built from them would be a
  guess, which this design refuses everywhere else. `Provider.plan_meter` is
  the seat it plugs into.
- **Mixed meters** (RUNTIMES.md open question 1) — unchanged, and for the
  reason given there.
