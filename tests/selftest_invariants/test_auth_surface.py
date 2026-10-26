"""Every human route says who may call it, and the saying is the check.

docs/AUTH.md rule 1. The failure this exists for is the commonest one in
multi-user software, and another agent platform shipped it as a security
advisory: an administrative route that authenticated the caller and never asked
whether *this* caller could touch *this* thing. Nothing fails when a handler
forgets — the suite stays green, the route works for the person who wrote it —
so the rule is mechanical: every `/admin`, `/observe` and `/auth` route carries
exactly one declaration from `yaaf.server.authority`, as a route dependency
FastAPI runs before the handler, and this file walks the live app to prove it.
"""

from __future__ import annotations

import inspect
import re

from yaaf.server import authority

#: Mounted prefixes whose routes are a person's, and the routers behind them.
#: Read from the app rather than restated, then checked against the OpenAPI
#: document so a router mounted later cannot be missed by this list.
HUMAN_PREFIXES = ("/admin", "/observe", "/auth")

#: The only routes on those prefixes allowed to be open, and why.
PUBLIC = {
    "POST /auth/login": "the door itself",
    "POST /auth/token": "the CLI's door, through the same throttle",
    "GET /auth/setup": "credentialed by the single-use link it carries",
    "POST /auth/setup": "credentialed by the single-use link it carries",
}

#: In-handler checks: the handler must call one of these.
CHECKS = re.compile(r"authority\.(require\w*|owns)\(|_require_\w+\(|_thread\(")


def _routers():
    from yaaf.server.api import admin, auth, founder, integrations, observe
    from yaaf.server.api import settings as app_settings

    return [("/admin", admin.router), ("/admin", integrations.router),
            ("/admin/founder", founder.router),
            ("/admin/settings", app_settings.router), ("/observe", observe.router),
            ("/auth", auth.router)]


def _declarations(dependant) -> list[str]:
    found = []
    for dep in dependant.dependencies:
        kind = getattr(dep.call, "__authority__", None)
        if kind is not None:
            found.append(kind)
        found.extend(_declarations(dep))
    return found


#: `{model:path}` in a route is `{model}` in the OpenAPI document; compare the
#: two in the document's spelling.
CONVERTER = re.compile(r"\{(\w+):\w+\}")


def _human_routes():
    for prefix, router in _routers():
        for route in router.routes:
            for method in sorted(route.methods or ()):
                yield f"{method} {prefix}{CONVERTER.sub(r'{\1}', route.path)}", route


def test_every_human_route_declares_exactly_one_authority():
    undeclared, doubled = [], []
    for name, route in _human_routes():
        found = _declarations(route.dependant)
        if not found:
            undeclared.append(name)
        elif len(found) > 1:
            doubled.append(f"{name}: {found}")
    assert not undeclared, (
        "these routes authenticate a person and never say what that person must be. "
        f"Add one of authority.READ/STEER/CHAIR/SIGNED_IN/INSTANCE_ADMIN/IN_HANDLER: "
        f"{undeclared}")
    assert not doubled, f"two declarations is two answers to one question: {doubled}"


def test_the_open_routes_are_exactly_the_ones_meant_to_be():
    public = {name for name, route in _human_routes()
              if _declarations(route.dependant) == ["public"]}
    assert public == set(PUBLIC), (
        f"open by accident: {public - set(PUBLIC)}; listed but not open: {set(PUBLIC) - public}")


def test_a_project_declaration_can_find_its_project():
    """READ/STEER/CHAIR resolve the project from the path. A route declaring
    one with nothing in its path to resolve from would let everybody through,
    because the gate has nothing to protect — and would look protected."""
    blind = []
    for name, route in _human_routes():
        gated = any(d.startswith("project:") for d in _declarations(route.dependant))
        if gated and not any(f"{{{p}}}" in route.path for p in authority.PROJECT_PARAMS):
            blind.append(name)
    assert not blind, f"project-level declarations with no project in the path: {blind}"


def test_an_in_handler_declaration_is_followed_by_a_check():
    """IN_HANDLER means "I check it myself". Hold every one of them to it."""
    unchecked = []
    for name, route in _human_routes():
        if _declarations(route.dependant) == ["in_handler"]:
            source = inspect.getsource(route.endpoint)
            if not CHECKS.search(source):
                unchecked.append(name)
    assert not unchecked, f"IN_HANDLER routes that never check anything: {unchecked}"


def test_this_file_sees_every_human_route_the_app_serves(client):
    """The walk above reads routers; this proves no router was left out of it."""
    walked = {name.split(" ", 1)[1] for name, _ in _human_routes()}
    served = {path for path in client.app.openapi()["paths"]
              if path.startswith(HUMAN_PREFIXES)}
    assert served <= walked, f"served but never walked: {served - walked}"
