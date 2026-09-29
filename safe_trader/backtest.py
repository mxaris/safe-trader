from __future__ import annotations

from dataclasses import dataclass, field

from safe_trader.broker import PaperBroker
from safe_trader.config import PaperConfig, RiskConfig
from safe_trader.engine import TradingEngine
from safe_trader.models import Candle, Fill
from safe_trader.risk import RiskManager
from safe_trader.strategies.base import Strategy


@dataclass
class Trade:
    entry: Fill
    exit: Fill

    @property
    def pnl(self) -> float:
        return (self.exit.value - self.exit.fee) - (self.entry.value + self.entry.fee)

    @property
    def return_pct(self) -> float:
        return self.pnl / (self.entry.value + self.entry.fee)


@dataclass
class BacktestResult:
    starting_equity: float
    final_equity: float
    buy_and_hold_return: float
    max_drawdown: float
    trades: list[Trade] = field(default_factory=list)
    fills: list[Fill] = field(default_factory=list)
    equity_curve: list[tuple[int, float]] = field(default_factory=list)
    halted: bool = False

    @property
    def total_return(self) -> float:
        return self.final_equity / self.starting_equity - 1

    @property
    def win_rate(self) -> float:
        return sum(t.pnl > 0 for t in self.trades) / len(self.trades) if self.trades else 0.0

    @property
    def profit_factor(self) -> float:
        gains = sum(t.pnl for t in self.trades if t.pnl > 0)
        losses = -sum(t.pnl for t in self.trades if t.pnl < 0)
        if losses == 0:
            return float("inf") if gains > 0 else 0.0
        return gains / losses

    @property
    def fees_paid(self) -> float:
        return sum(f.fee for f in self.fills)

    def summary(self) -> str:
        pf = self.profit_factor
        lines = [
            f"Starting equity : {self.starting_equity:,.2f}",
            f"Final equity    : {self.final_equity:,.2f}",
            f"Total return    : {self.total_return:+.2%}",
            f"Buy & hold      : {self.buy_and_hold_return:+.2%}",
            f"Max drawdown    : {self.max_drawdown:.2%}",
            f"Trades          : {len(self.trades)}",
            f"Win rate        : {self.win_rate:.1%}",
            f"Profit factor   : {'inf' if pf == float('inf') else f'{pf:.2f}'}",
            f"Fees paid       : {self.fees_paid:,.2f}",
        ]
        if self.halted:
            lines.append("Kill switch     : TRIGGERED (trading halted during backtest)")
        return "\n".join(lines)


def run_backtest(
    candles: list[Candle],
    strategy: Strategy,
    risk_config: RiskConfig,
    paper_config: PaperConfig,
    symbol: str = "BTC/USDT",
) -> BacktestResult:
    if len(candles) < strategy.warmup + 1:
        raise ValueError(f"need at least {strategy.warmup + 1} candles, got {len(candles)}")

    broker = PaperBroker(paper_config)
    risk = RiskManager(risk_config)
    fills: list[Fill] = []
    engine = TradingEngine(symbol, strategy, risk, broker, on_fill=fills.append)

    start = paper_config.starting_cash
    curve: list[tuple[int, float]] = []
    peak, max_dd = start, 0.0
    window = strategy.warmup
    for i, candle in enumerate(candles):
        engine.on_candles(candles[max(0, i + 1 - window): i + 1])
        equity = engine.equity(candle.close)
        curve.append((candle.timestamp, equity))
        peak = max(peak, equity)
        max_dd = max(max_dd, 1 - equity / peak)

    last = candles[-1]
    engine.close_all(last.close, last.timestamp, "end_of_backtest")
    final = engine.equity(last.close)
    if curve:
        curve[-1] = (last.timestamp, final)
        max_dd = max(max_dd, 1 - final / peak)

    trades, entry = [], None
    for f in fills:
        if f.side == "buy":
            entry = f
        elif entry is not None:
            trades.append(Trade(entry, f))
            entry = None

    return BacktestResult(
        starting_equity=start,
        final_equity=final,
        buy_and_hold_return=last.close / candles[0].close - 1,
        max_drawdown=max_dd,
        trades=trades,
        fills=fills,
        equity_curve=curve,
        halted=risk.state.halted,
    )
