"""GitHub workspace provisioning: one private repo per project.

A single platform-level secret named "github" (project_id NULL) holds a token
able to create repos and read the ones it owns. It is decrypted server-side in
exactly two places — provisioning here, and the runner's one-time clone — and
is never injected into a sandbox: agents push with their own role-scoped
"github" secret via the normal per-run injection.
"""

from __future__ import annotations

import logging

import httpx
from fastapi import HTTPException
from sqlalchemy.orm import Session

from yaaf.core.models import Project

API_BASE = "https://api.github.com"

log = logging.getLogger(__name__)


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


def check_runs(session: Session, repo: str, sha: str) -> list[dict]:
    """Every check run GitHub holds for one commit — the same objects a
    check_run webhook carries, read when no webhook can reach this install."""
    response = _call(session, "GET", f"/repos/{repo}/commits/{sha}/check-runs?per_page=100")
    if response.status_code != 200:
        raise GithubError(f"GitHub could not list checks for {sha[:12]}: "
                          f"{response.status_code}")
    return list(response.json().get("check_runs") or [])


def default_branch(session: Session, repo: str) -> str | None:
    """The repo's default branch as GitHub names it, or None if unreadable."""
    try:
        response = _call(session, "GET", f"/repos/{repo}")
    except GithubError:
        return None
    if response.status_code != 200:
        return None
    return response.json().get("default_branch") or None


def branch_head(session: Session, repo: str, branch: str) -> str | None:
    response = _call(session, "GET", f"/repos/{repo}/branches/{branch}")
    if response.status_code != 200:
        return None
    return ((response.json().get("commit") or {}).get("sha")) or None


def connect(session: Session, project: Project, name_or_full: str) -> dict:
    """Create (bare name) or check (owner/name) the project's repo and make it
    the project's: in a Code Factory, main protected and the webhook pointed
    here where GitHub can reach it. One path for the Settings card, the form
    and the janitor's late connect, so none of them half-does it."""
    from yaaf.core import ids, shape
    from yaaf.core.models import AuditLog

    full_name = provision_repo(session, project, name_or_full)
    project.github_repo = full_name
    integrations = {k: v for k, v in (project.integrations or {}).items()
                    if k not in ("repo_wanted", "repo_wanted_error", "repo_wanted_tried_at")}
    # The repo's own default branch. Every reader assumed "main", so a repo on
    # master or trunk could open no change and protected a branch it lacks.
    branch = default_branch(session, full_name)
    if branch:
        integrations["default_branch"] = branch
    protection = None
    if shape.is_factory(project):
        # Only the platform merges in a factory. GitHub's own rule says so too
        # where the token may set it; where it may not, the platform's push
        # detection is the rule that holds, and the result is recorded either
        # way so the project page can say which.
        protection = protect_main(session, full_name, branch or "main")
        integrations["branch_protection"] = protection
    project.integrations = integrations
    if shape.is_factory(project):
        project.integrations = {**project.integrations,
                                "github_hook": ensure_hook(session, project, full_name)}
    session.add(AuditLog(
        id=ids.new_id(ids.AUDIT), project_id=project.id, actor="system:github",
        action="project.github_attach", entity="project", entity_id=project.id,
        detail={"github_repo": full_name,
                **({"branch_protection": protection} if protection is not None else {})}))
    return {"github_repo": full_name,
            **({"branch_protection": protection} if protection is not None else {})}


#: How long a failed late connect waits before it is tried again.
RETRY_WANTED_S = 600


