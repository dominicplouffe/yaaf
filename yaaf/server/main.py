"""Entrypoint: API server plus the dispatcher/janitor background threads."""

from __future__ import annotations

import logging
import threading

import uvicorn

from yaaf.core.config import settings
from yaaf.server.app import create_app
from yaaf.server.dispatcher import dispatcher_loop
from yaaf.server.janitor import janitor_loop
from yaaf.server.slack.socket import fast_mirror_loop, socket_mode_loop
from yaaf.server.task_mirror import task_mirror_loop


def main() -> None:
    # Uvicorn configures only its own loggers; without this, every INFO line
    # from the dispatcher/janitor/slack threads is silently dropped — which
    # made a mute Slack socket undiagnosable live.
    logging.basicConfig(level=logging.INFO)
    threading.Thread(target=dispatcher_loop, name="dispatcher", daemon=True).start()
    threading.Thread(target=janitor_loop, name="janitor", daemon=True).start()
    threading.Thread(target=fast_mirror_loop, name="slack-mirror", daemon=True).start()
    threading.Thread(target=socket_mode_loop, name="slack-socket", daemon=True).start()
    threading.Thread(target=task_mirror_loop, name="task-mirror", daemon=True).start()
    uvicorn.run(create_app(), host=settings().api_host, port=settings().api_port)


if __name__ == "__main__":
    main()
