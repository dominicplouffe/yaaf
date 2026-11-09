"""A data source's tools, their permissions, and the one door agents reach them by.

A data source used to be an address and a token handed to the sandbox. Which
tools an agent could call was a list the sandbox checked against itself — in
the same process, beside the same URL and token, with a shell one tool away. A
rule an agent can step around by typing `curl` is a sentence, not a rule.

So the address and the token now stay here. An agent reaches a source only
through `/agent/mcp/<source>` with its run token, and every call is checked
against the tool's recorded permission immediately before it is forwarded. A
denied call never leaves this process: nothing is sent upstream, and the
refusal counts against the run's refusal cap like every other one.

Three facts about the permission, each held by code rather than by a prompt:

* **What a tool starts as is decided by what it does.** A tool that reads is
  allowed; one that writes, or one the platform cannot tell about, is denied.
  `classify` says which, from the server's own hints and the tool's name, and
  fails closed.
* **Exactly two doors move it**: the chairman's tick on the Data sources page,
  and his yes to the CEO's `tool_permission` request. Both are in
  `yaaf/server/api/admin.py`, behind a person's credential. No `/agent` route
  writes a permission — `test_data_source_permissions.py` reads the source to
  hold that, because "no route does X" is not something one request can prove.
* **Saying so is not a door.** The chairman telling the CEO "go ahead, call it"
  changes nothing: the gateway never asked anyone's opinion.

Nothing gets a call through a denied tool. A one-call approval used to (a
TOOL_REQUEST carrying a grant); it was retired, because a switch that says deny
while a call goes through is not a switch. The only way past is the chairman
moving the switch, and then the tool is no longer denied.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, timedelta

import httpx
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from yaaf.core import ids
from yaaf.core.models import (
    Agent,
    AuditLog,
    DataSourceTool,
    Project,
    Role,
    ToolPermission,
    utcnow,
)
from yaaf.server.policy import Refusal

log = logging.getLogger("yaaf.datasource")

#: A seam for tests: every request this module makes upstream goes through it.
#: None in production (a real network); the suite sets an offline transport.
TRANSPORT: httpx.BaseTransport | None = None

#: The protocol version this platform speaks to the servers it calls.
UPSTREAM_PROTOCOL = "2025-06-18"
#: What the gateway answers an MCP client that asks for nothing in particular.
GATEWAY_PROTOCOL = "2025-06-18"

CONNECT_TIMEOUT_S = 30
READ_TIMEOUT_S = 180
#: The size data_fetch would write to a workspace file. A result larger than
#: this was refused before; it is refused here now, before it is held in memory.
MAX_RESULT_BYTES = 64 * 1024 * 1024
MAX_LIST_PAGES = 20
DESCRIPTION_CHARS = 4000
SCHEMA_BYTES = 64 * 1024
#: How stale the recorded list may be before an agent's tools/list refreshes it.
#: The harness lists at the start of every run; a source whose server grew a
#: tool should not wait for the chairman to press "Test" before it is seen.
REFRESH_AFTER = timedelta(minutes=10)

#: How many gateway requests may be in flight at once, per server process. Its
#: own budget, separate from the threads every other route shares: twenty slow
#: exports wait their turn here rather than leaving the API with no threads.
GATEWAY_CONCURRENCY = 16
#: Request header asking for the whole list, denied tools included, each marked.
LISTING_HEADER = "x-yaaf-listing"


# ── read or write ───────────────────────────────────────────────────────────────

#: Verbs that only look. Deliberately short: a word that is a read in one API
#: and a write in another ("check" in, "resolve" an issue, "open" a pull
#: request) is not here, and a tool named by none of these is not a read.
READ_VERBS = frozenset({
    "get", "list", "search", "find", "read", "fetch", "query", "describe", "show",
    "view", "lookup", "count", "inspect", "retrieve", "browse", "export", "download",
    "preview", "explain", "summarize", "summarise", "analyze", "analyse", "stats",
    "whoami", "ping", "validate", "estimate", "sample", "aggregate", "calculate",
    "compute", "compare", "diff", "peek", "explore", "discover", "visualize",
})

#: Verbs that change something. A tool whose name leads with one is a write.
WRITE_VERBS = frozenset({
    "create", "update", "delete", "remove", "add", "set", "put", "post", "patch",
    "insert", "upsert", "write", "edit", "modify", "change", "move", "rename",
    "archive", "unarchive", "send", "publish", "unpublish", "submit", "approve",
    "reject", "cancel", "close", "open", "merge", "assign", "unassign", "invite",
    "enable", "disable", "start", "stop", "run", "execute", "trigger", "deploy",
    "upload", "import", "sync", "reset", "revoke", "grant", "pause", "resume",
    "schedule", "book", "pay", "charge", "refund", "transfer", "purchase", "buy",
    "order", "subscribe", "unsubscribe", "comment", "reply", "react", "follow",
    "unfollow", "tag", "label", "mark", "star", "pin", "unpin", "apply", "commit",
    "push", "release", "restore", "replace", "clear", "purge", "drop", "truncate",
    "save", "store", "register", "link", "unlink", "attach", "detach", "mutate",
    "toggle", "duplicate", "copy", "clone", "fork", "lock", "unlock", "ban", "block",
    "unblock", "mute", "notify", "accept", "decline", "complete", "resolve", "reopen",
    "increase", "decrease", "adjust", "allocate", "bid", "configure", "install",
    "uninstall", "provision", "destroy", "terminate", "kill", "restart", "rollback",
    "dismiss", "invoke", "generate", "make", "new", "batch", "bulk",
})

#: Words that mean a change wherever they appear in a name, not only first:
#: `search_and_delete` and `find_and_replace` lead with a read and are writes.
#: Only words that are not also ordinary nouns — `post`, `order` and `update`
#: are left out, or `get_post` and `list_orders` would be denied for their noun.
ALWAYS_WRITE = frozenset({
    "create", "delete", "remove", "insert", "upsert", "replace", "destroy", "purge",
    "drop", "truncate", "overwrite", "modify", "edit", "send", "publish", "revoke",
    "terminate", "write", "set",
})


def _words(name: str) -> list[str]:
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name)
    return [w for w in re.split(r"[^A-Za-z0-9]+", spaced.lower()) if w]


def classify(name: str, annotations: dict | None) -> tuple[str, str]:
    """("read" | "write" | "unknown", why) for one tool.

    The server's hints are read first, but a hint can only make a tool *more*
    restricted than its name: a server that marks `delete_rows` read-only is
    contradicting itself, and the contradiction is resolved toward deny. Unknown
    is denied by default like a write — a platform that cannot tell what a tool
    does has no business guessing that it is harmless.
    """
    hints = annotations or {}
    words = _words(name)
    lead = next((w for w in words if w in READ_VERBS or w in WRITE_VERBS), None)
    changing = next((w for w in words if w in ALWAYS_WRITE), None)

    if hints.get("destructiveHint") is True:
        return "write", "the server marks it destructive"
    if hints.get("readOnlyHint") is False:
        return "write", "the server marks it as changing things"
    if hints.get("readOnlyHint") is True:
        if changing is not None:
            return "write", (f"the server marks it read-only, but its name says "
                             f"“{changing}” — treated as a write")
        return "read", "the server marks it read-only"
    if changing is not None:
        return "write", f"its name says “{changing}”"
    if lead in WRITE_VERBS:
        return "write", f"its name starts with “{lead}”"
    if lead in READ_VERBS:
        return "read", f"its name starts with “{lead}”"
    return "unknown", "nothing in its name or the server's hints says it only reads"


def default_permission(kind: str, name: str, entry: dict) -> tuple[str, str]:
    """(permission, decided_by) for a tool seen for the first time.

    A source saved with an allowlist (`tools`) was decided by the chairman up
    front, and a tool he did not list is denied whatever it looks like. Without
    one, the kind decides: reads are allowed, everything else is denied.
    """
    allowlist = entry.get("tools")
    if allowlist is not None:
        return (ToolPermission.ALLOW if name in allowlist else ToolPermission.DENY,
                "allowlist")
    return (ToolPermission.ALLOW if kind == "read" else ToolPermission.DENY, "default")


# ── the catalog ─────────────────────────────────────────────────────────────────


def _bounded_schema(schema) -> dict | None:
    if not isinstance(schema, dict):
        return None
    if len(json.dumps(schema, default=str)) > SCHEMA_BYTES:
        return {"type": "object", "description": "schema too large to record"}
    return schema


def record_catalog(session: Session, project_id: str, entry: dict,
                   listed: list[dict]) -> list[DataSourceTool]:
    """Record what the server listed: new tools with their default permission,
    known ones refreshed, vanished ones marked gone (never deleted — a tool's
    permission and history come back with it).

    A tool whose description or hints changed is re-classified, and the change
    may only ever tighten a permission nobody chose: a default allow becomes a
    deny if the tool now writes, but a default deny is never loosened by the
    server rewording itself. A permission a person set — his tick, his yes, or
    the list he saved with the source — is never touched.

    Agents' requests discover too (a tool not seen yet, a stale list), so two
    can record the same new tool at once. Each insert is its own savepoint: one
    wins, the other reads the winner's row and carries on, as breakers.py does.
    """
    source = entry["name"]
    now = utcnow()
    rows = {r.name: r for r in session.execute(
        select(DataSourceTool).where(DataSourceTool.project_id == project_id,
                                     DataSourceTool.source == source)).scalars()}
    seen: set[str] = set()
    for tool in listed:
        name = str(tool.get("name") or "").strip()[:128]
        if not name or name in seen:
            continue
        seen.add(name)
        annotations = tool.get("annotations") if isinstance(tool.get("annotations"), dict) \
            else None
        kind, why = classify(name, annotations)
        description = (str(tool.get("description") or "") or None)
        row = rows.get(name)
        if row is None:
            permission, decided_by = default_permission(kind, name, entry)
            fresh = DataSourceTool(
                id=ids.new_id(ids.DATA_SOURCE_TOOL), project_id=project_id, source=source,
                name=name, kind=kind, kind_reason=why[:300], permission=permission,
                decided_by=decided_by, decided_at=now, first_seen_at=now, last_seen_at=now)
            try:
                with session.begin_nested():
                    session.add(fresh)
                row = fresh
            except IntegrityError:
                row = session.execute(select(DataSourceTool).where(
                    DataSourceTool.project_id == project_id, DataSourceTool.source == source,
                    DataSourceTool.name == name)).scalar_one()
            rows[name] = row
        else:
            if (row.decided_by == "default"
                    and row.permission == ToolPermission.ALLOW and kind != "read"
                    and row.kind == "read"):
                row.permission = ToolPermission.DENY
                row.decided_by = "default"
                row.decided_at = now
            row.kind, row.kind_reason = kind, why[:300]
        row.title = (str(tool.get("title") or "") or None)
        row.title = row.title[:200] if row.title else None
        row.description = description[:DESCRIPTION_CHARS] if description else None
        row.input_schema = _bounded_schema(tool.get("inputSchema") or tool.get("input_schema"))
        row.annotations = annotations
        row.last_seen_at = now
        row.gone_at = None
    for name, row in rows.items():
        if name not in seen and row.gone_at is None:
            row.gone_at = now
    session.flush()
    return sorted(rows.values(), key=lambda r: r.name)


def catalog(session: Session, project_id: str, source: str | None = None) -> list[DataSourceTool]:
    query = select(DataSourceTool).where(DataSourceTool.project_id == project_id)
    if source is not None:
        query = query.where(DataSourceTool.source == source)
    # populate_existing: another session may have recorded or moved a tool
    # since this one first loaded it, and the gateway decides on what is true now.
    return list(session.execute(
        query.order_by(DataSourceTool.source, DataSourceTool.name),
        execution_options={"populate_existing": True}).scalars())


def tool_row(row: DataSourceTool) -> dict:
    return {
        "source": row.source, "name": row.name, "title": row.title,
        "description": row.description, "input_schema": row.input_schema,
        "annotations": row.annotations, "kind": row.kind, "kind_reason": row.kind_reason,
        "permission": row.permission, "decided_by": row.decided_by,
        "decided_at": row.decided_at.isoformat() if row.decided_at else None,
        "first_seen_at": row.first_seen_at.isoformat() if row.first_seen_at else None,
        "last_seen_at": row.last_seen_at.isoformat() if row.last_seen_at else None,
        "gone": row.gone_at is not None,
    }


def allowed_names(session: Session, project_id: str, source: str) -> list[str]:
    return sorted(session.execute(
        select(DataSourceTool.name).where(
            DataSourceTool.project_id == project_id, DataSourceTool.source == source,
            DataSourceTool.permission == ToolPermission.ALLOW,
            DataSourceTool.gone_at.is_(None))).scalars())


def denied_names(session: Session, project_id: str, source: str) -> list[str]:
    return sorted(session.execute(
        select(DataSourceTool.name).where(
            DataSourceTool.project_id == project_id, DataSourceTool.source == source,
            DataSourceTool.permission == ToolPermission.DENY,
            DataSourceTool.gone_at.is_(None))).scalars())


def set_permission(session: Session, project: Project, source: str, tool: str,
                   permission: str, *, decided_by: str, user_id: str | None) -> dict:
    """Move one tool. The only function that writes a permission a person
    chose, and it is called from exactly two places — the chairman's own route
    and his decision on a `tool_permission` approval — both in admin.py, both
    behind a person's credential. The suite fails if a third caller appears."""
    if permission not in (ToolPermission.ALLOW, ToolPermission.DENY):
        raise ValueError(f"not a permission: {permission!r}")
    row = session.execute(
        select(DataSourceTool).where(DataSourceTool.project_id == project.id,
                                     DataSourceTool.source == source,
                                     DataSourceTool.name == tool)).scalar_one_or_none()
    if row is None:
        raise LookupError(f"{source} offers no tool named {tool!r}")
    was = row.permission
    row.permission = permission
    row.decided_by = decided_by
    row.decided_by_user_id = user_id
    row.decided_at = utcnow()
    return {"source": source, "tool": tool, "from": was, "to": permission}


