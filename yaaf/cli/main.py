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


def _register_spend() -> None:
    # Registered lazily at module bottom: spend.py drags in SQLAlchemy and
    # server-side helpers the HTTP commands never need.
    from yaaf.cli.spend import spend_report

    app.command(name="spend-report")(spend_report)


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
def seed_roles(
    directory: Path = typer.Argument(Path("roles")),
    only_missing: bool = typer.Option(
        False, "--only-missing",
        help="Skip roles the library already has, by slug, instead of stamping a new "
             "version of each. What you want after an update adds roles to the checkout."),
) -> None:
    """Load every roles/*.json into the role library.

    A plain run posts all of them, and every post stamps a NEW VERSION —
    that is how an edited prompt reaches a running install. `--only-missing`
    is the other job: an install seeded months ago has none of the roles the
    repository grew since, and a setup file from examples/ that hires one of
    them arrives at New Project with its team's role blank. That flag loads
    exactly those, leaving every role already in the library untouched.
    """
    with _client() as client:
        have: set[str] = set()
        if only_missing:
            response = client.get("/admin/roles")
            if response.status_code >= 400:
                typer.echo(f"could not read the role library: {response.status_code} "
                           f"{response.text[:120]}")
                raise typer.Exit(1)
            have = {role["slug"] for role in response.json()}
        for path in sorted(directory.glob("*.json")):
            body = json.loads(path.read_text())
            if only_missing and body.get("slug") in have:
                # Left alone means the prompt is left alone. Category and
                # domains live on the role rather than the version precisely
                # so an install seeded before the taxonomy can be refiled
                # without stamping a version and putting live teams behind.
                taxonomy = {
                    "name": body.get("name"),
                    "category": body.get("category"),
                    "domains": body.get("domains", []),
                }
                response = client.patch(
                    f"/admin/roles/{body['slug']}", json=taxonomy)
                filed = "refiled" if response.status_code < 400 else "unchanged"
                typer.echo(f"{path.name}: already in the library, prompt left "
                           f"alone, {filed} as {body.get('category') or 'uncategorised'}")
                continue
            response = client.post("/admin/roles", json=body)
            typer.echo(f"{path.name}: {response.status_code} {response.text[:120]}")


@app.command()
def seed_crews(
    directory: Path = typer.Argument(Path("crews")),
    only_missing: bool = typer.Option(
        False, "--only-missing",
        help="Skip crews the library already has, by slug. What you want after an "
             "update adds crews to the checkout without overwriting edited ones."),
) -> None:
    """Load every crews/*.json into the crew library.

    A crew is a named job with a team already on it — the answer to "who do I
    actually need?", which a categorised role list still leaves to the
    chairman. Crews are not versioned: one is read at hiring time and copied
    into the project's own teams, so re-seeding an edited crew can never reach
    a project that already started.

    Run it after `seed-roles` — a crew hiring a role the library lacks is
    accepted and listed with that slug marked missing, but the picker is more
    use when everything resolves.
    """
    with _client() as client:
        have: set[str] = set()
        if only_missing:
            response = client.get("/admin/crews")
            if response.status_code >= 400:
                typer.echo(f"could not read the crew library: {response.status_code} "
                           f"{response.text[:120]}")
                raise typer.Exit(1)
            have = {crew["slug"] for crew in response.json()}
        for path in sorted(directory.glob("*.json")):
            body = json.loads(path.read_text())
            if only_missing and body.get("slug") in have:
                typer.echo(f"{path.name}: already in the library, left alone")
                continue
            response = client.post("/admin/crews", json=body)
            typer.echo(f"{path.name}: {response.status_code} {response.text[:120]}")


@app.command()
def save_seed(directory: Path = typer.Argument(Path("seed"))) -> None:
    """Write this install's reusable design to a folder: every role, and every
    project as a ready-to-create spec.

    Roles are global and survive a project wipe on their own; goals do not, and
    retyping a page-long mandate is how a rebuild loses the thing that took
    longest to write. What lands here is design — prompts, goals, constitutions
    — never plumbing: no secrets, no Slack wiring, no webhook keys.
    """
    from yaaf.core import seed as seedlib

    with _client() as client:
        roles, projects = seedlib.save_seed(client, directory)
    typer.echo(f"{roles} roles → {directory / 'roles'}")
    typer.echo(f"{projects} projects → {directory / 'projects'}")


