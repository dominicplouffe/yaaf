"""Invariant selftests: the spend envelope (CEO Charter, stage C).

Real-world money gets three numbers, not a switch: the approval threshold
(unchanged), the per-purchase line, and the monthly external wall — the wall
enforced in EVERY billing mode, closing the audited gap where token-billed
projects metered real dollars and never capped them.

The lines these tests hold:

  * a purchase is cleared against the envelope BEFORE money moves, and the
    idempotency key makes a crash-retry unable to buy twice
  * finishing books exactly one ledger row; finishing twice books nothing more
  * the per-purchase line and the threshold both demand a covering approval;
    the monthly wall takes no approval at all
  * spend_book faces the same walls as the actuator — the tool that carried
    the money never changes what the money may do
  * the walls hold under subscription billing, where they never used to exist
"""

from __future__ import annotations

from tests.conftest import ADMIN, bearer, live_token, make_project
from yaaf.core.db import db_session
from yaaf.core.models import LedgerEntry, Notice, NoticeKind, SideEffectStatus
from yaaf.server import policy

ENVELOPE = {"external_monthly_usd_cap": 200.0, "max_usd_per_purchase": 50.0,
            "approval_threshold_usd": 250.0}


def project_with(client, constitution: dict | None = None, slug: str = "wallet",
                 **kwargs) -> dict:
    return make_project(client, slug=slug,
                        constitution=ENVELOPE if constitution is None else constitution,
                        **kwargs)


def token(project: dict) -> str:
    _, tok = live_token(project["id"], project["ceo_agent_id"])
    return tok


def begin(client, project: dict, usd: float, key: str = "buy-1", **extra):
    return client.post("/agent/purchases", headers=bearer(token(project)),
                       json={"vendor": "example.com", "usd": usd,
                             "description": "a domain for the launch",
                             "idempotency_key": key, **extra})


def finish(client, project: dict, purchase_id: str, ok: bool = True):
    return client.post(f"/agent/purchases/{purchase_id}/finish",
                       headers=bearer(token(project)),
                       json={"ok": ok, "receipt": "example.com receipt #4711: "
                                                  "example.com 1yr, $12.00 paid"})


def booked_usd(project_id: str) -> float:
    with db_session() as session:
        rows = session.query(LedgerEntry).filter(
            LedgerEntry.project_id == project_id,
            LedgerEntry.kind == "external_spend").all()
        return sum(r.usd or 0 for r in rows)


def approve_spend(client, project: dict, usd: float) -> str:
    """A chairman-approved spend approval covering usd, the machinery's way."""
    response = client.post("/agent/approvals", headers=bearer(token(project)),
                           json={"kind": "spend", "title": "a bigger purchase",
                                 "body": "Recommended: it unblocks the launch.",
                                 "amount_usd": usd})
    assert response.status_code == 200, response.text
    approval_id = response.json()["id"]
    decided = client.post(f"/admin/approvals/{approval_id}/decide",
                          json={"approve": True, "note": "go"}, headers=ADMIN)
    assert decided.status_code == 200, decided.text
    return approval_id


# ── the actuator path ───────────────────────────────────────────────────────────


def test_a_purchase_clears_pays_and_books_exactly_once(client):
    project = project_with(client)
    cleared = begin(client, project, 12.0)
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["duplicate"] is False
    assert cleared.json()["envelope_remaining_after"] == 188.0
    # Nothing is on the books until the receipt lands.
    assert booked_usd(project["id"]) == 0

    done = finish(client, project, cleared.json()["purchase_id"])
    assert done.status_code == 200, done.text
    assert done.json()["booked_usd"] == 12.0
    assert booked_usd(project["id"]) == 12.0

    # Finishing again books nothing more.
    again = finish(client, project, cleared.json()["purchase_id"])
    assert again.json()["duplicate"] is True
    assert booked_usd(project["id"]) == 12.0


def test_the_idempotency_key_stops_a_double_buy(client):
    project = project_with(client)
    first = begin(client, project, 12.0, key="domain-example-com")
    finish(client, project, first.json()["purchase_id"])
    retry = begin(client, project, 12.0, key="domain-example-com")
    assert retry.json()["duplicate"] is True
    assert retry.json()["status"] == SideEffectStatus.COMPLETED
    assert "DO NOT pay" in retry.json()["note"]


def test_a_failed_purchase_books_nothing(client):
    project = project_with(client)
    cleared = begin(client, project, 12.0)
    failed = finish(client, project, cleared.json()["purchase_id"], ok=False)
    assert failed.json()["status"] == SideEffectStatus.FAILED
    assert booked_usd(project["id"]) == 0