# ── speaking MCP to the server that holds the account ──────────────────────────


class UpstreamError(Exception):
    """The source could not be reached, or did not answer as an MCP server.
    Not a refusal: nothing was decided against the agent, and it does not
    count toward the refusal cap."""

    def __init__(self, message: str, *, state: str = "unreachable"):
        super().__init__(message)
        self.state = state


def _messages(response: httpx.Response, raw: bytes) -> list[dict]:
    """Every JSON-RPC message in a response, whether the server answered with
    JSON or with an event stream (which may carry progress notifications ahead
    of the result)."""
    text = raw.decode("utf-8", errors="replace")
    if "text/event-stream" in response.headers.get("content-type", ""):
        out, data = [], []
        for line in text.splitlines() + [""]:
            if line.startswith("data:"):
                data.append(line[5:].lstrip())
            elif not line.strip() and data:
                with contextlib.suppress(ValueError):
                    out.append(json.loads("\n".join(data)))
                data = []
        return out
    if not text.strip():
        return []
    parsed = json.loads(text)
    return parsed if isinstance(parsed, list) else [parsed]


class Upstream:
    """One short MCP session with a data source: initialize, then requests.
    Stateless on purpose — a session per call is what data_fetch did, and it
    means a server restart between two calls costs nothing."""

    def __init__(self, url: str, token: str | None, *, transport=None,
                 read_timeout: float = READ_TIMEOUT_S,
                 connect_timeout: float = CONNECT_TIMEOUT_S):
        self.url = url
        self.transport = transport if transport is not None else TRANSPORT
        self.read_timeout, self.connect_timeout = read_timeout, connect_timeout
        #: What the server said it is, from its handshake ({"name", "version"}).
        self.server_info: dict = {}
        self.headers = {"Content-Type": "application/json",
                        "Accept": "application/json, text/event-stream"}
        if token:
            self.headers["Authorization"] = f"Bearer {token}"
        self.session_id: str | None = None
        self._next_id = 0
        self._client: httpx.Client | None = None

    def __enter__(self) -> Upstream:
        self._client = httpx.Client(
            transport=self.transport, follow_redirects=True,
            timeout=httpx.Timeout(self.read_timeout, connect=self.connect_timeout))
        try:
            opened = self._post({"jsonrpc": "2.0", "id": self._id(), "method": "initialize",
                                 "params": {"protocolVersion": UPSTREAM_PROTOCOL,
                                            "capabilities": {},
                                            "clientInfo": {"name": "yaaf", "version": "1.0"}}})
            self.session_id = opened.get("_session")
            self.server_info = opened.get("serverInfo") or {}
            self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        except BaseException:
            self._client.close()
            raise
        return self

    def __exit__(self, *exc) -> None:
        if self._client is not None:
            self._client.close()

    def _id(self) -> int:
        self._next_id += 1
        return self._next_id

    def _post(self, payload: dict) -> dict:
        assert self._client is not None
        headers = dict(self.headers)
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        try:
            with self._client.stream("POST", self.url, json=payload, headers=headers) as resp:
                if resp.status_code in (401, 403):
                    raise UpstreamError(
                        f"the server refused the credential (HTTP {resp.status_code}) — "
                        "check the bearer token", state="unauthorized")
                if resp.status_code == 404:
                    raise UpstreamError(
                        "no MCP endpoint at that path (HTTP 404) — the address needs the "
                        "server's own path, often /mcp", state="not_found")
                if resp.status_code >= 400:
                    raise UpstreamError(
                        "something is listening, but it did not accept an MCP handshake "
                        f"(HTTP {resp.status_code})", state="not_mcp")
                chunks, size = [], 0
                for chunk in resp.iter_bytes():
                    size += len(chunk)
                    if size > MAX_RESULT_BYTES:
                        raise UpstreamError(
                            f"the result is over {MAX_RESULT_BYTES // (1024 * 1024)} MB. Use "
                            "the tool's own filter, date-range or pagination arguments and "
                            "fetch a slice.", state="too_large")
                    chunks.append(chunk)
                session_id = resp.headers.get("mcp-session-id")
                if "id" not in payload:
                    return {}
                try:
                    messages = _messages(resp, b"".join(chunks))
                except ValueError as exc:
                    raise UpstreamError("that address answered, but not with MCP — it looks "
                                        "like an ordinary web page or API",
                                        state="not_mcp") from exc
        except httpx.ProxyError as exc:
            # Before ConnectError: a proxy refusal is not "nothing is listening",
            # and telling someone to check a port that is fine sends them the wrong way.
            raise UpstreamError("a proxy between yaaf and that address refused the "
                                "connection — the host probably needs allowing on this "
                                "network", state="blocked") from exc
        except httpx.ConnectError as exc:
            raise UpstreamError("nothing is listening at that address — check host and "
                                "port, and that the server is running",
                                state="unreachable") from exc
        except httpx.TimeoutException as exc:
            raise UpstreamError(f"no answer within {int(self.read_timeout)} seconds — the "
                                "server may be down, or a firewall is dropping the "
                                "connection", state="timeout") from exc
        except httpx.HTTPError as exc:
            raise UpstreamError(f"could not connect: {exc.__class__.__name__}") from exc
        reply = next((m for m in messages if isinstance(m, dict) and m.get("id") == payload["id"]
                      and ("result" in m or "error" in m)), None)
        if reply is None:
            raise UpstreamError("the server sent no answer to the request", state="not_mcp")
        if "error" in reply:
            message = (reply.get("error") or {}).get("message", "unknown error")
            if payload["method"] == "initialize":
                raise UpstreamError(f"the server refused the handshake: {message}",
                                    state="not_mcp")
            raise UpstreamError(f"the server refused {payload['method']}: {message}",
                                state="rpc_error")
        result = reply.get("result") or {}
        if session_id:
            result = {**result, "_session": session_id}
        return result

    def request(self, method: str, params: dict) -> dict:
        result = self._post({"jsonrpc": "2.0", "id": self._id(), "method": method,
                             "params": params})
        result.pop("_session", None)
        return result

    def list_tools(self) -> list[dict]:
        tools: list[dict] = []
        cursor = None
        for _ in range(MAX_LIST_PAGES):
            page = self.request("tools/list", {"cursor": cursor} if cursor else {})
            tools += [t for t in (page.get("tools") or []) if isinstance(t, dict)]
            cursor = page.get("nextCursor")
            if not cursor:
                break
        return tools

    def call_tool(self, name: str, arguments: dict) -> dict:
        return self.request("tools/call", {"name": name, "arguments": arguments})