@app.command()
def seed(
    directory: Path = typer.Argument(Path("seed")),
    chairman: str = typer.Option(None, "--chairman",
                                 help="Chairman email for the restored projects"),
    roles_only: bool = typer.Option(False, "--roles-only",
                                    help="Restore the roles and stop, leaving projects to you"),
) -> None:
    """Restore a folder written by `save-seed` onto an empty install.

    Roles first, because a project spec names the roles its teams run. Projects
    are created in draft and never started: a seeded project should not begin
    spending because someone restored a backup.
    """
    from yaaf.core import seed as seedlib

    with _client() as client:
        for slug, status in seedlib.restore_roles(client, directory):
            typer.echo(f"role {slug}: {status}")
        if roles_only:
            return
        if seedlib.project_specs(directory) and not chairman:
            typer.echo("--chairman is required to restore projects (or pass --roles-only)")
            raise typer.Exit(1)
        for slug, status in seedlib.restore_projects(client, directory, chairman):
            typer.echo(f"project {slug}: {status}")


@app.command()
def create_user(email: str, name: str) -> None:
    with _client() as client:
        _show(client.post("/admin/users", json={"email": email, "display_name": name}))


@app.command()
def create_project(spec_file: Path) -> None:
    """Create a project from a JSON spec (see docs/CLI.md for the shape)."""
    with _client() as client:
        _show(client.post("/admin/projects", json=json.loads(spec_file.read_text())))


@app.command()
def set_secret(
    name: str,
    project: str = typer.Option(None, "--project", help="Project id/slug; omit for platform-level"),
    scopes: str = typer.Option("", "--scopes", help="Comma-separated role slugs"),
    kind: str = typer.Option("service", "--kind",
                             help="service (role-granted) or billing (refuses role grants)"),
) -> None:
    """Store an encrypted secret (value prompted, never echoed or logged)."""
    value = typer.prompt("Secret value", hide_input=True)
    with _client() as client:
        project_id = _resolve_project(client, project) if project else None
        _show(client.post("/admin/secrets", json={
            "project_id": project_id, "name": name, "value": value,
            "role_scopes": [s for s in scopes.split(",") if s], "kind": kind,
        }))


@app.command()
def settings() -> None:
    """Show the app-wide settings — the model credential and the Slack workspace.

    Values are never printed, only whether one is installed. Change them here
    or in the dashboard's Settings screen; both write the same rows.
    """
    with _client() as client:
        _show(client.get("/admin/settings"))


@app.command()
def set_platform_key(
    mode: str = typer.Option("api_key", "--mode", help="api_key | subscription"),
) -> None:
    """Install or rotate the platform model credential (value prompted).

    This is the Founder's key. It is never injected into a sandbox and never
    reaches a project's agents — a project pays its own way with its own
    billing credential, and there is no fallback.
    """
    value = typer.prompt("Credential value", hide_input=True)
    with _client() as client:
        _show(client.put("/admin/settings/anthropic", json={"value": value, "mode": mode}))


@app.command()
def set_slack(
    bot_token: bool = typer.Option(False, "--bot-token", help="Prompt for the xoxb- token"),
    signing_secret: bool = typer.Option(False, "--signing-secret", help="Prompt for it"),
    app_token: bool = typer.Option(False, "--app-token", help="Prompt for the xapp- token"),
    chairman: str = typer.Option(None, "--chairman", help="Your Slack member ID (U…)"),
    prefix: str = typer.Option(None, "--prefix", help="Channel name prefix; '' = project slug"),
    visibility: str = typer.Option(None, "--visibility", help="private | public"),
    auto_create: bool = typer.Option(None, "--auto-create/--no-auto-create",
                                     help="Provision a new project's rooms on creation"),
) -> None:
    """Configure the app-wide Slack workspace every project inherits.

    One workspace, every project, different channels: store the tokens once
    and a project needs nothing but its channel map — which `slack-channels`
    (or project creation) fills in for it.
    """
    payload: dict = {}
    for flag, key, label in ((bot_token, "bot_token", "Bot token (xoxb-…)"),
                             (signing_secret, "signing_secret", "Signing secret"),
                             (app_token, "app_token", "App token (xapp-…)")):
        if flag:
            payload[key] = typer.prompt(label, hide_input=True)
    if chairman is not None:
        payload["chairman_slack_user"] = chairman
    if prefix is not None:
        payload["channel_prefix"] = prefix
    if visibility is not None:
        payload["channel_visibility"] = visibility
    if auto_create is not None:
        payload["auto_create_channels"] = auto_create
    if not payload:
        typer.echo("nothing to change")
        raise typer.Exit(1)
    with _client() as client:
        _show(client.put("/admin/settings/slack", json=payload))


