# Models and providers

yaaf runs on any of 23 model providers, including a model on your own machine.
Each seat on the org chart can run on a different one. Every run is metered
against one budget per project, whoever it paid.

## How it fits together

Three ideas:

1. **A credential** is a key for one provider, stored encrypted. You add it
   once, under Settings → Providers.
2. **A project pays** with one credential, and can add more for other
   providers.
3. **A role names a class of model, not a model.** `@frontier`, `@balanced` or
   `@fast`. Each provider says which of its models each class means. Change
   that once and every seat follows.

| Class | Meant for |
|---|---|
| `@frontier` | The most capable model the provider sells |
| `@balanced` | The one you'd run all day |
| `@fast` | Cheap and quick: triage, formatting, lookups |

A seat can also name an exact model (for example `claude-sonnet-5-5`), or a
model the catalog doesn't list as `provider/model`, like
`openrouter/qwen/qwen3-coder` or `ollama/llama3.1:8b`.

## Providers

| Provider | Key | Searches the web itself | Classes set for you |
|---|---|---|---|
| Anthropic | API key, or subscription token | Yes | Yes |
| OpenAI | API key, or ChatGPT login | Yes | Yes |
| Google (Gemini) | API key, or Google login | Yes | Yes |
| Google Vertex AI | Service account key + project and region | Yes | Picked from your key |
| Azure OpenAI | API key + resource name | Yes | No |
| AWS Bedrock | API key + region | No | No |
| DeepSeek | API key | No | Yes |
| Mistral | API key | No | Yes |
| xAI (Grok), Moonshot (Kimi), Z.ai (GLM), Qwen, Cohere | API key | No | Picked from your key |
| OpenRouter, Groq, Together, Fireworks, Cerebras, NVIDIA NIM, Hugging Face | API key | No | No |
| Ollama, LM Studio | Address (key optional) | No | No |
| Any OpenAI-compatible server | Address + key | No | No |

`yaaf providers` prints this list from the install itself, with what each
needs.

**"Classes set for you."** Yes: yaaf ships a lineup for it. "Picked from your
key": yaaf reads the models your key can reach and picks by price and age.
No: name the model behind each class yourself, in Settings → Models or with
`yaaf set-lineup PROVIDER CLASS MODEL`. A seat whose class has no model is
refused before it starts, with a message that says so.

## Add a key

**Dashboard:** Settings → Providers → Add. Then press **Check now** on it to test
it with one real call.

**CLI:**

```bash
yaaf create-llm-credential my-anthropic --provider anthropic
yaaf create-llm-credential local-llama --provider ollama --base-url http://localhost:11434/v1
```

The value is prompted for and never echoed. Then prove it works:

```bash
yaaf smoke <credential-id>            # one tiny real call
yaaf check-provider <credential-id>   # can these models do yaaf's work? Shows the cost first
```

## Give a project its keys

A project pays with one credential, set when you create it or later in the
project's settings. To run some seats on another provider, add a second:

```bash
yaaf pay-provider my-project openai <credential-id>
```

Every run still counts against the project's one budget.

## Subscriptions

Anthropic, OpenAI and Google also take a subscription login instead of an API
key. Runs then use your plan's allowance instead of dollars, and the
dashboard shows what is left on the plan.

| Provider | What you paste | Seats run on |
|---|---|---|
| Anthropic | Subscription token | Claude Agent SDK |
| OpenAI | ChatGPT login (`auth.json`) | Codex CLI |
| Google | Google login (`oauth_creds.json`) | Gemini CLI |

Check your provider's terms before running many agents on a personal plan.
Anthropic's consumer terms do not permit it: use subscription mode only for
small, single-user tinkering.

## Harnesses

The harness is the program that drives the model inside a run. yaaf picks one
from the credential; you can override it per role or per seat.

| Harness | Providers |
|---|---|
| Claude Agent SDK, Claude Code CLI | Anthropic |
| Codex CLI | OpenAI |
| Gemini CLI | Google |
| OpenCode CLI | OpenAI and most OpenAI-compatible providers |
| yaaf's own loop | Every provider, on its own API |

The rules are the same on every harness. A harness decides how a model is
called, never what it may do.

## Web search

Most roles browse. Anthropic, OpenAI, Google, Vertex and Azure search on their
own. For every other provider, set one search service for the whole install:

```bash
yaaf set-web-search brave           # key prompted
yaaf set-web-search searxng --base-url http://searxng:8080   # self-hosted, no key
```

Choices: Brave, Tavily, Exa, Serper, Perplexity, or a self-hosted SearXNG.
Each search is billed into the run at the price you set (`--price`). Without a
search service, a role that needs the web is refused on a provider that can't
search.

## Prices

yaaf meters every run in dollars, so it needs each model's price.

- **Automatic.** Prices come from the [LiteLLM price feed](https://github.com/BerriAI/litellm),
  refreshed daily while upkeep is on (the default). A price you set by hand is
  never overwritten.
- **By hand.** `yaaf set-model-price MODEL INPUT_USD OUTPUT_USD`, per million
  tokens. Use it for a model the feed doesn't know.
- **Local models** cost nothing: price them at zero, like
  `yaaf set-model-price ollama/llama3.1:8b 0 0`. `yaaf setup` does this for you.
- **No network to GitHub?** Turn upkeep off (`yaaf upkeep --disable`) and run
  `yaaf import-prices --snapshot --apply` to load the price list that ships
  with yaaf.

A model with no price can't be hired, because its runs couldn't be counted
against the budget.

## Limits

```bash
yaaf set-provider-ceiling anthropic 4    # at most 4 runs on Anthropic at once, install-wide
```

Use it to stay under a provider's rate limit when several projects share one
key.

## Local models

Point the credential at the server's address.

- **Native install:** `http://localhost:11434/v1` (Ollama) or
  `http://localhost:1234/v1` (LM Studio).
- **Docker install:** runs happen in containers, where `localhost` is the
  container itself. Put the model server on the sandbox network
  (`YAAF_SANDBOX_NETWORK`, default `yaaf_sandbox`) and use its name, like
  `http://ollama:11434/v1`.

Local models cost nothing, but small ones often fail at tool use. Run
`yaaf check-provider` before you give one real work.

## The Founder's key

The Founder (the assistant that drafts a project with you) uses its own key:

```bash
yaaf set-platform-key --from-credential <credential-id>
```

## Network

Both the server and the agent sandboxes need outbound HTTPS to every provider
you use. Vertex AI also needs `oauth2.googleapis.com` from the server, which
mints its tokens there; the service account key never enters a sandbox.

## Useful commands

| Command | What it does |
|---|---|
| `yaaf providers` | Every provider, and what it needs |
| `yaaf models` | What each class means on each provider |
| `yaaf discover-models CRED` | What models a key can reach, with prices |
| `yaaf repick-lineup` | Move a picked class onto a newer model |
| `yaaf set-default-provider` | The provider used before a project pays one |
| `yaaf spend --by provider` | What each provider cost |
