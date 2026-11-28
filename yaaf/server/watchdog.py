"""The API's own pulse: a thread that asks /healthz, and ends the process when
the server has stopped answering it.

On 28 November 2026 the event loop blocked on a row lock and every request on
the server hung for ninety minutes. The container was "Up" the whole time:
Docker's restart policy restarts a process that exits, not one that stops
answering, and nothing else was watching. So this watches from inside, over
the same socket a browser uses, through the event loop and the thread pool
every sync route runs on, and touches no database (an unreachable database is
/readyz's to report, and restarting would not cure it).

Before exiting it writes every thread's stack to the log, which is the one
piece of evidence that hang left nobody: what each thread was waiting on.
"""

from __future__ import annotations

import faulthandler
import logging
import os
import sys
import threading
import time

import httpx

log = logging.getLogger(__name__)

#: How long the server may go without answering before it is restarted. Long
#: enough that a burst of slow work is not a hang; short enough that a hang
#: costs the chairman minutes rather than an evening.
STALL_S = 90.0
PROBE_EVERY_S = 10.0
PROBE_TIMEOUT_S = 5.0
#: The process exit status for a restart the watchdog asked for, so the log
#: line and `docker inspect` both say why the container came back.
EXIT_STALLED = 70


def watch(url: str, *, stall_s: float = STALL_S, every_s: float = PROBE_EVERY_S,
          timeout_s: float = PROBE_TIMEOUT_S, clock=time.monotonic, sleep=time.sleep,
          probe=None, die=None) -> None:
    """Probe `url` forever. Armed by the first answer, so a slow start is not a
    stall; after that, `stall_s` without one ends the process."""
    probe = probe or (lambda: httpx.get(url, timeout=timeout_s).status_code == 200)
    die = die or _die
    last_ok: float | None = None
    while True:
        try:
            ok = probe()
        except Exception:
            ok = False
        now = clock()
        if ok:
            last_ok = now
        elif last_ok is not None and now - last_ok >= stall_s:
            die(now - last_ok)
            return
        sleep(every_s)


def _die(silent_s: float) -> None:
    log.error("the API has not answered /healthz for %.0fs; writing every thread's "
              "stack and exiting so the container restarts", silent_s)
    faulthandler.dump_traceback(file=sys.stderr, all_threads=True)
    sys.stderr.flush()
    os._exit(EXIT_STALLED)


def start(port: int) -> threading.Thread:
    thread = threading.Thread(target=watch, args=(f"http://127.0.0.1:{port}/healthz",),
                              name="watchdog", daemon=True)
    thread.start()
    return thread
