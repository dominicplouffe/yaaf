"""FastAPI application factory.

The one piece of cross-cutting behavior that matters lives here: a
:class:`~yaaf.server.policy.Refusal` raised anywhere in the agent API is
returned as a structured error *and counted against the run's refusal cap*.
An agent that hits the cap has its token revoked mid-run — refuse-rephrase-
retry loops die in five calls, not five hundred.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from yaaf.core.constitution import effective
from yaaf.core.db import db_session
from yaaf.core.models import Run
from yaaf.server.policy import Refusal


def _web_dist() -> Path | None:
    candidate = Path(os.environ.get("YAAF_WEB_DIST", "web/dist"))
    return candidate if (candidate / "index.html").is_file() else None


def create_app() -> FastAPI:
    from yaaf.server.api import admin, agent, observe, webhooks, ws

    app = FastAPI(title="yaaf", version="0.1.0")
    app.include_router(agent.router, prefix="/agent", tags=["agent"])
    app.include_router(admin.router, prefix="/admin", tags=["admin"])
    app.include_router(observe.router, prefix="/observe", tags=["observe"])
    # Unauthenticated by bearer-token design: senders sign each body with a
    # chairman-provisioned secret instead. See yaaf.server.api.webhooks.
    app.include_router(webhooks.router, prefix="/webhooks", tags=["webhooks"])
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
                    remaining = max(0, cap - run.refusal_count)
                    if run.refusal_count >= cap:
                        run.token_revoked = True
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
