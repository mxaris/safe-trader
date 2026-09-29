# safe-trader

A risk-first automated crypto trading bot. It trades **spot, long-only** (no leverage, no shorting) on 100+ exchanges through [ccxt](https://github.com/ccxt/ccxt). It runs in **paper mode by default**.

> **Disclaimer.** This is software, not financial advice. Automated trading can lose money fast, and past backtest results don't predict future returns. Before you risk real funds, run the bot in paper mode for weeks. Then start with an amount you can afford to lose.

## Features

- **Backtesting** on exchange history or a CSV file, including fees, slippage, and conservative stop fills. Reports return, drawdown, win rate, profit factor, and a buy-and-hold comparison.
- **Paper trading** against live market prices with simulated fills.
- **Live trading** through ccxt market orders, with extra opt-ins (see below).
- **Pluggable strategies**: `sma_cross` (trend following) and `rsi_reversion` (mean reversion). You can add your own in about 30 lines.
- **Risk manager** (the core of the project):

  | Rule | Config key | Default |
  |---|---|---|
  | Position sized so a stop-out loses at most X% of equity | `risk_per_trade` | 1% |
  | Max share of equity in one position | `max_position_pct` | 25% |
  | Mandatory stop loss | `stop_loss_pct` | 3% |
  | Take profit (optional) | `take_profit_pct` | 6% |
  | Trailing stop (optional) | `trailing_stop_pct` | off |
  | No new entries for the rest of the UTC day | `max_daily_loss_pct` | 5% |
  | **Kill switch**: close the position and halt until manually reset | `max_drawdown_pct` | 20% |

  Unsafe values are rejected at startup: no stop loss, over 100% exposure, or more than 10% risk per trade.
- **Crash-safe state**: the open position, risk state, and paper balances go to `state/state.json` with atomic writes. Every fill is added to `state/trades.csv`. After a restart the bot keeps managing the open position. In live mode it first checks that position against the real exchange balance.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp config.example.yaml config.yaml      # edit symbol, exchange, strategy, risk

# 1. Backtest on a year of history (public data, no API keys needed)
safe-trader backtest --days 365
safe-trader backtest --strategy rsi_reversion --since 2024-01-01 --trades-out trades.csv

# Or download once and iterate offline
safe-trader fetch --days 730 -o data/btc_1h.csv
safe-trader backtest --csv data/btc_1h.csv

# 2. Paper trade with live prices (simulated money)
safe-trader run

# 3. Inspect state / clear a triggered kill switch
safe-trader status
safe-trader reset-halt
```

## Running with Docker

The image runs the bot as a non-root user and shuts down cleanly on `docker stop` (state is saved; an open position is resumed on the next start). Your `config.yaml` is mounted at runtime and never baked into the image. Credentials come from `.env`.

```bash
cp config.example.yaml config.yaml     # edit it
cp .env.example .env                   # only needed for live mode

docker compose up -d --build           # paper trading, restarts automatically
docker compose logs -f                 # follow trades and errors
docker compose run --rm safe-trader status
docker compose run --rm safe-trader reset-halt
docker compose down                    # stop (state volume is kept)
```

Backtests and data downloads use `./data`:

```bash
docker compose run --rm safe-trader fetch --days 730 -o /app/data/btc_1h.csv
docker compose run --rm safe-trader backtest --csv /app/data/btc_1h.csv
```

The container runs as UID 10001. On Linux, if `fetch` can't write to `./data`, run `sudo chown 10001 data` once.

For **live trading** with Docker, set `mode: live` in `config.yaml` **and** uncomment the `command: [... "--live"]` line in `docker-compose.yml`.

Bot state (position, risk state, `trades.csv`) lives in the `safe-trader-state` named volume. The container reports **unhealthy** if the bot hasn't saved state successfully for 15 minutes, for example when the exchange stays unreachable. That check assumes `poll_seconds` is well under 15 minutes.

Without Compose:

```bash
docker build -t safe-trader .
docker run -d --name safe-trader --restart unless-stopped \
  -v "$PWD/config.yaml:/app/config.yaml:ro" -v safe-trader-state:/app/state \
  --env-file .env safe-trader
```

## Going live

Live trading needs **all** of the following, so it can't happen by accident:

1. `mode: live` in `config.yaml`
2. The `--live` flag: `safe-trader run --live`
3. API credentials in the environment (never in the config file):
   ```bash
   export SAFE_TRADER_API_KEY=...
   export SAFE_TRADER_API_SECRET=...
   # export SAFE_TRADER_API_PASSWORD=...   # OKX, KuCoin, etc.
   ```

Recommendations:
- Create API keys with **trade permission only** and never enable withdrawals. Restrict them by IP if the exchange allows it.
- Start with `exchange.sandbox: true` on an exchange that has a testnet (e.g. Binance, Bybit).
- Use a dedicated sub-account that holds only the funds the bot may trade.
- Run under a process supervisor (the provided Docker Compose setup or systemd) and watch the logs.

### Coinbase

Backtesting and paper trading need **no API key**: they only read public prices. You need a key only for live trading.

1. Set up the config:
   ```yaml
   exchange:
     id: coinbase
     sandbox: false        # Coinbase has no testnet
   symbol: BTC/USD         # or BTC/USDC, BTC/EUR, ETH/USD ...
   paper:
     fee_rate: 0.006       # use your real taker fee tier; small accounts pay up to 1.2%
   ```
2. Paper trade for a while: `safe-trader run`.
3. For live trading, create a key in the [Coinbase Developer Platform](https://portal.cdp.coinbase.com/) (API keys → Secret API Keys → Create):
   - Permissions: **View** and **Trade** only. Never enable **Transfer**.
   - Under advanced settings choose the **ECDSA** signature algorithm, and add an IP allowlist if your server has a fixed IP.
   - Put the key **name** (`organizations/.../apiKeys/...`) in `SAFE_TRADER_API_KEY`. Put the **private key** in `SAFE_TRADER_API_SECRET`, on one line with `\n` for the line breaks (see `.env.example`).

Fees matter on Coinbase. The bot places market orders, so it pays the taker fee on every entry and exit. At the lowest tiers that's roughly 1–2.4% per round trip, which can wipe out a strategy's edge. Always backtest with your real `fee_rate`.

## How it works

```
exchange ──OHLCV──▶ Runner ──closed candles──▶ TradingEngine ──▶ Strategy (BUY/SELL/HOLD)
                      │                           │
                      └─ live price between ──▶   ├──▶ RiskManager (size, stops, limits, kill switch)
                         candles (stops only)     └──▶ Broker (PaperBroker | LiveBroker via ccxt)
```

- Strategies only see **closed** candles, and signals execute at that candle's close.
- Between candles, the runner polls the ticker every `poll_seconds` to enforce stops, targets, and the kill switch.
- In backtests, a candle that touches both the stop and the target counts as a **stop**. A gap through the stop fills at the open. Both choices make results slightly pessimistic.
- On exchange errors, the runner backs off exponentially (up to 5 minutes) and keeps going. On Ctrl-C or SIGTERM it saves state and exits without closing the position.

## Writing a strategy

```python
# safe_trader/strategies/my_strategy.py
from safe_trader.indicators import ema
from safe_trader.models import Signal
from safe_trader.strategies.base import Strategy

class EmaTrend(Strategy):
    name = "ema_trend"

    def __init__(self, period: int = 100):
        self.period = period

    @property
    def warmup(self) -> int:
        return self.period + 1

    def generate(self, candles):
        if len(candles) < self.warmup:
            return Signal.HOLD
        closes = [c.close for c in candles]
        e = ema(closes, self.period)
        if closes[-2] <= e[-2] and closes[-1] > e[-1]:
            return Signal.BUY
        if closes[-1] < e[-1]:
            return Signal.SELL
        return Signal.HOLD
```

Register it in `safe_trader/strategies/__init__.py` (`STRATEGIES`) and set `strategy.name: ema_trend`. Strategies only choose direction. Sizing and exits stay with the risk manager.

## Development

```bash
pytest -q
```

## Limitations / roadmap

- One symbol per process. To trade several pairs, run several processes, each with its own `state_dir`.
- Stops are enforced by polling, not by exchange-side stop orders. If the bot is offline, nothing protects the position.
- No notifications yet (Telegram/email), and no web dashboard.
- No walk-forward or parameter optimisation tools.
