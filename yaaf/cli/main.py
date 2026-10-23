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

app = typer.Typer(no_args_is_help=True, add_completion=False, rich_markup_mode="markdown")


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


class _Client(httpx.Client):
    """An HTTP client that says, in one line, when there is nobody to talk to.

    A server that was not running used to answer `yaaf projects` with a
    224-line traceback ending in `[Errno 111] Connection refused` — the most
    common first mistake, reported as a crash."""

    def send(self, request: httpx.Request, **kwargs) -> httpx.Response:
        try:
            return super().send(request, **kwargs)
        except httpx.ConnectError:
            typer.echo(f"Can't reach yaaf at {_api_base()}. Is the server running? "
                       "If it lives elsewhere, set YAAF_API to its address.", err=True)
            raise typer.Exit(1) from None
        except httpx.TimeoutException:
            typer.echo(f"yaaf at {_api_base()} did not answer in time. Try again, or "
                       "check the server's log.", err=True)
            raise typer.Exit(1) from None


def _client() -> httpx.Client:
    token = _token()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return _Client(base_url=_api_base(), timeout=30, headers=headers)


def _detail(response: httpx.Response) -> str:
    """A refusal as a person reads it. A validation error is a list of
    {loc, msg}; printing it raw was a 175-line dump for one missing field."""
    try:
        detail = response.json().get("detail", response.text[:200])
    except Exception:
        return response.text[:200]
    if isinstance(detail, list):
        lines = []
        for err in detail[:8]:
            if isinstance(err, dict):
                where = ".".join(str(p) for p in err.get("loc", []) if p != "body")
                lines.append(f"  {where or 'request'}: {err.get('msg', err)}")
            else:
                lines.append(f"  {err}")
        more = f"\n  … and {len(detail) - 8} more" if len(detail) > 8 else ""
        return "the request was refused:\n" + "\n".join(lines) + more
    return detail if isinstance(detail, str) else json.dumps(detail, default=str)


def _fail(response: httpx.Response):
    """Every command fails the same way: the status, the reason, and a hint
    where one is known. Most commands used to print the raw JSON body and no
    hint, so the same 401 read two different ways depending on the command."""
    detail = _detail(response)
    typer.echo(f"API error {response.status_code}: {detail}", err=True)
    if response.status_code == 401:
        typer.echo(f"Hint: run `yaaf login` (signing in to {_api_base()}), or set "
                   "YAAF_TOKEN to a personal token.", err=True)
    elif response.status_code == 404 and detail == "Not Found":
        # FastAPI's own wording for a route that does not exist — a base URL
        # with a path on it, not a missing project.
        typer.echo(f"Hint: is YAAF_API ({_api_base()}) the install's own address, "
                   "with no path after it?", err=True)
    raise typer.Exit(1)


def _json_or_die(response: httpx.Response):
    if response.status_code >= 400:
        _fail(response)
    return response.json()


def _show(response: httpx.Response) -> None:
    if response.status_code >= 400:
        _fail(response)
    try:
        typer.echo(json.dumps(response.json(), indent=2, default=str))
    except Exception:
        typer.echo(f"HTTP {response.status_code}: {response.text[:2000]}")


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
    failed = 0
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
            if response.status_code == 401:
                # Not signed in fails every file the same way: say it once, with
                # the fix, rather than 38 times and an exit of 0.
                _fail(response)
            typer.echo(f"{path.name}: {response.status_code} {response.text[:120]}")
            failed += response.status_code >= 400
        if failed:
            typer.echo(f"{failed} file(s) were refused — see above.", err=True)
            raise typer.Exit(1)


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
    failed = 0
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
            if response.status_code == 401:
                # Not signed in fails every file the same way: say it once, with
                # the fix, rather than 17 times and an exit of 0.
                _fail(response)
            typer.echo(f"{path.name}: {response.status_code} {response.text[:120]}")
            failed += response.status_code >= 400
        if failed:
            typer.echo(f"{failed} file(s) were refused — see above.", err=True)
            raise typer.Exit(1)


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
def doctor() -> None:
    """Check this install end to end and say what to do about anything wrong:
    the server, your sign-in, the dashboard, roles, keys, the runner, email."""
    def line(ok: bool, name: str, detail: str, fix: str | None = None,
             problem: bool = True) -> None:
        mark = "✓" if ok else ("✗" if problem else "!")
        typer.echo(f"{mark} {name}: {detail}")
        if not ok and fix:
            typer.echo(f"    → {fix}")

    with _client() as client:
        client.get("/healthz")  # an unreachable server exits here, in one line
        line(True, "server", f"answering at {_api_base()}")
        me = client.get("/auth/me")
        if me.status_code == 401:
            line(False, "signed in", "not signed in", "yaaf login")
            raise typer.Exit(1)
        who = me.json()
        line(True, "signed in", f"as {who.get('email', who.get('id', 'you'))}")
        report = client.get("/admin/settings/doctor")
        if report.status_code == 403:
            typer.echo("The rest of the checks are an instance admin's to run.")
            return
        body = _json_or_die(report)
    for check in body["checks"]:
        line(check["ok"], check["name"], check["detail"], check.get("fix"),
             problem=check["level"] == "problem")
    if not body["ok"]:
        raise typer.Exit(1)


