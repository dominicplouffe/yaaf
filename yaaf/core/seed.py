"""Seeding: the design an install keeps when its database does not.

The rebuild drops the database on purpose. Two things must not go with it.

Roles are global — `roles` carries no `project_id` — so dropping every project
leaves them untouched, and they need no help. Goals and constitutions do not
survive: a mandate is a page of writing that took longer than the code around
it, and a constitution is a set of arguments somebody already had. Retyping
either is how a rebuild quietly loses its most expensive input.

So this module reads both back out over the ordinary API and writes them as
JSON, and puts them back the same way. It lives here rather than in the CLI
because the CLI is a typer wrapper and this is behaviour worth testing.

What travels is *design*: prompts, goals, constitutions, team shapes. What
never travels is this install's plumbing — secrets, Slack wiring, webhook
signing keys — which the export endpoint already strips, and which
`test_seed_roundtrip` checks rather than assumes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol


class _Client(Protocol):
    """Just enough of httpx.Client for both the real CLI and the test client."""

    def get(self, url: str, **kwargs: Any) -> Any: ...
    def post(self, url: str, **kwargs: Any) -> Any: ...


def _ok(response: Any) -> Any:
    if response.status_code >= 400:
        raise RuntimeError(f"{response.request.method} {response.request.url} "
                           f"→ {response.status_code} {response.text[:200]}")
    return response.json()


def save_seed(client: _Client, directory: Path) -> tuple[int, int]:
    """Write every role and every project to `directory`. Returns the counts."""
    roles_dir, projects_dir = directory / "roles", directory / "projects"
    roles_dir.mkdir(parents=True, exist_ok=True)
    projects_dir.mkdir(parents=True, exist_ok=True)

    roles = _ok(client.get("/admin/roles"))
    for role in roles:
        body = _ok(client.get(f"/admin/roles/{role['slug']}"))
        # A seeded role starts a fresh version chain on the new install; the
        # number it happened to reach on the old one means nothing there.
        body.pop("version", None)
        (roles_dir / f"{body['slug']}.json").write_text(json.dumps(body, indent=2) + "\n")

    projects = _ok(client.get("/observe/projects"))
    for project in projects:
        bundle = _ok(client.get(f"/admin/projects/{project['id']}/export"))
        (projects_dir / f"{project['slug']}.json").write_text(json.dumps(bundle, indent=2) + "\n")

    return len(roles), len(projects)


def restore_roles(client: _Client, directory: Path) -> list[tuple[str, int]]:
    """Roles first: a project spec names the roles its teams run."""
    out = []
    for path in sorted((directory / "roles").glob("*.json")):
        response = client.post("/admin/roles", json=json.loads(path.read_text()))
        out.append((path.stem, response.status_code))
    return out


def restore_projects(client: _Client, directory: Path, chairman_email: str) -> list[tuple[str, int]]:
    """Projects are created in DRAFT and never started. Restoring a backup must
    not begin spending money — starting a project stays a deliberate act."""
    out = []
    for path in sorted((directory / "projects").glob("*.json")):
        bundle = json.loads(path.read_text())
        spec = bundle.get("project", bundle)
        spec.setdefault("slug", path.stem)
        # A bundle carries no chairman: that is an identity on this install,
        # not part of the design that travels.
        spec["chairman_email"] = chairman_email
        response = client.post("/admin/projects", json=spec)
        out.append((path.stem, response.status_code))
    return out


def project_specs(directory: Path) -> list[Path]:
    return sorted((directory / "projects").glob("*.json"))
