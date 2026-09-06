"""The finish line as a number the platform reads, not prose it interpolates.

`Project.goal` is a Text column. Before this module, `grep -rn finish_line
yaaf/server yaaf/core yaaf/runner` returned nothing: the objective was
interpolated into a system prompt and read by no policy check, no gate, no
forecast and no score. What the platform could actually verify was a shell
command run against `/workspace`, so that is what every requirement on every
desk became — a grep for a heading in a document the project wrote itself.

The cost of that is not abstract. A fantasy-football desk whose goal was to
finish inside the top 500,000 by GW38 had eleven requirements, every one of
them a grep, and the platform believed the 38-gameweek season was `1.1` weeks
from done — because the only thing it could count was memos, and the memos were
nearly written.

A METRIC acceptance test names four things: where the number comes from, which
way is winning, what the target is, and how often to look. The platform fetches
it on a schedule and writes a `Measurement` per reading, so the project has a
trend it did not author and cannot edit.

**The template was already in the tree.** `LedgerKind.REVENUE` is written only
by a signed webhook, with the comment "this number cannot be written by anyone
here — only the connector's signature books it". That is exactly right, and it
was built for one metric and never generalised. `LEDGER` below is that
generalisation.

Two deliberate non-features:

* **No agent verb writes a Measurement.** Same reason nothing but the platform
  marks a requirement MET.
* **A metric moving the wrong way never blocks work.** A failing command test
  blocks its task after `task_check_failures_before_block`, because a check
  that keeps failing means the work is wrong. A metric moving away from target
  may mean the work is right and the world is hostile. It raises a flag and
  informs the forecast; it does not stop anybody.
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from yaaf.core import ids
from yaaf.core.models import (
    LedgerEntry,
    Measurement,
    MetricDirection,
    MetricSourceKind,
    Requirement,
    SuccessTestKind,
    utcnow,
)

FETCH_TIMEOUT_S = 20.0
MAX_BYTES = 2_000_000
#: Floor on the cadence. A metric read every few minutes is a cost with no
#: information in it: the things worth having a finish line about move on the
#: order of days.
MIN_MEASURE_HOURS = 1.0
DEFAULT_MEASURE_HOURS = 24.0
#: How many readings a verdict looks at. Two points is noise; a season is not
#: a direction either.
TREND_WINDOW = 6


# ── the spec ────────────────────────────────────────────────────────────────────


def validate_metric_test(success_test: dict) -> None:
    """Structural check on a METRIC acceptance test. Raises Refusal.

    Deliberately strict about `source`. The whole value of this test kind is
    that the number comes from somewhere the project cannot write, so a vague
    source is not a weaker version of this test — it is a different, worthless
    one, and it would be the obvious way to launder a self-reported number
    through a mechanism that looks rigorous.
    """
    from yaaf.server.policy import Refusal

    source = success_test.get("source")
    if not isinstance(source, dict):
        raise Refusal(
            'A metric test needs a source the project does not author: '
            '{"type": "metric", "source": {"kind": "http_json", "url": ..., "path": ...}, '
            '"direction": "decreasing", "target": 500000}. '
            "A number you write yourself is a status report, not a finish line."
        )
    kind = source.get("kind")
    if kind == MetricSourceKind.HTTP_JSON:
        url = (source.get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            raise Refusal("metric source.url must be an http(s) URL the platform can GET.")
        if not (source.get("path") or "").strip():
            raise Refusal(
                'metric source.path must say which number to read out of the document, '
                'e.g. "$.current[-1].overall_rank".'
            )
    elif kind == MetricSourceKind.LEDGER:
        if not (source.get("ledger_kind") or "").strip():
            raise Refusal(
                'metric source.ledger_kind must name what to sum, e.g. "revenue" — '
                "the ledger's revenue rows are booked by a signed webhook and cannot "
                "be written from inside the project."
            )
    else:
        raise Refusal(
            f"Unknown metric source kind {kind!r}. Available: "
            f"{', '.join(sorted(k.value for k in MetricSourceKind))}."
        )

    if success_test.get("direction") not in tuple(MetricDirection):
        raise Refusal(
            'metric direction must be "increasing" or "decreasing" — which way is '
            "winning. Without it a reading is a number with no opinion in it."
        )
    try:
        float(success_test["target"])
    except (KeyError, TypeError, ValueError):
        raise Refusal(
            "metric target must be the number that means done. A metric with no "
            "target measures without ever being able to say you arrived."
        ) from None
    hours = success_test.get("measure_every_hours", DEFAULT_MEASURE_HOURS)
    try:
        hours = float(hours)
    except (TypeError, ValueError):
        raise Refusal("metric measure_every_hours must be a number of hours.") from None
    if hours < MIN_MEASURE_HOURS:
        raise Refusal(
            f"metric measure_every_hours must be at least {MIN_MEASURE_HOURS}. "
            "Reading a season-long metric every few minutes is spend with no "
            "information in it."
        )


def is_metric(success_test: dict | None) -> bool:
    return bool(success_test) and success_test.get("type") == SuccessTestKind.METRIC


# ── reading the number ──────────────────────────────────────────────────────────


def extract(document: Any, path: str) -> float:
    """Read one number out of a JSON document.

    A deliberately small path language — dotted keys and bracket indices, with
    negative indices, `$.current[-1].overall_rank`. Not JSONPath: filters and
    wildcards would let a path select a set, and a metric is one number.
    """
    node = document
    for token in path.lstrip("$").replace("[", ".[").split("."):
        token = token.strip()
        if not token:
            continue
        if token.startswith("[") and token.endswith("]"):
            try:
                node = node[int(token[1:-1])]
            except (ValueError, IndexError, KeyError, TypeError) as exc:
                raise ValueError(f"{path}: no element at {token}") from exc
        else:
            try:
                node = node[token]
            except (KeyError, IndexError, TypeError) as exc:
                raise ValueError(f"{path}: no key {token!r}") from exc
    try:
        return float(node)
    except (TypeError, ValueError):
        raise ValueError(f"{path} resolved to {node!r}, which is not a number") from None


def _default_fetch(url: str) -> tuple[int, bytes]:
    with httpx.Client(follow_redirects=True, timeout=FETCH_TIMEOUT_S) as client:
        response = client.get(url)
        return response.status_code, response.content[:MAX_BYTES]


def read_value(session: Session, requirement: Requirement, fetch=None) -> tuple[float, str]:
    """Take one reading. Returns (value, note); raises ValueError if it cannot."""
    spec = requirement.acceptance_test or {}
    source = spec.get("source") or {}
    kind = source.get("kind")

    if kind == MetricSourceKind.LEDGER:
        rows = session.execute(
            select(LedgerEntry).where(
                LedgerEntry.project_id == requirement.project_id,
                LedgerEntry.kind == source["ledger_kind"],
            )
        ).scalars().all()
        total = float(sum(r.usd or 0 for r in rows))
        return total, f"{len(rows)} {source['ledger_kind']} ledger row(s)"

    status, body = (fetch or _default_fetch)(source["url"])
    if status != 200:
        raise ValueError(f"GET {source['url']} returned {status}")
    try:
        document = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{source['url']} did not return JSON: {exc}") from exc
    value = extract(document, source["path"])
    return value, f"{source['url']} {source['path']} = {value}"


def record(session: Session, requirement: Requirement, fetch=None) -> Measurement:
    """One reading, written down whether or not it worked.

    A failed read is a row with `error` set, not a silence. A scoreboard that
    stops reporting looks exactly like a scoreboard reporting no change, and
    the difference is the whole point of having one.
    """
    row = Measurement(
        id=ids.new_id(ids.MEASUREMENT),
        project_id=requirement.project_id,
        requirement_id=requirement.id,
        value=0.0,
        at=utcnow(),
    )
    try:
        value, note = read_value(session, requirement, fetch=fetch)
        row.value, row.source_note = value, note[:2000]
    except Exception as exc:  # noqa: BLE001 — any failure is a recorded non-reading
        row.error = f"{type(exc).__name__}: {exc}"[:2000]
    session.add(row)
    return row


# ── the schedule ────────────────────────────────────────────────────────────────


def metric_requirements(session: Session, project_id: str | None = None) -> list[Requirement]:
    stmt = select(Requirement)
    if project_id:
        stmt = stmt.where(Requirement.project_id == project_id)
    return [r for r in session.execute(stmt).scalars() if is_metric(r.acceptance_test)]


def latest(session: Session, requirement_id: str) -> Measurement | None:
    return session.execute(
        select(Measurement)
        .where(Measurement.requirement_id == requirement_id)
        .order_by(Measurement.at.desc())
        .limit(1)
    ).scalars().first()


def trend(session: Session, requirement_id: str, limit: int = TREND_WINDOW) -> list[Measurement]:
    """Oldest-first readings that actually read something."""
    rows = session.execute(
        select(Measurement)
        .where(Measurement.requirement_id == requirement_id, Measurement.error.is_(None))
        .order_by(Measurement.at.desc())
        .limit(limit)
    ).scalars().all()
    return list(reversed(rows))


def is_due(session: Session, requirement: Requirement, now=None) -> bool:
    spec = requirement.acceptance_test or {}
    hours = float(spec.get("measure_every_hours", DEFAULT_MEASURE_HOURS))
    last = latest(session, requirement.id)
    if last is None:
        return True
    return (now or utcnow()) - last.at >= timedelta(hours=hours)


def measure_due(session: Session, fetch=None, now=None) -> int:
    """Take every reading that is due. The janitor's entry point."""
    taken = 0
    for requirement in metric_requirements(session):
        if is_due(session, requirement, now=now):
            record(session, requirement, fetch=fetch)
            taken += 1
    return taken


