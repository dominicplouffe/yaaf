"""SMTP out, IMAP in. Module-level functions so tests replace the network and
nothing else — the same seam the Slack bridge keeps around `_post_message`.

TLS is always on and always verified. Port 465 (implicit TLS) and 587
(STARTTLS) are both supported for sending, because a project mailbox is
whatever the chairman's provider gives him; the platform's own alert mail only
speaks 587, which is fine for one install-wide relay and not for this.
"""

from __future__ import annotations

import contextlib
import imaplib
import smtplib
import ssl
from dataclasses import dataclass, field
from datetime import datetime
from email.message import EmailMessage

from yaaf.server.mail.settings import MailSettings

TIMEOUT_S = 30
#: The most messages one look at the inbox will fetch. The rest wait for the
#: next look: a backlog must not hold the loop for every other project.
FETCH_BATCH = 50


@dataclass
class Fetched:
    """One look at the inbox. `last_uid` is where the next look starts from,
    whether or not anything was fetched this time."""

    uidvalidity: str
    last_uid: int
    messages: list[tuple[int, bytes]] = field(default_factory=list)


def _context() -> ssl.SSLContext:
    return ssl.create_default_context()


def smtp_send(cfg: MailSettings, message: EmailMessage) -> None:
    """Hand one message to the project's SMTP server, or raise."""
    if cfg.smtp_security == "ssl":
        server: smtplib.SMTP = smtplib.SMTP_SSL(cfg.smtp_host, cfg.smtp_port,
                                                timeout=TIMEOUT_S, context=_context())
    else:
        server = smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=TIMEOUT_S)
    with server:
        if cfg.smtp_security != "ssl":
            server.starttls(context=_context())
        server.login(cfg.username, cfg.password)
        server.send_message(message)


def _imap(cfg: MailSettings) -> imaplib.IMAP4_SSL:
    client = imaplib.IMAP4_SSL(cfg.imap_host, cfg.imap_port, ssl_context=_context(),
                               timeout=TIMEOUT_S)
    client.login(cfg.username, cfg.password)
    return client


def _ok(typ: str, data, what: str):
    if typ != "OK":
        raise RuntimeError(f"IMAP {what} failed: {data!r}")
    return data


def imap_fetch(cfg: MailSettings, *, uidvalidity: str | None, last_uid: int,
               since: datetime | None) -> Fetched:
    """Everything that arrived after `last_uid`, read-only and without marking
    anything seen: the mailbox may be a person's too, and the platform reading
    a message must not make it look read to them.

    A first look (or a mailbox whose UIDVALIDITY changed, which voids every
    UID) has no position to start from. With `since` it searches from that
    date — the first email the project sent, so no reply can have come before
    it; without, there is nothing it could be a reply to, and it only records
    where the box stands now.
    """
    client = _imap(cfg)
    try:
        _ok(*client.select(_quoted(cfg.imap_folder), readonly=True), what="SELECT")
        _, values = client.response("UIDVALIDITY")
        current = (values[0].decode() if values and values[0] else "") or "0"
        fresh = uidvalidity != current or last_uid <= 0
        if fresh and since is None:
            _, nexts = client.response("UIDNEXT")
            if nexts and nexts[0]:
                top = int(nexts[0]) - 1
            else:
                data = _ok(*client.uid("SEARCH", None, "ALL"), what="SEARCH")
                found = [int(x) for x in (data[0] or b"").split()]
                top = max(found, default=0)
            return Fetched(uidvalidity=current, last_uid=max(top, 0))
        if fresh:
            criteria = ("SINCE", _imap_date(since))
            floor = 0
        else:
            criteria = ("UID", f"{last_uid + 1}:*")
            floor = last_uid
        data = _ok(*client.uid("SEARCH", None, *criteria), what="SEARCH")
        # `UID n:*` always answers with the newest message even when its UID is
        # below n, so the floor is applied here rather than trusted to the server.
        uids = sorted(u for u in (int(x) for x in (data[0] or b"").split()) if u > floor)
        batch = uids[:FETCH_BATCH]
        out = Fetched(uidvalidity=current, last_uid=batch[-1] if batch else floor)
        for uid in batch:
            parts = _ok(*client.uid("FETCH", str(uid), "(BODY.PEEK[])"), what="FETCH")
            raw = next((p[1] for p in parts if isinstance(p, tuple) and len(p) > 1), None)
            if raw is not None:
                out.messages.append((uid, raw))
        return out
    finally:
        with contextlib.suppress(Exception):  # a failed goodbye loses nothing
            client.logout()


