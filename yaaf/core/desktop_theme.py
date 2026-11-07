"""A desktop's colour theme, as the dashboard's tokens — without its guarantees.

The dashboard's stylesheet was checked, not chosen: every text and status
colour clears 4.5:1 against its own ground (web/src/styles.css). A desktop
theme — Omarchy's Tokyo Night, Catppuccin Latte, anything a person makes — was
chosen for a terminal and a bar, and nothing says its grey reads on its
background. So a palette is never poured into the page. Each token takes the
first of the theme's own colours that clears the same bar against the page and
the surface; where none does, the theme's own colour is moved toward the ink
until it does — its hue, made legible. Only what cannot be made to clear keeps
the stylesheet's checked value, and the answer says which. The chart slots (--c1..--c5) are never touched: they
were tested for colour-vision separation, which a theme's palette was not.

Input is a palette as Omarchy writes it (`colors.toml`): names to `#rrggbb`.
Output is a map of CSS custom properties to values — hex, or the "r, g, b"
triples the stylesheet's rgba() rules read — and nothing else, so what reaches
the page's CSS is a colour, never text a stylesheet would interpret.
"""

from __future__ import annotations

import re

HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,59}$")

#: The colour names a palette may carry: Omarchy's colors.toml, all of it.
KEYS = frozenset({
    "accent", "selection", "muted",
    "background", "dark_background", "darker_background", "lighter_background",
    "foreground", "dark_foreground", "light_foreground", "bright_foreground",
    "red", "yellow", "orange", "green", "cyan", "blue", "magenta", "brown",
    "bright_red", "bright_yellow", "bright_green", "bright_cyan", "bright_blue",
    "bright_magenta",
})

#: Text on its ground, the stylesheet's own bar.
TEXT = 4.5

#: Every property this may set. The page applies these names and no others.
TOKENS = (
    "--page", "--surface", "--surface-2", "--sunken", "--ink", "--ink-2", "--muted",
    "--grid", "--border", "--border-strong",
    "--accent", "--accent-rgb", "--on-accent", "--on-accent-rgb",
    "--good", "--good-rgb", "--warning", "--warning-rgb", "--serious", "--serious-rgb",
    "--critical", "--critical-rgb", "--on-critical",
)


def valid(palette: dict) -> str | None:
    """Why this palette cannot be stored, or None."""
    mode = palette.get("mode")
    if mode not in ("dark", "light"):
        return "mode is dark or light"
    name = palette.get("name")
    if name is not None and (not isinstance(name, str) or not NAME.match(name)):
        return "a theme name is letters, digits, spaces and . _ - (60 at most)"
    colors = palette.get("colors")
    if not isinstance(colors, dict) or not colors:
        return "colors is a map of names to #rrggbb"
    for key, value in colors.items():
        if key not in KEYS:
            return f"{key!r} is not a colour a desktop theme names"
        if not isinstance(value, str) or not HEX.match(value):
            return f"{key} must be #rrggbb"
    if "background" not in colors:
        return "a palette needs its background"
    return None


def _rgb(hex_: str) -> tuple[int, int, int]:
    return int(hex_[1:3], 16), int(hex_[3:5], 16), int(hex_[5:7], 16)


def _hex(rgb: tuple[float, float, float]) -> str:
    return "#" + "".join(f"{max(0, min(255, round(c))):02x}" for c in rgb)


def _luminance(hex_: str) -> float:
    def channel(c: int) -> float:
        s = c / 255
        return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4
    r, g, b = (channel(c) for c in _rgb(hex_))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    """WCAG 2 contrast ratio, 1 to 21."""
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def mix(a: str, b: str, amount: float) -> str:
    """`a` moved `amount` of the way to `b`."""
    ra, rb = _rgb(a), _rgb(b)
    return _hex(tuple(x + (y - x) * amount for x, y in zip(ra, rb, strict=True)))


def _triple(hex_: str) -> str:
    return ", ".join(str(c) for c in _rgb(hex_))


def _clears(colour: str, grounds: list[str]) -> bool:
    return all(contrast(colour, g) >= TEXT for g in grounds)


