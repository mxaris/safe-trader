from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from safe_trader.config import RiskConfig
from safe_trader.models import Candle, Position

log = logging.getLogger(__name__)

# Keep a little cash aside so fees and slippage never push an order over the balance.
CASH_BUFFER = 0.99


def utc_day(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


@dataclass
class RiskState:
    peak_equity: float | None = None
    day: str | None = None
    day_start_equity: float | None = None
    halted: bool = False
    halt_reason: str | None = None


class RiskManager:
    """Decides whether and how much to trade, and when to force an exit.

    Rules enforced:
      * every position has a stop loss; size is chosen so hitting it loses
        ``risk_per_trade`` of equity, capped at ``max_position_pct`` and cash;
      * optional take profit and trailing stop;
      * daily loss limit: no new entries for the rest of the UTC day;
      * max drawdown kill switch: flatten and halt until manually reset.
    """

    def __init__(self, config: RiskConfig, state: RiskState | None = None):
        config.validate()
        self.config = config
        self.state = state or RiskState()

    # --- account-level limits -------------------------------------------------

    def update_equity(self, equity: float, timestamp_ms: int) -> None:
        s = self.state
        day = utc_day(timestamp_ms)
        if s.day != day:
            s.day, s.day_start_equity = day, equity
        if s.peak_equity is None or equity > s.peak_equity:
            s.peak_equity = equity
        if not s.halted and s.peak_equity > 0:
            drawdown = 1 - equity / s.peak_equity
            if drawdown >= self.config.max_drawdown_pct:
                s.halted = True
                s.halt_reason = (
                    f"max drawdown {drawdown:.1%} >= {self.config.max_drawdown_pct:.1%}"
                )
                log.error("KILL SWITCH: %s. Trading halted.", s.halt_reason)

    def daily_loss_hit(self, equity: float) -> bool:
        start = self.state.day_start_equity
        if not start:
            return False
        return (start - equity) / start >= self.config.max_daily_loss_pct

    def can_open(self, equity: float) -> tuple[bool, str]:
        if self.state.halted:
            return False, f"halted: {self.state.halt_reason}"
        if self.daily_loss_hit(equity):
            return False, "daily loss limit reached"
        return True, "ok"

    def reset_halt(self) -> None:
        self.state.halted = False
        self.state.halt_reason = None
        self.state.peak_equity = None

    # --- per-trade rules ------------------------------------------------------

    def size_position(self, equity: float, cash: float, price: float) -> float:
        """Amount of base currency to buy at ``price``. Returns 0 to skip."""
        if price <= 0 or equity <= 0:
            return 0.0
        c = self.config
        risk_amount = equity * c.risk_per_trade
        by_risk = risk_amount / (price * c.stop_loss_pct)
        by_exposure = equity * c.max_position_pct / price
        by_cash = cash * CASH_BUFFER / price
        amount = max(0.0, min(by_risk, by_exposure, by_cash))
        if amount * price < c.min_order_value:
            return 0.0
        return amount

    def open_position(self, symbol: str, amount: float, entry_price: float, ts: int) -> Position:
        c = self.config
        tp = entry_price * (1 + c.take_profit_pct) if c.take_profit_pct else None
        return Position(
            symbol=symbol,
            amount=amount,
            entry_price=entry_price,
            stop_price=entry_price * (1 - c.stop_loss_pct),
            take_profit_price=tp,
            opened_at=ts,
        )

    def trail(self, position: Position, high: float) -> None:
        if self.config.trailing_stop_pct:
            candidate = high * (1 - self.config.trailing_stop_pct)
            if candidate > position.stop_price:
                position.stop_price = candidate

    def protective_exit(self, position: Position, candle: Candle) -> tuple[str, float] | None:
        """Check stop / take profit against a candle; return (reason, fill price).

        Conservative assumptions: gaps fill at the open, and if both stop and
        target are inside one candle we assume the stop was hit first.
        """
        if candle.low <= position.stop_price:
            return "stop_loss", min(position.stop_price, candle.open)
        tp = position.take_profit_price
        if tp is not None and candle.high >= tp:
            return "take_profit", max(tp, candle.open)
        return None
