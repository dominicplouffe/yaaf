"""The evidence verifier: teeth for the most gameable field in the system.

The agent that does the work also writes the evidence row, so `external` is
never taken at its word. Agent-claimed external evidence counts only after the
platform — never the agent — has:

1. confirmed the source URL resolves (fetched server-side, hashed, snapshotted)
2. confirmed the domain is not one the project controls
3. confirmed the evidenced event postdates its task's creation

Platform-attested evidence (connectors, M4) skips this — agents can't forge it.
Failures are recorded with the reason; transient fetch errors retry a few times
and then fail closed. Unverified evidence never counts toward drought or the
external-evidence floor.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from urllib.parse import urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from yaaf.core.config import settings
from yaaf.core.models import (
    Evidence,
    EvidenceKind,
    EvidenceProvenance,
    Project,
    Task,
    VerificationStatus,
    utcnow,
)

log = logging.getLogger("yaaf.verifier")

MAX_ATTEMPTS = 5
MAX_BYTES = 2_000_000
FETCH_TIMEOUT_S = 20.0


def _domain_controlled(url: str, controlled_domains: list[str]) -> bool:
    host = (urlparse(url).hostname or "").lower()
    for domain in controlled_domains:
        domain = domain.lower().lstrip("*.")
        if host == domain or host.endswith("." + domain):
            return True
    return False


def _fail(evidence: Evidence, reason: str) -> None:
    evidence.verification = VerificationStatus.FAILED
    evidence.verifier_notes = ((evidence.verifier_notes or "") + f"\n[verifier] {reason}").strip()


def verify_pending_evidence(session: Session, fetch=None) -> int:
    """Process pending agent-claimed external evidence. `fetch` is injectable
    for tests; default fetches over HTTP with redirects and a size cap."""
    pending = list(
        session.execute(
            select(Evidence).where(
                Evidence.kind == EvidenceKind.EXTERNAL,
                Evidence.provenance == EvidenceProvenance.AGENT_CLAIMED,
                Evidence.verification == VerificationStatus.PENDING,
            )
        ).scalars()
    )
    processed = 0
    for evidence in pending:
        try:
            _verify_one(session, evidence, fetch or _default_fetch)
            processed += 1
        except Exception:
            log.exception("verifier crashed on %s", evidence.id)
    return processed


def _verify_one(session: Session, evidence: Evidence, fetch) -> None:
    project = session.get(Project, evidence.project_id)
    url = (evidence.source_url or "").strip()

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        _fail(evidence, f"source_url is not a fetchable http(s) URL: {url!r}")
        return

    # The domain must be outside the project's control. Evidence on your own
    # site is a diary entry, not the outside world acting.
    if _domain_controlled(url, project.controlled_domains or []):
        _fail(
            evidence,
            f"domain {parsed.hostname} is registered as controlled by this project; "
            "external evidence must come from a domain the project does not control",
        )
        return

    # The evidenced event must postdate the task it claims to support.
    if evidence.task_id:
        task = session.get(Task, evidence.task_id)
        if task is not None:
            event_at = evidence.occurred_at or evidence.created_at
            if event_at is not None and task.created_at is not None:
                task_created = task.created_at
                if event_at.tzinfo is None:
                    event_at = event_at.replace(tzinfo=task_created.tzinfo)
                if event_at < task_created:
                    _fail(
                        evidence,
                        "the evidenced event predates the task it claims to support "
                        f"({event_at.isoformat()} < {task_created.isoformat()})",
                    )
                    return

    evidence.verify_attempts += 1
    try:
        status_code, content = fetch(url)
    except Exception as exc:
        note = f"fetch attempt {evidence.verify_attempts} failed: {exc}"
        evidence.verifier_notes = ((evidence.verifier_notes or "") + f"\n[verifier] {note}").strip()
        if evidence.verify_attempts >= MAX_ATTEMPTS:
            _fail(evidence, f"unreachable after {MAX_ATTEMPTS} attempts; failing closed")
        return

    if status_code >= 400:
        if evidence.verify_attempts >= MAX_ATTEMPTS:
            _fail(evidence, f"source URL returns HTTP {status_code}; failing closed")
        else:
            evidence.verifier_notes = (
                (evidence.verifier_notes or "")
                + f"\n[verifier] attempt {evidence.verify_attempts}: HTTP {status_code}"
            ).strip()
        return

    digest = hashlib.sha256(content).hexdigest()
    snapshot_dir = Path(settings().artifacts_root) / "evidence"
    try:
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        snapshot_path = snapshot_dir / f"{evidence.id}.snapshot"
        snapshot_path.write_bytes(content[:MAX_BYTES])
        evidence.snapshot_path = str(snapshot_path)
    except OSError as exc:  # snapshot is best-effort; the hash is the record
        log.warning("could not write snapshot for %s: %s", evidence.id, exc)
    evidence.snapshot_sha256 = digest
    evidence.verification = VerificationStatus.VERIFIED
    evidence.verifier_notes = (
        (evidence.verifier_notes or "")
        + f"\n[verifier] verified at {utcnow().isoformat()}; sha256={digest[:16]}…"
    ).strip()


def _default_fetch(url: str) -> tuple[int, bytes]:
    with httpx.Client(follow_redirects=True, timeout=FETCH_TIMEOUT_S) as client:
        response = client.get(url, headers={"User-Agent": "yaaf-verifier/0.1"})
        return response.status_code, response.content[:MAX_BYTES]
