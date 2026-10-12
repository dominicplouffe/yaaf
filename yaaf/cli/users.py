"""`yaaf users`: people, from a shell on the box.

Like `yaaf spend-report`, and unlike every other command, these open the
server's database directly (YAAF_DATABASE_URL — see yaaf.core.config) rather
than calling the API. They are how a box with nobody signed in gets its first
admin, how a locked-out admin gets back in, and how the deploy scripts get a
credential for the ten minutes they need one.

That is safe for the reason it is the right door: a shell that can reach the
database already is root on the box. The API is for people proving who they
are; this is for whoever is holding the machine. Run it where the server runs:

    docker compose -f deploy/docker-compose.yml exec api yaaf users bootstrap \\
        --email you@example.com --name "You"
"""

from __future__ import annotations

import typer
from sqlalchemy import func, select

from yaaf.core import ids
from yaaf.core.db import db_session
from yaaf.core.models import AuditLog, User
from yaaf.server import identity

users = typer.Typer(no_args_is_help=True,
                    help="People, from a shell on the box (talks to the database directly).")

#: How the record names a change made from here: by whoever held the shell,
#: which the platform cannot know and so does not pretend to.
SHELL_ACTOR = "system:shell"


def _audit(session, action: str, user: User, detail: dict | None = None) -> None:
    session.add(AuditLog(id=ids.new_id(ids.AUDIT), project_id=None, actor=SHELL_ACTOR,
                         action=action, entity="user", entity_id=user.id,
                         detail=detail or {}))


def _active_admins(session) -> int:
    return int(session.execute(
        select(func.count(User.id)).where(User.is_admin.is_(True),
                                          User.deactivated_at.is_(None))
    ).scalar_one())


@users.command("has-admin")
def has_admin() -> None:
    """Exit 0 if the box has an active admin, 1 if not — for scripts."""
    with db_session() as session:
        raise typer.Exit(0 if _active_admins(session) else 1)


BASE_HELP = ("The origin the link should open on, e.g. http://localhost:8080. "
             "Defaults to YAAF_PUBLIC_BASE_URL, then YAAF_API_BASE_URL.")


@users.command("bootstrap")
def bootstrap(
    email: str = typer.Option(..., "--email", help="The first admin's email"),
    name: str = typer.Option("", "--name", help="Their name, if the account is new"),
    base_url: str = typer.Option("", "--base-url", help=BASE_HELP),
) -> None:
    """Make the first admin, on a box that has none, and print their setup link.

    Promotes the account with that email if it exists — the chairman you have
    been all along, with every project you founded — or creates it. Refused once
    any admin exists: after that, admins are made by admins, in the dashboard.
    """
    with db_session() as session:
        if _active_admins(session):
            typer.echo("This box already has an admin. Admins are made in the dashboard "
                       "(Settings → People), or reset one with `yaaf users setup-link`.")
            raise typer.Exit(1)
        address = identity.normalise_email(email)
        if "@" not in address:
            typer.echo(f"{email!r} is not an email address")
            raise typer.Exit(1)
        user = identity.find_user(session, address)
        created = user is None
        if created:
            user = User(id=ids.new_id(ids.USER), email=address,
                        display_name=name or address.split("@")[0])
            session.add(user)
            session.flush()
        user.is_admin = True
        user.deactivated_at = None
        link = identity.issue_setup_link(session, user, "bootstrap")
        _audit(session, "user.bootstrap", user, {"email": address, "created": created})
        had_password = bool(user.password_hash)
    typer.echo(f"{address} is this box's admin{' (new account)' if created else ''}.")
    if had_password:
        typer.echo("They already have a password and can sign in with it. To choose a "
                   "new one instead, open:")
    else:
        typer.echo("Open this link to choose a password — it works once, for "
                   f"{int(identity.SETUP_TTL.total_seconds() // 3600)} hours:")
    typer.echo(identity.setup_url(link, base_url or None))


@users.command("setup-link")
def setup_link(email: str,
               base_url: str = typer.Option("", "--base-url", help=BASE_HELP)) -> None:
    """Print a fresh one-time link that sets this person's password.

    For a forgotten password, or an invite whose link was lost. Any earlier
    unused link for them stops working.
    """
    with db_session() as session:
        user = identity.find_user(session, email)
        if user is None:
            typer.echo(f"nobody with the email {email!r} has an account here")
            raise typer.Exit(1)
        if user.deactivated_at is not None:
            typer.echo(f"{user.email} is deactivated; an admin can reactivate them first")
            raise typer.Exit(1)
        purpose = "reset" if user.password_hash else "invite"
        link = identity.issue_setup_link(session, user, purpose)
        _audit(session, f"user.setup_link.{purpose}", user, {"email": user.email})
    typer.echo(identity.setup_url(link, base_url or None))


@users.command("token")
def token(
    email: str = typer.Argument("", help="Whose token (omit with --first-admin)"),
    name: str = typer.Option("shell", "--name", help="What it is for, for the record"),
    days: int = typer.Option(0, "--days", help="Lifetime in days"),
    minutes: int = typer.Option(0, "--minutes", help="Lifetime in minutes (for scripts)"),
    first_admin: bool = typer.Option(False, "--first-admin",
                                     help="The box's longest-standing active admin"),
) -> None:
    """Print a personal token, and nothing else, so a script can capture it.

    The deploy scripts mint a ten-minute one to sync the role library; that is
    the case this exists for. A person at a terminal wants `yaaf login`.
    """
    with db_session() as session:
        if first_admin:
            user = session.execute(
                select(User).where(User.is_admin.is_(True), User.deactivated_at.is_(None))
                .order_by(User.created_at)
            ).scalars().first()
        else:
            user = identity.find_user(session, email)
        if user is None or user.deactivated_at is not None:
            typer.echo("no such active account", err=True)
            raise typer.Exit(1)
        try:
            if minutes:
                row, plaintext = identity.mint_token(session, user, name, minutes=minutes)
            else:
                row, plaintext = identity.mint_token(session, user, name, days=days or None)
        except ValueError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(1) from None
        _audit(session, "user.token_mint", user, {"name": row.name, "via": "shell",
                                                  "expires_at": row.expires_at.isoformat()})
    typer.echo(plaintext)


@users.command("list")
def list_users() -> None:
    """Everyone with an account, and what they are."""
    with db_session() as session:
        for user in session.execute(select(User).order_by(User.created_at)).scalars():
            flags = [f for f, on in (("admin", user.is_admin),
                                     ("deactivated", user.deactivated_at is not None),
                                     ("no password", not user.password_hash)) if on]
            typer.echo(f"{user.email:<40} {user.display_name:<24} {', '.join(flags)}")
