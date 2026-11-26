"""Signed provenance for a Code Factory release.

An in-toto Statement with an SLSA v1 provenance predicate: the subject is the
commit that shipped, and the predicate records what the platform knows and no
agent wrote — every change in the release, who signed it, the gates it
passed, the SBOM's digest, the environment and the times. It is signed with
this install's Ed25519 key, held as a platform secret that no role is ever
granted, so anyone with the public key (GET /observe/provenance_key) can check
that this install, and nothing else, made the claim.

What it proves is where the release came from and what it passed — not that
the code is safe. It says so in its own words, so nobody reads more into it.
"""

from __future__ import annotations

import base64
import hashlib
import json

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from sqlalchemy.orm import Session

from yaaf.core import platform
from yaaf.core.models import Change, Project, Release, utcnow

KEY_NAME = "provenance_signing_key"
BUILD_TYPE = "https://github.com/dominicplouffe/yaaf/code-factory/release@v1"


def _key(session: Session) -> Ed25519PrivateKey:
    raw = platform.secret_value(session, KEY_NAME)
    if raw:
        return serialization.load_pem_private_key(raw.encode(), password=None)
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()).decode()
    platform.set_secret(session, KEY_NAME, pem)
    session.flush()
    return key


def public_key_pem(session: Session) -> str:
    return _key(session).public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()


def canonical(statement: dict) -> bytes:
    return json.dumps(statement, sort_keys=True, separators=(",", ":")).encode()


def statement(session: Session, project: Project, release: Release,
              sbom_digest: str | None) -> dict:
    changes = [session.get(Change, cid) for cid in release.change_ids or []]
    return {
        "_type": "https://in-toto.io/Statement/v1",
        "subject": [{"name": f"git+https://github.com/{project.github_repo}",
                     "digest": {"gitCommit": release.sha}}],
        "predicateType": "https://slsa.dev/provenance/v1",
        "predicate": {
            "buildDefinition": {
                "buildType": BUILD_TYPE,
                "externalParameters": {"version": release.version,
                                       "environment": release.environment},
                "resolvedDependencies": [
                    {"uri": f"git+https://github.com/{project.github_repo}",
                     "digest": {"gitCommit": release.sha}}],
                "internalParameters": {
                    "changes": [{
                        "number": c.number, "title": c.title, "merged_sha": c.merged_sha,
                        "reviewed_by": c.reviewer_agent_id, "reviewed_sha": c.reviewed_sha,
                        "gates": {k: bool((v or {}).get("ok")) for k, v in
                                  (c.gates or {}).items()},
                    } for c in changes if c is not None],
                    "sbom_sha256": sbom_digest,
                    "means": "where this release came from and which checks it passed; "
                             "not a claim that the code is safe",
                },
            },
            "runDetails": {
                "builder": {"id": f"yaaf:{project.id}"},
                "metadata": {"invocationId": release.id,
                             "startedOn": release.created_at.isoformat()
                             if release.created_at else None,
                             "finishedOn": utcnow().isoformat()},
            },
        },
    }


def sign_release(session: Session, project: Project, release: Release,
                 sbom: bytes | None = None) -> None:
    digest = hashlib.sha256(sbom).hexdigest() if sbom else None
    body = statement(session, project, release, digest)
    release.provenance = body
    release.provenance_sig = base64.b64encode(_key(session).sign(canonical(body))).decode()


def verify(public_pem: str, body: dict, signature_b64: str) -> bool:
    from cryptography.exceptions import InvalidSignature

    key = serialization.load_pem_public_key(public_pem.encode())
    assert isinstance(key, Ed25519PublicKey)
    try:
        key.verify(base64.b64decode(signature_b64), canonical(body))
    except InvalidSignature:
        return False
    return True


def sbom_bytes(release: Release) -> bytes | None:
    from yaaf.core import artifacts

    if not release.sbom_uri:
        return None
    try:
        return artifacts.read_uri(release.sbom_uri)
    except Exception:  # noqa: BLE001 — a missing SBOM is recorded as missing
        return None

