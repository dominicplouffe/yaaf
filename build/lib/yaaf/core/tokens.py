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


def hash_token(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode()).hexdigest()
