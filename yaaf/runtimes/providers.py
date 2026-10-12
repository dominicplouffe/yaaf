"""Who sells the tokens, and every fact about paying them.

A provider owns its credential kinds. A kind is one way to pay that provider —
Anthropic takes an API key, metered in dollars, or a Claude subscription token,
metered in tokens — and it knows the variable it is injected as, the sentence a
malformed value earns, and how it bills. Everything that used to hardcode the
two Anthropic modes reads them from here instead.

Two names carry the meter for every provider: a kind billed in dollars is
`api_key`, a kind metered in tokens is `subscription`, whatever the vendor
calls it. `Project.billing_mode` is read as the project's meter in a dozen
places, and one project has one meter (R4) — so the mode *is* the meter, and a
second provider is a second row here, never a third mode everywhere.

A model the catalog does not list names its provider: `openrouter/qwen/…`,
`custom/llama3.1:8b`. That is how a seat on one of hundreds of models behind
one credential resolves without a row for each (yaaf.runtimes.catalog).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class CredentialKind:
    #: The value `Secret.llm_mode` and `Project.billing_mode` hold.
    mode: str
    #: "usd" | "tokens" — the unit the project's budget is kept in.
    meter: str
    env_var: str
    #: How a credential of this kind is described wherever one is chosen.
    bills: str
    shape_error: Callable[[str], str | None]


@dataclass(frozen=True)
class Provider:
    name: str
    label: str
    #: The harness a seat on this provider runs on when nothing names another.
    default_harness: str
    #: Ordered: the first is what a mode nobody registered is read as.
    kinds: tuple[CredentialKind, ...]
    #: What `stakes: high` lifts a producing seat to — within this provider,
    #: never across one. A dial labelled "how much this matters" that moved a
    #: seat to another vendor would be two decisions wearing one label. None
    #: for a provider with no catalog of its own to rank: it lifts nothing.
    craft_model: str | None
    #: Lower-case fragments that mean the credential is dead rather than the
    #: run unlucky. The daemon pauses a project on three in a row.
    failure_markers: tuple[str, ...]
    #: Where an API harness reaches it (the OpenAI-compatible chat API). None
    #: for a provider reached otherwise, or whose address is the credential's.
    base_url: str | None = None
    #: The address is part of the credential — a server the chairman runs.
    credential_base_url: bool = False
    #: Whether its API searches the web itself, for yaaf's loop to offer. A
    #: provider that does not cannot honour a role's `web` grant on the loop.
    search: bool = False
    #: Whether its API states each call's cost. Then a model needs no price in
    #: the catalog to be metered: the bill is reported, like a CLI's.
    reports_cost: bool = False

    def kind(self, mode: str | None) -> CredentialKind:
        """The kind a mode names. A mode nobody registered is read as the first
        kind — the API key — which is how the runner's two-line split always
        read it; `billing_mode` is validated at every door, so only a
        hand-edited row ever gets here."""
        for kind in self.kinds:
            if kind.mode == mode:
                return kind
        return self.kinds[0]


def _anthropic_api_key_shape(plaintext: str) -> str | None:
    if plaintext.startswith("sk-ant-oat"):
        return ("that credential is a subscription OAuth token, not an API key "
                "(sk-ant-api…). Paste a key from the Anthropic console — or switch "
                "to subscription mode.")
    return None


def _anthropic_subscription_shape(plaintext: str) -> str | None:
    if not plaintext.startswith("sk-ant-oat"):
        return ("that credential is not a subscription token (sk-ant-oat…). Run "
                "`claude setup-token` and paste the result — or switch to API-key mode.")
    return None


ANTHROPIC = Provider(
    name="anthropic",
    label="Anthropic",
    default_harness="claude_agent_sdk",
    kinds=(
        CredentialKind(mode="api_key", meter="usd", env_var="ANTHROPIC_API_KEY",
                       bills="Anthropic API key, billed in USD",
                       shape_error=_anthropic_api_key_shape),
        # A subscription token is a CLI credential, not a Messages API one: the
        # CLI owns the OAuth exchange. Which is why every harness that accepts
        # one runs the CLI underneath.
        CredentialKind(mode="subscription", meter="tokens",
                       env_var="CLAUDE_CODE_OAUTH_TOKEN",
                       bills="Claude subscription, metered in tokens",
                       shape_error=_anthropic_subscription_shape),
    ),
    craft_model="claude-opus-5-5",
    # The Messages API's own server tool (yaaf's loop offers it as web_search).
    search=True,
    failure_markers=(
        "not logged in", "please run /login", "invalid x-api-key",
        "authentication_error", "invalid bearer token", "oauth token has expired",
        "credit balance is too low",
    ),
)


def _no_shape(plaintext: str) -> str | None:
    """A vendor whose keys have no stable shape to check. The live check is
    the first call, and a dead key is read as `auth` there like any other."""
    return None


def _not_anthropic(plaintext: str) -> str | None:
    if plaintext.startswith("sk-ant-"):
        return ("that is an Anthropic credential — add it under the Anthropic "
                "provider, not this one")
    return None


#: Reached through yaaf's own loop, on the OpenAI-compatible chat API. Each is
#: a base URL, a credential kind and a few facts; the client is shared
#: (docs/RUNTIMES.md, M3). Prices are not shipped for any of them: yaaf ships
#: only prices it could check against the provider's own published list, and
#: an install sets the rest in Settings → Model prices.
DEEPSEEK = Provider(
    name="deepseek",
    label="DeepSeek",
    default_harness="openai_compatible",
    kinds=(CredentialKind(mode="api_key", meter="usd", env_var="DEEPSEEK_API_KEY",
                          bills="DeepSeek API key, billed in USD",
                          shape_error=_not_anthropic),),
    craft_model="deepseek-reasoner",
    failure_markers=("authentication fails", "insufficient balance"),
    base_url="https://api.deepseek.com/v1",
)

OPENROUTER = Provider(
    name="openrouter",
    label="OpenRouter",
    default_harness="openai_compatible",
    kinds=(CredentialKind(mode="api_key", meter="usd", env_var="OPENROUTER_API_KEY",
                          bills="OpenRouter API key, billed in USD",
                          shape_error=_not_anthropic),),
    # Hundreds of models from every vendor behind one key: there is no one
    # ladder to climb, so the stakes dial leaves an OpenRouter seat where it is.
    craft_model=None,
    failure_markers=("no auth credentials found", "insufficient credits"),
    base_url="https://openrouter.ai/api/v1",
    # It states each call's cost, so its models need no row of their own.
    reports_cost=True,
)

CUSTOM = Provider(
    name="custom",
    label="Custom endpoint (OpenAI-compatible)",
    default_harness="openai_compatible",
    kinds=(CredentialKind(mode="api_key", meter="usd", env_var="CUSTOM_LLM_API_KEY",
                          bills="Custom endpoint key, priced in USD at the rates set in "
                                "Settings",
                          shape_error=_no_shape),),
    craft_model=None,
    failure_markers=("custom endpoint rejected the credential",),
    # A local Ollama or vLLM server: its address is the chairman's to give.
    credential_base_url=True,
)

PROVIDERS: dict[str, Provider] = {p.name: p for p in (ANTHROPIC, DEEPSEEK, OPENROUTER, CUSTOM)}

#: Every credential row and every model the catalog does not know belongs here.
#: Until a second provider exists that is not a guess but the only answer, and
#: it is what the `secrets.provider` column defaults to.
DEFAULT_PROVIDER = ANTHROPIC.name

#: Every mode any provider registers — what the credential forms validate
#: against. A union: two providers may both say "api_key".
MODES: tuple[str, ...] = tuple(dict.fromkeys(k.mode for p in PROVIDERS.values()
                                             for k in p.kinds))
MODE_PATTERN = f"^({'|'.join(MODES)})$"


def provider(name: str | None) -> Provider:
    """The provider a name refers to; an empty name is the default provider.
    An unknown name is a KeyError — a row naming a provider this install does
    not have is not one to bill against quietly."""
    return PROVIDERS[name or DEFAULT_PROVIDER]


def credential_env(provider_name: str | None, mode: str | None, value: str) -> dict[str, str]:
    """The environment a credential reaches a model under — one variable."""
    return {provider(provider_name).kind(mode).env_var: value}


def shape_error(provider_name: str | None, mode: str, plaintext: str) -> str | None:
    """Whether this credential can possibly be what its mode says it is. A mode
    the provider does not register has no shape to check."""
    p = provider(provider_name)
    if mode not in {k.mode for k in p.kinds}:
        return None
    return p.kind(mode).shape_error(plaintext)


def failure_markers() -> tuple[str, ...]:
    """Every provider's dead-credential fragments. The daemon reads a run's
    summary against all of them: which provider a failed run was on is a fact
    the summary does not always carry, and a marker one vendor uses is not
    one another uses for something healthy."""
    return tuple(dict.fromkeys(m for p in PROVIDERS.values() for m in p.failure_markers))
