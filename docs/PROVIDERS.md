# Providers — any model, from the first screen

**Status: M7, the provider-neutral rebuild.** [RUNTIMES.md](RUNTIMES.md) made a
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

### 1. A role names a class of model, not a model

> **A role says how much model it needs. The project says whose.**

```
@frontier   the most capable model the provider sells
@balanced   the one you would run all day
@fast       cheap and quick: triage, formatting, lookups
```

A model reference (`RoleVersion.model`, `Agent.model_override`, a draft's
`model_overrides`, the Founder's model) is one of:

| Reference | Means |
|---|---|
| `claude-sonnet-5-5`, `gpt-6.1-sol` | that model — pinned, whatever pays the project |
| `openrouter/qwen/qwen3-coder`, `custom/llama3.1:8b` | a model the catalog does not list, named with its provider |
| `@balanced` | the project's primary provider's balanced model |
| `openai/@balanced` | OpenAI's balanced model, whatever the project's primary provider is |

**The project's primary provider is the provider its billing credential
pays.** A project with no credential yet (a draft) resolves against the
install's default provider (Settings → Models), which is Anthropic until an
administrator says otherwise. Swap the billing credential from Anthropic to
OpenAI and every class seat moves with it — which is what "switch this project
to OpenAI" means. A pinned model does not move: pinning is the chairman saying
*this* model, and R5 then asks for its provider to be paid too.

Each provider ships a **lineup** — `{frontier, balanced, fast}` → model — and
an install overrides any of it in Settings → Models. A provider yaaf ships no
lineup for (the long tail, whose lineups move monthly) has its lineup set by
the administrator, usually by picking from the provider's own live model list
(§8). A class seat on a provider with no model for that class is refused at
every door with that sentence and the place to fix it; it never falls through
to some other model.

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

**R6, restated.** Each model's catalog row lists the rungs it actually takes.
A seat's effort runs at **the lowest rung at or above the one it was hired at**,
and at the model's top rung when it asked for more than the model has. It never
runs *below* what it was hired at while a higher rung exists, which was the
point of the original rule ("`max` sent as `high` is a seat run cheaper than it
was hired at"). The rung it runs at is shown on the seat and handed to the
harness; `runs.effort` keeps what was asked for, so a report reads in yaaf's
vocabulary. An effort outside yaaf's vocabulary is still refused at every door.

The original rule refused instead of rounding because every model in the
catalog then shared Anthropic's ladder, so a mismatch meant a typo. With
provider-neutral roles it means "this role was written once and hired onto a
different vendor", which is the whole feature.

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
| `gemini` | Google, Vertex AI | `google_search` grounding | `thinkingConfig.thinkingLevel` |
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

- `yaaf_loop` offers `WebSearch` from the provider where the wire has a server
  tool (Anthropic, OpenAI, Gemini) and from the platform backend everywhere
  else;
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

---

## Migration — from what exists to the design

Nothing here rewrites history. Run rows keep the model, harness and provider
they ran on; role versions stay immutable.

| Change | How existing installs move |
|---|---|
| Roles name classes | the shipped `roles/*.json` move to `@frontier`/`@balanced`/`@fast` (Anthropic's lineup resolves them to exactly the models they named, so an Anthropic project runs identically). An install picks them up the way it picks up any role edit — `yaaf seed-roles` writes a new version; pinned projects keep theirs |
| `anthropic_api`, `openai_compatible` → `yaaf_loop` | migration `0054` rewrites `role_versions.harness` and `agents.harness_override`; the old names stay readable as aliases (export bundles, old clients, run history labels) |
| `Provider.default_harness` → `CredentialKind.default_harness` | in code; a seat with no harness named moves to its credential's default on its next run. OpenAI and Google API-key seats move from their CLIs to `yaaf_loop` — exact metering and full withholding, which is the improvement |
| `craft_model` → lineup `frontier` | in code |
| `DEFAULT_MODEL`, `DEFAULT_FOUNDER_MODEL` → classes | an install that set a Founder model keeps it; one that never did resolves `@frontier` on its credential's provider, which is `claude-opus-5-5` on Anthropic — unchanged |
| R6 rounds upward | in code; no seat that ran before runs differently (every catalog model then took every effort its seats used) |
| `model_prices.source` | migration `0054`; existing rows read as hand-set |
| lineups, default provider, search backend, provider ceilings | platform settings; absent means the shipped defaults |

**What deliberately did not move: `Project.billing_secret_id`.** A
from-scratch design still has a primary credential — the one that sets the
project's meter (R4) and names its primary provider (§1) — and that is what
this column is. It is also a public contract: MCP drafts, export bundles and
setup files all carry it by that name. Renaming it would change three hundred
lines and every client for no behaviour a chairman could see; the semantics,
which are what was Claude-shaped, did move.

---

## The rules, as M7 leaves them

R1–R7 are RUNTIMES.md's. M7 changes two and adds one.

| # | Rule | Change |
|---|---|---|
| R2 | A grant the harness cannot withhold or honour is refused | `web` is honoured by the platform search backend where the harness and the provider cannot search |
| R6 | An effort runs at its model's published rung — never lower while a higher rung exists | was: refuse any effort the model does not list |
| R8 | **A class seat on a provider with no model for that class is refused** — at every door R5 is at, and at the lease — naming the class, the provider and Settings → Models | new |

Each ships with its attack in `tests/selftest_invariants/`.
