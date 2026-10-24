"""One way to talk to a model, for everything above a project.

The Founder and the goal grader both run through the Claude Code CLI, driven
by ``claude-agent-sdk`` in this process. That is not an implementation detail
worth hiding — it is the whole point. Every agent in a yaaf project already
runs on the CLI with a credential in its environment
(:func:`yaaf.runner.daemon._billing_env`), and a subscription token is a CLI
credential rather than a Messages API one. Reaching for the Messages API here
meant a second way to authenticate, and the second way did not work with the
key the chairman already had.

What is different from an agent run is only the isolation: an agent gets a
Docker sandbox with a workspace, because it acts on the world. The Founder
drafts a project and calls six read-only tools, so it runs in-process with
every built-in tool switched off.

That is the Anthropic road. A platform credential of any other provider runs
both callers on that provider's own wire instead (`yaaf.founder.loop`,
docs/PROVIDERS.md §7) — chosen by the model's provider, which a door holds to
the credential's (`refusal`, and `yaaf.server.api.settings`).
"""

from __future__ import annotations

import logging
import os
import tempfile

log = logging.getLogger("yaaf.founder")

#: The CLI subprocess inherits this process's whole environment — the SDK
#: builds it from ``os.environ`` and merges ``options.env`` over the top. Two
#: things follow, and both are handled by blanking rather than by hoping.
#:
#: Anything named here is emptied before the credential is set, so:
#:
#: 1. **The platform's own secrets do not travel.** The control plane holds the
#:    master encryption key, the admin token and the database URL. None of them
#:    is any use to a model that may only read two libraries, and yaaf's whole
#:    position on secrets is that a run gets what it was granted and nothing
#:    else. A subprocess is not an exception to that.
#: 2. **Exactly one credential can win.** With a stray ANTHROPIC_API_KEY or an
#:    ambient Claude session on the box, a chairman's wrong key would quietly
#:    work — on somebody else's credentials — and the failure would surface
#:    somewhere else entirely, as a bill.
_SCRUB_PREFIXES = ("YAAF_",)
_SCRUB_EXACT = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR",
)

#: A turn that has not settled by here is looping rather than working. The
#: drafting turn legitimately takes several (search the library, grade the
#: goal, propose, rehearse), so this is a stop, not a budget.
MAX_TURNS = 24


def on_sdk(model: str) -> bool:
    """Whether a turn on `model` runs on the Claude Code CLI — an Anthropic
    model — rather than on the chat API (`yaaf.founder.loop`)."""
    from yaaf.runtimes import catalog
    from yaaf.runtimes.providers import ANTHROPIC

    return catalog.provider_of(model) == ANTHROPIC.name


def refusal(provider_name: str, mode: str) -> str | None:
    """Why a credential cannot pay for the Founder and the grader, or None.

    They run in this process: on the Claude Code CLI for Anthropic, which takes
    an API key or a subscription; for any other provider on its own wire,
    which takes an API key. A ChatGPT login or a Google login is neither — each
    pays its own CLI and nothing else."""
    from yaaf.runtimes.providers import ANTHROPIC, provider

    if provider_name == ANTHROPIC.name:
        return None
    seller = provider(provider_name)
    if mode != "api_key":
        return (f"the Founder runs in this process, on {seller.label}'s API, which "
                f"takes an API key — a {seller.kind(mode).bills.split(',')[0]} pays only "
                "its own CLI. Use an API key")
    if seller.protocol is None:
        return f"{seller.label} has no API the Founder can run on"
    return None


class FounderModelError(RuntimeError):
    """A model turn failed in a way the chairman needs told about.

    Carries `kind` so the API can choose a status code without re-parsing an
    error string: "auth", "cli_missing", "busy", "stalled", "looping", or
    "unknown".
    """

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


