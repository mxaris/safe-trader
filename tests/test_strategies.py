import pytest

from safe_trader.models import Signal
from safe_trader.strategies import build_strategy
from safe_trader.strategies.rsi_reversion import RsiReversion
from safe_trader.strategies.sma_cross import SmaCross
from tests.conftest import make_candles


def test_sma_cross_signals():
    s = SmaCross(fast=2, slow=4)
    down_then_up = make_candles([10, 9, 8, 7, 6, 12])
    assert s.generate(down_then_up) is Signal.BUY
    up_then_down = make_candles([6, 7, 8, 9, 10, 4])
    assert s.generate(up_then_down) is Signal.SELL
    assert s.generate(make_candles([1, 2, 3, 4, 5, 6])) is Signal.HOLD


def test_not_enough_data_holds():
    assert SmaCross(2, 4).generate(make_candles([1, 2])) is Signal.HOLD


def test_rsi_reversion_buy_on_recovery():
    s = RsiReversion(period=3, oversold=30, overbought=70)
    closes = [100] * 8 + [99, 98, 97, 96, 95, 96.5]
    assert s.generate(make_candles(closes)) is Signal.BUY


def test_registry():
    assert isinstance(build_strategy("sma_cross", {"fast": 5, "slow": 10}), SmaCross)
    with pytest.raises(ValueError):
        build_strategy("nope")
    with pytest.raises(ValueError):
        SmaCross(fast=10, slow=5)
