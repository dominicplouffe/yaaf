"""Daily obligations are judged at each deadline; cut ones stop being counted.

A standing requirement owed every day was judged only when all its work
closed, which a daily pipeline never does — so its test ran once and seven
weekly cycles closed "0 of 4 met, 1 carried forward" while the report landed
every day. Three of the four were cut versions of it that the roll kept
carrying into each new cycle and counting.

Schema: `standing_checks`, one verdict per deadline.

Data, once:
  * a cut standing requirement goes back to the cycle it was cut in, so it is
    no longer in the open cycle's scope;
  * every scorecard written before this (and the review records that froze a
    copy) is re-said in the new shape: cut obligations out of the count, and a
    standing obligation that was open at close recorded as missed when the
    platform ran its test that cycle and as never checked when it did not.
    Days cannot be recovered — nothing checked them — so none are invented.

Revision ID: 0063
Revises: 0062
"""
import sqlalchemy as sa
from alembic import op

from yaaf.core.migration_ops import create_index_if_missing, create_table_if_missing
from yaaf.core.models import JSONVariant, UTCDateTime

revision = "0063"
down_revision = "0062"
branch_labels = None
depends_on = None

NAME_LEN = 120


def upgrade() -> None:
    create_table_if_missing(
        "standing_checks",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("project_id", sa.String(40), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("requirement_id", sa.String(40), sa.ForeignKey("requirements.id"),
                  nullable=False),
        sa.Column("cycle_id", sa.String(40), nullable=False),
        sa.Column("due_at", UTCDateTime, nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("run_id", sa.String(40)),
        sa.Column("attempts", sa.Integer, nullable=False),
        sa.Column("exit_code", sa.Integer),
        sa.Column("check_output", sa.Text),
        sa.Column("judged_at", UTCDateTime),
        sa.Column("created_at", UTCDateTime, nullable=False),
        sa.Column("updated_at", UTCDateTime, nullable=False),
        sa.UniqueConstraint("requirement_id", "due_at"),
    )
    create_index_if_missing("ix_standing_checks_project_cycle", "standing_checks",
                            ["project_id", "cycle_id"])
    _repair()


def downgrade() -> None:
    op.drop_index("ix_standing_checks_project_cycle", table_name="standing_checks")
    op.drop_table("standing_checks")


# ── the one-time repair ─────────────────────────────────────────────────────────

requirements = sa.table(
    "requirements",
    sa.column("id", sa.String), sa.column("project_id", sa.String),
    sa.column("key", sa.String), sa.column("statement", sa.Text),
    sa.column("status", sa.String), sa.column("recurrence", sa.String),
    sa.column("cycle_id", sa.String), sa.column("cut_at", UTCDateTime),
    sa.column("meta", JSONVariant),
)
cycles = sa.table(
    "cycles",
    sa.column("id", sa.String), sa.column("project_id", sa.String),
    sa.column("ordinal", sa.Integer), sa.column("opened_at", UTCDateTime),
    sa.column("closed_at", UTCDateTime), sa.column("scorecard", JSONVariant),
)
events = sa.table(
    "requirement_events",
    sa.column("requirement_id", sa.String), sa.column("from_status", sa.String),
    sa.column("at", UTCDateTime),
)
reviews = sa.table(
    "reviews",
    sa.column("id", sa.String), sa.column("project_id", sa.String),
    sa.column("record", JSONVariant),
)


def _repair() -> None:
    bind = op.get_bind()
    all_cycles = bind.execute(sa.select(cycles)).mappings().all()
    by_project: dict[str, list] = {}
    for c in all_cycles:
        by_project.setdefault(c["project_id"], []).append(c)

    standing = bind.execute(sa.select(requirements).where(
        requirements.c.recurrence == "standing")).mappings().all()
    cut_rows = bind.execute(sa.select(requirements).where(
        requirements.c.status == "cut")).mappings().all()

    # 1. Cut obligations back to the cycle they were cut in.
    for r in standing:
        if r["status"] != "cut" or r["cut_at"] is None:
            continue
        home = next((c for c in by_project.get(r["project_id"], [])
                     if c["opened_at"] <= r["cut_at"]
                     and (c["closed_at"] is None or r["cut_at"] < c["closed_at"])), None)
        if home is not None and home["id"] != r["cycle_id"]:
            bind.execute(requirements.update().where(requirements.c.id == r["id"])
                         .values(cycle_id=home["id"]))

    # 2. Old scorecards, re-said.
    by_key = {(r["project_id"], r["key"]): r for r in standing}
    corrected: dict[tuple[str, int], dict] = {}
    for c in all_cycles:
        card = c["scorecard"]
        if not card or "standing" in card or c["closed_at"] is None:
            continue
        # Cut before the cycle opened: carried in by the bug, never its scope.
        zombies = [r for r in standing if r["project_id"] == c["project_id"]
                   and r["status"] == "cut" and r["cut_at"] is not None
                   and r["cut_at"] < c["opened_at"]]
        # Cut during it: its scope, decided, and named as cut — not counted.
        cut_in = sorted(r["key"] for r in cut_rows if r["project_id"] == c["project_id"]
                        and r["cut_at"] is not None
                        and c["opened_at"] <= r["cut_at"] < c["closed_at"])
        rows, carried, missed = [], [], []
        for key in card.get("met_keys") or []:
            r = by_key.get((c["project_id"], key))
            if r is not None:
                rows.append(_row(r, "honoured"))
        for key in card.get("carried_keys") or []:
            r = by_key.get((c["project_id"], key))
            if r is None:
                carried.append(key)
                continue
            ruled = bind.execute(sa.select(events.c.requirement_id).where(
                events.c.requirement_id == r["id"],
                events.c.from_status == "checking",
                events.c.at > c["opened_at"], events.c.at <= c["closed_at"]).limit(1)).first()
            rows.append(_row(r, "missed" if ruled else "unchecked"))
            missed.append(key)
        new = dict(card)
        new.update(
            accepted=max(0, (card.get("accepted") or 0) - len(zombies) - len(cut_in)),
            carried=len(carried), carried_keys=carried,
            missed=len(missed), missed_keys=missed,
            cut=len(cut_in), cut_keys=cut_in, standing=rows,
        )
        bind.execute(cycles.update().where(cycles.c.id == c["id"]).values(scorecard=new))
        corrected[(c["project_id"], c["ordinal"])] = new

    # 3. Reviews froze a copy of the card at open; give them the corrected one.
    if not corrected:
        return
    for review in bind.execute(sa.select(reviews)).mappings().all():
        record = review["record"]
        cycle = (record or {}).get("cycle")
        if not cycle:
            continue
        new = corrected.get((review["project_id"], cycle.get("ordinal")))
        if new is None:
            continue
        record = dict(record)
        record["cycle"] = {**cycle, **new}
        bind.execute(reviews.update().where(reviews.c.id == review["id"])
                     .values(record=record))


def _row(r, verdict: str) -> dict:
    title = ((r["meta"] or {}).get("title") or "").strip() or None
    return {"key": r["key"], "name": (title or r["statement"])[:NAME_LEN],
            "title": title, "verdict": verdict}