@app.command()
def whoami() -> None:
    """Who this CLI is signed in as, and against which install."""
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
    """Create a project from a JSON spec, or from a setup file such as the ones
    in examples/ — its missing roles are added to the library first, and its
    billing credential is matched by name."""
    try:
        spec = json.loads(spec_file.read_text())
    except OSError as exc:
        typer.echo(f"can't read {spec_file}: {exc.strerror}", err=True)
        raise typer.Exit(2) from None
    except ValueError as exc:
        typer.echo(f"{spec_file} is not valid JSON: {exc}", err=True)
        raise typer.Exit(2) from None
    with _client() as client:
        if isinstance(spec, dict) and "yaaf_bundle" in spec:
            # A setup file wraps the project with the roles it hires and the
            # credential names it expects. Sent bare, the server read it as a
            # project with no slug and answered with a page of validation.
            project = dict(spec.get("project") or {})
            known = {r["slug"] for r in _json_or_die(client.get("/admin/roles"))}
            for role in spec.get("roles") or []:
                if role.get("slug") and role["slug"] not in known:
                    _json_or_die(client.post("/admin/roles", json=role))
                    typer.echo(f"added role {role['slug']} from the setup file")
            wanted = project.pop("billing_secret_name", None)
            if wanted and not project.get("billing_secret_id"):
                listed = _json_or_die(client.get("/admin/settings/llm-credentials"))
                match = next((c for c in listed
                              if c["name"].lower() == str(wanted).lower()), None)
                if match:
                    project["billing_secret_id"] = match["id"]
                else:
                    typer.echo(f"no LLM credential called {wanted!r} — the project is "
                               "created unable to start; assign one in its Settings → Money",
                               err=True)
            spec = project
        _show(client.post("/admin/projects", json=spec))


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
def create_llm_credential(
    name: str,
    mode: str = typer.Option("api_key", "--mode"),
    provider: str = typer.Option("anthropic", "--provider",
                                 help="Which provider it pays: anthropic, openai, "
                                      "google, deepseek, openrouter, moonshot, qwen, "
                                      "mistral, xai, custom"),
    base_url: str = typer.Option(None, "--base-url",
                                 help="A custom endpoint's OpenAI-compatible address"),
) -> None:
    """Create a named LLM credential in Settings (value prompted)."""
    value = typer.prompt("Credential value", hide_input=True)
    body = {"name": name, "mode": mode, "value": value, "provider": provider}
    if base_url:
        body["base_url"] = base_url
    with _client() as client:
        _show(client.post("/admin/settings/llm-credentials", json=body))


@app.command()
def pay_provider(project: str, provider: str, credential_id: str) -> None:
    """Pay a second provider on a project with a stored credential — for seats
    on its models. Metered in the project's unit, against its one cap."""
    with _client() as client:
        project_id = _resolve_project(client, project)
        _show(client.put(f"/admin/projects/{project_id}/credentials/{provider}",
                         json={"credential_id": credential_id}))


