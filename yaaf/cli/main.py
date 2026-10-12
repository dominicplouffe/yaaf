"""The `yaaf` CLI: the chairman's terminal.

Observation plus the few powers the chairman actually has: boardroom speech,
approval decisions, hypothesis extension, kill switches, spot audits.

  export YAAF_API=http://localhost:8080
  yaaf login            # email + password, once; keeps a personal token

`YAAF_TOKEN` overrides the stored token, for a script that was handed one
(`yaaf users token` on the box mints a short-lived one). The static admin token
this used to read was retired — see docs/AUTH.md.
"""

from __future__ import annotations

import base64
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


def _api_base() -> str:
    return os.environ.get("YAAF_API", "http://localhost:8080").rstrip("/")


def _credentials_path() -> Path:
    """Where `yaaf login` keeps its token: one file, mode 0600, keyed by API
    base so a laptop that talks to two boxes holds two tokens."""
    override = os.environ.get("YAAF_CREDENTIALS")
    if override:
        return Path(override)
    config = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return config / "yaaf" / "credentials.json"


def _read_credentials() -> dict:
    try:
        return json.loads(_credentials_path().read_text())
    except (OSError, ValueError):
        return {}


def _write_credentials(store: dict) -> None:
    path = _credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Created 0600 rather than chmodded after: a token must never sit, even for
    # a moment, in a file another user on this machine can read.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(store, handle, indent=2)


def _token() -> str | None:
    return os.environ.get("YAAF_TOKEN") or (_read_credentials().get(_api_base()) or {}).get(
        "token")


def _client() -> httpx.Client:
    token = _token()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return httpx.Client(base_url=_api_base(), timeout=30, headers=headers)


def _json_or_die(response: httpx.Response):
    if response.status_code >= 400:
        try:
            detail = response.json().get("detail", response.text[:200])
        except Exception:
            detail = response.text[:200]
        typer.echo(f"API error {response.status_code}: {detail}")
        if response.status_code == 401:
            typer.echo(f"Hint: run `yaaf login` (signing in to {_api_base()}), or set "
                       "YAAF_TOKEN to a personal token.")
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


# ── the receipt ─────────────────────────────────────────────────────────────────


@app.command()
def receipt(
    project: str = typer.Argument(..., help="project id or slug"),
    as_json: bool = typer.Option(False, "--json", help="the payload instead of the text"),
) -> None:
    """What a project cost, and what the money bought.

    Meant to be pasted somewhere public. The left column is metered spend,
    including the runs that died; the right column is only what the platform
    itself wrote — a requirement marked met after it ran the acceptance test,
    an external claim it fetched and hashed, a close the janitor drew at
    random. So the interesting number is never the total: it is what the total
    bought that a cheaper run would not have.
    """
    from yaaf.server.receipt import render

    with _client() as client:
        data = _json_or_die(
            client.get(f"/observe/projects/{_resolve_project(client, project)}/receipt"))
    typer.echo(json.dumps(data, indent=2, default=str) if as_json else render(data))


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


# ── signing in ──────────────────────────────────────────────────────────────────


@app.command()
def login(
    email: str = typer.Option("", "--email", help="Your account's email"),
    name: str = typer.Option("", "--name", help="What to call this token (default: cli@host)"),
    days: int = typer.Option(30, "--days", help="How long the token lives (1-365)"),
) -> None:
    """Sign in with your email and password, and keep a personal token for this
    API base. The password is sent once and never stored."""
    import socket

    email = email or typer.prompt("Email")
    password = typer.prompt("Password", hide_input=True)
    label = name or f"cli@{socket.gethostname()}"[:80]
    with httpx.Client(base_url=_api_base(), timeout=30) as client:
        body = _json_or_die(client.post("/auth/token", json={
            "email": email, "password": password, "name": label, "days": days}))
    store = _read_credentials()
    store[_api_base()] = {"token": body["token"], "email": email, "name": body["name"],
                          "expires_at": body["expires_at"]}
    _write_credentials(store)
    typer.echo(f"Signed in to {_api_base()} as {email}. Token {body['name']!r} "
               f"expires {body['expires_at'][:10]}.")


