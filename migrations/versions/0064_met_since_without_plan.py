"""A meeting's "Met since last" counts requirements, and never R-00.

The count was every MET event in the window, R-00 included. On an Operate
project R-00 — the plan — reopens and re-closes itself at every weekly roll,
so a meeting in which nothing was met showed "Met since last: 1" directly over
"0 met". Every other count on the page already leaves R-00 out.

Data only: re-count the figure frozen into each review row over the window it
was frozen for — the previous meeting's opening (or the project's birth) to
this meeting's opening.

Revision ID: 0064
Revises: 0063
"""
import sqlalchemy as sa
from alembic import op

from yaaf.core.models import JSONVariant, UTCDateTime

revision = "0064"
down_revision = "0063"
branch_labels = None
depends_on = None

PLAN_KEY = "R-00"

reviews = sa.table(
    "reviews",
    sa.column("id", sa.String), sa.column("project_id", sa.String),
    sa.column("number", sa.Integer), sa.column("opened_at", UTCDateTime),
    sa.column("numbers", JSONVariant),
)
projects = sa.table("projects", sa.column("id", sa.String),
                    sa.column("created_at", UTCDateTime))
requirements = sa.table("requirements", sa.column("id", sa.String),
                        sa.column("key", sa.String))
events = sa.table(
    "requirement_events",
    sa.column("project_id", sa.String), sa.column("requirement_id", sa.String),
    sa.column("to_status", sa.String), sa.column("at", UTCDateTime),
)


def upgrade() -> None:
    bind = op.get_bind()
    born = dict(bind.execute(sa.select(projects.c.id, projects.c.created_at)).all())
    rows = bind.execute(sa.select(reviews).order_by(
        reviews.c.project_id, reviews.c.number)).mappings().all()
    previous: dict[str, object] = {}
    for review in rows:
        since = previous.get(review["project_id"]) or born.get(review["project_id"])
        previous[review["project_id"]] = review["opened_at"]
        numbers = review["numbers"] or {}
        scope = numbers.get("scope")
        if since is None or not isinstance(scope, dict) \
                or "met_since_last_review" not in scope:
            continue
        met = bind.execute(
            sa.select(sa.func.count(sa.distinct(events.c.requirement_id)))
            .select_from(events.join(requirements, requirements.c.id == events.c.requirement_id))
            .where(events.c.project_id == review["project_id"],
                   events.c.to_status == "met",
                   events.c.at >= since, events.c.at <= review["opened_at"],
                   requirements.c.key != PLAN_KEY)
        ).scalar_one()
        if met == scope["met_since_last_review"]:
            continue
        bind.execute(reviews.update().where(reviews.c.id == review["id"]).values(
            numbers={**numbers, "scope": {**scope, "met_since_last_review": int(met)}}))


def downgrade() -> None:
    """The old figure was wrong; there is nothing to restore."""
