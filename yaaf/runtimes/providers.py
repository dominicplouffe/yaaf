"""Who sells the tokens, and every fact about paying them.

A provider owns its credential kinds. A kind is one way to pay that provider —
Anthropic takes an API key, metered in dollars, or a Claude subscription token,
metered in tokens — and it knows the variable it is injected as, the sentence a
malformed value earns, and how it bills. Everything that used to hardcode the
two Anthropic modes reads them from here instead.
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
    #: seat to another vendor would be two decisions wearing one label.
    craft_model: str
    #: Lower-case fragments that mean the credential is dead rather than the
    #: run unlucky. The daemon pauses a project on three in a row.
    failure_markers: tuple[str, ...]

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
    failure_markers=(
        "not logged in", "please run /login", "invalid x-api-key",
        "authentication_error", "invalid bearer token", "oauth token has expired",
        "credit balance is too low",
    ),
)

PROVIDERS: dict[str, Provider] = {p.name: p for p in (ANTHROPIC,)}

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
