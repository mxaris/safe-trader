import pytest

from safe_trader.indicators import ema, rsi, sma


def test_sma():
    assert sma([1, 2, 3, 4, 5], 3) == [None, None, 2, 3, 4]


def test_ema_seeds_with_sma():
    out = ema([1, 2, 3, 4], 2)
    assert out[:2] == [None, 1.5]
    assert out[2] == pytest.approx(3 * 2 / 3 + 1.5 / 3)


def test_rsi_bounds():
    assert rsi(list(range(1, 30)), 14)[-1] == 100.0
    assert rsi(list(range(30, 1, -1)), 14)[-1] == pytest.approx(0.0)
    values = [v for v in rsi([1, 2, 1, 2, 1, 2] * 5, 14) if v is not None]
    assert all(0 <= v <= 100 for v in values)


def test_bad_period():
    with pytest.raises(ValueError):
        sma([1], 0)