# ── the source's address and credential, which never leave this process ──────


def _secret_value(session: Session, project_id: str, name: str | None) -> str | None:
    if not name:
        return None
    from yaaf.server.api.admin import _project_secret_value

    return _project_secret_value(session, project_id, name)


def credentials(session: Session, project: Project, entry: dict) -> tuple[str, str | None]:
    url = _secret_value(session, project.id, entry.get("url_secret"))
    if not url:
        raise UpstreamError(f"data source '{entry.get('name')}' has no stored address — "
                            "the chairman must re-save it", state="misconfigured")
    if entry.get("auth") == "oauth":
        token = _oauth_token(session, project.id, entry)
        if token is None:
            raise UpstreamError(f"data source '{entry.get('name')}' is not signed in — the "
                                "chairman must press Connect", state="needs_signin")
        return url, token
    return url, _secret_value(session, project.id, entry.get("bearer_secret"))


def _oauth_token(session: Session, project_id: str, entry: dict) -> str | None:
    """The access token to present now, renewed if it is about to expire.

    Renewing rewrites the source's entry in `project.integrations`, which is one
    JSON value the chairman edits too. Done on the caller's copy of the project
    — loaded before a slow call, and on every agent call — it would write back a
    stale list and undo whatever he saved meanwhile. So a token that is still
    good is read and nothing is written; a renewal runs in a short transaction
    of its own on the project row as it is now, locked where the database can
    (Postgres), and merges into that."""
    from yaaf.core.db import db_session
    from yaaf.server import mcp_oauth

    oauth = entry.get("oauth") or {}
    if not mcp_oauth._expires_soon(oauth.get("expires_at"), mcp_oauth.REFRESH_MARGIN_S):
        held = _secret_value(session, project_id, entry.get("bearer_secret"))
        if held:
            return held
    with db_session() as own:
        current = own.execute(select(Project).where(Project.id == project_id)
                              .with_for_update()).scalar_one()
        fresh = source_entry(current, entry["name"])
        return mcp_oauth.ensure_fresh_token(own, current, fresh) if fresh else None


