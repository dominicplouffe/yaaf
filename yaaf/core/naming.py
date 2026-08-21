"""One answer to "what env var does this secret become?".

The runner injects credentials under this mapping, so anything that TELLS
someone the variable's name (the fulfill response, the CEO's wake notice, a
UI hint) must derive it here — a second, slightly different normalization
once announced 'AWS.KEY' for a variable actually injected as 'AWS_KEY'.
"""

from __future__ import annotations

import re


def env_var_name(secret_name: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", secret_name).strip("_").upper()
