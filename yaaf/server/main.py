"""Entrypoint: API server plus the dispatcher/janitor background threads."""

from __future__ import annotations

import threading

import uvicorn

from yaaf.core.config import settings
from yaaf.server.app import create_app
from yaaf.server.dispatcher import dispatcher_loop
from yaaf.server.janitor import janitor_loop


def main() -> None:
    threading.Thread(target=dispatcher_loop, name="dispatcher", daemon=True).start()
    threading.Thread(target=janitor_loop, name="janitor", daemon=True).start()
    uvicorn.run(create_app(), host=settings().api_host, port=settings().api_port)


if __name__ == "__main__":
    main()
