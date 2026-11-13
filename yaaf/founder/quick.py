"""Quick founding: a brief in, a running project out, nobody answering.

The conversation (`yaaf.founder.agent`) is an interview, and it should be: the
chairman's answers are what refine a brief into a project. Quick founding is
for when nobody is there to answer — another agent framework handing off "find
the best home printer" — and the brief is all there is.

So it keeps the interview and drops the waiting. The Founder still writes the
round it would have asked, each question with its recommendation, because
naming a decision is what makes the model take one ("Shape: Deliver,
because…"). Every recommendation is accepted as asked, the answers are kept on
the thread like any chairman's, and the CEO's day-one briefing carries the
whole ledger — so what was assumed is on the record, not lost.

What it cannot do is anything that needs a person to have supplied something.
A quick project has:

  * no data sources and no secrets — nobody is there to grant them;
  * no email — a mailbox is set up by a person in Settings;
  * no controlled domains and no integrations;
  * one credential paying every seat, chosen by `billing_for`;
  * the platform's defaults for authority (autonomous) and stakes.

Those are refusals inside the turn (`problems`), so the Founder fixes a draft
that breaks one while it is still drafting, rather than the create failing
after it. A draft that is still refused after one more try ends the quick
founding with the refusal said in one sentence; nothing is created and nothing
is spent but the drafting.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from yaaf.core import ids, llm_credentials, platform
from yaaf.core.models import FoundingThread, Project, Secret, User, utcnow

log = logging.getLogger("yaaf.founder")

#: Where a quick founding is: `drafting` until the draft is accepted,
#: `creating` while the project is made, `starting` once it exists and its
#: agents are being started, then one of the last three. `created` means it
#: exists but did not start — asked not to, or refused, and it says why.
PHASES = ("drafting", "creating", "starting", "started", "created", "failed")

#: The phases a poller keeps waiting through.
UNDER_WAY = ("drafting", "creating", "starting")

#: How many turns the Founder gets to produce an acceptable draft. The second
#: carries the first's refusal; a third would be a loop, not a correction.
ATTEMPTS = 2


class QuickRefusal(ValueError):
    """Quick founding cannot begin; the message says what would let it."""


def billing_for(session: Session, user: User, name: str | None = None) -> Secret:
    """The credential that pays a quick project — "the default payment method".

    The install has no default, so this is the order a person would reach for:

    1. the one the caller named;
    2. the one this chairman's most recent project pays with — what they last
       chose, which is the nearest thing to a preference the install records;
    3. the only one there is.

    Otherwise it is refused with the menu. Picking among several by a rule
    nobody chose would spend somebody's money on a guess, which is the one
    thing the Founder is never allowed to do either.
    """
    if name:
        row = llm_credentials.resolve(session, name=name) \
            or llm_credentials.resolve(session, credential_id=name)
        if row is None:
            raise QuickRefusal(f"no LLM credential is called {name!r}; "
                               f"{llm_credentials.menu(session)}.")
        return row
    last = session.scalar(
        select(Project.billing_secret_id)
        .where(Project.chairman_user_id == user.id, Project.billing_secret_id.is_not(None))
        .order_by(Project.created_at.desc()).limit(1))
    if last:
        row = llm_credentials.resolve(session, credential_id=last)
        if row is not None:
            return row
    rows = llm_credentials.choices(session)
    if len(rows) == 1:
        return llm_credentials.resolve(session, credential_id=rows[0]["id"])
    if not rows:
        raise QuickRefusal("no LLM credential exists to pay for a project — an "
                           "administrator adds one in Settings.")
    raise QuickRefusal("there is more than one LLM credential and no earlier project "
                       "of yours to take a choice from; name the one that pays. "
                       f"{llm_credentials.menu(session)}.")


def problems(project: dict, billing_id: str) -> list[str]:
    """What a quick draft may not carry, as the refusals `propose_bundle` returns."""
    out = []
    if project.get("needs_mail"):
        out.append("needs_mail: a quick project cannot run through email — a person sets "
                   "up the mailbox. Draft it without, or say the brief needs the "
                   "conversation instead.")
    if project.get("controlled_domains"):
        out.append("controlled_domains: a quick project controls no domains; leave it empty.")
    if project.get("integrations"):
        out.append("integrations: a quick project has none; leave it out.")
    if project.get("github_repo"):
        out.append("github_repo: a quick project is given no repository; leave it out.")
    if project.get("billing_secret_id") not in (None, billing_id):
        out.append(f"billing_secret_id: this quick project is paid by {billing_id}; "
                   "name no other.")
    others = {p: c for p, c in (project.get("credentials") or {}).items() if c != billing_id}
    if others:
        out.append("credentials: one credential pays every seat of a quick project; seat "
                   "only roles whose class runs on its provider, and name no other "
                   f"credential (you named {', '.join(sorted(others))}).")
    return out


def instructions(billing: Secret) -> str:
    """What the Founder is told on top of the conversation's prompt."""
    return (
        "<quick-founding>\n"
        "Nobody will answer questions. This is quick founding: the brief above is all "
        "there is, and the project is created and started the moment your draft is "
        "accepted. In this one turn:\n"
        "1. `ask` the round you would have asked, each question with your recommendation "
        "— every recommendation is accepted as asked, and the round goes to the CEO as "
        "what was decided. Put everything you settled without asking in `settled`.\n"
        "2. `propose_bundle` the final draft, every recommendation folded in.\n"
        "3. One sentence saying what you built. Do not call `rehearse` or `grade_goal`.\n"
        "Fixed for a quick project — do not ask about these:\n"
        f"- It is paid by {billing.name!r} ({billing.provider}); billing_secret_id is set "
        "for you. Seat only roles whose class runs on that provider; name no other "
        "credential.\n"
        "- No data sources, secrets, email (needs_mail false), controlled domains, "
        "integrations or repository.\n"
        "- Authority: leave the authority rules out (the platform's autonomous default).\n"
        "- Name the tempo card that fits the shape; stakes `normal` unless the brief "
        "says otherwise.\n"
        "</quick-founding>"
    )