# ── what the readings mean ──────────────────────────────────────────────────────


def met(requirement: Requirement, value: float) -> bool:
    spec = requirement.acceptance_test or {}
    target = float(spec["target"])
    if spec.get("direction") == MetricDirection.DECREASING:
        return value <= target
    return value >= target


def scoreboard(session: Session, requirement: Requirement) -> dict:
    """The metric as the CEO and the chairman should see it.

    `verdict` is arithmetic, never a judgement about whether the news is worth
    anyone's time:

      * ``met``     — the target is reached right now
      * ``losing``  — the latest reading is further from target than the oldest
                      in the window. Not "worse than last time": one bad reading
                      is noise, and a metric that cried wolf on noise would be
                      muted within a week.
      * ``gaining`` — closer than it was, and not yet there
      * ``flat``    — moved neither way
      * ``unknown`` — fewer than two readings, or none that succeeded
    """
    spec = requirement.acceptance_test or {}
    rows = trend(session, requirement.id)
    target = float(spec["target"])
    direction = spec.get("direction")
    values = [r.value for r in rows]

    if not values:
        verdict = "unknown"
    elif met(requirement, values[-1]):
        verdict = "met"
    elif len(values) < 2:
        verdict = "unknown"
    else:
        first_gap = abs(target - values[0])
        last_gap = abs(target - values[-1])
        verdict = "losing" if last_gap > first_gap else "gaining" if last_gap < first_gap else "flat"

    last = latest(session, requirement.id)
    return {
        "requirement_key": requirement.key,
        "statement": requirement.statement,
        "value": values[-1] if values else None,
        "target": target,
        "direction": direction,
        "trend": values,
        "verdict": verdict,
        "measured_at": last.at.isoformat() if last else None,
        "error": last.error if last else None,
    }


