from __future__ import annotations

from typing import Sequence

from safe_trader.indicators import sma
from safe_trader.models import Candle, Signal
from safe_trader.strategies.base import Strategy


class SmaCross(Strategy):
    """Buy when the fast SMA crosses above the slow SMA, sell on the cross below."""

    name = "sma_cross"

    def __init__(self, fast: int = 20, slow: int = 50):
        if fast >= slow:
            raise ValueError("fast period must be smaller than slow period")
        self.fast = fast
        self.slow = slow

    @property
    def warmup(self) -> int:
        return self.slow + 1

    def generate(self, candles: Sequence[Candle]) -> Signal:
        if len(candles) < self.warmup:
            return Signal.HOLD
        closes = [c.close for c in candles[-self.warmup:]]
        fast, slow = sma(closes, self.fast), sma(closes, self.slow)
        prev_diff = fast[-2] - slow[-2]
        diff = fast[-1] - slow[-1]
        if prev_diff <= 0 < diff:
            return Signal.BUY
        if prev_diff >= 0 > diff:
            return Signal.SELL
        return Signal.HOLD
