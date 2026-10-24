"""Who sells the tokens, and every fact about reaching and paying them.

A provider is data: where it is, which wire it speaks (yaaf.runtimes.wire), how
a key is presented, the credential kinds it takes, the lineup a role's model
class resolves to on it, and the few facts that change what the platform may
allow — whether it searches, states its costs, reports a plan meter. Every
screen, every door and every wire reads this file; none names a provider
(docs/PROVIDERS.md).

A credential kind is one way to pay a provider. Two names carry the meter for
every provider: a kind billed in dollars is `api_key`, a kind metered in tokens
is `subscription`, whatever the vendor calls it. `Project.billing_mode` is read
as the project's meter in a dozen places, and one project has one meter (R4) —
so the mode *is* the meter, and a second provider is a second row here, never
a third mode everywhere. Which harness a seat runs on by default is the kind's:
an OpenAI API key runs on yaaf's own loop, a ChatGPT login can only pay the
Codex CLI.

A model the catalog does not list names its provider: `openrouter/qwen/…`,
`custom/llama3.1:8b`, `bedrock/amazon.nova-pro-v1:0`. That is how a seat on one
of hundreds of models behind one credential resolves without a row for each
(yaaf.runtimes.catalog).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

#: The harness every API key defaults to: yaaf's own loop, on the provider's
#: own wire (yaaf.runtimes.harnesses).
LOOP = "yaaf_loop"


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
    #: The harness a seat paid this way runs on when nothing names another.
    default_harness: str = LOOP
    #: What the credential form calls the value, shows as its placeholder,
    #: and says about where to get one.
    label: str = "API key"
    placeholder: str = ""
    help: str = ""
    #: A value that is a file (a login, a service account), pasted whole.
    multiline: bool = False


@dataclass(frozen=True)
class Provider:
    name: str
    label: str
    #: Ordered: the first is what a mode nobody registered is read as.
    kinds: tuple[CredentialKind, ...]
    #: Lower-case fragments that mean the credential is dead rather than the
    #: run unlucky. The daemon pauses a project on three in a row.
    failure_markers: tuple[str, ...]
    #: The wire yaaf's loop, the Founder, the live check and discovery speak
    #: to it (yaaf.runtimes.wire.PROTOCOLS). None: reached only through a CLI.
    protocol: str | None = None
    #: How a key is presented (wire.AUTH_STYLES).
    auth: str = "bearer"
    base_url: str | None = None
    #: The address is part of the credential — a server the chairman runs, an
    #: Azure resource, a Bedrock region, a Vertex project.
    credential_base_url: bool = False
    #: How that address is spelled, with its blanks in braces — what the
    #: credential form asks for and composes.
    base_url_template: str | None = None
    #: What each model class resolves to on this provider (docs/PROVIDERS.md
    #: §1). None or a missing class: the install's lineup must name it, or a
    #: class seat on this provider is refused (R8).
    lineup: Mapping[str, str] | None = None
    #: Whether its wire searches the web itself, for yaaf's loop to offer.
    search: bool = False
    #: What one of its searches costs, in USD — billed into the run beside the
    #: tokens. Set at or above the provider's published rate: over-metering is
    #: the safe direction for a budget.
    search_usd: float = 0.0
    #: Whether its API states each call's cost. Then a model needs no price to
    #: be metered: the bill is reported, like a CLI's.
    reports_cost: bool = False
    #: Whether a subscription on it reports a plan meter worth drawing.
    plan_meter: bool = False
    #: A server on the chairman's own network, which takes no key.
    local: bool = False
    #: The credential is a key file the control plane mints short-lived tokens
    #: from; the file itself never enters a sandbox (docs/PROVIDERS.md §4).
    mints_tokens: bool = False
    #: Where a key for it is made, for the credential form to link.
    console_url: str | None = None
    #: Headers every call carries beyond its key.
    headers: Mapping[str, str] = field(default_factory=dict)

    def kind(self, mode: str | None) -> CredentialKind:
        """The kind a mode names. A mode nobody registered is read as the first
        kind — the API key — which is how the runner's two-line split always
        read it; `billing_mode` is validated at every door, so only a
        hand-edited row ever gets here."""
        for kind in self.kinds:
            if kind.mode == mode:
                return kind
        return self.kinds[0]

    def default_harness(self, mode: str | None = None) -> str:
        return self.kind(mode).default_harness


# ── shape checks ────────────────────────────────────────────────────────────────


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


def _not_anthropic(plaintext: str) -> str | None:
    if plaintext.startswith("sk-ant-"):
        return ("that is an Anthropic credential — add it under the Anthropic "
                "provider, not this one")
    if plaintext.lstrip().startswith("{"):
        return ("that looks like a file (a login or a service account), not an API key — "
                "paste the key itself")
    return None


def _no_shape(plaintext: str) -> str | None:
    """A vendor whose keys have no stable shape to check. The live check is
    the first call, and a dead key is read as `auth` there like any other."""
    return None


def _openai_key_shape(plaintext: str) -> str | None:
    if plaintext.lstrip().startswith("{"):
        return ("that looks like a ChatGPT login (auth.json), not an API key — add it as "
                "the subscription kind instead")
    return _not_anthropic(plaintext)


def _chatgpt_login_shape(plaintext: str) -> str | None:
    login = _json(plaintext)
    if not isinstance(login, dict) or not isinstance(login.get("tokens"), dict):
        return ("a ChatGPT login is the auth.json `codex login` writes (in ~/.codex) — paste "
                "the whole file; it has a \"tokens\" section")
    return None


def _gemini_key_shape(plaintext: str) -> str | None:
    if plaintext.lstrip().startswith("{"):
        return ("that looks like a Google login (oauth_creds.json), not an API key — add it as "
                "the subscription kind instead, or under Vertex AI if it is a service account")
    return _not_anthropic(plaintext)


def _google_login_shape(plaintext: str) -> str | None:
    login = _json(plaintext)
    if not isinstance(login, dict) or not login.get("refresh_token"):
        return ("a Google login is the oauth_creds.json the Gemini CLI writes (in ~/.gemini) "
                "once you sign in — paste the whole file; it has a \"refresh_token\"")
    return None


def _service_account_shape(plaintext: str) -> str | None:
    account = _json(plaintext)
    if (not isinstance(account, dict) or account.get("type") != "service_account"
            or not account.get("private_key") or not account.get("client_email")):
        return ("a Vertex AI credential is a Google Cloud service account key — the JSON file "
                "IAM → Service accounts → Keys → Add key downloads; it has \"type\": "
                "\"service_account\", a private_key and a client_email")
    return None


def _json(plaintext: str):
    try:
        return json.loads(plaintext)
    except ValueError:
        return None


def _api_key(provider_label: str, env_var: str, *, shape=_not_anthropic, help: str = "",
             placeholder: str = "") -> CredentialKind:
    return CredentialKind(mode="api_key", meter="usd", env_var=env_var,
                          bills=f"{provider_label} API key, billed in USD", shape_error=shape,
                          help=help, placeholder=placeholder)


# ── the providers ───────────────────────────────────────────────────────────────

ANTHROPIC = Provider(
    name="anthropic",
    label="Anthropic",
    protocol="anthropic",
    auth="x-api-key",
    base_url="https://api.anthropic.com/v1",
    kinds=(
        CredentialKind(mode="api_key", meter="usd", env_var="ANTHROPIC_API_KEY",
                       bills="Anthropic API key, billed in USD",
                       shape_error=_anthropic_api_key_shape,
                       default_harness="claude_agent_sdk",
                       placeholder="sk-ant-api…",
                       help="Create one in the Anthropic console under API keys."),
        # A subscription token is a CLI credential, not a Messages API one: the
        # CLI owns the OAuth exchange. Which is why every harness that accepts
        # one runs the CLI underneath.
        CredentialKind(mode="subscription", meter="tokens",
                       env_var="CLAUDE_CODE_OAUTH_TOKEN",
                       bills="Claude subscription, metered in tokens",
                       shape_error=_anthropic_subscription_shape,
                       default_harness="claude_agent_sdk",
                       label="Subscription token", placeholder="sk-ant-oat…",
                       help="Run `claude setup-token` and paste what it prints."),
    ),
    lineup={"frontier": "claude-opus-5-5", "balanced": "claude-sonnet-5-5",
            "fast": "claude-haiku-4-5"},
    # The Messages API's own server tool (yaaf's loop offers it as web_search),
    # at the catalog's checked per-search price.
    search=True,
    search_usd=0.01,
    plan_meter=True,
    console_url="https://console.anthropic.com/settings/keys",
    failure_markers=(
        "not logged in", "please run /login", "invalid x-api-key",
        "authentication_error", "invalid bearer token", "oauth token has expired",
        "credit balance is too low",
    ),
)

#: OpenAI. An API key runs on yaaf's loop over the Responses API — native
#: search, a reasoning dial, every grant withholdable; a ChatGPT plan is a
#: login (the auth.json `codex login` writes) metered in tokens, and like a
#: Claude subscription a CLI credential: only the Codex CLI can use it.
OPENAI = Provider(
    name="openai",
    label="OpenAI",
    protocol="openai_responses",
    base_url="https://api.openai.com/v1",
    kinds=(
        _api_key("OpenAI", "OPENAI_API_KEY", shape=_openai_key_shape, placeholder="sk-…",
                 help="Create one at platform.openai.com under API keys."),
        CredentialKind(mode="subscription", meter="tokens", env_var="CODEX_AUTH_JSON",
                       bills="ChatGPT plan (Codex login), metered in tokens",
                       shape_error=_chatgpt_login_shape, default_harness="codex_cli",
                       label="ChatGPT login (auth.json)", multiline=True,
                       help="Run `codex login`, then paste the whole ~/.codex/auth.json."),
    ),
    lineup={"frontier": "gpt-6-astra", "balanced": "gpt-6.1-sol", "fast": "gpt-6-luna"},
    search=True,
    # Its published web search rates run $10–$25 per thousand calls by model;
    # metered at the top of that range.
    search_usd=0.025,
    console_url="https://platform.openai.com/api-keys",
    failure_markers=("incorrect api key provided", "unexpected status 401",
                     "unexpected status 403", "invalid_api_key"),
)

#: Google's Gemini API. A key runs on yaaf's loop over the native API —
#: Google Search grounding, the thinking dial; a Google account login (the
#: oauth_creds.json the Gemini CLI writes) is metered in tokens and pays only
#: that CLI.
GOOGLE = Provider(
    name="google",
    label="Google (Gemini)",
    protocol="gemini",
    auth="x-goog-api-key",
    base_url="https://generativelanguage.googleapis.com/v1beta",
    kinds=(
        _api_key("Gemini", "GEMINI_API_KEY", shape=_gemini_key_shape, placeholder="AIza…",
                 help="Create one in Google AI Studio under Get API key."),
        CredentialKind(mode="subscription", meter="tokens", env_var="GEMINI_OAUTH_CREDS",
                       bills="Google account login (Gemini CLI), metered in tokens",
                       shape_error=_google_login_shape, default_harness="gemini_cli",
                       label="Google login (oauth_creds.json)", multiline=True,
                       help="Sign in with the Gemini CLI, then paste ~/.gemini/oauth_creds.json."),
    ),
    lineup={"frontier": "gemini-3.1-pro-preview", "balanced": "gemini-3.8-flash",
            "fast": "gemini-2.5-flash"},
    search=True,
    # Grounding with Google Search is billed per grounded prompt; metered at
    # the higher of its published per-prompt rates.
    search_usd=0.035,
    console_url="https://aistudio.google.com/apikey",
    failure_markers=("api key not valid", "request had invalid authentication credentials",
                     "manual authorization is required", "api_key_invalid"),
)

#: Gemini on Google Cloud. The credential is a service account key, which
#: never enters a sandbox: the control plane mints hour-long tokens from it and
#: a run refreshes its own through the agent API.
VERTEX = Provider(
    name="vertex",
    label="Google Vertex AI",
    protocol="gemini",
    kinds=(CredentialKind(mode="api_key", meter="usd", env_var="VERTEX_ACCESS_TOKEN",
                          bills="Vertex AI service account, billed in USD",
                          shape_error=_service_account_shape, label="Service account key",
                          multiline=True,
                          help="IAM → Service accounts → Keys → Add key (JSON). The account "
                               "needs the Vertex AI User role."),),
    credential_base_url=True,
    base_url_template=("https://{region}-aiplatform.googleapis.com/v1/projects/{project}"
                       "/locations/{region}/publishers/google"),
    mints_tokens=True,
    # Grounding with Google Search, as on the Gemini API: the same call on
    # the same wire, at Vertex's grounding price per prompt.
    search=True,
    search_usd=0.035,
    console_url="https://console.cloud.google.com/iam-admin/serviceaccounts",
    failure_markers=("request had invalid authentication credentials", "permission_denied",
                     "unauthenticated"),
)

#: OpenAI's models on an Azure resource: the Responses API at the resource's
#: v1 address, a key in its own header, and a deployment's name as the model.
AZURE_OPENAI = Provider(
    name="azure_openai",
    label="Azure OpenAI",
    protocol="openai_responses",
    auth="api-key",
    kinds=(_api_key("Azure OpenAI", "AZURE_OPENAI_API_KEY",
                    help="Your resource's Keys and Endpoint page. A model here is a "
                         "deployment's name: azure_openai/<deployment>."),),
    credential_base_url=True,
    base_url_template="https://{resource}.openai.azure.com/openai/v1",
    search=True,
    search_usd=0.025,
    console_url="https://portal.azure.com/#view/Microsoft_Azure_ProjectOxford/"
                "CognitiveServicesHub/~/OpenAI",
    # Azure's own words for a dead key. Never a bare status code: "401" is in
    # a context-length error that counts 140,123 tokens too.
    failure_markers=("access denied due to invalid subscription key",
                     "invalid subscription key"),
)

#: Models on AWS Bedrock, through the Converse API, paid with a Bedrock API key
#: (a bearer token) for the region it was issued in.
BEDROCK = Provider(
    name="bedrock",
    label="AWS Bedrock",
    protocol="bedrock",
    kinds=(_api_key("AWS Bedrock", "AWS_BEARER_TOKEN_BEDROCK",
                    help="Bedrock console → API keys. Models are Bedrock model or "
                         "inference-profile ids: bedrock/<model id>."),),
    credential_base_url=True,
    base_url_template="https://bedrock-runtime.{region}.amazonaws.com",
    console_url="https://console.aws.amazon.com/bedrock/home#/api-keys",
    failure_markers=("unrecognizedclientexception", "the security token included in the "
                     "request is invalid", "invalid api key"),
)


def _chat(name: str, label: str, base_url: str, env_var: str, markers: tuple[str, ...],
          console_url: str, **extra) -> Provider:
    """A provider on the OpenAI chat shape: an address, a key and its words
    for a dead one. Each is a row and nothing else (docs/RUNTIMES.md, M3)."""
    help_text = extra.pop("help", f"Create a key in the {label} console.")
    return Provider(name=name, label=label, protocol="openai_chat", base_url=base_url,
                    kinds=(_api_key(label, env_var, help=help_text),), failure_markers=markers,
                    console_url=console_url, **extra)


#: DeepSeek keeps two names pointed at its current chat and reasoning models.
DEEPSEEK = _chat("deepseek", "DeepSeek", "https://api.deepseek.com/v1", "DEEPSEEK_API_KEY",
                 ("authentication fails", "insufficient balance"),
                 "https://platform.deepseek.com/api_keys",
                 lineup={"frontier": "deepseek-reasoner", "balanced": "deepseek-chat",
                         "fast": "deepseek-chat"})

#: Hundreds of models from every vendor behind one key: there is no one ladder
#: to climb, so no lineup ships — an install names one, from its list.
OPENROUTER = _chat("openrouter", "OpenRouter", "https://openrouter.ai/api/v1",
                   "OPENROUTER_API_KEY", ("no auth credentials found", "insufficient credits"),
                   "https://openrouter.ai/settings/keys",
                   # It states each call's cost, so its models need no row.
                   reports_cost=True)

#: Mistral keeps `-latest` names pointed at its current models of each size.
MISTRAL = _chat("mistral", "Mistral", "https://api.mistral.ai/v1", "MISTRAL_API_KEY",
                # Mistral's 401 is a bare {"message": "Unauthorized"}.
                ("http 401: unauthorized",), "https://console.mistral.ai/api-keys",
                lineup={"frontier": "mistral-large-latest",
                        "balanced": "mistral-medium-latest", "fast": "mistral-small-latest"})

#: The long tail (docs/RUNTIMES.md, M5; docs/PROVIDERS.md §4): each speaks the
#: OpenAI shape, so each is a row and nothing else. None ships a lineup: their
#: lineups move monthly and no model could be checked, so an install picks one
#: from the provider's own list (Settings → Models). Each address is the
#: vendor's international one; a key issued for another region is a custom
#: endpoint at that region's address.
XAI = _chat("xai", "xAI (Grok)", "https://api.x.ai/v1", "XAI_API_KEY",
            ("incorrect api key provided", "no credits"), "https://console.x.ai")
MOONSHOT = _chat("moonshot", "Moonshot (Kimi)", "https://api.moonshot.ai/v1", "MOONSHOT_API_KEY",
                 ("invalid authentication",), "https://platform.moonshot.ai/console/api-keys")
QWEN = _chat("qwen", "Qwen (Alibaba Model Studio)",
             "https://dashscope-intl.aliyuncs.com/compatible-mode/v1", "DASHSCOPE_API_KEY",
             ("incorrect api key provided", "invalid_api_key"),
             "https://modelstudio.console.alibabacloud.com/?tab=playground#/api-key")
GROQ = _chat("groq", "Groq", "https://api.groq.com/openai/v1", "GROQ_API_KEY",
             ("invalid api key",), "https://console.groq.com/keys")
TOGETHER = _chat("together", "Together AI", "https://api.together.xyz/v1", "TOGETHER_API_KEY",
                 ("invalid api key", "unauthorized"), "https://api.together.ai/settings/api-keys")
FIREWORKS = _chat("fireworks", "Fireworks AI", "https://api.fireworks.ai/inference/v1",
                  "FIREWORKS_API_KEY", ("invalid api key", "unauthorized"),
                  "https://fireworks.ai/account/api-keys")
CEREBRAS = _chat("cerebras", "Cerebras", "https://api.cerebras.ai/v1", "CEREBRAS_API_KEY",
                 ("wrong api key", "invalid api key"), "https://cloud.cerebras.ai")
COHERE = _chat("cohere", "Cohere", "https://api.cohere.ai/compatibility/v1", "COHERE_API_KEY",
               ("invalid api token",), "https://dashboard.cohere.com/api-keys")
NVIDIA = _chat("nvidia", "NVIDIA NIM", "https://integrate.api.nvidia.com/v1", "NVIDIA_API_KEY",
               ("authentication failed", "unauthorized"), "https://build.nvidia.com")
HUGGINGFACE = _chat("huggingface", "Hugging Face", "https://router.huggingface.co/v1",
                    "HF_TOKEN", ("invalid credentials", "invalid username or password"),
                    "https://huggingface.co/settings/tokens")
ZAI = _chat("zai", "Z.ai (GLM)", "https://api.z.ai/api/paas/v4", "ZAI_API_KEY",
            ("authentication", "token expired or incorrect"), "https://z.ai/manage-apikey/apikey-list")

CUSTOM = Provider(
    name="custom",
    label="Custom endpoint (OpenAI-compatible)",
    protocol="openai_chat",
    kinds=(CredentialKind(mode="api_key", meter="usd", env_var="CUSTOM_LLM_API_KEY",
                          bills="Custom endpoint key, priced in USD at the rates set in "
                                "Settings",
                          shape_error=_no_shape,
                          help="Any server speaking the OpenAI chat API: vLLM, LiteLLM, a "
                               "gateway. Its address goes with the key."),),
    failure_markers=("custom endpoint rejected the credential",),
    # Its address is the chairman's to give.
    credential_base_url=True,
    base_url_template="{base_url}",
)


def _local(name: str, label: str, example: str, env_var: str) -> Provider:
    """A model server on the chairman's own network. No key, no bill — priced
    at whatever the install says, usually zero (Settings → Model prices) — and
    its address is the credential: the sandbox reaches it over the sandbox
    network, so it is the server's name there, not localhost."""
    return Provider(
        name=name, label=label, protocol="openai_chat", auth="none", local=True,
        kinds=(CredentialKind(mode="api_key", meter="usd", env_var=env_var,
                              bills=f"{label} (local), priced in USD at the rates set in "
                                    "Settings",
                              shape_error=_no_shape, label="Key (optional)",
                              help=f"{label} takes no key. Its address must be reachable "
                                   f"from the sandbox network, e.g. {example}."),),
        failure_markers=(f"{name} rejected the request",),
        credential_base_url=True, base_url_template="{base_url}")