@app.command()
def logout() -> None:
    """Revoke this machine's token on the server, and forget it here."""
    store = _read_credentials()
    entry = store.pop(_api_base(), None)
    if entry:
        with httpx.Client(base_url=_api_base(), timeout=30,
                          headers={"Authorization": f"Bearer {entry['token']}"}) as client:
            client.post("/auth/logout")
        _write_credentials(store)
        typer.echo(f"Signed out of {_api_base()}.")
    else:
        typer.echo(f"Not signed in to {_api_base()}.")


@app.command()
def whoami() -> None:
    with _client() as client:
        _show(client.get("/auth/me"))


@app.command()
def create_user(email: str, name: str,
                admin: bool = typer.Option(False, "--admin", help="Make them an instance admin")
                ) -> None:
    """Invite somebody (instance admins only). Prints the one-time link that
    lets them choose a password — this box sends no mail, so hand it on."""
    with _client() as client:
        _show(client.post("/admin/users",
                          json={"email": email, "display_name": name, "is_admin": admin}))


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
                             help="service; manage model credentials with create-llm-credential"),
    from_file: Path = typer.Option(
        None, "--from-file", exists=True, dir_okay=False,
        help="Store a FILE credential — a service-account key, a PEM, a kubeconfig. "
             "Granted roles receive its PATH, not its contents"),
    filename: str = typer.Option(
        None, "--filename",
        help="What to call it inside the sandbox; defaults to the file's own name"),
) -> None:
    """Store an encrypted secret (value prompted, never echoed or logged).

    With --from-file the value is the file's bytes and the prompt is skipped —
    a key file is not something anybody retypes, and the hidden prompt cannot
    carry a newline anyway.
    """
    if from_file is None and filename is not None:
        raise typer.BadParameter("--filename only means something with --from-file")
    if from_file is None:
        value = typer.prompt("Secret value", hide_input=True)
        body: dict = {"value": value}
    else:
        body = {"value": base64.b64encode(from_file.read_bytes()).decode(),
                "filename": filename or from_file.name}
    with _client() as client:
        project_id = _resolve_project(client, project) if project else None
        _show(client.post("/admin/secrets", json={
            "project_id": project_id, "name": name,
            "role_scopes": [s for s in scopes.split(",") if s], "kind": kind, **body,
        }))


@app.command()
def settings() -> None:
    """Show the app-wide settings — the model credential and the Slack connection.

    Values are never printed, only whether one is installed. Change them here
    or in the dashboard's Settings screen; both write the same rows.
    """
    with _client() as client:
        _show(client.get("/admin/settings"))


@app.command()
def llm_credentials(
    include_hidden: bool = typer.Option(False, "--include-hidden",
                                        help="Also list credentials retired from the menus"),
) -> None:
    """List managed LLM credentials and their assigned projects; never values.

    Each project says whether it is still live, and the credential says whether
    any of them are — a key whose only projects have ended is safe to hide.
    """
    with _client() as client:
        _show(client.get("/admin/settings/llm-credentials",
                         params={"include_hidden": "true"} if include_hidden else None))


@app.command()
def hide_llm_credentials(credential_ids: list[str]) -> None:
    """Take credentials off every menu without deleting them.

    Hidden means unofferable: gone from Settings, the project picker, the
    Founder's list and any MCP spec that names one. The row and its receipts
    stay, so finished projects still say what paid for them. Refused for any
    credential a live project can still spend on.
    """
    with _client() as client:
        _show(client.post("/admin/settings/llm-credentials/hide",
                          json={"ids": list(credential_ids)}))


@app.command()
def unhide_llm_credentials(credential_ids: list[str]) -> None:
    """Put hidden credentials back on the menus. Refused if the name was taken."""
    with _client() as client:
        _show(client.post("/admin/settings/llm-credentials/unhide",
                          json={"ids": list(credential_ids)}))


@app.command()
def create_llm_credential(name: str, mode: str = typer.Option("api_key", "--mode")) -> None:
    """Create a named LLM credential in Settings (value prompted)."""
    value = typer.prompt("Credential value", hide_input=True)
    with _client() as client:
        _show(client.post("/admin/settings/llm-credentials",
                          json={"name": name, "mode": mode, "value": value}))


