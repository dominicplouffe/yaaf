"""Publishing through GitHub: sign in with a device code, then open the pull
request on the publisher's behalf.

The device flow is the one `gh auth login` and `mcp-publisher login github`
use: yaaf shows a code, the person enters it at github.com, and yaaf never sees
a password and needs no callback address, which an install on localhost could
not offer anyway. The token is kept encrypted, belongs to the person who signed
in, is never granted to a role and never enters a sandbox.

Every call here goes to api.github.com or github.com and nowhere else. Tests
replace `_request`.
"""

from __future__ import annotations

import base64
import time

import httpx

API = "https://api.github.com"
DEVICE_CODE_URL = "https://github.com/login/device/code"
TOKEN_URL = "https://github.com/login/oauth/access_token"
#: Enough to fork a public repository and open a pull request from the fork.
SCOPE = "public_repo"
TIMEOUT_S = 20.0


class GitHubError(Exception):
    def __init__(self, text: str, status: int = 502):
        super().__init__(text)
        self.text = text
        self.status = status


def _request(method: str, url: str, *, token: str | None = None, json: dict | None = None,
             data: dict | None = None) -> tuple[int, dict | list | None]:
    headers = {"Accept": "application/json", "User-Agent": "yaaf-publish/1",
               "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        response = httpx.request(method, url, headers=headers, json=json, data=data,
                                 timeout=TIMEOUT_S)
    except httpx.HTTPError as exc:
        raise GitHubError(f"could not reach GitHub: {type(exc).__name__}") from None
    try:
        body = response.json() if response.content else None
    except ValueError:
        body = None
    return response.status_code, body


def _ok(status: int, body, what: str):
    if status >= 400:
        said = (body or {}).get("message") if isinstance(body, dict) else None
        raise GitHubError(f"GitHub refused to {what}: {said or status}",
                          status=403 if status in (401, 403) else 502)
    return body


# ── signing in ─────────────────────────────────────────────────────────────────

def start_device(client_id: str) -> dict:
    status, body = _request("POST", DEVICE_CODE_URL, data={"client_id": client_id, "scope": SCOPE})
    body = _ok(status, body, "start a sign-in")
    if not isinstance(body, dict) or "device_code" not in body:
        raise GitHubError("GitHub did not start a sign-in")
    return {"device_code": body["device_code"], "user_code": body["user_code"],
            "verification_uri": body.get("verification_uri", "https://github.com/login/device"),
            "interval": int(body.get("interval", 5)), "expires_in": int(body.get("expires_in", 900))}


def poll_device(client_id: str, device_code: str) -> dict:
    """{"state": "pending" | "slow_down" | "done" | "expired" | "denied", "token"?}"""
    status, body = _request("POST", TOKEN_URL, data={
        "client_id": client_id, "device_code": device_code,
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code"})
    body = body if isinstance(body, dict) else {}
    if body.get("access_token"):
        return {"state": "done", "token": body["access_token"]}
    error = body.get("error")
    return {"state": {"authorization_pending": "pending", "slow_down": "slow_down",
                      "expired_token": "expired", "access_denied": "denied"}.get(error, "pending")
            if error else "pending"}


def login_of(token: str) -> str:
    status, body = _request("GET", f"{API}/user", token=token)
    body = _ok(status, body, "say who you are")
    return str(body["login"])


# ── submitting ─────────────────────────────────────────────────────────────────

def next_version(token: str, repo: str, handle: str, slug: str) -> int:
    path = f"listings/{handle.lower()}/{slug}/versions"
    status, body = _request("GET", f"{API}/repos/{repo}/contents/{path}", token=token)
    if status == 404:
        return 1
    body = _ok(status, body, "read your earlier versions")
    numbers = [int(item["name"][:-5]) for item in body or []
               if isinstance(item, dict) and item.get("name", "").endswith(".json")
               and item["name"][:-5].isdigit()]
    return max(numbers, default=0) + 1


def _fork(token: str, repo: str, login: str) -> str:
    status, body = _request("POST", f"{API}/repos/{repo}/forks", token=token, json={})
    body = _ok(status, body, "fork the registry")
    fork = body.get("full_name") or f"{login}/{repo.split('/')[1]}"
    for _ in range(10):  # a new fork takes a moment to exist
        s, _b = _request("GET", f"{API}/repos/{fork}", token=token)
        if s == 200:
            return fork
        time.sleep(1.5)
    raise GitHubError("GitHub has not finished making your copy of the registry; try again in a minute")


def submit(token: str, repo: str, *, branch: str, files: dict[str, bytes], title: str,
           body: str, base: str = "main") -> dict:
    """Fork, branch, write `files`, open (or reuse) the pull request."""
    login = login_of(token)
    fork = _fork(token, repo, login)
    status, ref = _request("GET", f"{API}/repos/{repo}/git/ref/heads/{base}", token=token)
    ref = _ok(status, ref, "read the registry")
    sha = ref["object"]["sha"]
    status, existing = _request("GET", f"{API}/repos/{fork}/git/ref/heads/{branch}", token=token)
    if status == 404:
        _ok(*_request("POST", f"{API}/repos/{fork}/git/refs", token=token,
                      json={"ref": f"refs/heads/{branch}", "sha": sha}), "make a branch")
    for path, raw in files.items():
        status, current = _request("GET", f"{API}/repos/{fork}/contents/{path}?ref={branch}", token=token)
        payload = {"message": title, "branch": branch, "content": base64.b64encode(raw).decode()}
        if status == 200 and isinstance(current, dict) and current.get("sha"):
            payload["sha"] = current["sha"]
        _ok(*_request("PUT", f"{API}/repos/{fork}/contents/{path}", token=token, json=payload),
            f"write {path}")
    owner = fork.split("/")[0]
    status, open_prs = _request("GET", f"{API}/repos/{repo}/pulls?state=open&head={owner}:{branch}",
                                token=token)
    if status == 200 and open_prs:
        pr = open_prs[0]
    else:
        pr = _ok(*_request("POST", f"{API}/repos/{repo}/pulls", token=token, json={
            "title": title, "head": f"{owner}:{branch}", "base": base, "body": body,
            "maintainer_can_modify": True}), "open the pull request")
    return {"login": login, "number": pr["number"], "url": pr["html_url"]}


def review_state(token: str | None, repo: str, number: int) -> dict:
    """Where a pull request stands, in the publisher's words."""
    status, pr = _request("GET", f"{API}/repos/{repo}/pulls/{number}", token=token)
    pr = _ok(status, pr, "read the pull request")
    if pr.get("merged_at"):
        return {"state": "published", "note": None}
    if pr.get("state") == "closed":
        return {"state": "closed", "note": None}
    status, reviews = _request("GET", f"{API}/repos/{repo}/pulls/{number}/reviews", token=token)
    reviews = reviews if status == 200 and isinstance(reviews, list) else []
    asked = [r for r in reviews if r.get("state") == "CHANGES_REQUESTED"]
    if asked and asked[-1] is reviews[-1]:
        return {"state": "changes_requested", "note": asked[-1].get("body") or None}
    return {"state": "submitted", "note": None}


def open_issue(token: str, repo: str, *, title: str, body: str) -> str:
    status, issue = _request("POST", f"{API}/repos/{repo}/issues", token=token,
                             json={"title": title, "body": body})
    issue = _ok(status, issue, "open the report")
    return str(issue["html_url"])
