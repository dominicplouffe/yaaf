"""One workspace, many projects: where a project's Slack wiring comes from.

The observation this module exists for is that a chairman reuses **one** Slack
workspace across every project and only ever changes the *channels*. So the
tokens live once, at platform scope, and a project inherits them; what stays
per-project is the channel map, because that is the part that genuinely
differs.

Inheritance is **per field**, not all-or-nothing. A project that names its own
``bot_token_secret`` overrides only the bot token and still inherits the
signing secret, the app token and the chairman's Slack user. That matters for
the one real case it exists for: a project that lives in a *different*
workspace than the rest, which needs its own tokens but nothing else special.

Three states, and the difference between the last two is the whole design:

- ``integrations["slack"]`` **absent or null** — inherit everything. This is
  the default a project is born in, and it is deliberately harmless: the
  channel map is still empty, so nothing mirrors until channels exist.
- ``integrations["slack"] = {...}`` — inherit, with the named fields winning.
- ``integrations["slack"] = {"enabled": false}`` — off, and stays off however
  the platform is configured. Without this a chairman could not turn Slack off
  for one project without unwiring the whole install.

Nothing here decrypts a token into anywhere an agent can reach. Platform Slack
secrets keep empty ``role_scopes`` exactly like the per-project ones already
did, so "no agent sandbox ever holds the bot token" survives the move to a
shared workspace unchanged.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from yaaf.core import platform
from yaaf.core.models import Project

#: Per-field defaults, and which platform setting each falls back to. Kept as
#: data so the API summary, the resolver and the UI cannot drift apart.
_TOKEN_FIELDS = {
    "bot_token": ("bot_token_secret", platform.SLACK_BOT_SECRET),
    "signing_secret": ("signing_secret", platform.SLACK_SIGNING_SECRET),
    "app_token": ("app_token_secret", platform.SLACK_APP_SECRET),
}


@dataclass(frozen=True)
class SlackSettings:
    """A project's *effective* Slack wiring, tokens resolved.

    ``sources`` says where each field came from ("project" or "global") so the
    settings screen can show a chairman what he is inheriting rather than
    making him guess from an empty input box.
    """

    bot_token: str | None = None
    signing_secret: str | None = None
    app_token: str | None = None
    chairman_slack_user: str = ""
    channel_map: dict[str, str] = field(default_factory=dict)
    channel_prefix: str = ""
    channel_visibility: str = platform.DEFAULT_SLACK_VISIBILITY
    sources: dict[str, str] = field(default_factory=dict)

    @property
    def can_mirror(self) -> bool:
        """Whether the outbound mirror has everything it needs. A channel map
        without a token mirrors nothing; a token without a map has nowhere to
        put it. Neither is an error — both are simply not wired yet."""
        return bool(self.bot_token and self.channel_map)

    @property
    def can_receive(self) -> bool:
        """Whether the inbound door can open at all: somebody has to be the
        one trusted human, and there has to be a boardroom to speak in."""
        return bool(self.chairman_slack_user and self.channel_map.get("boardroom"))


def project_config(project: Project) -> dict | None:
    """The raw per-project block, or None when the project inherits wholesale."""
    cfg = (project.integrations or {}).get("slack")
    return cfg if isinstance(cfg, dict) else None


def is_disabled(project: Project) -> bool:
    cfg = project_config(project)
    return bool(cfg) and cfg.get("enabled") is False


def mapped_rooms(project: Project) -> dict[str, str]:
    """The project's channel map, read straight off the row it is already on.

    Free, in memory, and the reason it is worth having: the fast mirror ticks
    every two seconds over every project, and :func:`resolve` costs a handful
    of secret and setting lookups. A project with nothing mapped mirrors
    nothing whatever the workspace says, so it never needs resolving at all.
    """
    if is_disabled(project):
        return {}
    cfg = project_config(project) or {}
    return {k: v for k, v in (cfg.get("channel_map") or {}).items() if v}


def _project_secret(session: Session, project_id: str, name: str | None) -> str | None:
    # Imported here rather than at module scope: bridge imports this module for
    # the resolver, and a top-level import back would be a cycle.
    from yaaf.server.slack.bridge import project_secret

    return project_secret(session, project_id, name)


def resolve(session: Session, project: Project) -> SlackSettings | None:
    """This project's effective Slack settings, or None when it is switched off.

    None means "do nothing" everywhere it is checked — the mirror skips the
    project, the socket manager never opens a connection, the webhook refuses.
    A returned value may still be unusable (no token, no channels); ask
    :attr:`SlackSettings.can_mirror` / :attr:`~SlackSettings.can_receive`
    rather than assuming a value means wired.
    """
    if is_disabled(project):
        return None
    cfg = project_config(project) or {}
    sources: dict[str, str] = {}
    tokens: dict[str, str | None] = {}
    for field_name, (cfg_key, platform_name) in _TOKEN_FIELDS.items():
        own = _project_secret(session, project.id, cfg.get(cfg_key))
        if own is not None:
            tokens[field_name] = own
            sources[field_name] = "project"
            continue
        inherited = platform.secret_value(session, platform_name)
        tokens[field_name] = inherited
        if inherited is not None:
            sources[field_name] = "global"

    chairman = (cfg.get("chairman_slack_user") or "").strip()
    if chairman:
        sources["chairman_slack_user"] = "project"
    else:
        chairman = (platform.get_setting(session, platform.SLACK_CHAIRMAN_USER, "") or "").strip()
        if chairman:
            sources["chairman_slack_user"] = "global"

    prefix = (cfg.get("channel_prefix") or "").strip()
    if prefix:
        sources["channel_prefix"] = "project"
    else:
        prefix = (platform.get_setting(session, platform.SLACK_CHANNEL_PREFIX, "") or "").strip()
        sources["channel_prefix"] = "global" if prefix else "slug"
    # An unset prefix is not an empty prefix: channels would collide across
    # projects the moment two of them wanted a #boardroom. The project slug is
    # the one name guaranteed to be unique per project already.
    prefix = prefix or project.slug

    visibility = (cfg.get("channel_visibility")
                  or platform.get_setting(session, platform.SLACK_CHANNEL_VISIBILITY)
                  or platform.DEFAULT_SLACK_VISIBILITY)

    channel_map = {k: v for k, v in (cfg.get("channel_map") or {}).items() if v}
    if channel_map:
        sources["channel_map"] = "project"

    return SlackSettings(
        bot_token=tokens["bot_token"],
        signing_secret=tokens["signing_secret"],
        app_token=tokens["app_token"],
        chairman_slack_user=chairman,
        channel_map=channel_map,
        channel_prefix=prefix,
        channel_visibility="public" if visibility == "public" else "private",
        sources=sources,
    )


# ── the app-wide record, for the settings screen ────────────────────────────────


def platform_summary(session: Session) -> dict:
    """What is configured app-wide, with no token values in it.

    Deliberately says only *whether* each token exists. A settings screen that
    can show you the bot token is a settings screen that can leak it into a
    screenshot; rotating is the only thing a chairman ever actually needs.
    """
    return {
        "bot_token_configured": platform.secret_row(session, platform.SLACK_BOT_SECRET) is not None,
        "signing_secret_configured":
            platform.secret_row(session, platform.SLACK_SIGNING_SECRET) is not None,
        "app_token_configured": platform.secret_row(session, platform.SLACK_APP_SECRET) is not None,
        "chairman_slack_user":
            platform.get_setting(session, platform.SLACK_CHAIRMAN_USER, "") or "",
        "channel_prefix": platform.get_setting(session, platform.SLACK_CHANNEL_PREFIX, "") or "",
        "channel_visibility": platform.get_setting(
            session, platform.SLACK_CHANNEL_VISIBILITY, platform.DEFAULT_SLACK_VISIBILITY),
        "auto_create_channels": auto_create(session),
    }


def auto_create(session: Session) -> bool:
    """Whether creating a project should provision its rooms on the spot.

    Default **on**, and only when a bot token exists: the whole ask is "I made
    a project, tell me its channels". An install with no app-wide Slack never
    reaches this, so the default costs nothing there.
    """
    stored = platform.get_setting(session, platform.SLACK_AUTO_CREATE)
    if stored is None:
        return True
    return stored == "true"


def is_configured(session: Session) -> bool:
    return platform.secret_row(session, platform.SLACK_BOT_SECRET) is not None


# ── channel naming ──────────────────────────────────────────────────────────────

#: Slack's rule, as of the conversations.create docs: lowercase, no spaces or
#: periods, up to 80 characters, letters/digits/hyphen/underscore only. Names
#: that break it are refused with `invalid_name_specials`, which is a confusing
#: way to be told your team is called "R&D".
_ILLEGAL = re.compile(r"[^a-z0-9_-]+")
MAX_CHANNEL_NAME = 80


def slugify(text: str) -> str:
    return _ILLEGAL.sub("-", text.strip().lower()).strip("-_") or "room"


def room_label(key: str, team_names: dict[str, str] | None = None) -> str:
    """The human name of a room key, for messages a chairman reads."""
    if key.startswith("team:"):
        name = (team_names or {}).get(key.split(":", 1)[1])
        return f"team {name}" if name else "team room"
    return key


def channel_name(prefix: str, key: str, team_names: dict[str, str] | None = None) -> str:
    """The Slack channel name for one room of one project.

    ``{prefix}-{room}`` — ``acme-boardroom``, ``acme-alpha``. Truncated from the
    *room* end rather than the prefix end when it would run past Slack's 80
    characters, because the prefix is what tells two projects apart.
    """
    if key.startswith("team:"):
        team_id = key.split(":", 1)[1]
        room = slugify((team_names or {}).get(team_id) or team_id)
    else:
        room = slugify(key)
    prefix = slugify(prefix)[:MAX_CHANNEL_NAME - 2]
    room = room[:max(1, MAX_CHANNEL_NAME - len(prefix) - 1)]
    return f"{prefix}-{room}"
