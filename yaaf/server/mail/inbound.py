"""What an email in the project's inbox must prove before the CEO hears of it.

Four things, in this order, and failing any one is final:

1. **It is not an auto-reply.** An out-of-office answering the CEO, answered by
   the CEO, answered by the out-of-office, is a paid loop with nobody in it.
   RFC 3834's `Auto-Submitted: auto-replied` is the mark. `auto-generated` is
   *not* refused — another agent writing back is the use case, and an agent's
   mail is generated.
2. **The sender is on the recipient list.** The chairman decides who the
   project corresponds with; an address he did not list is somebody else's
   mail, whatever it says.
3. **The sender is who the From line says.** A From line is a string anybody
   can type. The receiving server's verdict — the topmost
   `Authentication-Results` header, which is the one *it* added — must show
   DMARC, or DKIM or SPF aligned with the From domain, passing. A mailbox whose
   server records no verdict fails this, which the chairman can switch off
   (`verify_sender: false`) knowing what he is turning off.
4. **It answers something the project sent.** In-Reply-To or References must
   name a Message-ID this project minted. A correspondent can only continue a
   conversation the CEO started; nobody opens one at the project.

Nothing here touches the database or the network: it reads bytes and says yes
or no, with the reason a chairman would want in the audit log.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import getaddresses

from yaaf.server.mail.settings import domain_of, normalise

#: The most of a reply's text the platform keeps. A reply is feedback, not a
#: payload; anything longer than this belongs in an attachment or a link.
MAX_BODY_CHARS = 20_000

_MESSAGE_ID = re.compile(r"<[^<>\s]{1,398}>")


@dataclass
class Parsed:
    message_id: str | None
    from_addr: str | None
    subject: str
    references: list[str]
    auto_replied: bool
    auth_results: str | None
    body: str
    attachments: list[dict] = field(default_factory=list)
    #: The attachments' bytes, beside their descriptions, in the same order.
    #: Not stored on any row: the service decides what is kept and where.
    files: list[bytes] = field(default_factory=list, repr=False)


def parse(raw: bytes) -> Parsed:
    message: EmailMessage = BytesParser(policy=policy.default).parsebytes(raw)  # type: ignore[assignment]
    senders = getaddresses([str(v) for v in message.get_all("From") or []])
    # Two From addresses is a header built to confuse whoever reads the first
    # one. No single sender, no sender.
    from_addr = normalise(senders[0][1]) if len(senders) == 1 else None
    message_id = _ids(str(message.get("Message-ID") or ""))
    references = _ids(str(message.get("In-Reply-To") or "")) \
        + _ids(" ".join(str(v) for v in message.get_all("References") or []))
    auto = str(message.get("Auto-Submitted") or "").strip().lower()
    # get_all lists headers top-down, and the top one is the receiving server's
    # own. Anything below it arrived inside the message and proves nothing.
    results = message.get_all("Authentication-Results") or []
    return Parsed(
        message_id=message_id[0] if message_id else None,
        from_addr=from_addr,
        subject=str(message.get("Subject") or "")[:300],
        references=list(dict.fromkeys(references)),
        auto_replied=auto.startswith("auto-replied"),
        auth_results=str(results[0]) if results else None,
        body=_body(message),
        **_attachments(message),
    )


def _ids(value: str) -> list[str]:
    return _MESSAGE_ID.findall(value or "")


def _body(message: EmailMessage) -> str:
    part = message.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    try:
        text = part.get_content()
    except Exception:  # noqa: BLE001 - an undecodable body is an empty one
        return ""
    if part.get_content_subtype() == "html":
        text = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", text)
        text = re.sub(r"(?i)<br\s*/?>|</p>", "\n", text)
        text = html.unescape(re.sub(r"<[^>]+>", "", text))
    text = re.sub(r"\n{3,}", "\n\n", text.replace("\r\n", "\n")).strip()
    if len(text) > MAX_BODY_CHARS:
        text = text[:MAX_BODY_CHARS] + "\n\n[…truncated by the platform]"
    return text


def _attachments(message: EmailMessage) -> dict:
    """Each attachment's name, type and size, and its bytes. At most twenty: an
    email carrying more is not a document someone sent for a look."""
    described: list[dict] = []
    files: list[bytes] = []
    for part in message.iter_attachments():
        try:
            data = part.get_content()
        except Exception:  # noqa: BLE001 - an undecodable part is an empty one
            data = b""
        if isinstance(data, str):
            data = data.encode()
        elif not isinstance(data, bytes):
            # A message/rfc822 part decodes to a message; keep it as the mail it is.
            data = bytes(data) if hasattr(data, "__bytes__") else b""
        described.append({"name": str(part.get_filename() or "unnamed")[:200],
                          "type": part.get_content_type(), "size": len(data)})
        files.append(data)
        if len(described) == 20:
            break
    return {"attachments": described, "files": files}


# ── the sender check ─────────────────────────────────────────────────────────────

_METHOD = re.compile(r"^\s*([a-z0-9_-]+)\s*=\s*([a-z]+)", re.I)
_PROP = re.compile(r"\b(header\.d|header\.i|header\.from|smtp\.mailfrom|smtp\.from)"
                   r"\s*=\s*\"?([^\s;\"]+)", re.I)


def _aligned(claimed: str, from_domain: str) -> bool:
    """Relaxed alignment (DMARC's default): the authenticated domain is the From
    domain or a parent of it."""
    claimed = domain_of(claimed.lstrip("@")).strip(".").lower()
    return bool(claimed) and (from_domain == claimed or from_domain.endswith("." + claimed))


def sender_verified(auth_results: str | None, from_addr: str) -> tuple[bool, str]:
    """Whether the receiving server vouched for `from_addr`, and in which words."""
    if not auth_results:
        return False, "the mail server recorded no Authentication-Results for it"
    from_domain = domain_of(from_addr)
    # The first clause is the authserv-id, the server naming itself.
    clauses = auth_results.split(";")[1:]
    seen = []
    for clause in clauses:
        method = _METHOD.match(clause)
        if not method:
            continue
        name, result = method.group(1).lower(), method.group(2).lower()
        seen.append(f"{name}={result}")
        if result != "pass":
            continue
        props = {k.lower(): v for k, v in _PROP.findall(clause)}
        if name == "dmarc":
            claimed = props.get("header.from")
            if claimed is None or _aligned(claimed, from_domain):
                return True, "dmarc=pass"
        elif name == "dkim":
            claimed = props.get("header.d") or props.get("header.i")
            if claimed and _aligned(claimed, from_domain):
                return True, f"dkim=pass for {claimed.lstrip('@')}"
        elif name == "spf":
            claimed = props.get("smtp.mailfrom") or props.get("smtp.from")
            if claimed and _aligned(claimed, from_domain):
                return True, "spf=pass"
    said = ", ".join(seen) or "nothing it could read"
    return False, f"nothing aligned with {from_domain} passed (the server said: {said})"