@app.command()
def edit_llm_credential(credential_id: str, name: str,
                        rotate: bool = typer.Option(False, "--rotate")) -> None:
    """Rename a managed credential; optionally prompt for a replacement value."""
    payload = {"name": name}
    if rotate:
        payload["value"] = typer.prompt("Replacement credential value", hide_input=True)
    with _client() as client:
        _show(client.patch(f"/admin/settings/llm-credentials/{credential_id}", json=payload))


@app.command()
def llm_credential_usage(credential_id: str,
                         refresh: bool = typer.Option(False, "--refresh",
                                                      help="Ask Anthropic rather than "
                                                           "reading the last report "
                                                           "(floored at one call per "
                                                           "subscription every 15s)")) -> None:
    """What Anthropic says is left on a subscription credential's plan (session
    and weekly windows). Reported by every agent run on the credential; without
    `--refresh` this reads that report and costs nothing. API keys have no plan
    meter and say so, and a window that has reset since it was last reported
    says that rather than showing the number it had when it was still open.

    With `--refresh` the answer carries an `asked` block saying what the request
    actually did: `fresh` when Anthropic answered, `floored` inside the fifteen
    second minimum, `throttled` while a 429 is being honoured (with the wait),
    `rejected` when the token was refused — which is the one to act on, and
    means replacing the credential's value — and `unreachable` or `unreadable`
    when the call failed. Every one of those used to return the stored row and
    say nothing, so a press that could not ask looked exactly like one that
    did."""
    with _client() as client:
        _show(client.get(f"/admin/settings/llm-credentials/{credential_id}/usage",
                         params={"refresh": "true"} if refresh else None))


@app.command()
def delete_llm_credential(credential_id: str) -> None:
    """Delete an unused managed credential. Assigned credentials are protected."""
    typer.confirm("Permanently delete this credential?", abort=True)
    with _client() as client:
        _show(client.delete(f"/admin/settings/llm-credentials/{credential_id}"))


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
                                     help="Provision a new project's boardroom on creation"),
) -> None:
    """Configure the app-wide Slack connection every project inherits.

    One workspace, every project, different channels: store the tokens once
    and a project needs nothing but its channel map. Creating a project fills
    in the boardroom; `slack-channels` fills in the rest when the project is
    ready to be run from Slack.
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
def activity(
    project: str,
    since: str = typer.Option("today", "--since",
                              help="today, yesterday, 7d, 12h, a date, or an ISO timestamp"),
    until: str = typer.Option(None, "--until"),
) -> None:
    """Who spent the runs, and on what — one row a seat."""
    with _client() as client:
        params = {"since": since}
        if until:
            params["until"] = until
        _show(client.get(f"/observe/projects/{_resolve_project(client, project)}/activity",
                         params=params))


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
def asks(project: str) -> None:
    """Questions the CEO put to you, live and settled."""
    with _client() as client:
        _show(client.get(f"/observe/projects/{_resolve_project(client, project)}/asks"))


@app.command()
def flags(project: str) -> None:
    """What the platform obliged the CEO to tell you, and whether it has.

    `unraised` is the number to read first: news the arithmetic opened and the
    CEO has not brought to the boardroom yet.
    """
    with _client() as client:
        _show(client.get(f"/observe/projects/{_resolve_project(client, project)}/flags"))


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
def ack_flag(flag_id: str, note: str = typer.Option("", "--note")) -> None:
    """Answer a flag the CEO raised. A missed cycle or a month over budget
    closes here — nothing else ever closes those. A condition still true stays
    open under your note until the arithmetic turns."""
    with _client() as client:
        _show(client.post(f"/admin/flags/{flag_id}/ack", json={"note": note}))


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


# ── who sits on a project, and the box's people ────────────────────────────────

members = typer.Typer(no_args_is_help=True,
                      help="Who is on a project: chairman, co-chairs, observers.")
app.add_typer(members, name="members")


@members.command("list")
def members_list(project: str) -> None:
    """Everyone on a project and what they are there."""
    with _client() as client:
        _show(client.get(f"/admin/projects/{_resolve_project(client, project)}/members"))


@members.command("add")
def members_add(project: str, email: str,
                role: str = typer.Option("observer", "--role",
                                         help="co_chair (decides) or observer (reads)")) -> None:
    """Put somebody with an account on a project (its chairman only)."""
    with _client() as client:
        _show(client.post(f"/admin/projects/{_resolve_project(client, project)}/members",
                          json={"email": email, "role": role}))


@members.command("role")
def members_role(project: str, email: str, role: str) -> None:
    """Change a member's role between co_chair and observer (its chairman only)."""
    with _client() as client:
        pid = _resolve_project(client, project)
        _show(client.patch(f"/admin/projects/{pid}/members/{_member_id(client, pid, email)}",
                           json={"role": role}))


