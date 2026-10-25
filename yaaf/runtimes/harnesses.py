"""What runs the agent loop inside the sandbox, as data.

**Imports nothing from `yaaf`.** The sandbox image carries single files, not
the package, and the harness that runs there will need the same facts the
control plane holds about it. One copy, readable from both sides, is the point;
`tests/selftest_invariants/test_runtime_registries.py` holds the import rule.

A harness is transport. It decides how a model is driven, never what the model
may do — nothing in an entry here is enforcement (docs/RUNTIMES.md).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Harness:
    name: str
    label: str
    #: "sdk" | "cli" | "loop" | "mock"
    family: str
    #: Providers whose models this harness can serve. Empty means any — only
    #: the scripted backend, which serves no model at all.
    providers: tuple[str, ...]
    #: Credential kinds it can authenticate with, as "provider:mode". Empty
    #: means any, for the same reason.
    accepts: tuple[str, ...]
    #: The role grants it can actually take away — "files", "bash", "web". A
    #: harness that cannot run without a shell cannot honour a role that
    #: grants none, and is refused for it rather than quietly widening it.
    withholds: tuple[str, ...] = ("files", "bash", "web")
    #: "reported" — the harness bills each run itself (the Agent SDK, Claude
    #: Code); "priced" — yaaf prices it from the catalog, so a model with no
    #: price cannot run on it (R3).
    cost: str = "reported"
    #: The efforts it can express (R6). None: the model's own ladder governs
    #: (the catalog's `efforts`), or yaaf's names pass as they are (the Claude
    #: harnesses, whose names are yaaf's). Empty: it has no effort dial at all,
    #: and the seat's is unused.
    efforts: tuple[str, ...] | None = None
    #: Where a `web` grant's search comes from (R2): "own" — the harness
    #: searches; "loop" — the provider's wire where it has a server tool, else
    #: the platform's search backend (yaaf's loop); "platform" — only the
    #: platform's backend, over the yaaf tools' MCP server; "none" — nothing
    #: does, so a role granting `web` is refused.
    search: str = "own"
    #: The name the model sees for a tool `tool` of MCP server `server` —
    #: what a prompt naming a data source's tools renders through.
    tool_ref: str = "mcp__{server}__{tool}"
    #: Whether it can hold the boardroom conversation (R7): a session it can
    #: resume turn after turn. Off unless shown — the chat lane is the one
    #: place the chairman waits on a seat in real time.
    conversation: bool = False


#: Every provider yaaf's loop reaches: each speaks a wire `yaaf.runtimes.wire`
#: knows. Listed rather than derived, because this file reads nothing from the
#: provider registry; `test_runtime_registries.py` holds the two to each other.
LOOP_PROVIDERS = (
    "anthropic", "openai", "google", "vertex", "azure_openai", "bedrock", "deepseek",
    "mistral", "xai", "openrouter", "groq", "together", "fireworks", "cerebras", "cohere",
    "nvidia", "huggingface", "zai", "moonshot", "qwen", "ollama", "lmstudio", "custom",
)

#: Every provider OpenCode can reach: its OpenAI-compatible client, a bearer
#: key (or none, for a local server), at the provider's chat address. Not
#: OpenRouter, whose per-call cost never reaches OpenCode's stream; not the
#: providers whose address or key is shaped otherwise (Azure, Bedrock, Vertex).
OPENCODE_PROVIDERS = (
    "openai", "deepseek", "mistral", "xai", "groq", "together", "fireworks", "cerebras",
    "cohere", "nvidia", "huggingface", "zai", "moonshot", "qwen", "ollama", "lmstudio",
    "custom",
)

HARNESSES: dict[str, Harness] = {h.name: h for h in (
    Harness(
        name="claude_agent_sdk",
        label="Claude Agent SDK",
        family="sdk",
        providers=("anthropic",),
        accepts=("anthropic:api_key", "anthropic:subscription"),
        conversation=True,
    ),
    # The same CLI the SDK drives, driven directly: same model, same
    # credentials, same tools under the same names. The reference every other
    # CLI harness is built from, which is why it changes nothing else.
    Harness(
        name="claude_cli",
        label="Claude Code CLI (claude -p)",
        family="cli",
        providers=("anthropic",),
        accepts=("anthropic:api_key", "anthropic:subscription"),
        conversation=True,
    ),
    # yaaf's own agent loop, on whichever wire the seat's provider speaks
    # (yaaf.runtimes.wire): no CLI underneath, the built-in tools yaaf's own,
    # every call priced as it is billed. API keys only — a subscription or a
    # login is a CLI credential. The loop is yaaf's; the wire is the
    # provider's, so a provider of a known shape is a registry row and this
    # entry's two lists (docs/PROVIDERS.md §3). Search where the provider's
    # wire has it, and from the platform's own backend everywhere else.
    Harness(
        name="yaaf_loop",
        label="yaaf agent loop (the provider's own API)",
        family="loop",
        providers=LOOP_PROVIDERS,
        accepts=tuple(f"{p}:api_key" for p in LOOP_PROVIDERS),
        cost="priced",
        search="loop",
        conversation=True,
    ),
    # The Codex CLI (`codex exec --json`), driven like the Claude CLI: the
    # first CLI that is not Claude's. It reports tokens but no dollars, so
    # yaaf prices it; it can take away its shell and its search, and a role's
    # file grant without a shell is served by yaaf's own file tools over MCP.
    Harness(
        name="codex_cli",
        label="Codex CLI",
        family="cli",
        providers=("openai",),
        accepts=("openai:api_key", "openai:subscription"),
        # Its shell and its search go when a role leaves them off; its file
        # editor does not — the models it knows best are handed `apply_patch`
        # by Codex's own presets, shell or no shell — so a role that withholds
        # files is refused for it (R2) rather than quietly handed one.
        withholds=("bash", "web"),
        cost="priced",
        efforts=("low", "medium", "high", "xhigh", "max"),
        conversation=True,
    ),
    # The Gemini CLI (`gemini --output-format stream-json`), Google's agent.
    # Tokens but no dollars, so yaaf prices it; every grant withholdable by
    # an admin policy; its own search. Its thinking dial is two levels.
    Harness(
        name="gemini_cli",
        label="Gemini CLI",
        family="cli",
        providers=("google",),
        accepts=("google:api_key", "google:subscription"),
        cost="priced",
        efforts=("low", "high"),
        tool_ref="mcp_{server}_{tool}",
        conversation=True,
    ),
    # The OpenCode CLI (`opencode run --format json`): one CLI for every
    # provider on the OpenAI-compatible chat API, its own file tools and
    # shell, yaaf's tools over MCP. Its search is its own hosted service's,
    # so it searches for nobody here — the platform's backend, served as a
    # yaaf tool, is the only search a seat on it gets. OpenRouter is not among its providers:
    # the cost OpenRouter states per call never reaches OpenCode's stream,
    # and an OpenRouter model needs no price to be hired.
    Harness(
        name="opencode",
        label="OpenCode CLI",
        family="cli",
        providers=OPENCODE_PROVIDERS,
        accepts=tuple(f"{p}:api_key" for p in OPENCODE_PROVIDERS),
        cost="priced",
        efforts=(),
        search="platform",
        tool_ref="{server}_{tool}",
        conversation=True,
    ),
    # The scripted backend that drives the real API at $0: CI, the goldens and
    # the fire drill. It reads no credential and calls no model.
    Harness(
        name="mock",
        label="Scripted agents (tests and the fire drill)",
        family="mock",
        providers=(),
        accepts=(),
    ),
)}

#: What `integrations.runtime` said before seats had harnesses of their own.
#: It still means "run each seat on its own harness"; only "mock" overrides.
LEGACY_RUNTIME = "agent_sdk"

#: Names a harness used to go by. Each still resolves — in an export bundle
#: written before the rename, from an old client, on a run row that ran under
#: it — and none is ever written again (migration 0055 rewrote the stored
#: ones). `anthropic_api` and `openai_compatible` were yaaf's loop on two wires
#: before the wire became the provider's fact.
ALIASES = {"anthropic_api": "yaaf_loop", "openai_compatible": "yaaf_loop",
           LEGACY_RUNTIME: "claude_agent_sdk"}


def canonical(name: str | None) -> str | None:
    """A harness name as it is spelled now."""
    return ALIASES.get(name, name) if name else name


def find(name: str | None) -> Harness | None:
    """The harness a name — current or former — refers to."""
    return HARNESSES.get(canonical(name) or "")
