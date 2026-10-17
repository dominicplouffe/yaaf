"""The boardroom's unread count: what each member has not yet seen.

One mark per member (`project_members.boardroom_read_at`), the same shape as the
briefing's "since you were last here". Unread is every boardroom message after
the mark that somebody else wrote — the CEO, a co-chair, a platform line. What
you wrote yourself you have, by definition, read.

The mark only moves forward, and only on purpose: the chat page says how far
it showed, and speaking in the boardroom (from the page or from Slack) moves it
to what was said. Fetching never moves it — the sidebar polls, and a mark that
moved on every poll would always read zero.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from yaaf.core.models import Channel, ChannelKind, Message, Project, ProjectMember, utcnow


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def boardroom_unread(session: Session, project: Project) -> int | None:
    """The reader's unread count, or None for a reader with no member row —
    an instance admin looking at a project they are not on has no mark."""
    from yaaf.server.briefing import reading_member

    member = reading_member(session, project)
    if member is None:
        return None
    query = (
        select(func.count(Message.id))
        .join(Channel, Channel.id == Message.channel_id)
        .where(
            Message.project_id == project.id,
            Channel.kind == ChannelKind.BOARDROOM,
            or_(Message.sender_user_id.is_(None),
                Message.sender_user_id != member.user_id),
        )
    )
    if member.boardroom_read_at is not None:
        query = query.where(Message.created_at > member.boardroom_read_at)
    return int(session.execute(query).scalar_one())


def mark_boardroom_read(session: Session, project: Project,
                        at: datetime | None = None,
                        member: ProjectMember | None = None) -> bool:
    """Move the reader's mark up to `at` (default now). Never backwards: a stale
    tab reporting an older position must not resurrect what was read since.
    Returns False when there is no member row to stamp."""
    from yaaf.server.briefing import reading_member

    member = member or reading_member(session, project)
    if member is None:
        return False
    at = _aware(at) or utcnow()
    current = _aware(member.boardroom_read_at)
    if current is None or at > current:
        member.boardroom_read_at = at
    return True
