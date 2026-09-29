from __future__ import annotations

from typing import Sequence

from safe_trader.indicators import rsi
from safe_trader.models import Candle, Signal
from safe_trader.strategies.base import Strategy


class RsiReversion(Strategy):
    """Buy when RSI recovers up through the oversold level, sell when overbought."""

    name = "rsi_reversion"

    def __init__(self, period: int = 14, oversold: float = 30, overbought: float = 70):
        if not 0 < oversold < overbought < 100:
            raise ValueError("need 0 < oversold < overbought < 100")
        self.period = period
        self.oversold = oversold
        self.overbought = overbought

    @property
    def warmup(self) -> int:
        # Extra history lets Wilder smoothing settle before we trust the value.
        return self.period * 3 + 2

    def generate(self, candles: Sequence[Candle]) -> Signal:
        if len(candles) < self.warmup:
            return Signal.HOLD
        values = rsi([c.close for c in candles[-self.warmup:]], self.period)
        prev, cur = values[-2], values[-1]
        if prev < self.oversold <= cur:
            return Signal.BUY
        if cur >= self.overbought:
            return Signal.SELL
        return Signal.HOLD
