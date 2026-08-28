"""Per-run agent tokens.

An agent run gets one short-lived bearer token, scoped to exactly one agent and
one run. Only the SHA-256 hash is stored; the plaintext exists in the sandbox
environment and nowhere else. Revocation (refusal-cap trips, kill switches)
flips a bit on the run row — the next API call fails, whatever the prompt says.
"""

from __future__ import annotations

import hashlib
import secrets


def mint() -> tuple[str, str]:
    """Return (plaintext, sha256hex). Plaintext goes to the sandbox env only."""
    plaintext = f"yrt_{secrets.token_urlsafe(32)}"
    return plaintext, hash_token(plaintext)


def mint_mcp() -> tuple[str, str]:
    """Return (plaintext, sha256hex) for an MCP client token.

    A different prefix from a run token because it is a different principal
    with a different lifetime: this one is long-lived and sits in a desktop
    client's config file, so a token found in a log has to be identifiable at
    a glance as the thing to revoke. Storage is the same — hash only.
    """
    plaintext = f"ymt_{secrets.token_urlsafe(32)}"
    return plaintext, hash_token(plaintext)


def hash_token(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode()).hexdigest()
