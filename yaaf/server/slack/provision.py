"""Make a project's Slack rooms, and say what they are called.

The point of app-wide Slack settings is that creating a project should not be
followed by twenty minutes of clicking in Slack. With a Slack connection configured
once, the platform creates ``{prefix}-boardroom``, ``{prefix}-leadership`` and
one room per team, invites the chairman so private channels are actually
visible to him, writes the ids into the project's channel map, and hands back
the list of names.

Three properties this has to have, because provisioning is the kind of thing a
chairman runs twice:

- **Idempotent.** A room already in the channel map is left alone. A name Slack
  says is taken is adopted, not duplicated — ``name_taken`` is the normal
  answer on a second run, and treating it as an error would make the button a
  trap.
- **Partial by default.** One team room that fails to create does not undo the
  three that worked. Every row reports its own outcome and the caller shows
  them all.
- **Never fatal to its caller.** Project creation calls this; a Slack outage
  must not cost a chairman his project. :func:`provision_quietly` is the
  swallowing wrapper the creation path uses.

The bot token is decrypted here and used here. It is never written anywhere a
sandbox can read, exactly as the mirror's token never is.
"""

from __future__ import annotations

import contextlib
import logging

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from yaaf.core.models import Channel, ChannelKind, Project, Team
from yaaf.server.slack import settings as slack_settings

log = logging.getLogger("yaaf.slack.provision")

API_TIMEOUT_S = 15.0


def _call(token: str, method: str, payload: dict) -> dict:
    """One Slack Web API call. Module-level so tests monkeypatch the wire."""
    with httpx.Client(timeout=API_TIMEOUT_S) as http:
        response = http.post(
            f"https://slack.com/api/{method}",
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
        )
    try:
        return response.json()
    except ValueError:
        return {"ok": False, "error": f"http_{response.status_code}"}


def _get(token: str, method: str, params: dict) -> dict:
    """The read half. Slack's list endpoints are GET-with-query, and posting
    JSON at them returns `invalid_arguments` rather than anything useful."""
    with httpx.Client(timeout=API_TIMEOUT_S) as http:
        response = http.get(
            f"https://slack.com/api/{method}",
            headers={"Authorization": f"Bearer {token}"},
            params=params,
        )
    try:
        return response.json()
    except ValueError:
        return {"ok": False, "error": f"http_{response.status_code}"}


def workspace_check(token: str) -> dict:
    """Who this token says it is. `auth.test` is free and needs no scope, so it
    is the honest answer to "is this token any good" — the alternative is
    finding out on the first channel a chairman tries to create."""
    try:
        payload = _call(token, "auth.test", {})
    except httpx.HTTPError as exc:
        return {"ok": False, "detail": f"could not reach Slack: {type(exc).__name__}"}
    if not payload.get("ok"):
        return {"ok": False, "detail": f"Slack said: {payload.get('error') or 'unknown error'}"}
    return {
        "ok": True,
        "team": payload.get("team") or "",
        "bot": payload.get("user") or "",
        "detail": f"connected to the '{payload.get('team')}' workspace as {payload.get('user')}.",
    }


def find_channel(token: str, name: str) -> str | None:
    """The id of an existing channel by exact name, or None.

    Only reached when `conversations.create` says `name_taken`, which is the
    ordinary answer to re-running provisioning. Private channels the bot is not
    a member of are invisible here — that returns None, and the caller reports
    the name as taken-but-unreachable rather than silently mapping the wrong
    room.
    """
    cursor = ""
    for _ in range(20):  # a workspace is not infinite; a paging bug must not be
        payload = _get(token, "conversations.list", {
            "types": "public_channel,private_channel",
            "exclude_archived": "true",
            "limit": 200,
            **({"cursor": cursor} if cursor else {}),
        })
        if not payload.get("ok"):
            return None
        for channel in payload.get("channels") or []:
            if channel.get("name") == name:
                return str(channel["id"])
        cursor = ((payload.get("response_metadata") or {}).get("next_cursor")) or ""
        if not cursor:
            return None
    return None


def ensure_channel(token: str, name: str, *, private: bool) -> dict:
    """Create the channel, or adopt the one already wearing that name."""
    payload = _call(token, "conversations.create",
                    {"name": name, "is_private": private})
    if payload.get("ok"):
        channel = payload.get("channel") or {}
        return {"id": str(channel.get("id")), "name": str(channel.get("name") or name),
                "created": True}
    if payload.get("error") == "name_taken":
        found = find_channel(token, name)
        if found:
            return {"id": found, "name": name, "created": False}
        return {"error": f"a channel called #{name} already exists but this bot cannot see "
                         "it — invite the bot to it, or choose a different prefix"}
    return {"error": f"Slack refused to create #{name}: {payload.get('error') or 'unknown error'}"}


def _invite(token: str, channel_id: str, user: str) -> bool:
    """Put the chairman in the room. Best-effort, and `already_in_channel` is
    success — a private channel nobody was invited to is a room the chairman
    cannot read, which is the same as not having created it."""
    if not user:
        return False
    payload = _call(token, "conversations.invite", {"channel": channel_id, "users": user})
    return bool(payload.get("ok")) or payload.get("error") in (
        "already_in_channel", "cant_invite_self")


def _set_purpose(token: str, channel_id: str, purpose: str) -> None:
    with contextlib.suppress(httpx.HTTPError):  # cosmetic by contract
        _call(token, "conversations.setPurpose",
              {"channel": channel_id, "purpose": purpose[:250]})


