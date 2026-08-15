"""Invariant selftests: inbound evidence connectors. A webhook post becomes
platform_attested evidence only when its HMAC checks out — an agent can never
forge one because it never holds the webhook secret."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import timedelta

from sqlalchemy import select

from tests.conftest import ADMIN, make_project
from yaaf.core.db import db_session
from yaaf.core.models import Evidence, utcnow
from yaaf.server import policy


def _provision(client, project_id: str) -> dict:
    response = client.post(f"/admin/projects/{project_id}/webhook_secrets", headers=ADMIN)
    assert response.status_code == 200, response.text
    return response.json()


def _github_sign(secret: str, raw: bytes) -> str:
    # Computed exactly as GitHub documents it: sha256=<hex hmac of the raw body>.
    return "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()


def _star_payload(login: str = "stranger") -> bytes:
    return json.dumps({
        "action": "created",
        "sender": {"login": login},
        "repository": {"full_name": "acme/widget",
                       "html_url": "https://github.com/acme/widget"},
    }).encode()


def _github_headers(secret: str, raw: bytes, event: str = "star",
                    delivery: str = "delivery-1") -> dict:
    return {
        "X-GitHub-Event": event,
        "X-GitHub-Delivery": delivery,
        "X-Hub-Signature-256": _github_sign(secret, raw),
        "Content-Type": "application/json",
    }


def _evidence_count(project_id: str) -> int:
    with db_session() as session:
        return len(list(session.execute(
            select(Evidence).where(Evidence.project_id == project_id)
        ).scalars()))


def test_unsigned_or_forged_posts_are_refused(client):
    project = make_project(client)
    raw = _star_payload()

    # Before secrets exist there is nothing to verify against — refuse.
    unconfigured = client.post(
        f"/webhooks/github/{project['id']}", content=raw,
        headers={"X-GitHub-Event": "star", "Content-Type": "application/json"},
    )
    assert unconfigured.status_code == 403

    secrets = _provision(client, project["id"])

    unsigned = client.post(
        f"/webhooks/github/{project['id']}", content=raw,
        headers={"X-GitHub-Event": "star", "Content-Type": "application/json"},
    )
    assert unsigned.status_code == 403

    forged = client.post(
        f"/webhooks/github/{project['id']}", content=raw,
        headers={**_github_headers(secrets["github_webhook_secret"], raw),
                 "X-Hub-Signature-256": "sha256=" + "0" * 64},
    )
    assert forged.status_code == 403

    generic_forged = client.post(
        f"/webhooks/generic/{project['id']}",
        content=json.dumps({"description": "a stranger did something"}).encode(),
        headers={"X-Yaaf-Signature": "0" * 64, "Content-Type": "application/json"},
    )
    assert generic_forged.status_code == 403

    unknown = client.post(
        "/webhooks/github/proj_doesnotexist", content=raw,
        headers=_github_headers(secrets["github_webhook_secret"], raw),
    )
    assert unknown.status_code == 404

    assert _evidence_count(project["id"]) == 0


def test_signed_star_creates_countable_attested_evidence(client):
    project = make_project(client)
    secrets = _provision(client, project["id"])
    raw = _star_payload(login="stranger")

    response = client.post(
        f"/webhooks/github/{project['id']}", content=raw,
        headers=_github_headers(secrets["github_webhook_secret"], raw),
    )
    assert response.status_code == 200, response.text
    evidence_id = response.json()["evidence_id"]

    with db_session() as session:
        row = session.get(Evidence, evidence_id)
        assert row.kind == "external"
        assert row.provenance == "platform_attested"
        assert row.verification == "not_applicable"
        assert row.created_by_agent_id is None
        assert row.description == "github star by stranger on acme/widget"
        assert row.source_url == "https://github.com/acme/widget"
        # The whole point: attested rows count without the verifier touching them.
        window = utcnow() - timedelta(hours=1)
        assert policy.countable_external_evidence(session, project["id"], window) == 1


def test_controlled_account_star_is_ignored(client):
    # ProjectCreate passes controlled_domains straight through; "github:me"
    # registers the project's own GitHub account.
    project = make_project(client, slug="ctrl", controlled_domains=["github:me"])
    secrets = _provision(client, project["id"])
    raw = _star_payload(login="me")

    response = client.post(
        f"/webhooks/github/{project['id']}", content=raw,
        headers=_github_headers(secrets["github_webhook_secret"], raw),
    )
    assert response.status_code == 200
    assert response.json() == {"ignored": "controlled account"}
    assert _evidence_count(project["id"]) == 0


def test_duplicate_delivery_creates_one_row(client):
    project = make_project(client)
    secrets = _provision(client, project["id"])
    raw = _star_payload()
    headers = _github_headers(secrets["github_webhook_secret"], raw, delivery="dup-42")

    first = client.post(f"/webhooks/github/{project['id']}", content=raw, headers=headers)
    assert first.status_code == 200
    assert "evidence_id" in first.json()

    second = client.post(f"/webhooks/github/{project['id']}", content=raw, headers=headers)
    assert second.status_code == 200
    assert second.json() == {"ignored": "duplicate delivery"}
    assert _evidence_count(project["id"]) == 1


def test_generic_connector_with_valid_signature(client):
    project = make_project(client)
    secrets = _provision(client, project["id"])
    raw = json.dumps({
        "description": "newsletter signup from an unaffiliated reader",
        "source_url": "https://mailer.example.net/event/123",
    }).encode()

    response = client.post(
        f"/webhooks/generic/{project['id']}", content=raw,
        headers={
            "X-Yaaf-Signature": hmac.new(
                secrets["webhook_secret"].encode(), raw, hashlib.sha256
            ).hexdigest(),
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 200, response.text
    evidence_id = response.json()["evidence_id"]

    with db_session() as session:
        row = session.get(Evidence, evidence_id)
        assert row.provenance == "platform_attested"
        assert row.created_by_agent_id is None
        assert row.description == (
            "connector: newsletter signup from an unaffiliated reader"
        )
        assert row.source_url == "https://mailer.example.net/event/123"
        window = utcnow() - timedelta(hours=1)
        assert policy.countable_external_evidence(session, project["id"], window) == 1
