from __future__ import annotations

from safe_trader.strategies.base import Strategy
from safe_trader.strategies.rsi_reversion import RsiReversion
from safe_trader.strategies.sma_cross import SmaCross

STRATEGIES: dict[str, type[Strategy]] = {
    SmaCross.name: SmaCross,
    RsiReversion.name: RsiReversion,
}


def build_strategy(name: str, params: dict | None = None) -> Strategy:
    try:
        cls = STRATEGIES[name]
    except KeyError:
        raise ValueError(f"unknown strategy {name!r}; choose from {sorted(STRATEGIES)}") from None
    return cls(**(params or {}))


__all__ = ["Strategy", "SmaCross", "RsiReversion", "STRATEGIES", "build_strategy"]
