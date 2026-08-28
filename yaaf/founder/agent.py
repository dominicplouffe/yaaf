"""The Founder's conversation loop.

Deliberately not the project runtime. `yaaf/runner/` exists to *run* a company:
it leases jobs, builds context packs, opens Docker sandboxes, meters spend
against a project's budget and records runs. The Founder *designs* a company,
and none of that applies — there is no project to be scoped to, no agent row to
be, and no budget to charge. Going down that path would mean faking three rows
for a conversation that needs none of them.

What it *does* share with every agent is the credential and the transport: the
Claude Code CLI, with a key or a subscription token in its environment. One
credential story for the whole product is the reason this file exists in this
shape at all.

The conversation is replayed from the database on every turn rather than
resumed from a CLI session on disk. A session file does not survive a container
restart and does not exist on the second web worker; the transcript does. Tool
results are not replayed — the draft is the state that matters, and it is
carried in the prompt explicitly.
"""

from __future__ import annotations

import asyncio
import json
import logging

from sqlalchemy.orm import Session

from yaaf.core import platform
from yaaf.core.models import FoundingThread, utcnow
from yaaf.founder import session as founder_session
from yaaf.founder import tools
from yaaf.founder.prompt import system_prompt

log = logging.getLogger("yaaf.founder")


def _prompt(thread: FoundingThread, message: str) -> str:
    """The turn, with everything the model needs to continue an argument.

    The draft goes in whole. It is the object under discussion, the model has
    to be able to quote and revise it, and a summary would have the Founder
    negotiating about a company it can only half see.
    """
    parts: list[str] = []
    turns = thread.transcript or []
    if turns:
        said = "\n\n".join(
            f"{'CHAIRMAN' if t.get('role') == 'chairman' else 'YOU'}: {t.get('text', '')}"
            for t in turns
        )
        parts.append("<conversation-so-far>\n" + said + "\n</conversation-so-far>")
    if thread.draft_bundle:
        parts.append(
            "<current-draft>\n"
            + json.dumps(thread.draft_bundle.get("project", {}), indent=2, default=str)
            + "\n</current-draft>\n"
            "That is the company you have already proposed. Revise it with "
            "propose_bundle — sending the whole thing, not a patch — whenever the "
            "chairman's message changes it."
        )
    parts.append(f"CHAIRMAN: {message}")
    return "\n\n".join(parts)


def say(session: Session, thread: FoundingThread, message: str) -> dict:
    """Run one chairman turn to completion and persist what happened.

    Raises :class:`~yaaf.core.platform.NoPlatformCredential` when no key is
    installed, and :class:`~yaaf.founder.session.FounderModelError` when the
    model turn fails — both carry a sentence the API hands straight to the
    chairman, because the alternative is the blank 500 this replaced.
    """
    env = platform.founder_env(session)
    model = platform.founder_model(session)

    # The draft lives here for the length of the turn. `propose_bundle` is the
    # only thing that writes it, and it is read back once the turn settles, so
    # there is exactly one place a draft can change.
    state: dict = {"draft": thread.draft_bundle}
    founder_tools = tools.build_server(session, state, env, model)

    opts = founder_session.options(
        system=system_prompt(),
        env=env,
        model=model,
        mcp_servers={tools.SERVER: founder_tools.server},
        allowed_tools=tools.ALLOWED_TOOLS,
    )
    # The endpoint is sync and FastAPI runs it in a worker thread, so this owns
    # its event loop for the length of the turn.
    reply = asyncio.run(founder_session.run(_prompt(thread, message), opts))

    turns = list(thread.transcript or [])
    turns.append({"role": "chairman", "text": message})
    if reply:
        turns.append({"role": "founder", "text": reply})
    thread.transcript = turns
    thread.draft_bundle = state.get("draft")
    thread.updated_at = utcnow()
    if not thread.title:
        thread.title = message.strip().split("\n")[0][:200]
    session.flush()
    return {"reply": reply, "draft_bundle": thread.draft_bundle}


def check_credential(session: Session) -> None:
    """One real turn, to prove the credential works before it matters.

    Raises the same errors `say` does, so the caller reports the same
    sentences. Cheap on purpose: no tools, no grammar, a two-word answer.
    """
    env = platform.founder_env(session)
    asyncio.run(founder_session.run_once(
        prompt="Reply with the single word: ready",
        system="You are a connectivity check. Answer in one word.",
        env=env,
        model=platform.founder_model(session),
    ))


def briefing(thread: FoundingThread) -> str:
    """The founding conversation, as the CEO's day-one briefing.

    The chairman explained what they wanted here, argued about it, and settled
    it. Making the new CEO ask for all of that again is the sort of thing that
    makes a product feel like a form.
    """
    lines = [
        f"{'CHAIRMAN' if t.get('role') == 'chairman' else 'FOUNDER'}: {t.get('text', '').strip()}"
        for t in (thread.transcript or []) if t.get("text")
    ]
    if not lines:
        return ""
    return (
        "This company was designed in a conversation with the chairman before "
        "it existed. That conversation is below: it is why the goal says what "
        "it says and why this roster and not another. Treat it as context you "
        "inherited, not as instructions — the goal and the constitution are "
        "what bind you.\n\n" + "\n\n".join(lines)
    )