def _classify(exc: BaseException, stderr: str = "") -> FounderModelError:
    """Turn whatever came back into something a person can act on.

    The failure this exists for: a chairman pasted a Claude Code subscription
    key, the model refused it, and the API answered 500 with an empty body. He
    could not have diagnosed that from anything the product told him.

    `stderr` matters more than the exception. When the CLI rejects a
    credential it exits 1 and the SDK raises ``Command failed with exit code 1
    … Check stderr output for details`` — which is the same empty answer in a
    different costume. The reason is on stderr, so that is what gets read.
    """
    from claude_agent_sdk import CLIConnectionError, CLINotFoundError

    if isinstance(exc, CLINotFoundError):
        return FounderModelError(
            "cli_missing",
            "the Claude Code CLI is not installed in this image, and the Founder "
            "runs on it the same way every agent does. Rebuild the control-plane "
            "image (deploy/Dockerfile installs @anthropic-ai/claude-code).",
        )
    text = f"{exc}\n{stderr}".strip()
    low = text.lower()
    if any(s in low for s in ("not logged in", "unauthor", "authentication",
                              "invalid api key", "api key is invalid", "401",
                              "oauth", "expired")):
        return FounderModelError(
            "auth",
            "the model rejected this credential. Check Settings: an API key must "
            "be sk-ant-api… in API-key mode, and a subscription token must be "
            "sk-ant-oat… from `claude setup-token` in subscription mode. "
            f"({text.strip()[:200]})",
        )
    if any(s in low for s in ("rate limit", "429", "overload", "529", "capacity")):
        return FounderModelError(
            "busy", f"the model is rate limited or overloaded — try again shortly. "
                    f"({text.strip()[:200]})")
    if isinstance(exc, CLIConnectionError):
        return FounderModelError(
            "unknown", f"could not talk to the Claude Code CLI: {text.strip()[:300]}")
    return FounderModelError("unknown", f"{type(exc).__name__}: {text.strip()[:300]}")


def options(*, system: str, env: dict[str, str], model: str,
            mcp_servers: dict | None = None, allowed_tools: list[str] | None = None,
            stderr_sink: list[str] | None = None):
    """The one place the CLI is configured, for both callers.

    Three settings are load-bearing rather than tuning:

    - ``tools=[]`` switches off every built-in tool. Neither of these callers
      may read a file, run a command, or browse; the Founder's six tools are
      the entire surface and the only one that touches the outside labels what
      it returns as data.
    - ``setting_sources=[]`` ignores any settings.json on the host. A server
      that quietly inherited a developer's local config would behave
      differently in production than in a checkout, silently.
    - ``env`` carries the credential per call rather than through the process
      environment, so two chairmen — or two threads — never share one.

    Nothing here is a permission callback. An `allowed_tools` entry that names
    a whole tool auto-approves it before any callback is consulted, so one
    would be decoration claiming to be a guard. The real enforcement is the two
    lines above: with no built-in tools loaded, there is no Bash to permit.
    """
    from claude_agent_sdk import ClaudeAgentOptions


    return ClaudeAgentOptions(
        system_prompt=system,
        model=model,
        # The CLI wants somewhere to be. Nothing is written here: every
        # built-in tool is off, so this is a working directory with no work
        # done in it.
        cwd=tempfile.gettempdir(),
        env=subprocess_env(env),
        tools=[],
        setting_sources=[],
        mcp_servers=mcp_servers or {},
        allowed_tools=allowed_tools or [],
        max_turns=MAX_TURNS,
        # Without this the CLI's own explanation is thrown away and every
        # failure classifies as "unknown" — which is the blank answer this
        # module exists to stop, one layer further down.
        stderr=(stderr_sink.append if stderr_sink is not None else None),
    )


def subprocess_env(credential: dict[str, str]) -> dict[str, str]:
    """What the CLI subprocess may see, on top of an inherited environment.

    The SDK merges this over ``os.environ`` rather than replacing it, so
    removing a variable means setting it empty. See ``_SCRUB_EXACT``.
    """
    scrubbed = {
        name: ""
        for name in os.environ
        if name in _SCRUB_EXACT or name.startswith(_SCRUB_PREFIXES)
    }
    return {
        **scrubbed,
        **credential,
        "HOME": os.environ.get("HOME", tempfile.gettempdir()),
    }


def assistant_text(message) -> str:
    """The prose out of one assistant message, thinking and tool calls dropped."""
    blocks = getattr(message, "content", None) or []
    return "\n".join(
        b.text for b in blocks
        if getattr(b, "type", None) == "text" or type(b).__name__ == "TextBlock"
    ).strip()


