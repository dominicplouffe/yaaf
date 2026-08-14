"""The `yaaf` CLI: the chairman's terminal until the SPA lands.

Observation plus the few powers the chairman actually has: boardroom speech,
approval decisions, hypothesis extension, kill switches, spot audits.

  export YAAF_API=http://localhost:8080
  export YAAF_ADMIN_TOKEN=...
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import httpx
import typer

app = typer.Typer(no_args_is_help=True, add_completion=False)


def _env_file_token() -> str | None:
    """Fall back to deploy/.env (cwd or repo root) so the CLI works without
    exporting anything."""
    for candidate in (Path("deploy/.env"), Path(".env"),
                      Path(__file__).resolve().parents[2] / "deploy" / ".env"):
        try:
            if candidate.is_file():
                for line in candidate.read_text().splitlines():
                    if line.startswith("YAAF_ADMIN_TOKEN="):
                        return line.split("=", 1)[1].strip()
        except OSError:
            continue
    return None


def _client() -> httpx.Client:
    base = os.environ.get("YAAF_API", "http://localhost:8080")
    token = os.environ.get("YAAF_ADMIN_TOKEN") or _env_file_token() or "dev-admin-token"
    return httpx.Client(base_url=base, timeout=30,
                        headers={"Authorization": f"Bearer {token}"})


def _json_or_die(response: httpx.Response):
    if response.status_code >= 400:
        try:
            detail = response.json().get("detail", response.text[:200])
        except Exception:
            detail = response.text[:200]
        typer.echo(f"API error {response.status_code}: {detail}")
        if response.status_code == 401:
            typer.echo("Hint: set YAAF_ADMIN_TOKEN (see deploy/.env) and YAAF_API, "
                       "or run from the repo root so deploy/.env is auto-detected.")
        raise typer.Exit(1)
    return response.json()


def _show(response: httpx.Response) -> None:
    try:
        typer.echo(json.dumps(response.json(), indent=2, default=str))
    except Exception:
        typer.echo(f"HTTP {response.status_code}: {response.text[:2000]}")
    if response.status_code >= 400:
        raise typer.Exit(1)


def _resolve_project(client: httpx.Client, ref: str) -> str:
    """Accept a project id or slug."""
    if ref.startswith("proj_"):
        return ref
    for project in _json_or_die(client.get("/observe/projects")):
        if project["slug"] == ref:
            return project["id"]
    typer.echo(f"no project with slug {ref!r}")
    raise typer.Exit(1)


# ── setup ───────────────────────────────────────────────────────────────────────


@app.command()
def seed_roles(directory: Path = typer.Argument(Path("roles"))) -> None:
    """Load every roles/*.json into the role library."""
    with _client() as client:
        for path in sorted(directory.glob("*.json")):
            body = json.loads(path.read_text())
            response = client.post("/admin/roles", json=body)
            typer.echo(f"{path.name}: {response.status_code} {response.text[:120]}")


@app.command()
def create_user(email: str, name: str) -> None:
    with _client() as client:
        _show(client.post("/admin/users", json={"email": email, "display_name": name}))


@app.command()
def create_project(spec_file: Path) -> None:
    """Create a project from a JSON spec (see README for the shape)."""
    with _client() as client:
        _show(client.post("/admin/projects", json=json.loads(spec_file.read_text())))


@app.command()
def set_secret(
    name: str,
    project: str = typer.Option(None, "--project", help="Project id/slug; omit for platform-level"),
    scopes: str = typer.Option("", "--scopes", help="Comma-separated role slugs"),
) -> None:
    """Store an encrypted secret (value prompted, never echoed or logged)."""
    value = typer.prompt("Secret value", hide_input=True)
    with _client() as client:
        project_id = _resolve_project(client, project) if project else None
        _show(client.post("/admin/secrets", json={
            "project_id": project_id, "name": name, "value": value,
            "role_scopes": [s for s in scopes.split(",") if s],
        }))


@app.command()
def configure(
    project: str,
    runtime: str = typer.Option(None, "--runtime", help="mock | agent_sdk"),
    billing_secret_id: str = typer.Option(None, "--billing-secret-id"),
    billing_mode: str = typer.Option(None, "--billing-mode", help="api_key | subscription"),
    ceo_heartbeat_hours: float = typer.Option(None, "--ceo-heartbeat-hours"),
    max_concurrent_runs: int = typer.Option(None, "--max-concurrent-runs"),
) -> None:
    """Update a project's mutable configuration (never its constitution)."""
    config: dict = {}
    if runtime is not None:
        config["runtime"] = runtime
    if ceo_heartbeat_hours is not None:
        config["ceo_heartbeat_hours"] = ceo_heartbeat_hours
    if max_concurrent_runs is not None:
        config["max_concurrent_runs"] = max_concurrent_runs
    payload: dict = {}
    if config:
        payload["config"] = config
    if billing_secret_id is not None:
        payload["billing_secret_id"] = billing_secret_id
    if billing_mode is not None:
        payload["billing_mode"] = billing_mode
    if not payload:
        typer.echo("nothing to change")
        raise typer.Exit(1)
    with _client() as client:
        _show(client.request("PATCH",
                             f"/admin/projects/{_resolve_project(client, project)}",
                             json=payload))


# ── lifecycle ───────────────────────────────────────────────────────────────────


@app.command()
def start(project: str) -> None:
    with _client() as client:
        _show(client.post(f"/admin/projects/{_resolve_project(client, project)}/start"))


@app.command()
def pause(project: str, reason: str = typer.Option("", "--reason")) -> None:
    with _client() as client:
        _show(client.post(f"/admin/projects/{_resolve_project(client, project)}/pause",
                          json={"reason": reason}))


@app.command()
def stop(project: str, reason: str = typer.Option("", "--reason")) -> None:
    with _client() as client:
        _show(client.post(f"/admin/projects/{_resolve_project(client, project)}/stop",
                          json={"reason": reason}))


# ── observation ─────────────────────────────────────────────────────────────────


@app.command()
def projects() -> None:
    with _client() as client:
        _show(client.get("/observe/projects"))


@app.command()
def dashboard(project: str) -> None:
    with _client() as client:
        _show(client.get(f"/observe/projects/{_resolve_project(client, project)}"))


@app.command()
def board(project: str) -> None:
    with _client() as client:
        _show(client.get(f"/observe/projects/{_resolve_project(client, project)}/board"))


@app.command()
def org(project: str) -> None:
    with _client() as client:
        _show(client.get(f"/observe/projects/{_resolve_project(client, project)}/org"))


@app.command()
def channels(project: str) -> None:
    with _client() as client:
        _show(client.get(f"/observe/projects/{_resolve_project(client, project)}/channels"))


@app.command()
def tail(channel_id: str, limit: int = typer.Option(30, "--limit")) -> None:
    with _client() as client:
        response = client.get(f"/observe/channels/{channel_id}/messages",
                              params={"limit": limit})
        if response.status_code >= 400:
            _show(response)
        for message in response.json():
            who = message["from_agent"] or message["from_user"] or "?"
            frozen = " [FROZEN]" if message.get("frozen") else ""
            typer.echo(f"[{message['at']}] {who}{frozen}: {message['body']}")


@app.command()
def runs(project: str, limit: int = typer.Option(20, "--limit")) -> None:
    with _client() as client:
        _show(client.get(f"/observe/projects/{_resolve_project(client, project)}/runs",
                         params={"limit": limit}))


@app.command()
def ledger(project: str) -> None:
    with _client() as client:
        _show(client.get(f"/observe/projects/{_resolve_project(client, project)}/ledger"))


@app.command()
def evidence(project: str) -> None:
    with _client() as client:
        _show(client.get(f"/observe/projects/{_resolve_project(client, project)}/evidence"))


@app.command()
def hypotheses(project: str) -> None:
    with _client() as client:
        _show(client.get(f"/observe/projects/{_resolve_project(client, project)}/hypotheses"))


@app.command()
def approvals(project: str) -> None:
    with _client() as client:
        _show(client.get(f"/observe/projects/{_resolve_project(client, project)}/approvals"))


@app.command()
def audit(project: str, n: int = typer.Option(5, "--n")) -> None:
    """A random spot-audit sample of recent evidence and completed tasks."""
    with _client() as client:
        _show(client.get(f"/observe/projects/{_resolve_project(client, project)}/spot_audit",
                         params={"n": n}))


# ── chairman actions ────────────────────────────────────────────────────────────


@app.command()
def say(project: str, message: str) -> None:
    """Speak in the boardroom (the chairman's only channel)."""
    with _client() as client:
        _show(client.post(f"/admin/projects/{_resolve_project(client, project)}/boardroom",
                          json={"body": message}))


@app.command()
def decide(approval_id: str, approve: bool = typer.Option(..., "--approve/--deny"),
           note: str = typer.Option("", "--note")) -> None:
    with _client() as client:
        _show(client.post(f"/admin/approvals/{approval_id}/decide",
                          json={"approve": approve, "note": note}))


@app.command()
def extend(hypothesis_id: str, new_kill_date: str, reason: str) -> None:
    """The chairman's one-time hypothesis extension (ISO datetime)."""
    with _client() as client:
        _show(client.post(f"/admin/hypotheses/{hypothesis_id}/extend",
                          json={"new_kill_date": new_kill_date, "reason": reason}))


@app.command()
def unfreeze(message_id: str) -> None:
    with _client() as client:
        _show(client.post(f"/admin/threads/{message_id}/unfreeze"))


@app.command()
def audit_evidence(evidence_id: str, ok: bool = typer.Option(..., "--ok/--bad"),
                   note: str = typer.Option("", "--note")) -> None:
    with _client() as client:
        _show(client.post(f"/admin/evidence/{evidence_id}/audit",
                          json={"ok": ok, "note": note}))


if __name__ == "__main__":
    app()