def _wanted_rooms(session: Session, project: Project) -> tuple[list[str], dict[str, str]]:
    """Every room key this project has a bus channel for, plus team names.

    Driven off the bus channels rather than the team table, so the rooms that
    get a Slack home are exactly the rooms that have something to mirror.
    """
    team_names = {
        team.id: team.name
        for team in session.execute(
            select(Team).where(Team.project_id == project.id)).scalars()
    }
    keys: list[str] = []
    for channel in session.execute(
        select(Channel).where(Channel.project_id == project.id)
    ).scalars():
        key = (f"team:{channel.team_id}" if channel.kind == ChannelKind.TEAM
               else str(channel.kind))
        if key not in keys:
            keys.append(key)
    order = {"boardroom": 0, "leadership": 1}
    keys.sort(key=lambda k: (order.get(k, 2), k))
    return keys, team_names


def provision(session: Session, project: Project) -> dict:
    """Create every missing room for this project and record the ids.

    Returns a report the chairman reads verbatim: one row per room with the
    channel name he should now look for in Slack, whether it was created or
    adopted, and — when something went wrong — what Slack said.
    """
    effective = slack_settings.resolve(session, project)
    if effective is None:
        return {"ok": False, "channels": [],
                "detail": "Slack is switched off for this project."}
    if not effective.bot_token:
        return {"ok": False, "channels": [],
                "detail": "no Slack bot token is configured — set one app-wide in Settings, "
                          "or give this project its own."}

    keys, team_names = _wanted_rooms(session, project)
    private = effective.channel_visibility == "private"
    channel_map = dict(effective.channel_map)
    rows: list[dict] = []
    for key in keys:
        label = slack_settings.room_label(key, team_names)
        name = slack_settings.channel_name(effective.channel_prefix, key, team_names)
        if channel_map.get(key):
            rows.append({"key": key, "label": label, "name": name,
                         "channel_id": channel_map[key], "created": False,
                         "already_mapped": True, "invited": False})
            continue
        try:
            result = ensure_channel(effective.bot_token, name, private=private)
        except httpx.HTTPError as exc:
            result = {"error": f"could not reach Slack: {type(exc).__name__}"}
        if "error" in result:
            rows.append({"key": key, "label": label, "name": name, "channel_id": None,
                         "created": False, "already_mapped": False, "invited": False,
                         "error": result["error"]})
            continue
        channel_map[key] = result["id"]
        invited = False
        try:
            invited = _invite(effective.bot_token, result["id"],
                              effective.chairman_slack_user)
            _set_purpose(effective.bot_token, result["id"],
                         f"yaaf · {project.name} · {label}")
        except httpx.HTTPError:  # pragma: no cover - best-effort by contract
            log.warning("post-create Slack calls failed for #%s", name, exc_info=True)
        rows.append({"key": key, "label": label, "name": result["name"],
                     "channel_id": result["id"], "created": result["created"],
                     "already_mapped": False, "invited": invited})

    _write_channel_map(project, channel_map)
    made = [r for r in rows if r["created"]]
    errors = [r for r in rows if r.get("error")]
    return {
        "ok": not errors,
        "visibility": effective.channel_visibility,
        "prefix": effective.channel_prefix,
        "channels": rows,
        "detail": _detail(made, rows, errors, effective),
    }


def _detail(made: list[dict], rows: list[dict], errors: list[dict],
            effective: slack_settings.SlackSettings) -> str:
    if not rows:
        return "this project has no channels to mirror yet."
    parts: list[str] = []
    if made:
        parts.append(
            f"created {len(made)} {effective.channel_visibility} channel"
            f"{'' if len(made) == 1 else 's'}: "
            + ", ".join(f"#{r['name']}" for r in made))
    adopted = [r for r in rows if not r["created"] and not r.get("error")
               and not r.get("already_mapped")]
    if adopted:
        parts.append("adopted existing " + ", ".join(f"#{r['name']}" for r in adopted))
    kept = [r for r in rows if r.get("already_mapped")]
    if kept:
        parts.append(f"{len(kept)} already mapped")
    if errors:
        parts.append("failed: " + "; ".join(r["error"] for r in errors))
    if made and not effective.chairman_slack_user:
        parts.append("nobody was invited — set your Slack member ID so private rooms "
                     "are visible to you")
    elif made and not all(r["invited"] for r in made):
        parts.append("some invites did not land; check the bot has `groups:write`")
    return ". ".join(parts) + "."


def _write_channel_map(project: Project, channel_map: dict[str, str]) -> None:
    """Merge the ids into the project's own slack block.

    Written even when the project was inheriting everything else: the channel
    map is the one field that is always the project's, because it is the one
    field that is genuinely different per project.
    """
    integrations = dict(project.integrations or {})
    existing = integrations.get("slack")
    block = dict(existing) if isinstance(existing, dict) else {}
    block["channel_map"] = channel_map
    integrations["slack"] = block
    project.integrations = integrations


def provision_quietly(session: Session, project: Project) -> dict | None:
    """Provisioning for the project-creation path: never raises, ever.

    A chairman who has configured Slack wants his rooms; a chairman whose Slack
    is having a bad afternoon wants his project anyway. Returns None when there
    was nothing to do — no Slack connection configured, or auto-create turned off.
    """
    try:
        if not slack_settings.is_configured(session):
            return None
        if not slack_settings.auto_create(session):
            return None
        return provision(session, project)
    except Exception:
        log.warning("slack auto-provisioning failed for project %s; the project is "
                    "created and unaffected — use Settings → Slack to retry",
                    project.slug, exc_info=True)
        return {"ok": False, "channels": [],
                "detail": "Slack channel provisioning failed; the project is fine. "
                          "Retry from Settings → Slack."}