def begin(session: Session, user: User, brief: str, *, billing: str | None = None,
          start: bool = True, via: str | None = None) -> FoundingThread:
    """Record a quick founding and return its thread; the caller submits it.

    Everything that can be refused is refused here, on the request that asked —
    no credential for the Founder, no credential to pay — rather than as a
    background failure nobody is watching for yet.
    """
    from yaaf.founder import agent

    brief = (brief or "").strip()
    if not brief:
        raise QuickRefusal("say what the project is for — the brief is all quick "
                           "founding has to go on.")
    if not platform.has_credential(session):
        raise QuickRefusal(str(platform.NoPlatformCredential(
            "no platform credential is configured — point the Founder at one of your "
            "LLM credentials, or give it a key of its own, in Settings → Founder.")))
    paying = billing_for(session, user, billing)
    thread = FoundingThread(
        id=ids.new_id(ids.FOUNDING_THREAD), user_id=user.id, transcript=[],
        quick={"phase": "drafting", "billing_id": paying.id, "billing": paying.name,
               "start": bool(start), **({"via": via} if via else {})})
    session.add(thread)
    session.flush()
    agent.start_turn(session, thread, brief)
    return thread


def set_phase(thread: FoundingThread, phase: str, **more) -> None:
    thread.quick = {**(thread.quick or {}), "phase": phase, **more}
    thread.updated_at = utcnow()


def accept_recommendations(thread: FoundingThread) -> None:
    """Answer the Founder's round as a chairman agreeing with all of it would.

    Kept as a chairman turn with answers, exactly as the page sends them, so
    the CEO's briefing and every other reader of a transcript read a quick
    founding the way they read a conversation."""
    from yaaf.founder import agent

    asked = agent.open_round(thread)
    if not asked:
        return
    answers = [{"id": q["id"]} for q in asked["questions"]]
    note = "Quick founding: every recommendation accepted as asked."
    turns = list(thread.transcript or [])
    turns.append({"role": "chairman",
                  "text": agent.render_answers(asked, answers, note),
                  "answers": answers, "note": note})
    thread.transcript = turns


