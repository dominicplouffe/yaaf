"""The scale forecast, as the platform imports it.

The arithmetic lives in `yaaf.ppc.kit` — one file the sandbox gets verbatim as
`ppckit.py` — so an analyst's script, the reviewer's re-run and the platform
compute the same curve. See the kit for the model, its priors and ceilings.
"""

from __future__ import annotations

from yaaf.ppc.kit import (  # noqa: F401 — the platform's names for the kit's forecast
    IMPRESSION_SHARE_CAP,
    INCREMENTALITY,
    KINDS,
    MAX_SCALE,
    MIN_DAYS,
    MIN_LOG_SPEND_SD,
    PRIORS,
    SHARE_GAIN,
    SHRINK_DAYS,
    Curve,
    PoolProblem,
    allocate,
    curve,
    fit_exponent,
)
