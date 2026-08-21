"""FastAPI application factory.

The one piece of cross-cutting behavior that matters lives here: a
:class:`~yaaf.server.policy.Refusal` raised anywhere in the agent API is
returned as a structured error *and counted against the run's refusal cap*.
An agent that hits the cap has its token revoked mid-run — refuse-rephrase-
retry loops die in five calls, not five hundred.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from yaaf.core.constitution import effective
from yaaf.core.db import db_session
from yaaf.core.models import Run, RunKind
from yaaf.server.policy import Refusal

log = logging.getLogger("yaaf.api")

#: Refusal timestamps per resident chat session, newest last. A chat run row
#: lives for days, so the lifetime count on the row is the wrong number to cap:
#: five mistakes spread across a week would revoke the boardroom, drop the
#: chairman's reply into stderr and leave the room silent with no explanation.
#: Loop protection wants a rate. Held in the process rather than the database
#: because it is a backstop, not a record — the durable count stays on the run
#: row for the Runs page, and a restart forgiving an old window is the failure
#: direction we want.
_CHAT_REFUSALS: dict[str, list[float]] = {}
CHAT_REFUSAL_WINDOW_S = 3600.0


def _recent_refusals(run_id: str) -> int:
    """Refusals this chat session has hit inside the trailing window, counting
    the one being handled now."""
    now = time.monotonic()
    hits = [t for t in _CHAT_REFUSALS.get(run_id, []) if now - t < CHAT_REFUSAL_WINDOW_S]
    hits.append(now)
    _CHAT_REFUSALS[run_id] = hits
    # Sessions end; their windows should not outlive the process for them.
    if len(_CHAT_REFUSALS) > 256:
        for stale in [k for k, v in _CHAT_REFUSALS.items()
                      if not v or now - v[-1] > CHAT_REFUSAL_WINDOW_S]:
            _CHAT_REFUSALS.pop(stale, None)
    return len(hits)


def _web_dist() -> Path | None:
    candidate = Path(os.environ.get("YAAF_WEB_DIST", "web/dist"))
    return candidate if (candidate / "index.html").is_file() else None


def create_app() -> FastAPI:
    from yaaf.server.api import admin, agent, oauth, observe, webhooks, ws

    app = FastAPI(title="yaaf", version="0.1.0")
    app.include_router(agent.router, prefix="/agent", tags=["agent"])
    app.include_router(admin.router, prefix="/admin", tags=["admin"])
    app.include_router(observe.router, prefix="/observe", tags=["observe"])
    # Unauthenticated by bearer-token design: senders sign each body with a
    # chairman-provisioned secret instead. See yaaf.server.api.webhooks.
    app.include_router(webhooks.router, prefix="/webhooks", tags=["webhooks"])
    # Also unauthenticated by design: the browser redirect back from an MCP
    # data source's authorization server, credentialed by a single-use state
    # parameter instead. See yaaf.server.api.oauth.
    app.include_router(oauth.router, prefix="/oauth", tags=["oauth"])
    # Live-update sockets: a latency hint over the SPA's polling, never a
    # replacement for it (mirrors LISTEN/NOTIFY over the dispatcher's floor).
    app.include_router(ws.router, tags=["ws"])

    @app.exception_handler(Refusal)
    def refusal_handler(request: Request, exc: Refusal) -> JSONResponse:
        remaining = None
        run_id = getattr(request.state, "run_id", None)
        if run_id is not None:
            with db_session() as session:
                run = session.get(Run, run_id)
                if run is not None:
                    run.refusal_count += 1
                    run.last_refusal = exc.message[:500]
                    try:
                        cap = effective(session, run.project_id).refusal_cap_per_run
                    except LookupError:
                        cap = 5
                    # The cap is "refusals this run", and a chat session is one
                    # run row that lives for days. Counted over a lifetime it
                    # meant five mistakes spread across a week revoked the
                    # boardroom — the chairman's reply was then dropped into
                    # stderr and the room went silent. Loop protection wants a
                    # rate, not a total; the lifetime count stays on the row
                    # because the Runs page is right to show it.
                    if run.kind == RunKind.CHAT_SESSION:
                        hits = _recent_refusals(run.id)
                        remaining = max(0, cap - hits)
                        if hits >= cap:
                            run.token_revoked = True
                    else:
                        remaining = max(0, cap - run.refusal_count)
                        if run.refusal_count >= cap:
                            run.token_revoked = True
        # Every wall an agent hits, in the operator's log. Until this line, a
        # refusal existed only inside the run that died of it — the platform
        # could refuse the same agent the same way for a week and the only
        # trace was a banner that needs three terminated runs to appear.
        log.warning("refusal %s on %s %s (run=%s, %s left): %s",
                    exc.status_code, request.method, request.url.path, run_id,
                    "?" if remaining is None else remaining, exc.message)
        body = {"error": exc.message}
        if remaining is not None:
            body["refusals_remaining"] = remaining
            if remaining == 0:
                body["error"] += (
                    " [Your run token has been revoked: too many refused calls this run. "
                    "Rephrasing a refused request is not a strategy — the rule is the rule.]"
                )
        return JSONResponse(status_code=exc.status_code, content=body)

    @app.get("/healthz")
    def healthz() -> dict:
        return {"ok": True}

    dist = _web_dist()
    if dist is not None:
        app.mount("/app", StaticFiles(directory=dist, html=True), name="app")

        # The SPA shell must never be cached: hashed assets change every build,
        # and a stale index.html means a stale UI over a new backend.
        @app.middleware("http")
        async def no_cache_shell(request: Request, call_next):
            response = await call_next(request)
            if request.url.path in ("/app", "/app/", "/app/index.html"):
                response.headers["Cache-Control"] = "no-store"
            return response

        @app.get("/")
        def root() -> RedirectResponse:
            return RedirectResponse("/app/")

        # The SPA declares its own icon, so this is for the pages that can't:
        # /docs and any bare-root request, both of which fall back to the
        # well-known path. Served as SVG regardless of the .ico spelling —
        # browsers dispatch on the content type, and every engine that asks
        # for a favicon today renders SVG.
        @app.get("/favicon.ico", include_in_schema=False)
        def favicon() -> FileResponse:
            return FileResponse(dist / "favicon.svg", media_type="image/svg+xml")
    else:

        @app.get("/")
        def root() -> dict:
            return {
                "service": "yaaf",
                "version": app.version,
                "note": "The web UI is not built (web/dist missing). "
                        "Use the `yaaf` CLI, or browse the API at /docs.",
                "health": "/healthz",
                "api_docs": "/docs",
            }

    return app
