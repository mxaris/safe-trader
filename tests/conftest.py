import math
import random

import pytest

from safe_trader.models import Candle

HOUR = 3_600_000
T0 = 1_704_067_200_000  # 2024-01-01T00:00:00Z


def make_candles(closes, start=T0, step=HOUR, spread=0.002):
    out, prev = [], closes[0]
    for i, c in enumerate(closes):
        o = prev
        out.append(Candle(start + i * step, o, max(o, c) * (1 + spread),
                          min(o, c) * (1 - spread), c, 1.0))
        prev = c
    return out


def random_walk(n=2000, seed=7, start=100.0):
    rng = random.Random(seed)
    price, closes = start, []
    for i in range(n):
        price *= math.exp(rng.gauss(0.0002, 0.01) + 0.004 * math.sin(i / 60))
        closes.append(price)
    return closes


@pytest.fixture
def walk_candles():
    return make_candles(random_walk())
