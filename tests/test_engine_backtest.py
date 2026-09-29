import pytest

from safe_trader.backtest import run_backtest
from safe_trader.broker import PaperBroker
from safe_trader.config import PaperConfig, RiskConfig
from safe_trader.engine import TradingEngine
from safe_trader.models import Candle, Signal
from safe_trader.risk import RiskManager
from safe_trader.strategies import SmaCross
from safe_trader.strategies.base import Strategy
from tests.conftest import HOUR, T0


class Scripted(Strategy):
    name = "scripted"

    def __init__(self, signals):
        self.signals = list(signals)

    @property
    def warmup(self):
        return 1

    def generate(self, candles):
        return self.signals.pop(0) if self.signals else Signal.HOLD


def engine(signals, **risk):
    risk = {"max_position_pct": 1.0, "risk_per_trade": 0.1, "stop_loss_pct": 0.1,
            "take_profit_pct": 0, **risk}
    broker = PaperBroker(PaperConfig(starting_cash=1000, fee_rate=0, slippage=0))
    return TradingEngine("BTC/USDT", Scripted(signals), RiskManager(RiskConfig(**risk)), broker)


def c(i, o, h, low, cl):
    return Candle(T0 + i * HOUR, o, h, low, cl, 1)


def test_buy_then_signal_sell():
    e = engine([Signal.BUY, Signal.HOLD, Signal.SELL])
    assert e.on_candles([c(0, 100, 100, 100, 100)])[0].side == "buy"
    assert e.position.stop_price == pytest.approx(90)
    e.on_candles([c(1, 100, 105, 99, 105)])
    fills = e.on_candles([c(2, 105, 110, 104, 110)])
    assert fills[0].reason == "signal" and e.position is None
    assert e.equity(110) == pytest.approx(1000 + 990 / 100 * 10)


def test_stop_loss_exit():
    e = engine([Signal.BUY])
    e.on_candles([c(0, 100, 100, 100, 100)])
    fills = e.on_candles([c(1, 100, 100, 80, 85)])
    assert fills[0].reason == "stop_loss" and fills[0].price == pytest.approx(90)
    assert e.equity(85) == pytest.approx(1000 - 990 / 100 * 10)


def test_kill_switch_flattens():
    e = engine([Signal.BUY], stop_loss_pct=0.5, max_drawdown_pct=0.05, max_daily_loss_pct=1.0)
    e.on_candles([c(0, 100, 100, 100, 100)])
    fills = e.on_candles([c(1, 100, 100, 70, 70)])  # 2 units * -30 = -6%
    assert fills[0].reason == "kill_switch" and e.position is None
    assert e.risk.state.halted


def test_on_price_stop():
    e = engine([Signal.BUY])
    e.on_candles([c(0, 100, 100, 100, 100)])
    assert e.on_price(95, T0 + HOUR) == []
    assert e.on_price(89, T0 + HOUR)[0].reason == "stop_loss"


def test_backtest_runs_and_respects_risk(walk_candles):
    risk = RiskConfig(risk_per_trade=0.01, max_position_pct=0.25, stop_loss_pct=0.03)
    result = run_backtest(walk_candles, SmaCross(10, 30), risk, PaperConfig(starting_cash=1000))
    assert result.trades, "expected the strategy to trade on the synthetic series"
    assert len(result.equity_curve) == len(walk_candles)
    for t in result.trades:
        assert t.entry.value <= 0.25 * 1000 * 1.5  # exposure cap (equity can grow a bit)
        if t.exit.reason == "stop_loss":
            # a stopped-out trade loses roughly risk_per_trade of equity, never much more
            assert t.pnl > -0.02 * result.starting_equity * 1.5
    assert 0 <= result.max_drawdown < 0.2
    assert "Total return" in result.summary()


def test_backtest_needs_enough_data(walk_candles):
    with pytest.raises(ValueError):
        run_backtest(walk_candles[:10], SmaCross(10, 30), RiskConfig(), PaperConfig())
