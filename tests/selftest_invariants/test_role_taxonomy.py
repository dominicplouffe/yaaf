"""Does the taxonomy actually survive the round-trip the seed depends on?"""
import collections
import json
from pathlib import Path

from tests.conftest import ADMIN

ROLE_FILES = sorted(Path("roles").glob("*.json"))
CREW_FILES = sorted(Path("crews").glob("*.json"))


def load(path: Path) -> dict:
    return json.loads(path.read_text())


def seed_roles(client) -> None:
    for path in ROLE_FILES:
        client.post("/admin/roles", json=load(path), headers=ADMIN)


def test_every_shipped_role_seeds_with_its_category(client):
    for path in ROLE_FILES:
        r = client.post("/admin/roles", json=load(path), headers=ADMIN)
        assert r.status_code < 400, (path, r.text)

    rows = client.get("/admin/roles", headers=ADMIN).json()
    assert len(rows) == 37
    assert all(r["category"] for r in rows), \
        [r["slug"] for r in rows if not r["category"]]

    counts = collections.Counter(r["category"] for r in rows)
    assert counts == {"research": 10, "content": 8, "challenge": 8,
                      "growth": 5, "leadership": 3, "build": 3}, counts

    # The seed round-trip: GET detail -> POST it back must not drop the fields.
    detail = client.get("/admin/roles/compliance-officer", headers=ADMIN).json()
    assert detail["category"] == "challenge"
    assert detail["domains"] == ["markets"]
    again = client.post("/admin/roles", json={
        k: v for k, v in detail.items() if k != "version"}, headers=ADMIN)
    assert again.status_code < 400, again.text
    back = client.get("/admin/roles/compliance-officer", headers=ADMIN).json()
    assert back["category"] == "challenge" and back["domains"] == ["markets"]


def test_a_role_seeded_before_the_taxonomy_can_be_refiled_without_a_new_version(client):
    client.post("/admin/roles", json={
        "slug": "legacy", "name": "Legacy", "description": "seeded long ago",
        "system_prompt": "x", "model": "claude-sonnet-5"}, headers=ADMIN)
    before = client.get("/admin/roles/legacy", headers=ADMIN).json()
    assert before["category"] is None

    r = client.patch("/admin/roles/legacy", headers=ADMIN, json={
        "name": "Legacy", "category": "growth", "domains": ["general"]})
    assert r.status_code < 400, r.text
    after = client.get("/admin/roles/legacy", headers=ADMIN).json()
    assert after["category"] == "growth"
    # The whole point: no new version, so no live team goes behind its role.
    assert after["version"] == before["version"] == 1


def test_a_bad_category_is_refused_by_name(client):
    r = client.post("/admin/roles", json={
        "slug": "nope", "name": "Nope", "category": "vibes",
        "description": "d", "system_prompt": "x", "model": "claude-sonnet-5"},
        headers=ADMIN)
    assert r.status_code == 422 and "vibes" not in r.text and "leadership" in r.text


def test_every_shipped_crew_seeds_and_resolves_against_the_library(client):
    seed_roles(client)
    for path in CREW_FILES:
        r = client.post("/admin/crews", json=load(path), headers=ADMIN)
        assert r.status_code < 400, (path, r.text)

    crews = client.get("/admin/crews", headers=ADMIN).json()
    assert len(crews) == 17
    for crew in crews:
        assert crew["missing_role_slugs"] == [], crew
        assert crew["teams"], crew["slug"]
        for team in crew["teams"]:
            assert team["role_name"], (crew["slug"], team)

    desk = next(c for c in crews if c["slug"] == "a-markets-desk")
    assert desk["ceo_role_slug"] == "cio"

    # The examples come first, in the order examples/README recommends them —
    # alphabetical put every crew whose name starts with "A" in front.
    sourced = [c["source"] for c in crews if (c["source"] or "").startswith("examples/")]
    assert sourced == sorted(sourced)
    assert crews[0]["source"].startswith("examples/01")


def test_a_crew_naming_an_unknown_role_stays_visible_by_name(client):
    client.post("/admin/crews", json={
        "slug": "ghost", "name": "Ghost", "outcome": "nothing",
        "teams": [{"name": "T", "role_slug": "not-installed", "employees": ["A"]}]},
        headers=ADMIN)
    crew = next(c for c in client.get("/admin/crews", headers=ADMIN).json()
                if c["slug"] == "ghost")
    assert crew["missing_role_slugs"] == ["ceo", "not-installed"]
    assert crew["teams"][0]["role_name"] is None
