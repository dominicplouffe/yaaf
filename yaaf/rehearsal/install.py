"""What this install already has wired, for a drill that cannot see it.

The drill runs in its own process against a throwaway database, which is what
makes it safe to invoke from a server — and it is also why the report used to
say the same thing about Slack on every install in the world: *a drill reads no
secret and opens no socket*. True, and useless. A chairman who had already
connected a Slack workspace app-wide read that line, and the Founder read it
too, and both concluded the same wrong thing — that Slack was an unattached
dependency he still had to go and wire. It was attached. The drill just had no
way to know, because the live database is exactly the thing it refuses to open.

So the facts are collected by the caller that *does* hold a live session — the
admin route the dashboard and the Founder both reach the drill through — and
travel into the subprocess as data. Never by the drill itself: a drill that
looked up platform settings would be a drill reading the live database, and
that is the property the whole design exists to keep.

Booleans only. Whether a bot token exists is the entire question a chairman is
asking; the token itself, and his Slack user id, have no business in a report
that goes to a language model and onto a screen.

`yaaf rehearse` on a checkout passes nothing, and the wording falls back to
saying nothing either way — which is the honest answer for a drill with no
install behind it.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

#: The app-wide Slack pieces, in the order a chairman attaches them, with the
#: name he sees on the settings screen. `bot_token` is the pivot: without it
#: nothing mirrors and nothing is provisioned, so it is reported as the
#: difference between "wired" and "not wired" rather than as one item of four.
SLACK_PIECES = [
    ("bot_token", "a bot token"),
    ("signing_secret", "a signing secret"),
    ("app_token", "an app token"),
    ("chairman_user", "your Slack user"),
]


def collect(session: Session) -> dict:
    """The wiring facts, read off the live install. Caller-side, always."""
    from yaaf.core.config import settings
    from yaaf.server.slack import settings as slack_settings

    summary = slack_settings.platform_summary(session)
    live = settings()
    return {
        "slack": {
            "bot_token": bool(summary["bot_token_configured"]),
            "signing_secret": bool(summary["signing_secret_configured"]),
            "app_token": bool(summary["app_token_configured"]),
            "chairman_user": bool(summary["chairman_slack_user"]),
            "auto_create_channels": bool(summary["auto_create_channels"]),
        },
        "notifications": {
            "email": bool(live.smtp_host),
            "webhook": bool(live.chairman_webhook_url),
        },
    }


def _join(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return f"{', '.join(items[:-1])} and {items[-1]}"


def slack_line(install: dict | None, *, disabled_by_spec: bool = False) -> str:
    """What the report says about Slack, given what the install actually has.

    Three different sentences for three different situations, because they ask
    the chairman for three different things: nothing, an attachment, or a
    single check that a message arrives.
    """
    if disabled_by_spec:
        return ("Slack — not exercised, and this spec switches it off for the "
                "project (integrations.slack.enabled is false), so it would "
                "mirror nothing however this install is connected")
    slack = (install or {}).get("slack")
    if not isinstance(slack, dict):
        return ("Slack — the drill opens no socket, so it says nothing either "
                "way about the workspace this install talks to")

    have = [label for key, label in SLACK_PIECES if slack.get(key)]
    missing = [label for key, label in SLACK_PIECES if not slack.get(key)]
    if not slack.get("bot_token"):
        short = f" (it has {_join(have)}, but no bot token)" if have else ""
        return ("Slack — the drill opens no socket, and this install has no "
                f"app-wide Slack connection for a project to inherit{short}. "
                "A project created here mirrors nowhere until one is attached")

    line = ("Slack — not exercised (the drill opens no socket), but already "
            f"wired: this install has {_join(have)} at platform scope, and a "
            "project created here inherits that")
    if slack.get("auto_create_channels"):
        line += " and has its boardroom provisioned as it is created"
    if missing:
        # "Nothing to attach" would be a lie here, and the point of the whole
        # line is not to make one in the other direction either: what is
        # attached is attached, and the rest is named so he knows which box is
        # still empty.
        return (f"{line}. Still unset app-wide: {_join(missing)} — the rest is "
                "attached already, and the drill cannot prove any of it answers")
    return (line + ". Nothing to attach — the drill simply cannot prove the "
            "workspace answers")


def notification_line(install: dict | None) -> str:
    """What the report says about the chairman's own notice channels.

    The drill unsets them for its own safety, and that fact used to be the
    whole line — which reads as "your notices go nowhere" to anyone whose
    install has mail configured, and as nothing at all to the one person for
    whom it is genuinely a finding.
    """
    base = ("chairman notifications — the mail and webhook channels are unset "
            "for the duration, so the notices this pretend project raised were "
            "logged and never delivered to anyone")
    notifications = (install or {}).get("notifications")
    if not isinstance(notifications, dict):
        return base

    have = [label for key, label in (("email", "email"),
                                     ("webhook", "a chairman webhook"))
            if notifications.get(key)]
    if have:
        return (f"{base}. On this install {_join(have)} is configured, so a real "
                "project's notices do have somewhere to go — the drill muted "
                "the transport, not your setup")
    return (f"{base}, and this install has neither an SMTP host nor a chairman "
            "webhook set either, so a real project's notices would go nowhere "
            "as well — worth fixing before you rely on a daily line arriving")


__all__ = ["SLACK_PIECES", "collect", "notification_line", "slack_line"]
