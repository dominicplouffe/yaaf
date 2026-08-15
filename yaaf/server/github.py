"""GitHub workspace provisioning: one private repo per project.

A single platform-level secret named "github" (project_id NULL) holds a token
able to create repos and read the ones it owns. It is decrypted server-side in
exactly two places — provisioning here, and the runner's one-time clone — and
is never injected into a sandbox: agents push with their own role-scoped
"github" secret via the normal per-run injection.
"""

from __future__ import annotations

import httpx
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from yaaf.core.models import Project, Secret

API_BASE = "https://api.github.com"


def platform_token(session: Session) -> str | None:
    """The decrypted platform-level "github" token, or None when the secret
    was never installed. Callers pick their own failure mode: the admin API
    turns None into a 422, the runner degrades to a plain local dir."""
    from yaaf.core.crypto import decrypt

    secret = session.execute(
        select(Secret).where(Secret.project_id.is_(None), Secret.name == "github")
    ).scalar_one_or_none()
    if secret is None:
        return None
    return decrypt(secret.ciphertext)


def _request(method: str, path: str, token: str, json: dict | None = None) -> httpx.Response:
    """One hop to api.github.com — the single seam tests monkeypatch."""
    return httpx.request(
        method,
        f"{API_BASE}{path}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        json=json,
        timeout=30.0,
    )


def provision_repo(session: Session, project: Project, name_or_full: str) -> str:
    """Ensure the project's private repo exists and return its full_name.

    "owner/repo" verifies an existing repo is reachable with the platform
    token; a bare name creates a PRIVATE auto-init repo under the token's
    user. Auto-init matters: an empty repo has no default branch, and the
    runner's first clone should produce a working tree, not a warning.
    """
    token = platform_token(session)
    if token is None:
        raise HTTPException(
            status_code=422,
            detail='no platform-level "github" secret; install one '
                   "(POST /admin/secrets with project_id null, name 'github') first",
        )
    try:
        if "/" in name_or_full:
            response = _request("GET", f"/repos/{name_or_full}", token)
            if response.status_code != 200:
                raise HTTPException(
                    status_code=422,
                    detail=f"repo '{name_or_full}' is not accessible with the platform "
                           f"token (GitHub said {response.status_code})",
                )
        else:
            response = _request(
                "POST", "/user/repos", token,
                json={
                    "name": name_or_full,
                    "private": True,
                    "auto_init": True,
                    "description": f"yaaf workspace for project '{project.slug}'",
                },
            )
            if response.status_code not in (200, 201):
                raise HTTPException(
                    status_code=422,
                    detail=f"GitHub refused to create '{name_or_full}': "
                           f"{response.status_code} {response.text[:300]}",
                )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502, detail=f"could not reach GitHub: {type(exc).__name__}"
        ) from exc
    return response.json()["full_name"]


def clone_url(full_name: str, token: str) -> str:
    """The authenticated clone URL. Kept as its own seam so tests can point
    the runner's clone at a local file:// repo, and so the credentialed form
    exists only for the duration of the clone itself."""
    return f"https://x-access-token:{token}@github.com/{full_name}.git"


def public_url(full_name: str) -> str:
    """The credential-free remote the workspace keeps after cloning."""
    return f"https://github.com/{full_name}.git"
