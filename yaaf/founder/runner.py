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


#: Turns still running on threads of their own.
_RUNNING: set = set()


def wait_for_idle(timeout: float = 10.0) -> None:
    """Block until every turn and every background grade this process started
    has finished, or `timeout` passes — for a caller that must not outrun them
    (a test about to empty the database)."""
    import time

    from yaaf.founder.tools import wait_for_grades

    deadline = time.monotonic() + timeout
    for worker in list(_RUNNING):
        worker.join(max(0.0, deadline - time.monotonic()))
    wait_for_grades(max(0.0, deadline - time.monotonic()))


def _work(thread_id: str, quick: bool = False) -> None:
    try:
        _turn(thread_id, quick)
    finally:
        _RUNNING.discard(threading.current_thread())


def _turn(thread_id: str, quick: bool = False) -> None:
    try:
        with db_session() as session:
            thread = session.get(FoundingThread, thread_id)
            if thread is None:  # deleted while we were starting
                progress.end(thread_id, ok=False)
                return
            if quick:
                from yaaf.founder import quick as quick_founding

                quick_founding.run(session, thread)
            else:
                agent.run_turn(session, thread)
        # The session's commit lands as the block exits, so by the time the
        # turn is marked finished the reply and the draft are readable. Same
        # rule as the failure path below: record, then signal.
        progress.end(thread_id)
        return
    except progress.Stopped:
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
                if thread.quick:
                    thread.quick = {**thread.quick, "phase": "failed"}
    except Exception:  # pragma: no cover - the log is the last resort
        log.exception("could not record the failure of founding turn %s", thread_id)
    progress.end(thread_id, ok=False, error=detail, kind=kind)


def _stopped(thread_id: str) -> None:
    """The chairman stopped the turn: take his message back, then say so.

    The same order as a failure, for the same reason — the page waits on "not
    running" and then reads what it was given back, so it is written first.
    Nothing goes on `last_error`: stopping is something he did, not something
    that went wrong.
    """
    withdrawn = None
    try:
        with db_session() as session:
            thread = session.get(FoundingThread, thread_id)
            if thread is not None:
                withdrawn = agent.withdraw(thread)
                # A stopped quick founding hands its brief back like any
                # stopped turn, and what is left is an ordinary blank draft:
                # whatever he sends next is a conversation, unless he asks
                # for quick again.
                thread.quick = None
    except Exception:  # pragma: no cover - the log is the last resort
        log.exception("could not withdraw the stopped turn on %s", thread_id)
    log.info("founding turn %s stopped by the chairman", thread_id)
    progress.end(thread_id, ok=False, stopped=True, withdrawn=withdrawn)


def submit(thread_id: str, quick: bool = False) -> None:
    """Start the turn — or, with `quick`, the whole quick founding. Returns
    immediately; the work outlives the request."""
    worker = threading.Thread(target=_work, args=(thread_id, quick),
                              name=f"founder-{thread_id[:12]}", daemon=True)
    _RUNNING.add(worker)
    worker.start()