@app.command()
def set_model_price(model: str, input_usd: float, output_usd: float,
                    cache_read_usd: float = typer.Option(0.0, "--cache-read")) -> None:
    """Price a model on this install, USD per million tokens — how a model yaaf
    ships no price for (DeepSeek, a custom server) can be hired at all."""
    with _client() as client:
        _show(client.put(f"/admin/settings/model-prices/{model}",
                         json={"input": input_usd, "output": output_usd,
                               "cache_read": cache_read_usd}))


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
    from_credential: str = typer.Option(
        None, "--from-credential",
        help="Draft on this LLM credential (name or id) instead of pasting a second key"),
) -> None:
    """Set the key the Founder drafts on: a new one (value prompted), or one of
    your LLM credentials with --from-credential.

    Never injected into a sandbox and never a project's billing key. Borrowing
    an LLM credential means one key serves both, and rotating it rotates both.
    """
    with _client() as client:
        if from_credential:
            listed = _json_or_die(client.get("/admin/settings/llm-credentials"))
            wanted = from_credential.strip().lower()
            match = next((c for c in listed
                          if c["id"] == from_credential or c["name"].lower() == wanted), None)
            if match is None:
                names = ", ".join(c["name"] for c in listed) or "none yet"
                typer.echo(f"no LLM credential called {from_credential!r} — you have: {names}",
                           err=True)
                raise typer.Exit(2)
            _show(client.put("/admin/founder/credential/llm",
                             json={"credential_id": match["id"]}))
            return
        value = typer.prompt("Credential value", hide_input=True)
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
    """Start a project, or resume a paused one."""
    with _client() as client:
        _show(client.post(f"/admin/projects/{_resolve_project(client, project)}/start"))


@app.command()
def pause(project: str, reason: str = typer.Option("", "--reason")) -> None:
    """Pause a project: no new runs until you start it again. Runs in flight finish."""
    with _client() as client:
        _show(client.post(f"/admin/projects/{_resolve_project(client, project)}/pause",
                          json={"reason": reason}))


@app.command()
def stop(project: str, reason: str = typer.Option("", "--reason")) -> None:
    """Stop a project for good. A stopped project cannot be started again."""
    with _client() as client:
        _show(client.post(f"/admin/projects/{_resolve_project(client, project)}/stop",
                          json={"reason": reason}))


# ── observation ─────────────────────────────────────────────────────────────────


@app.command()
def projects(as_json: bool = typer.Option(False, "--json", help="the raw payload")) -> None:
    """Every project you can open: status, spend against the budget and the hard
    stop, and how many things on it wait for you."""
    with _client() as client:
        if as_json:
            _show(client.get("/observe/projects"))
            return
        desk = _json_or_die(client.get("/observe/desk"))
    rows = desk.get("projects", [])
    if not rows:
        typer.echo("No projects yet. `yaaf rehearse examples/01-your-first-hour.json` "
                   "tries one for nothing.")
        return

    def money(budget: dict, n: float) -> str:
        return f"${n:,.0f}" if budget.get("unit") == "usd" else f"{n:,.0f} tok"

    table = [("PROJECT", "STATUS", "SPENT / BUDGET", "HARD STOP", "WAITING ON YOU", "NOW")]
    for row in rows:
        budget = row.get("budget") or {}
        waiting = row.get("awaiting_you") or {}
        count = (len(row.get("pending_approvals") or []) + len(row.get("pending_asks") or [])
                 + (1 if waiting.get("plan") else 0) + len(waiting.get("verdicts") or [])
                 + (1 if waiting.get("commitment") else 0))
        cap = budget.get("cap") or 0
        table.append((row.get("slug", ""), row.get("status", ""),
                      f"{money(budget, budget.get('spent') or 0)} / {money(budget, cap)}",
                      money(budget, cap * (budget.get("hard_stop_multiple") or 1)),
                      str(count) if count else "—",
                      # The server's verdict in words; the money is in its own column.
                      (row.get("status_line") or {}).get("headline", "")))
    widths = [max(len(r[c]) for r in table) for c in range(len(table[0]))]
    for r in table:
        typer.echo("  ".join(cell.ljust(widths[c]) for c, cell in enumerate(r)).rstrip())


@app.command()
def dashboard(project: str) -> None:
    """Everything one project's Overview reads, as JSON."""
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
    verbose: bool = typer.Option(False, "--verbose",
                                 help="also print the platform's own log lines"),
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
    import logging

    from yaaf import rehearsal
    from yaaf.rehearsal import report as rehearsal_report

    # The drill runs the real platform in this process, and the platform logs
    # like a server: a deployment warning, the rules it relaxed as a raw dict,
    # every refusal its scripted crew hit. Printed above the report, that was
    # the first thing a newcomer read. The report already says all of it.
    if not verbose:
        logging.getLogger("yaaf").setLevel(logging.ERROR)

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
    """The task board: every open task, who holds it, and the requirement it serves."""
    with _client() as client:
        _show(client.get(f"/observe/projects/{_resolve_project(client, project)}/board"))


@app.command()
def org(project: str) -> None:
    """Teams, agents and schedules — who is on the project and when each one wakes."""
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
    """The project's channels, with their ids for `yaaf tail`."""
    with _client() as client:
        _show(client.get(f"/observe/projects/{_resolve_project(client, project)}/channels"))


