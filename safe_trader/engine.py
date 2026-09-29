from __future__ import annotations

import logging
from typing import Callable, Sequence

from safe_trader.broker import Broker
from safe_trader.models import Candle, Fill, Position, Signal
from safe_trader.risk import RiskManager
from safe_trader.strategies.base import Strategy

log = logging.getLogger(__name__)


class TradingEngine:
    """Glues strategy, risk manager and broker together for a single symbol.

    Spot, long-only: a BUY opens a position when flat, a SELL closes it.
    """

    def __init__(
        self,
        symbol: str,
        strategy: Strategy,
        risk: RiskManager,
        broker: Broker,
        position: Position | None = None,
        on_fill: Callable[[Fill], None] | None = None,
    ):
        self.symbol = symbol
        self.strategy = strategy
        self.risk = risk
        self.broker = broker
        self.position = position
        self.on_fill = on_fill

    def equity(self, price: float) -> float:
        quote, _ = self.broker.balances(self.symbol)
        held = self.position.amount if self.position else 0.0
        return quote + held * price

    def on_candles(self, candles: Sequence[Candle]) -> list[Fill]:
        """Process the most recent *closed* candle (``candles[-1]``)."""
        last = candles[-1]
        ts = last.timestamp
        fills: list[Fill] = []

        # 1. Protective exits first, using the stop that was active during this candle.
        if self.position:
            exit_ = self.risk.protective_exit(self.position, last)
            if exit_:
                fills.append(self._close(exit_[1], ts, exit_[0]))

        # 2. Account-level limits.
        equity = self.equity(last.close)
        self.risk.update_equity(equity, ts)

        # 3. Strategy.
        signal = self.strategy.generate(candles)
        if self.position:
            if self.risk.state.halted:
                fills.append(self._close(last.close, ts, "kill_switch"))
            elif signal is Signal.SELL:
                fills.append(self._close(last.close, ts, "signal"))
            else:
                self.risk.trail(self.position, last.high)
        elif signal is Signal.BUY:
            fill = self._open(last.close, ts, equity)
            if fill:
                fills.append(fill)
        return fills

    def on_price(self, price: float, ts: int) -> list[Fill]:
        """Intra-candle check with a live price: stops, targets and kill switch only."""
        if not self.position:
            return []
        tick = Candle(ts, price, price, price, price, 0.0)
        exit_ = self.risk.protective_exit(self.position, tick)
        if exit_:
            return [self._close(price, ts, exit_[0])]
        self.risk.update_equity(self.equity(price), ts)
        if self.risk.state.halted:
            return [self._close(price, ts, "kill_switch")]
        self.risk.trail(self.position, price)
        return []

    def close_all(self, price: float, ts: int, reason: str) -> list[Fill]:
        return [self._close(price, ts, reason)] if self.position else []

    def _open(self, price: float, ts: int, equity: float) -> Fill | None:
        allowed, why = self.risk.can_open(equity)
        if not allowed:
            log.info("BUY signal ignored: %s", why)
            return None
        quote, _ = self.broker.balances(self.symbol)
        amount = self.risk.size_position(equity, quote, price)
        if amount <= 0:
            log.info("BUY signal ignored: position size below minimum")
            return None
        fill = self.broker.market_buy(self.symbol, amount, price, ts, "signal")
        if fill.amount <= 0:
            log.warning("buy order returned no fill")
            return None
        self.position = self.risk.open_position(self.symbol, fill.amount, fill.price, ts)
        log.info(
            "OPEN %s %.8f @ %.2f stop=%.2f tp=%s",
            self.symbol, fill.amount, fill.price, self.position.stop_price,
            f"{self.position.take_profit_price:.2f}" if self.position.take_profit_price else "-",
        )
        self._emit(fill)
        return fill

    def _close(self, price: float, ts: int, reason: str) -> Fill:
        assert self.position is not None
        fill = self.broker.market_sell(self.symbol, self.position.amount, price, ts, reason)
        pnl = fill.value - fill.fee - self.position.amount * self.position.entry_price
        log.info("CLOSE %s %.8f @ %.2f reason=%s pnl~%.2f",
                 self.symbol, fill.amount, fill.price, reason, pnl)
        self.position = None
        self._emit(fill)
        return fill

    def _emit(self, fill: Fill) -> None:
        if self.on_fill:
            self.on_fill(fill)