_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _imap_date(when: datetime) -> str:
    """RFC 3501's date, spelled out rather than strftime'd: `%b` follows the
    process locale, and a server asked for "SINCE 08-nov.-2026" finds nothing."""
    return f"{when.day:02d}-{_MONTHS[when.month - 1]}-{when.year}"


def _quoted(folder: str) -> str:
    return '"' + folder.replace("\\", "\\\\").replace('"', '\\"') + '"'


def check(cfg: MailSettings) -> dict:
    """Log in to both servers and send nothing. The settings screen's test: a
    wrong password should surface there, not as the first deliverable that
    silently never left."""
    result: dict = {"smtp": {"ok": False}, "imap": {"ok": False}}
    try:
        if cfg.smtp_security == "ssl":
            server: smtplib.SMTP = smtplib.SMTP_SSL(cfg.smtp_host, cfg.smtp_port,
                                                    timeout=TIMEOUT_S, context=_context())
        else:
            server = smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=TIMEOUT_S)
        with server:
            if cfg.smtp_security != "ssl":
                server.starttls(context=_context())
            server.login(cfg.username, cfg.password)
        result["smtp"] = {"ok": True}
    except Exception as exc:  # noqa: BLE001 - every failure is an answer here
        result["smtp"] = {"ok": False, "error": plain(exc, cfg.smtp_host)}
    try:
        client = _imap(cfg)
        try:
            _ok(*client.select(_quoted(cfg.imap_folder), readonly=True), what="SELECT")
        finally:
            client.logout()
        result["imap"] = {"ok": True}
    except Exception as exc:  # noqa: BLE001
        result["imap"] = {"ok": False, "error": plain(exc, cfg.imap_host)}
    result["ok"] = result["smtp"]["ok"] and result["imap"]["ok"]
    return result


def plain(exc: BaseException, host: str = "") -> str:
    """An error a chairman can act on, in words — never a class name, and never
    the password: smtplib and imaplib do not echo it, and this is the place
    that would have to change if one did."""
    import socket

    text = (str(exc) or "").strip()
    where = host or "the mail server"
    if isinstance(exc, smtplib.SMTPAuthenticationError) or (
            isinstance(exc, imaplib.IMAP4.error) and "auth" in text.lower()):
        return ("the server refused the username or password. Gmail needs an app password; "
                "Microsoft 365 may not allow passwords for SMTP and IMAP at all.")
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        refused = "; ".join(f"{addr}: {code} {msg.decode(errors='replace') if isinstance(msg, bytes) else msg}"
                            for addr, (code, msg) in exc.recipients.items())
        return f"the server refused the recipient — {refused[:240]}"
    if isinstance(exc, smtplib.SMTPResponseException):
        said = exc.smtp_error.decode(errors="replace") if isinstance(exc.smtp_error, bytes) \
            else str(exc.smtp_error)
        return f"the server said no ({exc.smtp_code}): {said[:240]}"
    if isinstance(exc, socket.gaierror):
        return f"{where} could not be found — check the server name"
    if isinstance(exc, ConnectionRefusedError):
        return f"{where} refused the connection — check the port" + (f" ({text[:160]})" if text else "")
    if isinstance(exc, TimeoutError | socket.timeout):
        return f"{where} did not answer in time"
    if isinstance(exc, ssl.SSLError):
        return f"the secure connection to {where} failed — check the port and security setting"
    if isinstance(exc, LookupError | FileNotFoundError):
        return text or "the attached deliverable is no longer in the store"
    return text[:300] or type(exc).__name__

