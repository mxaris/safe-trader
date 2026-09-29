import pytest

from safe_trader.config import RiskConfig
from safe_trader.models import Candle
from safe_trader.risk import RiskManager
from tests.conftest import HOUR, T0


def rm(**kw):
    return RiskManager(RiskConfig(**kw))


def test_size_by_risk():
    r = rm(risk_per_trade=0.01, stop_loss_pct=0.05, max_position_pct=1.0)
    # risk 10 of 1000 with a 5% stop => 200 of exposure => 2 units at 100
    assert r.size_position(1000, 1000, 100) == pytest.approx(2.0)


def test_size_capped_by_exposure_and_cash():
    r = rm(risk_per_trade=0.05, stop_loss_pct=0.01, max_position_pct=0.25)
    assert r.size_position(1000, 1000, 100) == pytest.approx(2.5)
    assert r.size_position(1000, 100, 100) == pytest.approx(0.99)


def test_min_order_value():
    assert rm(min_order_value=50).size_position(100, 100, 10) == 0.0


def test_stop_and_target():
    r = rm(stop_loss_pct=0.05, take_profit_pct=0.10)
    pos = r.open_position("BTC/USDT", 1, 100, T0)
    assert pos.stop_price == pytest.approx(95)
    assert r.protective_exit(pos, Candle(T0, 100, 101, 99, 100, 0)) is None
    assert r.protective_exit(pos, Candle(T0, 100, 111, 94, 100, 0)) == ("stop_loss", 95)
    assert r.protective_exit(pos, Candle(T0, 90, 91, 89, 90, 0)) == ("stop_loss", 90)  # gap
    reason, price = r.protective_exit(pos, Candle(T0, 100, 112, 99, 110, 0))
    assert reason == "take_profit" and price == pytest.approx(110)


def test_trailing_stop_only_moves_up():
    r = rm(stop_loss_pct=0.05, trailing_stop_pct=0.05)
    pos = r.open_position("BTC/USDT", 1, 100, T0)
    r.trail(pos, 120)
    assert pos.stop_price == pytest.approx(114)
    r.trail(pos, 110)
    assert pos.stop_price == pytest.approx(114)


def test_daily_loss_limit_resets_next_day():
    r = rm(max_daily_loss_pct=0.05, max_drawdown_pct=0.5)
    r.update_equity(1000, T0)
    r.update_equity(940, T0 + HOUR)
    assert r.can_open(940) == (False, "daily loss limit reached")
    r.update_equity(940, T0 + 24 * HOUR)
    assert r.can_open(940)[0]


def test_kill_switch_is_sticky():
    r = rm(max_drawdown_pct=0.2, max_daily_loss_pct=1.0)
    r.update_equity(1000, T0)
    r.update_equity(790, T0 + HOUR)
    assert r.state.halted
    r.update_equity(2000, T0 + 2 * HOUR)
    assert not r.can_open(2000)[0]
    r.reset_halt()
    assert r.can_open(2000)[0]


@pytest.mark.parametrize("field,value", [
    ("stop_loss_pct", 0), ("max_position_pct", 1.5), ("risk_per_trade", 0.5),
])
def test_config_rejects_unsafe_values(field, value):
    with pytest.raises(ValueError):
        RiskConfig(**{field: value}).validate()