def project_scoreboard(session: Session, project_id: str) -> list[dict]:
    return [
        scoreboard(session, r) for r in metric_requirements(session, project_id)
    ]


# ── settling ────────────────────────────────────────────────────────────────────


def settle_metrics(session: Session, project_id: str) -> int:
    """Mark a metric requirement MET when the number arrives, and only then.

    The other test kinds settle when the *work* closes: every task done, then
    run the check. A metric cannot use that path and must not — the whole point
    is that it can be accepted before anyone knows what work will move it, so
    "all tasks closed" says nothing about whether it is true. What settles it is
    a reading at or past target, taken by the platform from a source the project
    does not author.

    The reverse move is deliberately absent. A metric that reaches target and
    later drifts back does NOT reopen: `met_at` records that the number was hit,
    which is a fact about a moment and stays true. Whether it is *still* true is
    what `scoreboard()` reports every cycle, and what a losing verdict is for.
    Silently un-metting scope would make a burn-down that walks backwards for
    reasons no task caused, and nobody could plan against it.
    """
    from yaaf.core.models import RequirementStatus
    from yaaf.server import policy

    settled = 0
    for requirement in metric_requirements(session, project_id):
        if requirement.status not in policy.OPEN_REQUIREMENT_STATUSES:
            continue
        if requirement.status == RequirementStatus.PROPOSED:
            continue  # not scope yet; the chairman has not accepted it
        last = latest(session, requirement.id)
        if last is None or last.error is not None or not met(requirement, last.value):
            continue
        was = requirement.status
        requirement.status = RequirementStatus.MET
        requirement.met_at = last.at
        policy.record_requirement_event(
            session, requirement, RequirementStatus.MET, "platform",
            f"measured {last.value} against target {requirement.acceptance_test['target']}",
            from_status=was,
        )
        settled += 1
    return settled
