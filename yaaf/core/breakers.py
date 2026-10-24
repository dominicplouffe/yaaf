"""Failover: when a credential's provider keeps failing, the projects it bills
move their class seats to their fallback credential's provider until it
recovers (docs/PROVIDERS.md §9).

An automatic state change, not a notice — the house rule. A provider outage
used to stop every project on it: each run failed transient, was retried,
failed again, and the work stood still until a person noticed and re-pointed
billing by hand. Now the runs themselves are the signal:

- **Closed** — normal. Each run on the credential that fails *transient* (the
  provider overloaded, rate-limited past its retries, unreachable) counts;
  any run that works resets the count.
- **Open** — `TRIP_AFTER` transient failures in a row. For a cooling-off
  period, every project billed by this credential that holds a fallback
  resolves its bare classes (`@balanced`) on the fallback's provider
  (yaaf.core.model_policy.context) — every reader follows, because every
  reader resolves through there: the lease, the seat doors, the chat lane,
  the health checklist.
- **Half-open** — the period over, the next run tries the provider again. A
  success closes it; a transient failure opens it again for twice as long, up
  to `MAX_COOL`.

What never moves: a seat pinned to a model, or a class named with its provider
(`openai/@fast`) — somebody chose that provider on purpose, and a run on
another would be a seat hired as something it is not. Those seats wait out the
outage as before. A dead key is not an outage either (`auth` is the three-
strikes pause's, yaaf.runner.daemon); only `transient` failures trip this.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from yaaf.core.models import CredentialBreaker, utcnow

TRIP_AFTER = 3
FIRST_COOL = timedelta(minutes=5)
MAX_COOL = timedelta(hours=1)


def _row(session: Session, secret_id: str, *, lock: bool) -> CredentialBreaker | None:
    query = select(CredentialBreaker).where(CredentialBreaker.secret_id == secret_id)
    return session.scalar(query.with_for_update() if lock else query)


def is_open(session: Session, secret_id: str | None) -> bool:
    if not secret_id:
        return False
    row = _row(session, secret_id, lock=False)
    return bool(row and row.open_until and utcnow() < row.open_until)


def state(session: Session, secret_id: str | None) -> dict | None:
    """For the screens: whether it is open, since when, until when, and why."""
    row = _row(session, secret_id, lock=False) if secret_id else None
    if row is None or (not row.failures and not row.opened_at):
        return None
    now = utcnow()
    status = ("open" if row.open_until and now < row.open_until
              else "half_open" if row.opened_at else "closed")
    return {"status": status, "failures": row.failures,
            "opened_at": row.opened_at.isoformat() if row.opened_at else None,
            "open_until": row.open_until.isoformat() if row.open_until else None,
            "last_error": row.last_error}


def record(session: Session, secret_id: str, *, ok: bool, kind: str | None,
           detail: str = "") -> str | None:
    """One finished run on the credential. Returns the transition it caused —
    "opened" or "closed" — or None."""
    if not ok and kind != "transient":
        return None
    row = _row(session, secret_id, lock=True)
    if row is None:
        if ok:
            return None
        row = CredentialBreaker(secret_id=secret_id, failures=0, trips=0)
        session.add(row)
    now = utcnow()
    if ok:
        was_open = row.opened_at is not None
        row.failures, row.trips, row.opened_at, row.open_until = 0, 0, None, None
        return "closed" if was_open else None
    row.failures = (row.failures or 0) + 1
    row.last_error = detail[:300] or row.last_error
    half_open = row.opened_at is not None and (row.open_until is None or now >= row.open_until)
    if half_open or (row.opened_at is None and row.failures >= TRIP_AFTER):
        cool = min(FIRST_COOL * (2 ** (row.trips or 0)), MAX_COOL)
        row.open_until = now + cool
        row.trips = (row.trips or 0) + 1
        first = row.opened_at is None
        row.opened_at = row.opened_at or now
        return "opened" if first else None
    return None
