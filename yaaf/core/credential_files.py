"""What makes a stored credential a file, judged in one place.

A file credential is an ordinary `Secret` whose `filename` is set. Its
ciphertext holds **base64 of the file's bytes** rather than the value itself,
which is what lets a `.p12`, a DER certificate or anything else binary ride the
same column as a Stripe key.

Everything here is a refusal at the door. The alternative — accepting a
filename and sanitizing it, accepting a value and hoping it decodes — pushes
the failure into the sandbox, where it surfaces hours later inside one agent's
run summary as a path that does not exist.
"""

from __future__ import annotations

import base64
import binascii
import re

# One path component, and only ever that. No separators, no traversal, no
# leading dot (a credential the chairman cannot see in `ls` is a debugging
# session). This string becomes a path inside the container.
FILENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")

# Text that is a FILE wearing a string's clothes. A chairman who pastes a
# service-account JSON into the value box gets a variable holding JSON where
# every Google library expects a path, and the resulting error names a missing
# file rather than the mistake. Cheaper to say so now.
FILE_SHAPED_PREFIXES = ("-----BEGIN", "{", "apiVersion:")


# A file credential travels to the sandbox inside the run's environment, and
# `execve` bounds the whole environment at roughly 2 MB — shared with the
# context, the tool permissions and every other credential. A file that blows
# that budget does not fail here; it fails as a container that will not start.
MAX_BYTES = 128 * 1024


class BadCredentialFile(ValueError):
    """The message is the interface — it is shown to the chairman verbatim."""


def check_filename(filename: str) -> str:
    if not FILENAME_RE.match(filename or ""):
        raise BadCredentialFile(
            f"'{filename}' is not a usable file name — it becomes a file in the "
            "agent's sandbox, so it must be a single name of letters, digits, "
            "dot, dash or underscore (no slashes, no '..', no leading dot)")
    if filename in (".", ".."):  # unreachable via the regex; stated anyway
        raise BadCredentialFile("a credential file needs a real name")
    return filename


def decode(value: str) -> bytes:
    """The stored plaintext of a file credential, as bytes."""
    try:
        body = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise BadCredentialFile(
            "a file credential's value must be base64 of the file's bytes — "
            "the CLI's --from-file and the dashboard's file picker do this for you"
        ) from exc
    if len(body) > MAX_BYTES:
        raise BadCredentialFile(
            f"that file is {len(body) // 1024} KB; a credential file travels to the "
            f"sandbox inside the run's environment and cannot exceed "
            f"{MAX_BYTES // 1024} KB. Something this large is data, not a credential — "
            "put it in the workspace or behind a data source")
    return body


def encode(body: bytes) -> str:
    return base64.b64encode(body).decode()


def default_filename(env_var: str, body: bytes) -> str:
    """A name for a file the chairman did not name. The extension is sniffed
    because some tooling genuinely cares (kubectl, openssl), and a wrong guess
    is cheaper than no name at all — the chairman can always supply one."""
    stem = env_var.lower() or "credential"
    head = body[:16].lstrip()
    if head.startswith(b"{"):
        return f"{stem}.json"
    if head.startswith(b"-----BEGIN"):
        return f"{stem}.pem"
    if head.startswith(b"apiVersion:"):
        return f"{stem}.yaml"
    return stem


def looks_like_a_file(value: str) -> bool:
    """True when a STRING credential's value is really a file's contents.

    Deliberately narrow: it fires on the two shapes that are unambiguous (a PEM
    armour line, a JSON object) rather than on anything multi-line, because a
    multi-line string credential is a legitimate thing to store and refusing it
    would be the platform overruling the chairman about his own deployment.
    """
    stripped = (value or "").strip()
    if not stripped.startswith(FILE_SHAPED_PREFIXES):
        return False
    if stripped.startswith("{"):
        import json
        try:
            return isinstance(json.loads(stripped), dict)
        except ValueError:
            return False
    return True
