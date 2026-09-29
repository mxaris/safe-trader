from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Sequence

from safe_trader.models import Candle, Signal


class Strategy(ABC):
    """A strategy looks at closed candles and says BUY, SELL or HOLD.

    Strategies only decide direction. Position sizing, stops and all other
    risk decisions belong to :class:`safe_trader.risk.RiskManager`.
    """

    name: str = "base"

    @property
    @abstractmethod
    def warmup(self) -> int:
        """Number of candles needed before the strategy can emit signals."""

    @abstractmethod
    def generate(self, candles: Sequence[Candle]) -> Signal:
        """Return a signal for the most recent closed candle."""
