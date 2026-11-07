"""`yaaf theme`: the dashboard in your desktop's colours.

`sync` reads the desktop's current palette — Omarchy's, from where Omarchy 4
writes it on every theme switch, or any file in the same shape — and hands it
to yaaf, which works the dashboard's tokens out from it without giving up the
stylesheet's contrast (yaaf.core.desktop_theme). On Omarchy, `yaaf desktop
install` puts a theme-set hook in place that runs it on every switch, so the
dashboard changes theme when the desktop does.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

import httpx
import typer

from yaaf.core import desktop_theme

theme_app = typer.Typer(no_args_is_help=True, help="The dashboard in your desktop's colours.")


def _omarchy_current() -> Path:
    """Where Omarchy 4 keeps the theme in use (omarchy-theme-color reads it)."""
    state = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
    return state / "omarchy" / "current"


def palette_from(colors_file: Path, name: str | None = None) -> dict:
    """A colors.toml — Omarchy's shape: names to #rrggbb, and a mode — as the
    palette yaaf stores. Anything that is not one of a theme's colour names, or
    not a #rrggbb, is left out here rather than refused later."""
    raw = tomllib.loads(colors_file.read_text())
    colors = {k: v.lower() for k, v in raw.items()
              if k in desktop_theme.KEYS and isinstance(v, str) and desktop_theme.HEX.match(v)}
    mode = raw.get("mode")
    if mode not in ("dark", "light") and "background" in colors:
        mode = "dark" if desktop_theme._luminance(colors["background"]) < 0.4 else "light"
    clean = (name or "").strip()
    return {"name": clean if desktop_theme.NAME.match(clean or "-") and clean else None,
            "mode": mode or "dark", "colors": colors}


def omarchy_palette() -> dict | None:
    current = _omarchy_current()
    colors = current / "theme" / "colors.toml"
    if not colors.exists():
        return None
    try:
        name = (current / "theme.name").read_text().strip()
    except OSError:
        name = None
    return palette_from(colors, name)


def push(client: httpx.Client, palette: dict) -> dict:
    sent = client.put("/me/desktop-theme", json=palette)
    if sent.status_code != 200:
        from yaaf.cli import main as cli

        raise ValueError(cli._detail(sent))
    return sent.json()["theme"]


def sync_quietly() -> bool:
    """For the installer: sync if there is a desktop theme to sync, say nothing."""
    from yaaf.cli import main as cli

    palette = omarchy_palette()
    if palette is None:
        return False
    try:
        with cli._client() as client:
            push(client, palette)
    except (httpx.HTTPError, ValueError):
        return False
    return True


@theme_app.command("sync")
def sync(
    from_file: Path = typer.Option(None, "--from", help="A colors.toml to read instead of "
                                                        "Omarchy's current theme"),
    quiet: bool = typer.Option(False, "--quiet", help="Say nothing; for a hook"),
) -> None:
    """Dress the dashboard in the desktop's current theme."""
    from yaaf.cli import main as cli

    # A file named for its theme (gruvbox.toml), or a theme folder's colors.toml.
    palette = (palette_from(from_file, from_file.parent.name if from_file.name == "colors.toml"
                            else from_file.stem) if from_file else omarchy_palette())
    if palette is None:
        if not quiet:
            typer.echo(f"No desktop theme found at {_omarchy_current() / 'theme'}. "
                       "On another desktop, pass --from with a colors.toml.", err=True)
        raise typer.Exit(1)
    try:
        with cli._client() as client:
            view = push(client, palette)
    except (httpx.HTTPError, ValueError) as exc:
        if not quiet:
            typer.echo(f"Not synced: {exc}", err=True)
        raise typer.Exit(1) from None
    if quiet:
        return
    typer.echo(f"The dashboard now wears {view.get('name') or 'your desktop theme'} "
               f"({view['mode']}).")
    kept = [k for k in view.get("kept", []) if not k.endswith("-rgb")]
    if kept:
        typer.echo("Kept the dashboard's own " + ", ".join(kept)
                   + ": nothing in the theme reads on its background.")


@theme_app.command("show")
def show() -> None:
    """The desktop theme the dashboard wears now, if any."""
    from yaaf.cli import main as cli

    with cli._client() as client:
        got = client.get("/me/desktop-theme")
    view = (got.json() or {}).get("theme") if got.status_code == 200 else None
    if not view:
        typer.echo("The dashboard wears its own light and dark. `yaaf theme sync` changes that.")
        return
    typer.echo(f"{view.get('name') or 'desktop theme'} ({view['mode']}): "
               f"page {view['tokens']['--page']}, ink {view['tokens']['--ink']}, "
               f"accent {view['tokens'].get('--accent', 'its own')}")


@theme_app.command("clear")
def clear() -> None:
    """Back to the dashboard's own light and dark."""
    from yaaf.cli import main as cli

    with cli._client() as client:
        client.delete("/me/desktop-theme")
    typer.echo("The dashboard wears its own light and dark again.")