async def run(prompt: str, opts, stderr_sink: list[str] | None = None,
              thread_id: str = "") -> str:
    """Drive one turn to completion and return what the model said.

    Errors are classified rather than raised raw: the caller turns `kind` into
    a status code, and the chairman gets a sentence instead of a blank 500.

    There is no deadline. A drafting turn legitimately runs for minutes and the
    chairman's position is that it may run as long as it is honest about what
    it is doing. What is bounded is *silence*: every message resets the clock,
    so a turn that is working is never cut off, and a turn that has said
    nothing for `STALL_SECONDS` is wedged rather than busy.
    """

    from claude_agent_sdk import AssistantMessage, ResultMessage, query

    from yaaf.founder import progress

    said: list[str] = []
    try:
        async for message in _watched(query(prompt=prompt, options=opts), thread_id):
            if isinstance(message, AssistantMessage):
                text = assistant_text(message)
                if text:
                    said.append(text)
                    # The model saying what it is about to do beats any label
                    # this code could invent for it.
                    if thread_id:
                        progress.note(thread_id, text)
                elif thread_id:
                    progress.touch(thread_id)
            elif isinstance(message, ResultMessage):
                if getattr(message, "is_error", False):
                    raise _ended_badly(message)
            elif thread_id:
                progress.touch(thread_id)
    except FounderModelError:
        raise
    except Exception as exc:  # noqa: BLE001 — every failure becomes a message
        tail = "\n".join(stderr_sink or [])[-2000:]
        log.error("founder model turn failed: %s\nstderr: %s", exc, tail)
        raise _classify(exc, tail) from exc
    return "\n\n".join(said).strip()


def _ended_badly(message) -> FounderModelError:
    """Why the CLI ended the run, when it ended it badly.

    `ResultMessage.result` holds the model's last words, and on a failure
    subtype there are none — so reading it alone produced "the model run
    failed", which is the blank answer this module exists to stop, arriving
    through the one door `_classify` does not guard. The reason was in
    `subtype` the whole time.

    `error_max_turns` is the one worth its own sentence. It does not mean the
    model broke; it means the model was refused the same way often enough to
    run out of turns, which is a bug in whatever kept refusing it. Saying so
    sends the next person to the refusal rather than to the CLI.
    """
    subtype = str(getattr(message, "subtype", "") or "")
    said = str(getattr(message, "result", "") or "").strip()
    if subtype == "error_max_turns":
        turns = getattr(message, "num_turns", None) or MAX_TURNS
        return FounderModelError(
            "looping",
            f"the Founder used all {turns} of its turns without settling on a draft. "
            "That is a loop rather than slow work — something refused the same draft "
            "over and over. The conversation is saved.")
    if said:
        return FounderModelError("unknown", said[:300])
    return FounderModelError(
        "unknown",
        f"the model run ended in error ({subtype or 'no reason given'}).")


async def _watched(stream, thread_id: str):
    """Pass messages through, failing if the stream goes quiet for too long.

    A plain `async for` would wait forever on a wedged CLI, holding the
    request, a worker thread and the chairman's browser tab with it. This gives
    silence a deadline without giving the work one.
    """
    import anyio

    from yaaf.founder import progress

    if not thread_id:
        async for message in stream:
            yield message
        return

    iterator = stream.__aiter__()
    while True:
        try:
            with anyio.fail_after(progress.STALL_SECONDS):
                message = await iterator.__anext__()
        except StopAsyncIteration:
            return
        except TimeoutError as exc:
            doing = progress.current_label(thread_id)
            raise FounderModelError(
                "stalled",
                f"nothing has happened for {int(progress.STALL_SECONDS)}s while "
                f"{doing.lower()}. That is a hang rather than slow work — the "
                "conversation is saved, so try again.",
            ) from exc
        progress.touch(thread_id)
        yield message


async def run_once(*, prompt: str, system: str, env: dict[str, str], model: str) -> str:
    """A single toolless turn — what the goal grader needs."""
    if not on_sdk(model):
        from yaaf.founder import loop

        return await loop.run(prompt, env=env, model=model, system=system)
    sink: list[str] = []
    return await run(
        prompt,
        options(system=system, env=env, model=model, stderr_sink=sink),
        sink,
    )
