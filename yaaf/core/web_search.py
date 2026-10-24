"""The install's own web search: one backend every harness can search with
(docs/PROVIDERS.md §6).

A role granted `web` is promised search. Claude, GPT and Gemini search on their
own wires; most providers do not, and neither does OpenCode. Rather than refuse
every such seat (R2's honour half, as it stood), an administrator may name one
search backend here — its key and its price per query — and yaaf supplies
`WebSearch` from it wherever nothing else searches: as a built-in in yaaf's
loop, and as a yaaf tool over the stdio MCP server a CLI harness already
mounts.

Every query is billed into the run at the price set here, so search spend is
in the run's cost and under its cap, exactly like Anthropic's per-search
charge. The key is a platform secret: never granted to a role, never in an
agent's shell, injected only beside the model credential and hidden the same
way.
"""

from __future__ import annotations

import json

from fastapi import HTTPException
from sqlalchemy.orm import Session

from yaaf.core import platform
from yaaf.runtimes.wire import SEARCH_BACKENDS

BACKEND = "search.backend"
BASE_URL = "search.base_url"
PRICE = "search.price_usd"
SECRET = "platform-search-key"
#: The variables a run receives it under. The key's is hidden from every shell
#: a harness gives the model, like the model credential's.
SPEC_ENV = "YAAF_SEARCH"
KEY_ENV = "YAAF_SEARCH_KEY"


def config(session: Session) -> dict | None:
    """What is configured, without the key — or None."""
    name = platform.get_setting(session, BACKEND)
    backend = SEARCH_BACKENDS.get(name or "")
    if backend is None:
        return None
    try:
        price = float(platform.get_setting(session, PRICE) or backend.suggested_usd)
    except ValueError:
        price = backend.suggested_usd
    return {"backend": backend.name, "label": backend.label,
            "base_url": platform.get_setting(session, BASE_URL) or backend.base_url or "",
            "price_usd": price, "has_key": platform.secret_row(session, SECRET) is not None}


def configured(session: Session) -> bool:
    """Whether a run can be offered platform search: a backend, its address,
    and its key where it takes one."""
    found = config(session)
    if found is None or not found["base_url"]:
        return False
    return found["has_key"] or not SEARCH_BACKENDS[found["backend"]].key


def configure(session: Session, backend: str, *, key: str | None = None,
              base_url: str | None = None, price_usd: float | None = None) -> dict:
    """Name the backend, and its key, address and price. A blank key keeps the
    stored one, so changing the price does not mean pasting the key again."""
    found = SEARCH_BACKENDS.get(backend)
    if found is None:
        raise HTTPException(status_code=422, detail=(
            f"unknown search backend '{backend}' — one of: {', '.join(SEARCH_BACKENDS)}"))
    address = (base_url or "").strip() or found.base_url
    if not address or not address.startswith(("http://", "https://")):
        raise HTTPException(status_code=422, detail=(
            f"{found.label} needs its address — the base URL of your instance, e.g. "
            "http://searxng:8080"))
    if price_usd is not None and price_usd < 0:
        raise HTTPException(status_code=422, detail="a price is zero or more")
    has_key = platform.secret_row(session, SECRET) is not None
    if found.key and not (key and key.strip()) and not (
            has_key and platform.get_setting(session, BACKEND) == backend):
        raise HTTPException(status_code=422, detail=f"{found.label} needs its API key")
    platform.set_setting(session, BACKEND, backend)
    platform.set_setting(session, BASE_URL, address.rstrip("/"))
    platform.set_setting(session, PRICE, str(found.suggested_usd if price_usd is None
                                             else price_usd))
    if key and key.strip():
        platform.set_secret(session, SECRET, key.strip())
    elif not found.key:
        platform.clear_secret(session, SECRET)
    return config(session) or {}


def clear(session: Session) -> bool:
    had = platform.get_setting(session, BACKEND) is not None
    for key in (BACKEND, BASE_URL, PRICE):
        platform.set_setting(session, key, "")
    platform.clear_secret(session, SECRET)
    return had


def run_env(session: Session) -> dict[str, str]:
    """The environment a run searches with: the backend's spec, and its key
    under a variable every harness hides from the model's shell."""
    if not configured(session):
        return {}
    found = config(session) or {}
    env = {SPEC_ENV: json.dumps({"backend": found["backend"], "base_url": found["base_url"],
                                 "price_usd": found["price_usd"]})}
    key = platform.secret_value(session, SECRET)
    if key:
        env[KEY_ENV] = key
    return env
