from __future__ import annotations

import logging
import signal
import time

from safe_trader.broker import Broker, LiveBroker, PaperBroker, make_exchange, split_symbol
from safe_trader.config import Config, ExchangeConfig, load_credentials
from safe_trader.data import closed_candles, timeframe_ms, to_candles
from safe_trader.engine import TradingEngine
from safe_trader.risk import RiskManager
from safe_trader.storage import StateStore
from safe_trader.strategies import build_strategy

log = logging.getLogger(__name__)

MAX_BACKOFF_SECONDS = 300


class Runner:
    """Polls the exchange and feeds closed candles / live prices to the engine."""

    def __init__(self, config: Config, exchange, engine: TradingEngine, store: StateStore,
                 last_candle_ts: int | None = None):
        self.config = config
        self.exchange = exchange
        self.engine = engine
        self.store = store
        self.last_candle_ts = last_candle_ts
        self.tf_ms = timeframe_ms(exchange, config.timeframe)
        self._stop = False

    def step(self) -> None:
        cfg = self.config
        now = self.exchange.milliseconds()
        rows = self.exchange.fetch_ohlcv(cfg.symbol, cfg.timeframe,
                                         limit=self.engine.strategy.warmup + 5)
        candles = closed_candles(to_candles(rows), self.tf_ms, now)
        if candles and (self.last_candle_ts is None or candles[-1].timestamp > self.last_candle_ts):
            self.engine.on_candles(candles)
            self.last_candle_ts = candles[-1].timestamp
        elif self.engine.position:
            price = self.exchange.fetch_ticker(cfg.symbol).get("last")
            if price:
                self.engine.on_price(float(price), now)
        self.save()

    def save(self) -> None:
        broker = self.engine.broker
        paper = broker.balances(self.config.symbol) if isinstance(broker, PaperBroker) else None
        self.store.save(self.engine.position, self.engine.risk.state, self.last_candle_ts, paper)

    def stop(self, *_):
        log.info("shutdown requested; finishing current step")
        self._stop = True

    def run_forever(self) -> None:
        signal.signal(signal.SIGINT, self.stop)
        signal.signal(signal.SIGTERM, self.stop)
        backoff = self.config.poll_seconds
        log.info("running %s %s %s in %s mode (strategy=%s)", self.config.exchange.id,
                 self.config.symbol, self.config.timeframe, self.config.mode,
                 self.engine.strategy.name)
        while not self._stop:
            try:
                self.step()
                backoff = self.config.poll_seconds
            except Exception:  # noqa: BLE001 - keep running through transient exchange errors
                log.exception("step failed; retrying in %ss", backoff)
                backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)
            self._sleep(backoff)
        self.save()
        if self.engine.position:
            log.warning("exiting with an OPEN position (state saved); it will be managed on restart")

    def _sleep(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        while not self._stop and time.monotonic() < end:
            time.sleep(min(1.0, end - time.monotonic()))


def build_runner(config: Config) -> Runner:
    store = StateStore(config.state_path)
    saved = store.load()
    position = StateStore.position_from(saved)
    if position and position.symbol != config.symbol:
        raise RuntimeError(f"saved state has an open {position.symbol} position but config "
                           f"trades {config.symbol}; close it or use another state_dir")

    broker: Broker
    if config.mode == "live":
        exchange = make_exchange(config.exchange, load_credentials())
        broker = LiveBroker(exchange)
        position = _reconcile(broker, config.symbol, position)
    else:
        # Paper mode needs only public prices; use the real market, not a testnet.
        exchange = make_exchange(ExchangeConfig(id=config.exchange.id, sandbox=False))
        balances = saved.get("paper_balances")
        broker = PaperBroker(config.paper, *balances) if balances else PaperBroker(config.paper)

    engine = TradingEngine(
        config.symbol,
        build_strategy(config.strategy.name, config.strategy.params),
        RiskManager(config.risk, StateStore.risk_state_from(saved)),
        broker,
        position=position,
        on_fill=store.journal,
    )
    return Runner(config, exchange, engine, store, saved.get("last_candle_ts"))


def _reconcile(broker: Broker, symbol: str, position):
    """Make sure the saved position still exists on the exchange."""
    if not position:
        return None
    _, free_base = broker.balances(symbol)
    base, _ = split_symbol(symbol)
    if free_base < position.amount * 0.999:
        if free_base * position.entry_price < 1:
            log.warning("saved %s position not found on exchange; forgetting it", base)
            return None
        log.warning("only %.8f %s on exchange (state says %.8f); adjusting",
                    free_base, base, position.amount)
        position.amount = free_base
    return position
