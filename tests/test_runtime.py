import pytest

from safe_trader.cli import main
from safe_trader.config import config_from_dict
from safe_trader.models import Fill, Position
from safe_trader.risk import RiskState
from safe_trader.runner import Runner
from safe_trader.storage import StateStore
from safe_trader.broker import PaperBroker
from safe_trader.engine import TradingEngine
from safe_trader.risk import RiskManager
from safe_trader.strategies import SmaCross
from tests.conftest import HOUR, T0, make_candles, random_walk


def test_config_from_dict_and_unknown_keys():
    cfg = config_from_dict({"symbol": "ETH/USDT", "risk": {"stop_loss_pct": 0.02}})
    assert cfg.symbol == "ETH/USDT" and cfg.risk.stop_loss_pct == 0.02
    with pytest.raises(ValueError):
        config_from_dict({"risk": {"stop_loss": 0.02}})
    with pytest.raises(ValueError):
        config_from_dict({"mode": "yolo"})


def test_state_roundtrip(tmp_path):
    store = StateStore(tmp_path)
    pos = Position("BTC/USDT", 0.5, 100, 95, 110, T0)
    store.save(pos, RiskState(peak_equity=1000, halted=True, halt_reason="x"), T0, (500, 0.5))
    data = store.load()
    assert StateStore.position_from(data) == pos
    assert StateStore.risk_state_from(data).halted
    store.journal(Fill("BTC/USDT", "buy", 0.5, 100, 0.05, T0, "signal"))
    assert store.journal_file.read_text().count("\n") == 2


class FakeExchange:
    def __init__(self, candles, start_index):
        self.candles = candles
        self.now = candles[start_index].timestamp + HOUR  # that candle just closed

    def milliseconds(self):
        return self.now

    def parse_timeframe(self, tf):
        return 3600

    def fetch_ohlcv(self, symbol, timeframe, limit=100, since=None):
        visible = [c for c in self.candles if c.timestamp + HOUR <= self.now]
        # include a still-forming candle, which the runner must ignore
        forming = [[self.now, 1, 1, 1, 1, 0]]
        return [[c.timestamp, c.open, c.high, c.low, c.close, c.volume]
                for c in visible[-limit:]] + forming

    def fetch_ticker(self, symbol):
        return {"last": [c for c in self.candles if c.timestamp < self.now][-1].close}


def test_runner_steps_and_persists(tmp_path):
    cfg = config_from_dict({"state_dir": str(tmp_path), "strategy": {"name": "sma_cross"}})
    candles = make_candles(random_walk(300))
    ex = FakeExchange(candles, start_index=100)
    store = StateStore(tmp_path)
    eng = TradingEngine(cfg.symbol, SmaCross(5, 20), RiskManager(cfg.risk),
                        PaperBroker(cfg.paper), on_fill=store.journal)
    runner = Runner(cfg, ex, eng, store)
    for _ in range(150):
        runner.step()
        runner.step()  # same candle again: only intra-candle price checks, no double-processing
        ex.now += HOUR
    assert runner.last_candle_ts == candles[249].timestamp
    assert store.journal_file.exists(), "expected some trades over 150 candles"
    assert store.load()["paper_balances"] is not None


def test_cli_refuses_live_without_flag(tmp_path, capsys):
    cfg = tmp_path / "c.yaml"
    cfg.write_text(f"mode: live\nstate_dir: {tmp_path}\n")
    assert main(["run", "-c", str(cfg)]) == 2
    assert "Refusing" in capsys.readouterr().err
    paper = tmp_path / "p.yaml"
    paper.write_text("mode: paper\n")
    assert main(["run", "-c", str(paper), "--live"]) == 2


def test_cli_backtest_csv(tmp_path, capsys):
    from safe_trader.data import save_csv

    csv_path = tmp_path / "d.csv"
    save_csv(make_candles(random_walk(500)), csv_path)
    cfg = tmp_path / "c.yaml"
    cfg.write_text("strategy:\n  name: sma_cross\n  params: {fast: 5, slow: 20}\n")
    trades = tmp_path / "t.csv"
    assert main(["backtest", "-c", str(cfg), "--csv", str(csv_path),
                 "--trades-out", str(trades)]) == 0
    assert "Final equity" in capsys.readouterr().out
    assert trades.exists()