def discover(session: Session, project: Project, entry: dict) -> list[DataSourceTool]:
    """Ask the server what it offers and record it."""
    url, token = credentials(session, project, entry)
    with Upstream(url, token) as upstream:
        listed = upstream.list_tools()
    return record_catalog(session, project.id, entry, listed)


def owned_secret_names(project: Project) -> set[str]:
    """Every secret a data source holds: its address, its token, and its OAuth
    refresh token and client secret. None of them is ever handed to a sandbox."""
    names: set[str] = set()
    for entry in (project.integrations or {}).get("mcp_servers", []) or []:
        oauth = entry.get("oauth") or {}
        names.update(n for n in (entry.get("url_secret"), entry.get("bearer_secret"),
                                 oauth.get("refresh_secret"),
                                 oauth.get("client_secret_secret")) if n)
    return names


# ── the gateway: who may call what, decided before anything is sent ───────────


def source_entry(project: Project, name: str) -> dict | None:
    for entry in (project.integrations or {}).get("mcp_servers", []) or []:
        if entry.get("name") == name:
            return entry
    return None


def agent_role_slug(session: Session, agent: Agent) -> str | None:
    from yaaf.server.contextpack import role_version_for

    version = role_version_for(session, agent)
    role = session.get(Role, version.role_id) if version is not None else None
    return role.slug if role is not None else None


