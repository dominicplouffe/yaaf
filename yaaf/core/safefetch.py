"""Fetching a URL an agent chose, from inside the platform.

Agent runs happen in a sandboxed container behind a per-role egress allowlist.
These fetches do not: the evidence verifier and the metric measurer run in the
janitor, on the platform's own network, with the platform's own credentials
ambient in the environment. A URL an agent supplies is therefore a request to
make the *control plane* connect to somewhere of the agent's choosing.

Left open, that reaches the cloud metadata endpoint (169.254.169.254), the
database on localhost, any admin port bound to the loopback interface, and
anything else on the private network the box sits in. The response body is not
handed back verbatim — but the *error* is, and "connection refused" versus a
parse failure is enough to map a network one port at a time.

So:

* http and https only — no file://, no gopher://, no redirect to either;
* the resolved address must be a public one, checked against every address the
  name resolves to, not just the first;
* redirects are followed by hand so each hop is re-checked, because validating
  only the URL the agent typed is validated by whoever controls that domain;
* the body is capped while streaming rather than after, so a multi-gigabyte
  response cannot be buffered into the janitor's memory before the slice.

DNS rebinding is the residual risk: a name can resolve to a public address at
check time and a private one microseconds later when httpx resolves it again.
Closing that needs the connection pinned to the address that was checked, which
httpx does not expose cleanly; it is noted here rather than silently accepted,
and the blast radius is bounded by everything else on this list.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

import httpx

MAX_REDIRECTS = 3
DEFAULT_TIMEOUT_S = 20.0
DEFAULT_MAX_BYTES = 2_000_000


class UnsafeURL(ValueError):
    """The URL is not one the platform will connect to on an agent's behalf."""


def _check_host(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise UnsafeURL(f"{parsed.scheme or 'that'} is not a scheme the platform will fetch")
    host = parsed.hostname
    if not host:
        raise UnsafeURL("the URL names no host")
    try:
        infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80))
    except socket.gaierror as exc:
        raise UnsafeURL(f"{host} does not resolve") from exc
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        # `is_global` is False for private, loopback, link-local, reserved,
        # multicast and unspecified — one check rather than a list to forget
        # a member of.
        if not address.is_global:
            raise UnsafeURL(
                f"{host} resolves to {address}, which is not a public address. The "
                "platform fetches this from its own network, so an internal address "
                "here would be asking it to read something the project cannot reach."
            )


def fetch(url: str, *, timeout: float = DEFAULT_TIMEOUT_S,
          max_bytes: int = DEFAULT_MAX_BYTES,
          user_agent: str = "yaaf/0.1") -> tuple[int, bytes]:
    """GET a URL an agent chose. Raises UnsafeURL before connecting anywhere."""
    seen = 0
    current = url
    with httpx.Client(follow_redirects=False, timeout=timeout) as client:
        while True:
            _check_host(current)
            with client.stream("GET", current, headers={"User-Agent": user_agent}) as response:
                if response.is_redirect and seen < MAX_REDIRECTS:
                    location = response.headers.get("location")
                    if not location:
                        return response.status_code, b""
                    current = str(httpx.URL(current).join(location))
                    seen += 1
                    continue
                body = bytearray()
                for chunk in response.iter_bytes():
                    body += chunk
                    if len(body) >= max_bytes:
                        # Stop reading rather than truncating a buffered body:
                        # the cap is about not holding it, not about the slice.
                        raise UnsafeURL(
                            f"the response exceeded {max_bytes} bytes; a metric reads one "
                            "number, so this is the wrong document or the wrong path"
                        )
                return response.status_code, bytes(body)
