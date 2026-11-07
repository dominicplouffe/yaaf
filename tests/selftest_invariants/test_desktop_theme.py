"""Invariant selftests: the dashboard wears a desktop's theme without giving up
what its stylesheet promises, and without letting anything but colour in.

Two things a desktop theme could cost. Legibility: the stylesheet's text and
status colours were each checked to clear 4.5:1 on their ground, and a theme
was chosen for a terminal. And the page itself: a palette becomes CSS custom
properties, so a value that is not a colour is text a stylesheet interprets.
These attack both — across every theme Omarchy ships, and over HTTP with the
values a hostile palette would send.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

from tests.conftest import ADMIN, headers_of, person
from yaaf.cli import omarchy, theme
from yaaf.core import desktop_theme as dt

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "omarchy" / "themes"
KIT = Path(__file__).resolve().parents[2] / "web" / "src" / "ui" / "kit.tsx"
THEMES = sorted(FIXTURES.glob("*.toml"))
TEXT_TOKENS = ("--ink", "--ink-2", "--muted", "--accent", "--good", "--warning",
               "--serious", "--critical")


def _palette(path: Path) -> dict:
    return theme.palette_from(path, path.stem)


TOKYO = _palette(FIXTURES / "tokyo-night.toml")


# ── legibility, theme by theme ──────────────────────────────────────────────────


def test_every_omarchy_theme_is_on_the_test():
    assert len(THEMES) >= 20, "the fixtures hold every theme Omarchy ships"


@pytest.mark.parametrize("path", THEMES, ids=[p.stem for p in THEMES])
def test_no_omarchy_theme_costs_the_dashboard_its_contrast(path):
    """Every text and status colour the page will show clears 4.5:1 on the
    page and on a surface; text on the accent and on a critical badge too."""
    palette = _palette(path)
    assert dt.valid(palette) is None, dt.valid(palette)
    tokens, kept = dt.tokens(palette)
    assert tokens, f"{path.stem}: no legible ink"
    for token in TEXT_TOKENS:
        if token in tokens:
            for ground in ("--page", "--surface"):
                ratio = dt.contrast(tokens[token], tokens[ground])
                assert ratio >= dt.TEXT, f"{path.stem}: {token} on {ground} is {ratio:.2f}:1"
    for on, under in (("--on-accent", "--accent"), ("--on-critical", "--critical")):
        if on in tokens:
            assert dt.contrast(tokens[on], tokens[under]) >= dt.TEXT, f"{path.stem}: {on}"
    # A token kept at the stylesheet's value was checked on the stylesheet's
    # page, not this theme's. Every shipped theme gets all of its own, so none
    # leans on that; a new theme that does is worth a look before it ships.
    assert not kept, f"{path.stem} keeps {kept}"


@pytest.mark.parametrize("path", THEMES, ids=[p.stem for p in THEMES])
def test_a_theme_sets_only_the_tokens_it_may(path):
    """The chart slots were tested for colour-vision separation; a theme's
    palette was not, so it never reaches them."""
    tokens, _ = dt.tokens(_palette(path))
    assert set(tokens) <= set(dt.TOKENS)
    assert not any(t.startswith("--c") and t[3:].isdigit() for t in tokens)
    for value in tokens.values():
        assert re.fullmatch(r"#[0-9a-f]{6}|\d{1,3}, \d{1,3}, \d{1,3}", value), value


def test_a_palette_whose_text_cannot_be_read_is_not_worn():
    unreadable = {"mode": "dark", "colors": {"background": "#202020", "foreground": "#262626"}}
    assert dt.view(unreadable) is None, "half a theme with illegible text is worse than none"


def test_the_page_and_the_server_agree_on_what_may_be_set():
    """Two lists of the same names, in two languages: the server's (what it
    works out) and the page's (what it will write). A token added to one and
    not the other is either silently dropped or never checked."""
    block = re.search(r"const DESKTOP_TOKENS = new Set\(\[(.*?)\]\);", KIT.read_text(), re.S)
    assert block, "kit.tsx no longer lists the desktop tokens"
    assert set(re.findall(r'"(--[a-z0-9-]+)"', block.group(1))) == set(dt.TOKENS)


# ── over HTTP: only colour gets in ──────────────────────────────────────────────


def test_a_desktop_theme_is_stored_and_worn(client):
    put = client.put("/me/desktop-theme", headers=ADMIN, json=TOKYO)
    assert put.status_code == 200, put.text
    got = client.get("/me/desktop-theme", headers=ADMIN).json()
    assert got["theme"]["mode"] == "dark" and got["theme"]["name"] == "tokyo-night"
    assert got["theme"]["tokens"]["--page"] == "#1a1b26"


@pytest.mark.parametrize("hostile", [
    {"colors": {"background": "#1a1b26; background-image: url(https://x.example/)"}},
    {"colors": {"background": "red"}},
    {"colors": {"background": "#fff"}},
    {"colors": {"background": "expression(alert(1))"}},
    {"colors": {"background": "#1a1b26", "--page": "#000000"}},
    {"colors": {"background": "#1a1b26", "background-image": "#000000"}},
    {"name": "<script>alert(1)</script>"},
    {"mode": "auto"},
    {"css": ":root { --page: red }"},
    {"colors": {"foreground": "#ffffff"}},
], ids=["css-after-colour", "named-colour", "short-hex", "expression", "token-as-key",
        "property-as-key", "markup-name", "unknown-mode", "extra-field", "no-background"])
def test_anything_that_is_not_a_colour_is_refused_whole(client, hostile):
    body = {**TOKYO, **hostile}
    if "colors" in hostile and "foreground" not in hostile["colors"]:
        body["colors"] = {**hostile["colors"], "foreground": "#c0caf5"}
    refused = client.put("/me/desktop-theme", headers=ADMIN, json=body)
    assert refused.status_code == 422, refused.text
    assert client.get("/me/desktop-theme", headers=ADMIN).json()["palette"] is None, \
        "a refused palette must leave nothing behind"


def test_a_theme_is_the_persons_own(client):
    """Stored per person, like pins: one person's desktop never dresses
    another's dashboard, and nobody can set somebody else's."""
    client.put("/me/desktop-theme", headers=ADMIN, json=TOKYO)
    other = headers_of(person("observer@example.com"))
    assert client.get("/me/desktop-theme", headers=other).json()["theme"] is None
    latte = _palette(FIXTURES / "catppuccin-latte.toml")
    client.put("/me/desktop-theme", headers=other, json=latte)
    assert client.get("/me/desktop-theme", headers=ADMIN).json()["theme"]["name"] == "tokyo-night"


def test_nobody_signed_out_reads_or_writes_one(client):
    assert client.get("/me/desktop-theme").status_code == 401
    assert client.put("/me/desktop-theme", json=TOKYO).status_code == 401


def test_clearing_it_gives_the_dashboard_its_own_theme_back(client):
    client.put("/me/desktop-theme", headers=ADMIN, json=TOKYO)
    client.delete("/me/desktop-theme", headers=ADMIN)
    assert client.get("/me/desktop-theme", headers=ADMIN).json() == \
        {"palette": None, "theme": None}


# ── reading the desktop ─────────────────────────────────────────────────────────


def test_omarchys_current_theme_is_read_where_omarchy_4_writes_it(tmp_path, monkeypatch):
    current = tmp_path / "state" / "omarchy" / "current"
    (current / "theme").mkdir(parents=True)
    (current / "theme" / "colors.toml").write_text((FIXTURES / "gruvbox.toml").read_text()
                                                   + '\nnot_a_colour = "hello"\n')
    (current / "theme.name").write_text("gruvbox\n")
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    palette = theme.omarchy_palette()
    assert palette["name"] == "gruvbox" and palette["mode"] == "dark"
    assert "not_a_colour" not in palette["colors"]
    assert palette["colors"] == {k: v.lower() for k, v in tomllib.loads(
        (FIXTURES / "gruvbox.toml").read_text()).items() if k in dt.KEYS}


def test_a_palette_with_no_mode_takes_it_from_its_background(tmp_path):
    light = tmp_path / "colors.toml"
    light.write_text('background = "#fafafa"\nforeground = "#202020"\n')
    assert theme.palette_from(light)["mode"] == "light"


def test_on_omarchy_every_theme_switch_resyncs_and_never_fails_the_switch(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    files = omarchy.plugin_files("/opt/yaaf/bin/yaaf")
    hook = tmp_path / "omarchy" / "hooks" / "theme-set.d" / "yaaf"
    assert hook in files
    assert "/opt/yaaf/bin/yaaf theme sync --quiet" in files[hook]
    assert files[hook].rstrip().endswith("|| true"), \
        "a yaaf that is down must not turn a theme switch into an error"