def granted_entry(session: Session, project: Project, agent: Agent, name: str) -> dict:
    """The source, if this agent's role was given it. The grant is the
    chairman's "who gets its tools", read here at call time — so taking a team
    off a source takes effect on its next call, not its next run."""
    entry = source_entry(project, name)
    if entry is None:
        known = ", ".join(sorted(e.get("name", "?") for e in
                                 (project.integrations or {}).get("mcp_servers", []) or []))
        raise Refusal(f"{name!r} is not one of this project's data sources"
                      f"{f' (it has: {known})' if known else ''}.", status_code=404)
    if agent_role_slug(session, agent) not in (entry.get("roles") or []):
        raise Refusal(
            f"Data source '{name}' is not given to your role. The chairman decides who "
            "gets each source's tools; if your work needs it, say so to your CEO.",
            status_code=403)
    return entry


def catalog_fresh(session: Session, project_id: str, source: str) -> bool:
    newest = session.execute(
        select(func.max(DataSourceTool.last_seen_at)).where(
            DataSourceTool.project_id == project_id, DataSourceTool.source == source)
    ).scalar_one_or_none()
    if newest is None:
        return False
    if newest.tzinfo is None:  # func.max can drop the column's type on SQLite
        newest = newest.replace(tzinfo=UTC)
    return utcnow() - newest < REFRESH_AFTER