@members.command("remove")
def members_remove(project: str, email: str) -> None:
    """Take somebody off a project, from their next request on (its chairman only)."""
    with _client() as client:
        pid = _resolve_project(client, project)
        _show(client.request("DELETE",
                             f"/admin/projects/{pid}/members/{_member_id(client, pid, email)}"))


@members.command("hand-on")
def members_hand_on(project: str, email: str) -> None:
    """Make somebody else the chairman; you stay on as a co-chair. The chairman
    may, and an instance admin may for a chairman who has left."""
    with _client() as client:
        _show(client.post(f"/admin/projects/{_resolve_project(client, project)}/chairman",
                          json={"email": email}))


def _member_id(client: httpx.Client, project_id: str, email: str) -> str:
    for row in _json_or_die(client.get(f"/admin/projects/{project_id}/members")):
        if row["email"] == email.strip().lower():
            return row["user_id"]
    typer.echo(f"{email} is not on this project")
    raise typer.Exit(1)


people = typer.Typer(no_args_is_help=True,
                     help="The box's people, over the API (instance admins only).")
app.add_typer(people, name="people")


@people.command("list")
def people_list() -> None:
    with _client() as client:
        _show(client.get("/admin/users"))


def _person_id(client: httpx.Client, email: str) -> str:
    for row in _json_or_die(client.get("/admin/users")):
        if row["email"] == email.strip().lower():
            return row["id"]
    typer.echo(f"nobody with the email {email} has an account here")
    raise typer.Exit(1)


@people.command("link")
def people_link(email: str) -> None:
    """A fresh one-time link that sets their password; earlier ones stop working."""
    with _client() as client:
        _show(client.post(f"/admin/users/{_person_id(client, email)}/setup-link"))


@people.command("promote")
def people_promote(email: str) -> None:
    with _client() as client:
        _show(client.patch(f"/admin/users/{_person_id(client, email)}", json={"is_admin": True}))


@people.command("demote")
def people_demote(email: str) -> None:
    with _client() as client:
        _show(client.patch(f"/admin/users/{_person_id(client, email)}",
                           json={"is_admin": False}))


@people.command("deactivate")
def people_deactivate(email: str) -> None:
    """Every credential they hold stops working at once. Refused while they chair
    a live project: hand it on first."""
    with _client() as client:
        _show(client.patch(f"/admin/users/{_person_id(client, email)}", json={"active": False}))


@people.command("reactivate")
def people_reactivate(email: str) -> None:
    with _client() as client:
        _show(client.patch(f"/admin/users/{_person_id(client, email)}", json={"active": True}))


# ── MCP clients ─────────────────────────────────────────────────────────────────

mcp_token = typer.Typer(no_args_is_help=True, help="Tokens for model clients that speak MCP.")
app.add_typer(mcp_token, name="mcp-token")


@mcp_token.command("mint")
def mcp_token_mint(
    name: str = typer.Argument(..., help="What this client is: 'dom-laptop', 'claude-desktop'"),
    scopes: str = typer.Option("read", "--scopes",
                               help="Comma-separated: read, steer, create, configure"),
) -> None:
    """Mint a scoped MCP client token. The value is printed once and never again.

    It acts for you — whoever `yaaf login` signed in — and reaches what you
    reach: reading any project you are a member of, deciding on the ones you
    chair or co-chair. Scopes narrow that further. Start with `read`. It is
    most of the value and none of the risk — a client with it can see every
    project you can and sign for nothing.
    """
    with _client() as client:
        _show(client.post("/admin/mcp_clients", json={
            "name": name,
            "scopes": [s.strip() for s in scopes.split(",") if s.strip()],
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


def _register_users() -> None:
    # Lazily, for the same reason as spend: these open the database directly.
    from yaaf.cli.users import users

    app.add_typer(users, name="users")


_register_spend()
_register_users()

if __name__ == "__main__":
    app()