def _readable(candidates: list[str | None], grounds: list[str],
              toward: str | None = None) -> str | None:
    """The first candidate that clears the text bar on every ground. When none
    does and `toward` is given (the ink), the first candidate is moved toward
    it a step at a time until it does: the theme's own hue, made legible,
    rather than the stylesheet's colour on a ground it was never checked on."""
    present = [c for c in candidates if c]
    for colour in present:
        if _clears(colour, grounds):
            return colour
    if toward and present:
        for step in range(1, 10):
            moved = mix(present[0], toward, step / 10)
            if _clears(moved, grounds):
                return moved
    return None


def _on(colour: str, choices: list[str]) -> str | None:
    """Text to sit on `colour`: whichever choice reads best, if any reads."""
    best = max(choices, key=lambda c: contrast(c, colour))
    return best if contrast(best, colour) >= TEXT else None


def tokens(palette: dict) -> tuple[dict[str, str], list[str]]:
    """The dashboard's tokens for a valid palette, and the ones kept at the
    stylesheet's own value because nothing in the palette cleared the bar."""
    c = palette["colors"]
    dark = palette["mode"] == "dark"
    page = c["background"]
    lighter = c.get("lighter_background")
    # A surface sits a step off the page: lighter in both themes, as the
    # stylesheet's own surfaces do. A "lighter" background that is not, or one
    # so far off it reads as a different colour, is not used.
    surface = lighter if lighter and _luminance(lighter) >= _luminance(page) \
        and contrast(lighter, page) < 1.6 else (mix(page, "#ffffff", 0.04) if dark else page)
    grounds = [page, surface]
    ink = _readable([c.get("bright_foreground"), c.get("foreground"),
                     c.get("light_foreground")] if dark
                    else [c.get("foreground"), c.get("dark_foreground"),
                          c.get("bright_foreground")], grounds)
    out: dict[str, str] = {"--page": page, "--surface": surface}
    kept: list[str] = []
    out["--sunken"] = c.get("dark_background") or mix(page, "#000000", 0.2 if dark else 0.03)
    if ink is None:
        # Text that cannot be read decides it: without legible ink the theme
        # is not used at all, rather than half-used.
        return {}, list(TOKENS)
    out["--ink"] = ink
    out["--surface-2"] = mix(surface, ink, 0.04)
    ink2 = _readable([c.get("foreground"), mix(ink, page, 0.2)], grounds)
    muted = _readable([c.get("dark_foreground"), mix(ink, page, 0.35), mix(ink, page, 0.25),
                       c.get("foreground")], grounds)
    for token, value in (("--ink-2", ink2), ("--muted", muted)):
        if value:
            out[token] = value
        else:
            kept.append(token)
    out["--border"] = out["--grid"] = c.get("selection") or mix(page, ink, 0.12)
    out["--border-strong"] = c.get("muted") or mix(page, ink, 0.22)
    if contrast(out["--border-strong"], page) < 1.3:
        out["--border-strong"] = mix(page, ink, 0.22)
    accent = _readable([c.get("accent"), c.get("blue"), c.get("bright_blue")], grounds, ink)
    on_accent = _on(accent, [page, ink, "#ffffff", "#000000"]) if accent else None
    if accent and on_accent:
        out.update({"--accent": accent, "--accent-rgb": _triple(accent),
                    "--on-accent": on_accent, "--on-accent-rgb": _triple(on_accent)})
    else:
        kept += ["--accent", "--accent-rgb", "--on-accent", "--on-accent-rgb"]
    for token, names in (("--good", ("green", "bright_green")),
                         ("--warning", ("yellow", "bright_yellow")),
                         ("--serious", ("orange", "bright_red")),
                         ("--critical", ("red", "bright_red"))):
        value = _readable([c.get(n) for n in names], grounds, ink)
        if value:
            out[token] = value
            out[f"{token}-rgb"] = _triple(value)
        else:
            kept += [token, f"{token}-rgb"]
    if "--critical" in out:
        on_critical = _on(out["--critical"], [page, ink, "#ffffff", "#000000"])
        if on_critical:
            out["--on-critical"] = on_critical
        else:
            kept.append("--on-critical")
    else:
        kept.append("--on-critical")
    assert set(out) <= set(TOKENS)
    return out, kept


def view(palette: dict | None) -> dict | None:
    """What the dashboard reads: the mode it lays the tokens over, the tokens,
    and the ones it kept — or None when nothing usable was sent."""
    if not palette:
        return None
    built, kept = tokens(palette)
    if not built:
        return None
    return {"name": palette.get("name"), "mode": palette["mode"], "tokens": built,
            "kept": kept}