def ensure_catalog(session: Session, project: Project, entry: dict, *,
                   refresh: bool = False) -> list[DataSourceTool]:
    """The recorded tools, discovering them first when there are none (or when
    asked to refresh). A failed refresh falls back to what is recorded; a
    failed first discovery has nothing to fall back to and raises."""
    rows = catalog(session, project.id, entry["name"])
    if rows and not refresh:
        return rows
    try:
        return discover(session, project, entry)
    except UpstreamError:
        if rows:
            log.warning("could not refresh the tools of %s on %s", entry["name"], project.id)
            return rows
        raise


def deny_message(source: str, tool: str, row: DataSourceTool | None, ceo: bool,
                 callable_now: list[str] | None = None) -> str:
    if row is None:
        return (f"{source}.{tool} is not a tool this source offers, so it was not sent. "
                "List what it offers with data_fetch(server) and no tool.")
    why = {"default": f"it is a {row.kind if row.kind != 'unknown' else 'tool that may write'} "
                      "and those start denied",
           "allowlist": "it is not on the list the chairman saved for this source",
           "chairman": "the chairman denied it",
           "approval": "the chairman denied it"}.get(row.decided_by, "it is denied")
    ask = ("Ask the chairman to allow it with tool_permission_request (he decides; you "
           "cannot change it)" if ceo else
           "If your work needs it, tell your CEO — only the CEO can ask the chairman to "
           "allow it, and only the chairman can")
    allowed = ", ".join(callable_now or []) or "none"
    return (f"{source}.{tool} is DENIED on this project ({why}), so the call was refused "
            f"before anything was sent to {source}. Callable on {source} now: {allowed}. "
            f"{ask}. Nothing else gets a call through a denied tool.")


