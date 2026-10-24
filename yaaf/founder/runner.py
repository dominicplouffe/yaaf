"""Running a founding turn off the request that asked for it.

A drafting turn takes about five minutes. Held on the request, that is three
separate problems: a refresh throws the work away, any reverse proxy ends it
(nginx cuts at 60s, Cloudflare at 100), and a threadpool slot is occupied for
the whole time. So the request records what the chairman said and returns; the
turn happens here.

The shape is the one `yaaf.server.notify` already uses — a daemon thread,
fire and forget. It differs in one way worth saying out loud: notify's rule is
that "the thread itself never touches the database", and a founding turn must,
because it writes the transcript and the draft and its tools read the role and
crew libraries. So it opens a session of its own. Using the request's would
mean writing through a session that is closed the moment the response is sent.

A failure is *recorded*, never raised. There is nobody on the other end of this
thread to catch it, and an exception that only reaches a log leaves the
chairman looking at a turn that stopped for no stated reason — the thing this
whole feature exists to stop.
"""

from __future__ import annotations

import logging
import threading

from yaaf.core import platform
from yaaf.core.db import db_session
from yaaf.core.models import FoundingThread, utcnow
from yaaf.founder import agent, progress
from yaaf.founder.session import FounderModelError

log = logging.getLogger("yaaf.founder")


def _work(thread_id: str) -> None:
    try:
        with db_session() as session:
            thread = session.get(FoundingThread, thread_id)
            if thread is None:  # deleted while we were starting
                progress.end(thread_id, ok=False)
                return
            agent.run_turn(session, thread)
        # The session's commit lands as the block exits, so by the time the
        # turn is marked finished the reply and the draft are readable. Same
        # rule as the failure path below: record, then signal.
        progress.end(thread_id)
        return
    except agent.TurnStopped:
        _stopped(thread_id)
        return
    except (FounderModelError, platform.NoPlatformCredential) as exc:
        kind = getattr(exc, "kind", "auth")
        detail = str(exc)
        log.warning("founding turn %s failed: %s", thread_id, detail)
    except Exception as exc:  # noqa: BLE001 — nobody is catching this for us
        kind, detail = "unknown", f"{type(exc).__name__}: {exc}"
        log.exception("founding turn %s failed", thread_id)

    # Written BEFORE the turn is marked finished, and in a second short-lived
    # session because the one above was rolled back by its context manager —
    # the failure would go with the work that caused it.
    #
    # The order is the point: "not running" is the signal a poller waits for,
    # so anything it will then go and read has to already be there. Ending the
    # turn first leaves a window where the page sees a finished turn with no
    # reply and no reason, which is the blank-failure problem again.
    try:
        with db_session() as session:
            thread = session.get(FoundingThread, thread_id)
            if thread is not None:
                thread.last_error = detail
                thread.turn_started_at = None
                thread.updated_at = utcnow()
    except Exception:  # pragma: no cover - the log is the last resort
        log.exception("could not record the failure of founding turn %s", thread_id)
    progress.end(thread_id, ok=False, error=detail, kind=kind)


def _stopped(thread_id: str) -> None:
    """The chairman pressed Stop. Same order as a failure — record, then
    signal — but what is recorded is the message withdrawn, not a reason."""
    try:
        with db_session() as session:
            thread = session.get(FoundingThread, thread_id)
            if thread is not None:  # "Start over" stops and deletes in one press
                agent.withdraw_turn(thread)
    except Exception:  # pragma: no cover - the log is the last resort
        log.exception("could not withdraw stopped founding turn %s", thread_id)
    progress.end(thread_id, ok=False, kind="stopped")


def submit(thread_id: str) -> None:
    """Start the turn. Returns immediately; the work outlives the request."""
    threading.Thread(target=_work, args=(thread_id,),
                     name=f"founder-{thread_id[:12]}", daemon=True).start()