# ── the three lines ─────────────────────────────────────────────────────────────


def test_the_per_purchase_line_demands_an_approval_below_the_threshold(client):
    project = project_with(client)
    refused = begin(client, project, 80.0)  # over 50, under 250
    assert refused.status_code == 403
    assert "per-purchase line" in refused.text

    approval_id = approve_spend(client, project, 80.0)
    cleared = begin(client, project, 80.0, approval_id=approval_id)
    assert cleared.status_code == 200, cleared.text


def test_the_monthly_wall_takes_no_approval(client):
    project = project_with(client)
    first = begin(client, project, 40.0, key="buy-a")
    finish(client, project, first.json()["purchase_id"])
    second = begin(client, project, 40.0, key="buy-b")
    finish(client, project, second.json()["purchase_id"])
    third = begin(client, project, 40.0, key="buy-c")
    finish(client, project, third.json()["purchase_id"])
    fourth = begin(client, project, 40.0, key="buy-d")
    finish(client, project, fourth.json()["purchase_id"])
    # 160 booked; 41+ would cross 200.
    approval_id = approve_spend(client, project, 45.0)
    walled = begin(client, project, 45.0, key="buy-e", approval_id=approval_id)
    assert walled.status_code == 403
    assert "amendment" in walled.text
    # A smaller one still fits.
    fits = begin(client, project, 30.0, key="buy-f")
    assert fits.status_code == 200, fits.text


def test_spend_book_faces_the_same_walls(client):
    project = project_with(client)
    over_line = client.post("/agent/ledger/spend", headers=bearer(token(project)),
                            json={"usd": 80.0, "note": "paid by card at checkout"})
    assert over_line.status_code == 403
    assert "per-purchase line" in over_line.text

    fine = client.post("/agent/ledger/spend", headers=bearer(token(project)),
                       json={"usd": 30.0, "note": "paid by card at checkout"})
    assert fine.status_code == 200, fine.text
    assert booked_usd(project["id"]) == 30.0


def test_the_wall_holds_under_subscription_billing(client):
    """The audited gap: token-billed projects metered real dollars and never
    capped them. Now the envelope is billing-mode-blind."""
    project = project_with(client, slug="tokens",
                           billing_mode="subscription",
                           constitution={"external_monthly_usd_cap": 100.0,
                                         "max_usd_per_purchase": 0})
    first = begin(client, project, 90.0, key="buy-a")
    assert first.status_code == 200, first.text
    finish(client, project, first.json()["purchase_id"])
    walled = begin(client, project, 20.0, key="buy-b")
    assert walled.status_code == 403


def test_zero_turns_a_line_off(client):
    project = project_with(client, slug="open",
                           constitution={"external_monthly_usd_cap": 0,
                                         "max_usd_per_purchase": 0,
                                         "approval_threshold_usd": 250.0})
    cleared = begin(client, project, 200.0)
    assert cleared.status_code == 200, cleared.text
    # The approval threshold still stands on its own.
    over = begin(client, project, 260.0, key="buy-big")
    assert over.status_code == 403
    assert "approval threshold" in over.text


# ── the janitor's warning ───────────────────────────────────────────────────────


def test_the_envelope_warns_once_at_the_ratio_and_pauses_nothing(client):
    from yaaf.core.constitution import effective
    from yaaf.core.models import Project, ProjectStatus
    from yaaf.server import janitor

    project = project_with(client)
    first = begin(client, project, 45.0, key="buy-a",
                  approval_id=approve_spend(client, project, 45.0))
    finish(client, project, first.json()["purchase_id"])
    second = begin(client, project, 45.0, key="buy-b",
                   approval_id=approve_spend(client, project, 45.0))
    finish(client, project, second.json()["purchase_id"])
    third = begin(client, project, 45.0, key="buy-c",
                  approval_id=approve_spend(client, project, 45.0))
    finish(client, project, third.json()["purchase_id"])
    third = begin(client, project, 45.0, key="buy-d",
                  approval_id=approve_spend(client, project, 45.0))
    finish(client, project, third.json()["purchase_id"])
    # 180 of 200 booked — over the 0.8 ratio.
    with db_session() as session:
        row = session.get(Project, project["id"])
        janitor.enforce_budget(session, row, effective(session, row.id))
        janitor.enforce_budget(session, row, effective(session, row.id))
    with db_session() as session:
        warnings = session.query(Notice).filter(
            Notice.project_id == project["id"],
            Notice.kind == NoticeKind.EXTERNAL_SPEND_WARNING).count()
        assert warnings == 1
        assert session.get(Project, project["id"]).status == ProjectStatus.RUNNING