@app.command()
def inbox(project: str) -> None:
    """Each agent's unread messages, newest first — what the inbox banner counts."""
    with _client() as client:
        response = client.get(f"/observe/projects/{_resolve_project(client, project)}/inbox")
        if response.status_code >= 400:
            _show(response)
        view = response.json()
        reading = view["reading"]
        typer.echo(f"{view['unread']} unread · inbox reads {reading['used_today']}/"
                   f"{reading['cap_today']} today, {reading['used_this_hour']}/"
                   f"{reading['cap_this_hour']} this hour")
        for seat in view["agents"]:
            if not seat["unread"]:
                continue
            typer.echo(f"\n{seat['name']} — {seat['unread']} unread")
            for message in seat["messages"]:
                typer.echo(f"  [{message['at']}] {message['from_name'] or '?'} in "
                           f"{message['channel']}: {message['body']}")


@app.command()
def tail(channel_id: str, limit: int = typer.Option(30, "--limit")) -> None:
    """The latest messages in one channel (ids from `yaaf channels`)."""
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
    """Recent runs: which agent, why it woke, how it ended, what it cost."""
    with _client() as client:
        _show(client.get(f"/observe/projects/{_resolve_project(client, project)}/runs",
                         params={"limit": limit}))


@app.command()
def ledger(project: str) -> None:
    """Every metered line of spend this month."""
    with _client() as client:
        _show(client.get(f"/observe/projects/{_resolve_project(client, project)}/ledger"))


@app.command()
def evidence(project: str) -> None:
    """Every claim the agents filed, and what the platform could verify about it."""
    with _client() as client:
        _show(client.get(f"/observe/projects/{_resolve_project(client, project)}/evidence"))


@app.command()
def hypotheses(project: str) -> None:
    """An Explore project's bets, with their kill dates and what each has earned."""
    with _client() as client:
        _show(client.get(f"/observe/projects/{_resolve_project(client, project)}/hypotheses"))


@app.command()
def approvals(project: str) -> None:
    """Approvals waiting on you, and what each decides if you do nothing."""
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
    """Approve or deny an approval (ids from `yaaf approvals`). The note reaches the CEO."""
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
def ack_heads_up(heads_up_id: str, note: str = typer.Option("", "--note")) -> None:
    """Mark noted something you asked to be told. Nothing waits on it; a note is
    held until the CEO answers it."""
    with _client() as client:
        _show(client.post(f"/admin/heads_up/{heads_up_id}/ack", json={"note": note}))


@app.command()
def unfreeze(message_id: str) -> None:
    """Unfreeze a thread the loop breaker froze, so its agents may talk again."""
    with _client() as client:
        _show(client.post(f"/admin/threads/{message_id}/unfreeze"))


@app.command()
def audit_evidence(evidence_id: str, ok: bool = typer.Option(..., "--ok/--bad"),
                   note: str = typer.Option("", "--note")) -> None:
    """Record your own verdict on one piece of evidence (ids from `yaaf evidence`)."""
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
    """Everybody with an account on this install."""
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
    """Make somebody an instance admin."""
    with _client() as client:
        _show(client.patch(f"/admin/users/{_person_id(client, email)}", json={"is_admin": True}))


@people.command("demote")
def people_demote(email: str) -> None:
    """Take instance admin away from somebody. The last admin cannot be demoted."""
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
    """Let a deactivated person sign in again."""
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

#: Where each command sits in `yaaf --help`, in the order somebody new reaches
#: for them. The help used to be sixty commands in the order they were written.
PANELS = {
    "Get started": ("login", "logout", "whoami", "doctor", "rehearse", "create_project",
                    "projects"),
    "Run a project": ("start", "pause", "stop", "configure", "amend"),
    "Decide": ("say", "decide", "extend", "ack_flag", "ack_heads_up", "unfreeze", "audit",
               "audit_evidence"),
    "Read": ("briefing", "agenda", "receipt", "dashboard", "board", "org", "activity",
             "channels", "inbox", "tail", "runs", "ledger", "evidence", "hypotheses",
             "approvals", "asks", "flags", "spend_report"),
}
_ORDER = [*PANELS, "Set up the install"]


def _panel(name: str) -> str:
    return next((panel for panel, names in PANELS.items() if name in names),
                "Set up the install")


for _info in app.registered_commands:
    _info.rich_help_panel = _panel(_info.callback.__name__)
app.registered_commands.sort(key=lambda info: _ORDER.index(info.rich_help_panel))
for _group in app.registered_groups:
    _group.rich_help_panel = "Set up the install"

if __name__ == "__main__":
    app()