OLLAMA = _local("ollama", "Ollama", "http://ollama:11434/v1", "OLLAMA_API_KEY")
LMSTUDIO = _local("lmstudio", "LM Studio", "http://lmstudio:1234/v1", "LMSTUDIO_API_KEY")

PROVIDERS: dict[str, Provider] = {p.name: p for p in (
    ANTHROPIC, OPENAI, GOOGLE, VERTEX, AZURE_OPENAI, BEDROCK, DEEPSEEK, MISTRAL, XAI,
    OPENROUTER, GROQ, TOGETHER, FIREWORKS, CEREBRAS, COHERE, NVIDIA, HUGGINGFACE, ZAI,
    MOONSHOT, QWEN, OLLAMA, LMSTUDIO, CUSTOM,
)}

#: Every credential row and every model the catalog does not know belongs here
#: when nothing says otherwise — what the `secrets.provider` column defaults to,
#: and where a class resolves on an install that never chose a default
#: provider (yaaf.core.model_policy).
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


def spec(seller: Provider, mode: str | None, base_url: str | None = None) -> dict:
    """What a wire needs about a provider, as JSON — for a sandbox that cannot
    import this file (`YAAF_PROVIDER`) and for the control plane's own calls
    alike. The address is the credential's where the provider's is."""
    return {"name": seller.name, "protocol": seller.protocol, "auth": seller.auth,
            "base_url": (base_url if seller.credential_base_url else seller.base_url) or "",
            "key_env": seller.kind(mode).env_var, "reports_cost": seller.reports_cost,
            "search": seller.search, "search_usd": seller.search_usd,
            "mints_tokens": seller.mints_tokens, "headers": dict(seller.headers),
            "failure_markers": list(seller.failure_markers)}