def test_external_spent_this_month_counts_only_external(client):
    project = project_with(client)
    with db_session() as session:
        from yaaf.core import ids

        session.add(LedgerEntry(id=ids.new_id(ids.LEDGER), project_id=project["id"],
                                kind="model_usage", usd=999.0))
    with db_session() as session:
        assert policy.external_spent_this_month(session, project["id"]) == 0.0


# ── reservations ────────────────────────────────────────────────────────────────


def test_a_cleared_purchase_reserves_the_envelope(client):
    """Two overlapping begins must not each clear against the same headroom:
    cleared-but-unfinished money is committed until finish closes it, and a
    failed finish gives the headroom back."""
    project = project_with(client, slug="reserve")
    first = begin(client, project, 150.0, key="buy-a",
                  approval_id=approve_spend(client, project, 150.0))
    assert first.status_code == 200, first.text
    assert booked_usd(project["id"]) == 0, "nothing booked yet — reserved, not spent"

    walled = begin(client, project, 100.0, key="buy-b",
                   approval_id=approve_spend(client, project, 100.0))
    assert walled.status_code == 403
    assert "reserves the envelope" in walled.text

    failed = finish(client, project, first.json()["purchase_id"], ok=False)
    assert failed.status_code == 200, failed.text
    fits = begin(client, project, 100.0, key="buy-c",
                 approval_id=approve_spend(client, project, 100.0))
    assert fits.status_code == 200, fits.text


# ── money that already moved ────────────────────────────────────────────────────


def test_spend_book_records_an_overshoot_instead_of_hiding_it(client):
    """The wall refuses a purchase BEFORE money moves. spend_book is the
    other case: the money is gone, so refusing the record would un-report
    real spending. It books, audits, and warns instead."""
    from yaaf.core.models import AuditLog

    project = project_with(client, slug="overshoot",
                           constitution={"external_monthly_usd_cap": 100.0,
                                         "max_usd_per_purchase": 0,
                                         "approval_threshold_usd": 250.0})
    fine = client.post("/agent/ledger/spend", headers=bearer(token(project)),
                       json={"usd": 80.0, "note": "paid by card at checkout"})
    assert fine.status_code == 200, fine.text
    assert "warning" not in fine.json()

    over = client.post("/agent/ledger/spend", headers=bearer(token(project)),
                       json={"usd": 60.0, "note": "the second card charge"})
    assert over.status_code == 200, over.text
    assert "over the" in over.json()["warning"]
    assert booked_usd(project["id"]) == 140.0, "booked in full, not clipped"
    with db_session() as session:
        audit = session.query(AuditLog).filter(
            AuditLog.project_id == project["id"],
            AuditLog.action == "spend.over_envelope").one()
        assert audit.detail["committed_after"] == 140.0

    # The wall still stands where it can help: the NEXT purchase is refused.
    walled = begin(client, project, 10.0, key="buy-after")
    assert walled.status_code == 403


# ── revenue is not spend ────────────────────────────────────────────────────────


def test_revenue_never_counts_as_spend(client):
    """Earning money must not burn the model budget (auto-pause) or the
    external envelope — summing revenue as spend once paused a project for
    succeeding."""
    from yaaf.core.constitution import effective
    from yaaf.core.models import Project, ProjectStatus
    from yaaf.server import janitor

    project = project_with(client, slug="earns",
                           constitution={**ENVELOPE, "monthly_usd_cap": 50.0})
    with db_session() as session:
        from yaaf.core import ids

        session.add(LedgerEntry(id=ids.new_id(ids.LEDGER), project_id=project["id"],
                                kind="revenue", usd=10_000.0))
    with db_session() as session:
        assert policy.external_spent_this_month(session, project["id"]) == 0.0
        usd, _ = janitor.month_to_date(session, project["id"])
        assert usd == 0.0
        row = session.get(Project, project["id"])
        janitor.enforce_budget(session, row, effective(session, row.id))
    with db_session() as session:
        assert session.get(Project, project["id"]).status == ProjectStatus.RUNNING
    # And the envelope still has its full headroom.
    cleared = begin(client, project, 12.0, key="buy-after-earning")
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["envelope_remaining_after"] == 188.0