def connect_wanted(session: Session, project: Project) -> dict | None:
    """The repo a factory was founded wanting, made once it can be.

    The form asks for the repo name and makes it right after Create — with
    the platform's GitHub key. Founded before that key was saved, the make
    failed and the name was lost, and the chairman met an empty field. The
    name is kept (`repo_wanted`) and made here, on the janitor's tick, as soon
    as the key exists. A refusal (the name is taken, the key may not create
    repos) is written where the Settings card shows it and tried again later,
    not every tick."""
    import time

    wanted = (project.integrations or {}).get("repo_wanted")
    if project.github_repo or not wanted or platform_token(session) is None:
        return None
    tried = float((project.integrations or {}).get("repo_wanted_tried_at") or 0)
    if time.time() - tried < RETRY_WANTED_S:
        return None
    try:
        return connect(session, project, str(wanted))
    except (HTTPException, GithubError) as exc:
        said = exc.detail if isinstance(exc, HTTPException) else str(exc)
        project.integrations = {**(project.integrations or {}),
                                "repo_wanted_error": str(said)[:300],
                                "repo_wanted_tried_at": time.time()}
        log.warning("could not make %s's repo %s: %s", project.slug, wanted, said)
        return None


def ensure_hook(session: Session, project: Project, repo: str) -> dict:
    """Point the repo's webhook at this install, when GitHub can reach it.

    Without a public address there is nothing GitHub could deliver to, and
    the platform reads the repo itself every minute (changes.poll_github);
    the hook then only makes it quicker. Never fatal: the answer is recorded
    and shown, and polling covers what a missing hook would have carried."""
    from yaaf.core.config import settings

    base = (settings().public_base_url or "").rstrip("/")
    if not base.startswith("https://"):
        return {"ok": False, "detail": "no public https address (YAAF_PUBLIC_BASE_URL); "
                                       "the platform checks GitHub every minute instead"}
    secret = (project.integrations or {}).get("github_webhook_secret")
    if not secret:
        return {"ok": False, "detail": "the project has no webhook secret"}
    url = f"{base}/webhooks/github/{project.id}"
    try:
        listed = _call(session, "GET", f"/repos/{repo}/hooks")
        if listed.status_code == 200 and any(
                (h.get("config") or {}).get("url") == url for h in listed.json()):
            return {"ok": True, "detail": "already in place", "url": url}
        made = _call(session, "POST", f"/repos/{repo}/hooks", json={
            "name": "web", "active": True,
            "events": ["pull_request", "check_run", "push", "issues", "issue_comment"],
            "config": {"url": url, "content_type": "json", "secret": secret,
                       "insecure_ssl": "0"}})
    except GithubError as exc:
        return {"ok": False, "detail": str(exc)[:300]}
    if made.status_code not in (200, 201):
        return {"ok": False, "detail": f"GitHub refused the webhook ({made.status_code}); "
                                       "the platform checks GitHub every minute instead"}
    return {"ok": True, "detail": "created", "url": url}


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
        # A rename's old name too: moving a workflow or a test out of its
        # path is a change to that path, and the new name alone hides it.
        files.extend({"filename": f.get("filename"), "status": f.get("status"),
                      "patch": f.get("patch"),
                      **({"previous_filename": f["previous_filename"]}
                         if f.get("previous_filename") else {})} for f in batch)
        if len(batch) < 100:
            break
    return files


def create_release(session: Session, repo: str, *, tag: str, sha: str, name: str,
                   body: str) -> None:
    """Tag the commit that went live, with its notes. The record of what
    shipped is the platform's; the tag is where people look for it."""
    response = _call(session, "POST", f"/repos/{repo}/releases",
                     json={"tag_name": tag, "target_commitish": sha, "name": name,
                           "body": body})
    if response.status_code not in (200, 201):
        raise GithubError(f"GitHub refused the release tag: {response.status_code}")


def read_file(session: Session, repo: str, path: str, ref: str | None = None) -> str | None:
    """One file's text from the repo (main unless `ref`), or None if absent."""
    import base64

    suffix = f"?ref={ref}" if ref else ""
    response = _call(session, "GET", f"/repos/{repo}/contents/{path}{suffix}")
    if response.status_code == 404:
        return None
    if response.status_code != 200:
        raise GithubError(f"GitHub could not read {path}: {response.status_code}")
    body = response.json() or {}
    try:
        return base64.b64decode(body.get("content") or "").decode("utf-8", "replace")
    except ValueError:
        return None
