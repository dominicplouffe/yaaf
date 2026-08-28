"""Live-update WebSocket for the SPA.

Design: the socket is a *latency hint* layered over the SPA's polling, exactly
like LISTEN/NOTIFY over the dispatcher's polling floor (see yaaf.core.db.notify
and the cadence comments in yaaf.core.config). The client keeps its usePoll
intervals; this endpoint merely tells it "something changed, poll now". If the
socket dies — network blip, proxy timeout, DB hiccup — nothing breaks: polling
remains the source of truth, and this whole file could be deleted without
losing correctness, only freshness.

Protocol: the server pushes a JSON "change stamp" — cheap per-table aggregates
(counts + latest timestamps) for the project — and only when the stamp differs
from the last one sent. The client never inspects the stamp's contents; any
message means "reload".

Auth: browsers cannot set headers on a WebSocket, so the admin token rides as a
?token= query param and is compared against settings().admin_token the same way
deps.admin_auth compares its bearer token (constant-time via compare_digest).
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
from datetime import datetime

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import func
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from yaaf.core.config import settings
from yaaf.core.db import session_factory
from yaaf.core.models import (
    Approval,
    Evidence,
    Message,
    Notice,
    Project,
    Run,
    Task,
)

router = APIRouter()

# Stamp cadence (seconds). Module constant so tests can shrink it; deliberately
# looser than any client poll would need to be — this is a hint, not the poll.
WS_POLL_S = 2.0


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _count_max(session: Session, model, ts_column, project_id: str) -> list:
    """[row count, latest timestamp] — one indexed aggregate query per table."""
    count, latest = (
        session.query(func.count(model.id), func.max(ts_column))
        .filter(model.project_id == project_id)
        .one()
    )
    return [count, _iso(latest)]


def _change_stamp(project_id: str) -> dict | None:
    """A cheap fingerprint of everything the dashboard shows. Two stamps being
    equal means "nothing the UI cares about moved"; the client treats any
    difference as "poll now". Returns None for an unknown project."""
    with session_factory()() as session:
        if session.get(Project, project_id) is None:
            return None
        # Run/approval STATUS changes mutate rows in place (no new row, no new
        # created_at), so those tables get status-shaped extras.
        run_statuses = dict(
            session.query(Run.status, func.count(Run.id))
            .filter(Run.project_id == project_id)
            .group_by(Run.status)
            .all()
        )
        approvals = _count_max(session, Approval, Approval.created_at, project_id)
        approvals.append(
            _iso(
                session.query(func.max(Approval.decided_at))
                .filter(Approval.project_id == project_id)
                .scalar()
            )
        )
        return {
            "type": "stamp",
            "project": project_id,
            "messages": _count_max(session, Message, Message.created_at, project_id),
            # updated_at has onupdate, so it also covers status transitions.
            "tasks": _count_max(session, Task, Task.updated_at, project_id),
            "runs": _count_max(session, Run, Run.created_at, project_id)
            + [run_statuses],
            "approvals": approvals,
            "evidence": _count_max(session, Evidence, Evidence.created_at, project_id),
            "notices": _count_max(session, Notice, Notice.created_at, project_id),
        }


async def _drain_inbound(websocket: WebSocket) -> None:
    """The client never speaks on this socket; receiving exists purely so a
    disconnect is noticed promptly. Any actual inbound frame is ignored."""
    while True:
        await websocket.receive_text()


async def _stamp_loop(websocket: WebSocket, project_id: str) -> None:
    last: dict | None = None
    while True:
        # Sync SQLAlchemy in the threadpool — same model as the HTTP endpoints.
        stamp = await run_in_threadpool(_change_stamp, project_id)
        if stamp is None:
            return  # project vanished (or never existed): close, don't spin
        if stamp != last:
            await websocket.send_json(stamp)
            last = stamp
        await asyncio.sleep(WS_POLL_S)


@router.websocket("/ws/projects/{project_id}")
async def project_live(websocket: WebSocket, project_id: str, token: str = "") -> None:
    await websocket.accept()
    # Same comparison idiom as deps.admin_auth, minus the header transport.
    if not hmac.compare_digest(token.encode(), settings().admin_token.encode()):
        await websocket.close(code=1008, reason="bad admin token")
        return

    watcher = asyncio.ensure_future(_drain_inbound(websocket))
    sender = asyncio.ensure_future(_stamp_loop(websocket, project_id))
    try:
        # First task to finish ends the session: the watcher on client
        # disconnect, the sender on unknown project or DB/send failure.
        done, pending = await asyncio.wait(
            {watcher, sender}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in done:
            exc = task.exception()
            if exc is not None and not isinstance(exc, WebSocketDisconnect):
                raise exc
    except WebSocketDisconnect:
        pass
    except Exception:
        # A DB hiccup (or any surprise) closes THIS socket and nothing else —
        # the client falls back to its polling floor and may reconnect.
        pass
    finally:
        # Teardown races the server (or test client) cancelling THIS coroutine:
        # any await below can take a CancelledError mid-cleanup, which would
        # strand the session as a cancelled task instead of a closed socket.
        # Cleanup survives it (BaseException, because cancellation is not an
        # Exception), and the close still runs when the gather is cut short.
        for task in (watcher, sender):
            task.cancel()
        with contextlib.suppress(BaseException):
            await asyncio.gather(watcher, sender, return_exceptions=True)
        with contextlib.suppress(BaseException):
            await websocket.close()