@app.command()
def slack_channels(project: str) -> None:
    """Create the Slack rooms this project is missing, and print their names.

    Idempotent: a room already mapped is left alone and a name Slack already
    holds is adopted, so running it after adding a team creates only the new
    team's room.
    """
    with _client() as client:
        _show(client.post(
            f"/admin/projects/{_resolve_project(client, project)}/slack/channels"))


@app.command()
def configure(
    project: str,
    runtime: str = typer.Option(None, "--runtime", help="mock | agent_sdk"),
    billing_secret_id: str = typer.Option(None, "--billing-secret-id"),
    billing_mode: str = typer.Option(None, "--billing-mode", help="api_key | subscription"),
) -> None:
    """Update a project's outward wiring — runtime and billing.

    Limits are not here. Wake cadence, concurrency and every other rule an
    agent runs under live in the constitution, so they change through `amend`:
    with a written reason, an audit entry, and disclosure to the CEO.
    """
    integrations: dict = {}
    if runtime is not None:
        integrations["runtime"] = runtime
    payload: dict = {}
    if integrations:
        payload["integrations"] = integrations
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


@app.command()
def amend(
    project: str,
    reason: str = typer.Option(..., "--reason", help="Why. Recorded, audited, and shown to the CEO"),
    set_: list[str] = typer.Option(None, "--set", help="rule=value, repeatable"),
) -> None:
    """Change a constitution rule.

    Every limit an agent runs under lives in the constitution, so this is the
    only way to move one. The value is parsed as JSON when it can be, so
    `--set runs_per_project_per_day=80` gives a number and
    `--set ceo_daily_at='"09:00"'` gives a string. An invalid overlay is
    refused whole rather than half-applied.
    """
    changes: dict = {}
    for pair in set_ or []:
        if "=" not in pair:
            typer.echo(f"--set wants rule=value, got {pair!r}")
            raise typer.Exit(1)
        key, raw = pair.split("=", 1)
        try:
            changes[key.strip()] = json.loads(raw)
        except json.JSONDecodeError:
            changes[key.strip()] = raw
    if not changes:
        typer.echo("nothing to change — pass at least one --set rule=value")
        raise typer.Exit(1)
    with _client() as client:
        _show(client.post(
            f"/admin/projects/{_resolve_project(client, project)}/amendments",
            json={"reason": reason, "changes": changes},
        ))


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
def rehearse(
    spec_file: str,
    days: int = typer.Option(30, "--days", help="the window the pacing arithmetic covers"),
    usd_per_run: float = typer.Option(
        0.0, "--usd-per-run",
        help="what you actually pay for a run; without it the drill reports the "
             "break-even instead of inventing a price"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Run a project spec as a project for nothing, before you fund it.

    The real platform — dispatcher, janitor, policy engine, every refusal — on
    the mock runtime, against your own spec. It tells you whether the thing
    gets off the ground, how often it will stop and wait for you, what it costs
    in runs, and which of the four limits paces it. It opens no sockets, runs
    no commands out of the file, and touches no database but the throwaway one
    it creates.

    Needs no deployment and no API key: `yaaf rehearse examples/01-your-first-hour.json`
    works on a fresh checkout.
    """
    import json as _json

    from yaaf import rehearsal
    from yaaf.rehearsal import report as rehearsal_report

    try:
        result = rehearsal.rehearse(
            rehearsal.load_spec(spec_file), days=days,
            usd_per_run=usd_per_run or None)
    except rehearsal.RehearsalError as exc:
        typer.echo(f"rehearsal: {exc}")
        raise typer.Exit(2) from exc
    typer.echo(_json.dumps(result, indent=2, default=str) if as_json
               else rehearsal_report.render_text(result))
    if not result["ok"]:
        raise typer.Exit(1)


@app.command()
def briefing(
    project: str = typer.Argument(None, help="a slug or id; omit for every live project"),
    hours: float = typer.Option(0, "--hours", help="force a window instead of your read mark"),
    seen: bool = typer.Option(False, "--seen", help="mark it read, so the next window starts here"),
) -> None:
    """What happened since you last read one — as prose, not four tables.

    Composed by the platform from the record at zero tokens, which is why it
    works on a paused project and a broke one. Reading never moves your mark;
    `--seen` does, and only for the window this command just printed.
    """
    from yaaf.server.briefing import render_text

    with _client() as client:
        query = f"?hours={hours}" if hours else ""
        if project is None:
            response = client.get(f"/observe/briefing{query}")
            briefs = (response.json() or {}).get("projects", []) if response.is_success else []
        else:
            response = client.get(
                f"/observe/projects/{_resolve_project(client, project)}/briefing{query}")
            briefs = [response.json()] if response.is_success else []
        if not response.is_success:
            _show(response)
            return
        if not briefs:
            typer.echo("No live projects.")
            return
        for brief in briefs:
            typer.echo(f"\n── {brief['name']} " + "─" * max(0, 60 - len(brief["name"])))
            typer.echo(render_text(brief))
        if seen:
            for brief in briefs:
                client.post(f"/admin/projects/{brief['project_id']}/briefing/seen",
                            json={"until": brief["until"]})
            typer.echo(f"\nMarked {len(briefs)} briefing(s) read.")


@app.command()
def agenda(project: str, days: int = typer.Option(14, "--days")) -> None:
    """What this project is scheduled to do next: the wakes, the standing
    meeting, the rhythm, the clocks that run out, and the promise."""
    with _client() as client:
        response = client.get(
            f"/observe/projects/{_resolve_project(client, project)}/agenda?days={days}")
        if not response.is_success:
            _show(response)
            return
        view = response.json()
        typer.echo(f"next {view['days']} days · {view['timezone']}")
        for rhythm in view["rhythms"]:
            when = (rhythm["next"] or "")[:16].replace("T", " ")
            typer.echo(f"  ~ {rhythm['label']}: {rhythm['every']}"
                       + (f" (next {when})" if when else ""))
        for event in view["events"]:
            typer.echo(f"  {event['day']}  {event['label']}")
            if event["detail"]:
                typer.echo(f"              {event['detail']}")
        for note in view["notes"]:
            typer.echo(f"  ! {note}")
        if not view["events"] and not view["rhythms"]:
            typer.echo("  nothing scheduled in this window.")


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


@app.command()
def set_password(email: str) -> None:
    """Give a chairman a password, so they can sign in to the consent screen.

    Needed only for OAuth clients — claude.ai among them, which offers no field
    for a pasted token. A terminal client with a minted token never sees this.
    """
    value = typer.prompt("Password", hide_input=True, confirmation_prompt=True)
    with _client() as client:
        _show(client.post("/admin/users/password", json={"email": email, "password": value}))


# ── MCP clients ─────────────────────────────────────────────────────────────────

mcp_token = typer.Typer(no_args_is_help=True, help="Tokens for model clients that speak MCP.")
app.add_typer(mcp_token, name="mcp-token")


@mcp_token.command("mint")
def mcp_token_mint(
    name: str = typer.Argument(..., help="What this client is: 'dom-laptop', 'claude-desktop'"),
    chairman: str = typer.Option(..., "--chairman", help="Email of the chairman it acts for"),
    scopes: str = typer.Option("read", "--scopes",
                               help="Comma-separated: read, steer, create, configure"),
    project: list[str] = typer.Option(None, "--project",
                                      help="Slug or id, repeatable; omit for all this "
                                           "chairman's projects"),
) -> None:
    """Mint a scoped MCP client token. The value is printed once and never again.

    Start with `read`. It is most of the value and none of the risk: a client
    with it can see every project and sign for nothing.
    """
    with _client() as client:
        _show(client.post("/admin/mcp_clients", json={
            "name": name,
            "chairman_email": chairman,
            "scopes": [s.strip() for s in scopes.split(",") if s.strip()],
            "projects": list(project or []),
        }))


@mcp_token.command("list")
def mcp_token_list() -> None:
    """Every client token, what it holds, and when it was last used."""
    with _client() as client:
        _show(client.get("/admin/mcp_clients"))


@mcp_token.command("revoke")
def mcp_token_revoke(client_id: str) -> None:
    """Revoke a client token. The row survives — the audit trail names it."""
    with _client() as client:
        _show(client.request("DELETE", f"/admin/mcp_clients/{client_id}"))


_register_spend()

if __name__ == "__main__":
    app()
