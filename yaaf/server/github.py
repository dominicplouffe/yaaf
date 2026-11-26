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
from sqlalchemy.orm import Session

from yaaf.core.models import Project

API_BASE = "https://api.github.com"


def platform_token(session: Session) -> str | None:
    """The decrypted platform-level "github" token, or None when the secret
    was never installed. Callers pick their own failure mode: the admin API
    turns None into a 422, the runner degrades to a plain local dir."""
    from yaaf.core import platform

    return platform.secret_value(session, "github")


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


# ── Code Factory: the calls only the platform makes ─────────────────────────────
#
# Opening, reading and merging a pull request, and protecting main, all happen
# with the platform token. An agent pushes a branch with its own token and asks
# for the rest; nothing an agent holds can merge.


class GithubError(Exception):
    """GitHub said no, or could not be reached. Carries what to tell a person."""


def _call(session: Session, method: str, path: str, json: dict | None = None) -> httpx.Response:
    token = platform_token(session)
    if token is None:
        raise GithubError('no platform-level "github" secret is installed')
    try:
        return _request(method, path, token, json=json)
    except httpx.HTTPError as exc:
        raise GithubError(f"could not reach GitHub: {type(exc).__name__}") from exc


def open_pull_request(session: Session, repo: str, *, head: str, base: str,
                      title: str, body: str) -> dict:
    """Open the PR and return GitHub's object: number, head sha and the line
    counts the size gate reads — GitHub's count, never the agent's."""
    response = _call(session, "POST", f"/repos/{repo}/pulls",
                     json={"head": head, "base": base, "title": title, "body": body,
                           "maintainer_can_modify": False})
    if response.status_code not in (200, 201):
        raise GithubError(f"GitHub refused to open the pull request: "
                          f"{response.status_code} {response.text[:300]}")
    return response.json()


def get_pull_request(session: Session, repo: str, number: int) -> dict:
    response = _call(session, "GET", f"/repos/{repo}/pulls/{number}")
    if response.status_code != 200:
        raise GithubError(f"GitHub could not read pull request #{number}: "
                          f"{response.status_code}")
    return response.json()


def merge_pull_request(session: Session, repo: str, number: int, *, sha: str,
                       title: str) -> str:
    """Squash-merge, pinned to the head the gates were run on. GitHub refuses
    the merge if the branch moved since (`sha` is its compare-and-swap), so a
    push that lands after the review cannot ride the review in. Returns the
    merge commit's sha."""
    response = _call(session, "PUT", f"/repos/{repo}/pulls/{number}/merge",
                     json={"sha": sha, "merge_method": "squash", "commit_title": title})
    if response.status_code != 200:
        raise GithubError(f"GitHub refused the merge: {response.status_code} "
                          f"{response.text[:300]}")
    return response.json().get("sha") or ""


def close_pull_request(session: Session, repo: str, number: int, comment: str) -> None:
    _call(session, "POST", f"/repos/{repo}/issues/{number}/comments", json={"body": comment})
    _call(session, "PATCH", f"/repos/{repo}/pulls/{number}", json={"state": "closed"})


def protect_main(session: Session, repo: str, branch: str = "main") -> dict:
    """Ask GitHub to refuse direct pushes to main: changes only through a pull
    request, force-pushes and deletion off. Best effort and said so — a plan
    or a token without admin rights cannot set it, and then the platform's own
    detection (a push to main it did not make freezes merging) is the rule
    that still holds."""
    try:
        response = _call(session, "PUT", f"/repos/{repo}/branches/{branch}/protection", json={
            "required_status_checks": None,
            "enforce_admins": False,
            # Zero approvals, but a pull request required: that is what stops a
            # direct push. The approval that counts is a challenge seat's, which
            # the platform checks itself before it merges.
            "required_pull_request_reviews": {"required_approving_review_count": 0},
            "restrictions": None,
            "required_linear_history": True,
            "allow_force_pushes": False,
            "allow_deletions": False,
        })
    except GithubError as exc:
        return {"ok": False, "detail": str(exc)}
    if response.status_code != 200:
        return {"ok": False, "detail": f"GitHub said {response.status_code}: "
                                       f"{response.text[:200]}"}
    return {"ok": True, "detail": f"{branch} accepts changes only through a pull request"}


def list_pull_files(session: Session, repo: str, number: int) -> list[dict]:
    """Every file in the pull request with its patch, as GitHub counts them —
    up to 3,000, which is far past any change the size gate lets through."""
    files: list[dict] = []
    for page in range(1, 31):
        response = _call(session, "GET",
                         f"/repos/{repo}/pulls/{number}/files?per_page=100&page={page}")
        if response.status_code != 200:
            raise GithubError(f"GitHub could not list the files of #{number}: "
                              f"{response.status_code}")
        batch = response.json() or []
        files.extend({"filename": f.get("filename"), "status": f.get("status"),
                      "patch": f.get("patch")} for f in batch)
        if len(batch) < 100:
            break
    return files