def _arguments_digest(arguments: dict | None) -> str:
    canonical = json.dumps(arguments or {}, sort_keys=True, separators=(",", ":"),
                           default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


@dataclass
class Decision:
    entry: dict
    row: DataSourceTool


def decide(session: Session, project: Project, agent: Agent, source: str,
           tool: str) -> Decision:
    """Whether this call may be forwarded: only if the tool is allowed, and
    nothing else is consulted — no approval, no argument, nothing anyone said.
    Raises Refusal when it may not, and nothing in this function, or before
    it, has sent the call to the source."""
    from yaaf.core.models import AgentKind

    entry = granted_entry(session, project, agent, source)
    rows = {r.name: r for r in catalog(session, project.id, source)}
    if tool not in rows:
        # A tool the platform has never seen has no permission yet. Ask the
        # server what it offers before deciding — a listing, never the call.
        # If it cannot be asked, that raises UpstreamError: "unreachable" is
        # the honest answer, and not one the agent should pay a refusal for.
        rows = {r.name: r for r in discover(session, project, entry)}
    row = rows.get(tool)
    if row is None or row.gone_at is not None:
        raise Refusal(deny_message(source, tool, None, agent.kind == AgentKind.CEO),
                      status_code=404)
    if row.permission != ToolPermission.ALLOW:
        raise Refusal(deny_message(source, tool, row, agent.kind == AgentKind.CEO,
                                   sorted(n for n, r in rows.items()
                                          if r.permission == ToolPermission.ALLOW
                                          and r.gone_at is None)),
                      status_code=403)
    return Decision(entry, row)


def forward(session: Session, project: Project, agent: Agent, decision: Decision,
            arguments: dict, run_id: str | None = None) -> dict:
    """Make the call `decide` allowed. Anything it recorded is committed first:
    an upstream call can take minutes, and must not hold a write lock that long."""
    session.commit()
    url, token = credentials(session, project, decision.entry)
    session.commit()  # a refreshed OAuth token, kept even if the call fails
    with Upstream(url, token) as upstream:
        result = upstream.call_tool(decision.row.name, arguments)
    if decision.row.kind != "read":
        # Reads are the common case and are not written down call by call. A
        # tool that may change the chairman's account is, every time — and it
        # is the run doing something, which a read is not (deps.GATEWAY_PREFIX).
        from yaaf.core.models import Run

        run = session.get(Run, run_id) if run_id else None
        if run is not None:
            run.produced = (run.produced or 0) + 1
        session.add(AuditLog(
            id=ids.new_id(ids.AUDIT), project_id=project.id, actor=f"agent:{agent.id}",
            action="data_source.call", entity="data_source", entity_id=decision.entry["name"],
            detail={"source": decision.entry["name"], "tool": decision.row.name,
                    "kind": decision.row.kind,
                    "arguments_sha256": _arguments_digest(arguments),
                    "is_error": bool(result.get("isError"))}))
    return result
