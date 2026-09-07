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

from sqlalchemy import select
from sqlalchemy.orm import Session

from yaaf.core import ids, safefetch
from yaaf.core.models import (
    LedgerEntry,
    Measurement,
    MetricDirection,
    MetricSourceKind,
    Project,
    ProjectStatus,
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
#: Requirement statuses worth spending a fetch on: accepted scope, not yet
#: settled. Mirrors `policy.OPEN_REQUIREMENT_STATUSES`, spelled out here so this
#: module does not import policy at module scope.
LIVE_STATUSES = ("pending", "in_progress", "checking")

#: Ledger kinds a metric may sum: the ones no agent verb can write.
#:
#: `LedgerKind`'s own docstring says REVENUE "is written only by signed webhooks
#: — no agent verb exists", and that is the entire reason this source kind is
#: allowed to exist. MODEL_USAGE and EXTERNAL_SPEND are booked by agent routes
#: (`/agent/chat_usage`, `/agent/ledger/spend`, `/agent/purchases/{id}/finish`),
#: so a metric summing either is a target the project reaches by spending its
#: own money — the self-authored finish line this whole test kind exists to
#: make impossible.
UNFORGEABLE_LEDGER_KINDS = frozenset({"revenue"})
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
        ledger_kind = (source.get("ledger_kind") or "").strip()
        if ledger_kind not in UNFORGEABLE_LEDGER_KINDS:
            raise Refusal(
                f"metric source.ledger_kind must be one of "
                f"{', '.join(sorted(UNFORGEABLE_LEDGER_KINDS))} — the ledger rows this "
                "project cannot write. `model_usage` and `external_spend` are booked by "
                "agent verbs from inside the project, so a metric summing them is a "
                "number you can move by spending money, which is a status report with "
                "extra steps."
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
    """Through the SSRF guard, because this runs in the janitor and not in the
    sandbox: the per-role egress allowlist that governs an agent's own traffic
    does not apply to a fetch the platform makes on its behalf."""
    return safefetch.fetch(url, timeout=FETCH_TIMEOUT_S, max_bytes=MAX_BYTES,
                           user_agent="yaaf-metrics/0.1")


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


def metric_requirements(session: Session, project_id: str | None = None,
                        *, live_only: bool = False) -> list[Requirement]:
    """Metric requirements, optionally only the ones worth spending a fetch on.

    `live_only` is what the measuring loop uses, and it matters for more than
    tidiness. Without it the platform polls:

    * **PROPOSED** requirements the chairman never accepted — so proposing
      alone, with no human in the loop, would be enough to start outbound
      traffic on a schedule the agent chose;
    * **MET and CUT** ones, forever, because nothing else ever stops a metric;
    * requirements on paused, stopped and completed projects.

    Reporting (`scoreboard`, the Plan screen) deliberately does NOT pass it: a
    settled metric still has readings worth showing, and a proposal the chairman
    is looking at should display what it would measure.
    """
    stmt = select(Requirement)
    if project_id:
        stmt = stmt.where(Requirement.project_id == project_id)
    if live_only:
        stmt = stmt.where(Requirement.status.in_(LIVE_STATUSES))
    rows = [r for r in session.execute(stmt).scalars() if is_metric(r.acceptance_test)]
    if not live_only:
        return rows
    running = {
        pid for (pid,) in session.execute(
            select(Project.id).where(Project.status == ProjectStatus.RUNNING)
        )
    }
    return [r for r in rows if r.project_id in running]


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


def measure_due(session: Session, project_id: str | None = None,
                fetch=None, now=None) -> int:
    """Take every reading that is due for one project. The janitor's entry point.

    Per-project, and called from `_janitor_project`, so it reaches BOTH janitor
    entry points. It was first wired into `janitor_once` alone — whose docstring
    says in as many words that it is the test harness's entry point and that
    production uses `janitor_all` — which made the entire feature green in CI
    and dead in production: no reading was ever taken, so every scoreboard sat
    at `unknown` and no metric requirement could ever settle.
    """
    taken = 0
    for requirement in metric_requirements(session, project_id, live_only=True):
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

    # Two different readings, kept apart on purpose. `value` and `measured_at`
    # both come from the last SUCCESSFUL reading, because stamping a week-old
    # number with this morning's failed-read timestamp is exactly the confusion
    # `record` exists to prevent — "a scoreboard that stops reporting looks
    # exactly like one reporting no change". `error` and `attempted_at` describe
    # the most recent attempt of any kind, so a source that has gone down says
    # so beside a number that is honestly labelled as old.
    last_ok = rows[-1] if rows else None
    last_any = latest(session, requirement.id)
    stale = bool(last_any is not None and last_any.error and last_ok is not None
                 and last_any.id != last_ok.id)
    return {
        "requirement_key": requirement.key,
        "statement": requirement.statement,
        "value": last_ok.value if last_ok else None,
        "target": target,
        "direction": direction,
        "trend": values,
        "verdict": verdict,
        "measured_at": last_ok.at.isoformat() if last_ok else None,
        "attempted_at": last_any.at.isoformat() if last_any else None,
        "error": last_any.error if last_any else None,
        # The number is real but is not current, and every surface that prints
        # it needs to be able to say so rather than implying a fresh reading.
        "stale": stale,
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
