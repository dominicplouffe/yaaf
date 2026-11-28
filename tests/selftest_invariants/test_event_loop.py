"""Invariant selftests: nothing waits on the event loop, and a server that has
stopped answering does not stay up looking alive.

28 November 2026: the GitHub webhook was `async def` and did its database work
inline. Two deliveries for one change arrived a second apart; the second's
UPDATE waited, on the event loop, for the first's row lock, and the first could
not commit without the loop. Every request on the server hung for ninety
minutes while the container reported "Up".

Attacked here:

  * a route that takes a database session is a plain `def` (FastAPI runs it on
    the thread pool), unless it is listed below with the reason it is safe
  * the webhooks still read and verify the raw body they are signed over
  * the watchdog ends a server that stops answering, and only one that has
    answered at least once
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import inspect
import json

from fastapi.routing import APIRoute

#: `async def` routes that hold a database session, and why each is safe. A
#: reason is required: a new one has to say what it does instead of waiting.
ASYNC_WITH_SESSION = {
    "POST /agent/mcp/{source}":
        "reads the body, commits on a worker thread and closes the session, then "
        "runs the forwarded call on threads of its own limiter",
    "POST /admin/projects/{project_id}/regrade-goal":
        "only reads before its one await (a read never waits on a row lock), and "
        "rolls the session back before the model call so nothing is held across it",
}


def _takes_session(route: APIRoute) -> bool:
    from yaaf.server.deps import db

    def walk(dep) -> bool:
        return dep.call is db or any(walk(d) for d in dep.dependencies)

    return walk(route.dependant)


def _routes(routes, prefix=""):
    """Every APIRoute with its full path, through included routers (FastAPI
    keeps an included router whole, with its prefix on the include)."""
    for route in routes:
        if isinstance(route, APIRoute):
            yield prefix + route.path, route
        elif (inner := getattr(route, "original_router", None)) is not None:
            context = getattr(route, "include_context", None)
            yield from _routes(inner.routes, prefix + (getattr(context, "prefix", "") or ""))


def test_no_route_waits_on_the_event_loop_for_the_database(client):
    found = {}
    for path, route in _routes(client.app.routes):
        if inspect.iscoroutinefunction(route.endpoint) and _takes_session(route):
            for method in route.methods:
                found[f"{method} {path}"] = route
    unlisted = sorted(set(found) - set(ASYNC_WITH_SESSION))
    assert not unlisted, (
        f"{unlisted} are `async def` and hold a database session: a query there that "
        "waits on a lock waits on the event loop, and every request on the server "
        "waits with it. Make it a plain `def` (read the body with a dependency, as "
        "webhooks._raw_body does), or list it in ASYNC_WITH_SESSION with why it is safe.")
    stale = sorted(set(ASYNC_WITH_SESSION) - set(found))
    assert not stale, f"{stale} are listed but no longer async with a session: remove them"


def test_the_signed_webhooks_still_verify_the_raw_body(client):
    from tests.selftest_invariants.test_merge_gate import _factory
    from yaaf.core.db import db_session
    from yaaf.core.models import Project

    project = _factory(client)
    with db_session() as session:
        row = session.get(Project, project["id"])
        row.integrations = {**(row.integrations or {}), "github_webhook_secret": "s3cret"}
    body = json.dumps({"action": "opened", "pull_request": {"number": 999}}).encode()
    good = "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
    url = f"/webhooks/github/{project['id']}"
    headers = {"X-GitHub-Event": "pull_request", "Content-Type": "application/json"}
    forged = client.post(url, content=body, headers={**headers,
                                                      "X-Hub-Signature-256": "sha256=0"})
    assert forged.status_code in (401, 403), forged.text
    signed = client.post(url, content=body, headers={**headers, "X-Hub-Signature-256": good})
    assert signed.status_code == 200, signed.text


def _run_watch(answers: list[bool], **kw) -> tuple[list[float], int]:
    from yaaf.server import watchdog

    now = [0.0]
    died: list[float] = []
    calls = iter(answers)

    def probe():
        value = next(calls, None)
        if value is None:
            raise _Done
        return value

    def sleep(seconds):
        now[0] += seconds

    with contextlib.suppress(_Done):
        watchdog.watch("http://x/healthz", clock=lambda: now[0], sleep=sleep,
                       probe=probe, die=died.append, stall_s=90, every_s=10, **kw)
    return died, int(now[0])


class _Done(BaseException):
    """Ends a watch in a test; a BaseException, so the probe's guard lets it by."""


def test_the_watchdog_ends_a_server_that_stopped_answering():
    died, _ = _run_watch([True] + [False] * 20)
    assert died and died[0] >= 90


def test_the_watchdog_forgives_a_blip_and_waits_for_a_slow_start():
    died, _ = _run_watch([True, False, False, False, True] + [True] * 5)
    assert died == [], "40 seconds of silence is slow work, not a hang"
    died, _ = _run_watch([False] * 30)
    assert died == [], "never armed: a server still starting is not stalled"


def test_a_probe_that_raises_counts_as_no_answer():
    from yaaf.server import watchdog

    now = [0.0]
    died: list[float] = []
    seq = iter([True])

    def probe():
        try:
            return next(seq)
        except StopIteration:
            if now[0] > 500:
                raise KeyboardInterrupt from None
            raise ConnectionError("refused") from None

    def sleep(seconds):
        now[0] += seconds

    watchdog.watch("http://x/healthz", clock=lambda: now[0], sleep=sleep, probe=probe,
                   die=died.append, stall_s=90, every_s=10)
    assert died and died[0] >= 90