def run(session: Session, thread: FoundingThread) -> None:
    """Draft, accept, create, start, brief — the whole quick founding.

    Runs on the founder runner's thread. Each phase is committed as it is
    reached, so whoever is polling sees `creating` while the project is made
    rather than `drafting` until the end. Raises what a Founder turn raises;
    a draft that cannot be made acceptable ends as `failed` with the refusal.
    """
    from yaaf.founder import agent

    quick = dict(thread.quick or {})
    for attempt in range(1, ATTEMPTS + 1):
        refused = agent.run_turn(session, thread, quick=quick).get("refused")
        if thread.draft_bundle and not refused:
            break
        if attempt == ATTEMPTS:
            set_phase(thread, "failed")
            thread.last_error = (
                "the Founder could not draft a project quick founding can create"
                + (f": {'; '.join(refused)[:400]}" if refused else ".")
                + " Nothing was created. Try the conversation instead, where it can ask.")
            return
        turns = list(thread.transcript or [])
        turns.append({"role": "chairman", "text": (
            "Quick founding: that turn ended without an accepted draft"
            + (f" — it was refused for: {'; '.join(refused)}" if refused else "")
            + ". Propose it again, fixed. Nobody will answer questions.")})
        thread.transcript = turns
        thread.turn_started_at = utcnow()
        session.commit()

    accept_recommendations(thread)
    set_phase(thread, "creating")
    session.commit()
    create_and_start(session, thread)


def create_and_start(session: Session, thread: FoundingThread) -> None:
    """Make the drafted project as its chairman, start it, and brief its CEO."""
    from fastapi import HTTPException

    from yaaf.server import acting
    from yaaf.server.api import admin
    from yaaf.server.api.founder import hand_over

    user = session.get(User, thread.user_id)
    quick = thread.quick or {}
    body = {k: v for k, v in ((thread.draft_bundle or {}).get("project") or {}).items()
            if k not in ("billing_secret_name", "credential_names",
                         "fallback_credential_name", "fallback_secret_id")}
    body.update(chairman_email=user.email, billing_secret_id=quick.get("billing_id"),
                billing_mode=None)
    with acting.acting(acting.person(user.id, quick.get("via"))):
        try:
            created = admin.create_project(
                body=admin.ProjectCreate.model_validate(body), session=session)
        except HTTPException as exc:
            set_phase(thread, "failed")
            thread.last_error = f"the project could not be created: {exc.detail}"
            return
        except ValueError as exc:  # pydantic: a draft the endpoint's model refuses
            set_phase(thread, "failed")
            thread.last_error = f"the project could not be created: {exc}"[:600]
            return
        hand_over(session, thread, session.get(Project, created["id"]), user.id)
        starting = quick.get("start", True)
        set_phase(thread, "starting" if starting else "created",
                  project_id=created["id"], slug=body.get("slug"))
        session.commit()
        if not starting:
            return
        try:
            admin.start_project(project_id=created["id"], session=session)
        except HTTPException as exc:
            set_phase(thread, "created")
            thread.last_error = f"created, but it did not start: {exc.detail}"
            return
    set_phase(thread, "started")


def status(thread: FoundingThread) -> dict:
    """A quick founding as a caller polling it needs it: where it is, the
    project once there is one, what was decided, and what went wrong."""
    quick = thread.quick or {}
    asked = next((t.get("round") for t in reversed(thread.transcript or [])
                  if t.get("round")), None) or {}
    decided = [{"question": q["title"],
                "answer": (q["options"][q["recommended"]]["label"]
                           if isinstance(q.get("recommended"), int) else "left open")}
               for q in asked.get("questions") or []]
    decided += [{"question": e["topic"], "answer": e["decision"]}
                for e in asked.get("settled") or []]
    project = ((thread.draft_bundle or {}).get("project") or {})
    return {
        "thread": thread.id,
        "phase": quick.get("phase"),
        "project_id": quick.get("project_id") or thread.created_project_id,
        "slug": quick.get("slug"),
        "name": project.get("name"),
        "mode": project.get("mode"),
        "billing": quick.get("billing"),
        "teams": [{"name": t.get("name"), "role": t.get("role_slug"),
                   "seats": len(t.get("employees") or []) or 1}
                  for t in project.get("teams") or []],
        "decided": decided,
        "error": thread.last_error,
        "note": next((t.get("text") for t in reversed(thread.transcript or [])
                      if t.get("role") == "founder" and t.get("text")), None),
    }
