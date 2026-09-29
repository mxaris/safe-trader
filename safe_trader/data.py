from __future__ import annotations

import csv
import logging
import time
from pathlib import Path

from safe_trader.models import Candle

log = logging.getLogger(__name__)


def to_candles(rows) -> list[Candle]:
    return [Candle(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5] or 0))
            for r in rows]


def timeframe_ms(exchange, timeframe: str) -> int:
    return exchange.parse_timeframe(timeframe) * 1000


def closed_candles(candles: list[Candle], tf_ms: int, now_ms: int) -> list[Candle]:
    """Drop the still-forming candle at the end, if any."""
    return [c for c in candles if c.timestamp + tf_ms <= now_ms]


def fetch_history(exchange, symbol: str, timeframe: str, since_ms: int,
                  until_ms: int | None = None, batch: int = 1000) -> list[Candle]:
    """Page through public OHLCV history. No API keys required."""
    until_ms = until_ms or exchange.milliseconds()
    tf_ms = timeframe_ms(exchange, timeframe)
    out: dict[int, Candle] = {}
    cursor = since_ms
    while cursor < until_ms:
        rows = exchange.fetch_ohlcv(symbol, timeframe, since=cursor, limit=batch)
        if not rows:
            break
        for c in to_candles(rows):
            if c.timestamp < until_ms:
                out[c.timestamp] = c
        next_cursor = rows[-1][0] + tf_ms
        if next_cursor <= cursor:
            break
        cursor = next_cursor
        log.info("fetched %d candles (up to %s)", len(out), exchange.iso8601(rows[-1][0]))
        time.sleep(exchange.rateLimit / 1000)
    return closed_candles(sorted(out.values(), key=lambda c: c.timestamp), tf_ms, until_ms)


def save_csv(candles: list[Candle], path: str | Path) -> None:
    with open(path, "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["timestamp", "open", "high", "low", "close", "volume"])
        for c in candles:
            writer.writerow([c.timestamp, c.open, c.high, c.low, c.close, c.volume])


def load_csv(path: str | Path) -> list[Candle]:
    with open(path, newline="") as fh:
        reader = csv.reader(fh)
        header = next(reader)
        if header[0].strip().lower() != "timestamp":
            raise ValueError("CSV must start with header: timestamp,open,high,low,close,volume")
        return to_candles(reader)
