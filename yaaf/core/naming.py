"""One answer to "what does this stored credential become in a sandbox?" —
its environment variable, and, when the credential is a file, how its bytes
travel there and what the agent is told it holds.

The runner injects credentials under this mapping, so anything that TELLS
someone the variable's name (the fulfill response, the CEO's wake notice, a
UI hint) must derive it here — a second, slightly different normalization
once announced 'AWS.KEY' for a variable actually injected as 'AWS_KEY'.
"""

from __future__ import annotations

import json
import re


def env_var_name(secret_name: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", secret_name).strip("_").upper()


# A file credential travels to the sandbox under a CARRIER variable, because
# the thing the agent must end up holding is a path and the path does not exist
# until something inside the container has written the file. The entrypoint
# writes it, sets the real variable to the path, and deletes the carrier — so
# the credential's own variable holds a path from the first instruction the
# model runs, rather than bytes that something downstream has to recognise.
# (The deletion is about that meaning, not about hiding the credential from the
# role it was granted to — see sandbox_entry.py for what it does and does not
# buy.)
#
# sandbox_entry.py mirrors this constant rather than importing it: it is copied
# into the sandbox image alone and must not import the yaaf package. The mirror
# is held honest by a test, not by hope.
FILE_CRED_PREFIX = "YAAF_FILE_CRED__"


def file_cred_carrier(env_name: str) -> str:
    return f"{FILE_CRED_PREFIX}{env_name}"


def is_reserved(env_name: str) -> bool:
    """Is this a variable the platform itself owns in a sandbox?

    Role credentials are merged into the run's environment last, so a credential
    named `yaaf-api-token` overwrites the per-run token and the run loses its
    ability to talk to the platform at all — a failure that looks like an outage.
    `YAAF_FILE_CRED__*` matters for a second reason: it is how a file's bytes
    travel, and a string credential landing on that name would be handed to the
    materializer as if it were one.
    """
    return env_name.startswith("YAAF_")


def announced_credentials(env: dict) -> tuple[list[str], dict[str, str]]:
    """Split an injected credential env into what to TELL the agent it holds.

    Returns the variable names as they will exist inside the sandbox — carriers
    resolved back to the name they will be rewritten to — and {name: filename}
    for the file ones. Both run lanes build their CREDENTIALS paragraph from
    this, because the naive `list(env)` announces `YAAF_FILE_CRED__GOOGLE_SA`,
    a variable that is deleted before the model can look for it.
    """
    names: list[str] = []
    files: dict[str, str] = {}
    for key, value in env.items():
        if not key.startswith(FILE_CRED_PREFIX):
            names.append(key)
            continue
        name = key[len(FILE_CRED_PREFIX):]
        names.append(name)
        try:
            files[name] = str(json.loads(value)["filename"])
        except Exception:
            files[name] = "a credential file"
    return names, files
