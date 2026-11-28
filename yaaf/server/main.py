"""Entrypoint: API server plus the dispatcher/janitor background threads."""

from __future__ import annotations

import logging
import threading

import uvicorn

from yaaf.core.config import settings
from yaaf.server.app import create_app
from yaaf.server.dispatcher import dispatcher_loop
from yaaf.server.janitor import janitor_loop
from yaaf.server.mail.service import mail_loop
from yaaf.server.slack.socket import fast_mirror_loop, socket_mode_loop

log = logging.getLogger("yaaf.server.main")


def main() -> None:
    # Uvicorn configures only its own loggers; without this, every INFO line
    # from the dispatcher/janitor/slack threads is silently dropped — which
    # made a mute Slack socket undiagnosable live.
    logging.basicConfig(level=logging.INFO)
    _recover_plugin_installs()
    threading.Thread(target=dispatcher_loop, name="dispatcher", daemon=True).start()
    threading.Thread(target=janitor_loop, name="janitor", daemon=True).start()
    threading.Thread(target=fast_mirror_loop, name="slack-mirror", daemon=True).start()
    threading.Thread(target=socket_mode_loop, name="slack-socket", daemon=True).start()
    threading.Thread(target=mail_loop, name="mail", daemon=True).start()
    uvicorn.run(create_app(), host=settings().api_host, port=settings().api_port)


if __name__ == "__main__":
    main()


def _recover_plugin_installs() -> None:
    """A plugin install the last process was building cannot finish now: mark it
    failed, with the reason, rather than leave it `installing` forever."""
    from yaaf.core.db import db_session
    from yaaf.plugins import registry
    from yaaf.server import acting

    try:
        with acting.acting(acting.Actor("system", "startup")), db_session() as session:
            count = registry.recover_interrupted(session)
        if count:
            log.warning("marked %d interrupted plugin install(s) failed", count)
    except Exception:  # a database not migrated yet: the install screens will say so
        log.exception("could not check for interrupted plugin installs")
