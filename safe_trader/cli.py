from __future__ import annotations

import argparse
import csv
import logging
import sys
from datetime import datetime, timedelta, timezone

from safe_trader.backtest import run_backtest
from safe_trader.config import Config, ExchangeConfig, load_config
from safe_trader.data import fetch_history, load_csv, save_csv
from safe_trader.risk import RiskState
from safe_trader.storage import StateStore
from safe_trader.strategies import STRATEGIES, build_strategy

LIVE_WARNING = """\
============================================================
  LIVE TRADING: real orders with real money on {exchange}{sandbox}
  Symbol {symbol}  |  risk/trade {risk:.1%}  |  kill switch at -{dd:.0%}
  Automated trading can lose money quickly. Use API keys WITHOUT
  withdrawal permission and only funds you can afford to lose.
============================================================"""


def _since_ms(args) -> int:
    if args.since:
        dt = datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc)
    else:
        dt = datetime.now(timezone.utc) - timedelta(days=args.days)
    return int(dt.timestamp() * 1000)


def _public_exchange(cfg: Config):
    """Real (non-sandbox) exchange without keys: testnets have unrealistic history."""
    from safe_trader.broker import make_exchange

    return make_exchange(ExchangeConfig(id=cfg.exchange.id, sandbox=False))


def _load(args) -> Config:
    cfg = load_config(args.config)
    if getattr(args, "strategy", None):
        cfg.strategy.name = args.strategy
        cfg.strategy.params = {}
    return cfg


def cmd_fetch(args) -> int:
    cfg = load_config(args.config)
    candles = fetch_history(_public_exchange(cfg), args.symbol or cfg.symbol,
                            args.timeframe or cfg.timeframe, _since_ms(args))
    save_csv(candles, args.out)
    print(f"saved {len(candles)} candles to {args.out}")
    return 0


def cmd_backtest(args) -> int:
    if not args.verbose:
        logging.getLogger("safe_trader.engine").setLevel(logging.WARNING)
    cfg = _load(args)
    if args.csv:
        candles = load_csv(args.csv)
    else:
        candles = fetch_history(_public_exchange(cfg), cfg.symbol, cfg.timeframe, _since_ms(args))
    strategy = build_strategy(cfg.strategy.name, cfg.strategy.params)
    result = run_backtest(candles, strategy, cfg.risk, cfg.paper, cfg.symbol)
    print(f"{cfg.symbol} {cfg.timeframe} | {len(candles)} candles | strategy={strategy.name}")
    print(result.summary())
    if args.trades_out:
        with open(args.trades_out, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["entry_ts", "entry_price", "exit_ts", "exit_price", "amount",
                        "exit_reason", "pnl", "return_pct"])
            for t in result.trades:
                w.writerow([t.entry.timestamp, t.entry.price, t.exit.timestamp, t.exit.price,
                            t.entry.amount, t.exit.reason, round(t.pnl, 8),
                            round(t.return_pct, 6)])
        print(f"trades written to {args.trades_out}")
    return 0


def cmd_run(args) -> int:
    from safe_trader.runner import build_runner

    cfg = _load(args)
    if cfg.mode == "live":
        if not args.live:
            print("config has mode: live, but --live was not passed. Refusing to trade.",
                  file=sys.stderr)
            return 2
        print(LIVE_WARNING.format(
            exchange=cfg.exchange.id, sandbox=" (SANDBOX)" if cfg.exchange.sandbox else "",
            symbol=cfg.symbol, risk=cfg.risk.risk_per_trade, dd=cfg.risk.max_drawdown_pct))
    elif args.live:
        print("--live passed but config mode is 'paper'; set mode: live to trade for real.",
              file=sys.stderr)
        return 2
    build_runner(cfg).run_forever()
    return 0


def cmd_status(args) -> int:
    cfg = load_config(args.config)
    data = StateStore(cfg.state_path).load()
    if not data:
        print("no saved state")
        return 0
    pos = StateStore.position_from(data)
    risk = StateStore.risk_state_from(data)
    print(f"position : {pos.to_dict() if pos else 'flat'}")
    print(f"halted   : {risk.halted}" + (f" ({risk.halt_reason})" if risk.halted else ""))
    print(f"peak eq. : {risk.peak_equity}")
    if data.get("paper_balances"):
        q, b = data["paper_balances"]
        print(f"paper    : quote={q:.2f} base={b:.8f}")
    return 0


def cmd_reset_halt(args) -> int:
    cfg = load_config(args.config)
    store = StateStore(cfg.state_path)
    data = store.load()
    risk = StateStore.risk_state_from(data)
    if not risk.halted:
        print("not halted")
        return 0
    risk = RiskState(day=risk.day, day_start_equity=risk.day_start_equity)
    balances = data.get("paper_balances")
    store.save(StateStore.position_from(data), risk, data.get("last_candle_ts"),
               tuple(balances) if balances else None)
    print("kill switch reset; peak equity will be re-measured from the next update")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="safe-trader", description="Risk-first automated crypto trading bot.")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)

    def with_config(sp):
        sp.add_argument("-c", "--config", default="config.yaml")
        return sp

    def with_range(sp):
        g = sp.add_mutually_exclusive_group()
        g.add_argument("--since", help="start date, e.g. 2024-01-01")
        g.add_argument("--days", type=int, default=365, help="days of history (default 365)")

    f = with_config(sub.add_parser("fetch", help="download OHLCV history to CSV"))
    f.add_argument("--symbol")
    f.add_argument("--timeframe")
    f.add_argument("-o", "--out", required=True)
    with_range(f)
    f.set_defaults(func=cmd_fetch)

    b = with_config(sub.add_parser("backtest", help="simulate the strategy on history"))
    b.add_argument("--csv", help="use candles from a CSV instead of downloading")
    b.add_argument("--strategy", choices=sorted(STRATEGIES), help="override (default params)")
    b.add_argument("--trades-out", help="write individual trades to this CSV")
    with_range(b)
    b.set_defaults(func=cmd_backtest)

    r = with_config(sub.add_parser("run", help="run the bot (paper by default)"))
    r.add_argument("--live", action="store_true", help="required together with mode: live")
    r.add_argument("--strategy", choices=sorted(STRATEGIES), help="override (default params)")
    r.set_defaults(func=cmd_run)

    s = with_config(sub.add_parser("status", help="show saved position and risk state"))
    s.set_defaults(func=cmd_status)

    h = with_config(sub.add_parser("reset-halt", help="clear a triggered kill switch"))
    h.set_defaults(func=cmd_reset_halt)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    import ccxt

    try:
        return args.func(args)
    except ccxt.BaseError as exc:
        print(f"exchange error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    except (ValueError, RuntimeError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
