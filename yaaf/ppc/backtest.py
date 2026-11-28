"""How well the forecast would have done on this account's own past — the
kit's backtest under the platform's name (`yaaf.ppc.kit.backtest`)."""

from __future__ import annotations

from yaaf.ppc.kit import BACKTEST_MIN_DAYS as MIN_DAYS
from yaaf.ppc.kit import BACKTEST_MIN_TESTED as MIN_TESTED
from yaaf.ppc.kit import BACKTEST_MOVED as MOVED
from yaaf.ppc.kit import DEFAULT_BAND
from yaaf.ppc.kit import backtest as run

__all__ = ["DEFAULT_BAND", "MIN_DAYS", "MIN_TESTED", "MOVED", "run"]
