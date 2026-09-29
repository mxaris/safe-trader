from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum


@dataclass(frozen=True)
class Candle:
    timestamp: int  # open time, milliseconds since epoch (UTC)
    open: float
    high: float
    low: float
    close: float
    volume: float


class Signal(Enum):
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"


@dataclass
class Position:
    symbol: str
    amount: float
    entry_price: float
    stop_price: float
    take_profit_price: float | None
    opened_at: int

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Position":
        return cls(**data)


@dataclass(frozen=True)
class Fill:
    symbol: str
    side: str  # "buy" | "sell"
    amount: float
    price: float
    fee: float  # in quote currency
    timestamp: int
    reason: str

    @property
    def value(self) -> float:
        return self.amount * self.price
