from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from pathlib import Path

import yaml


@dataclass
class ExchangeConfig:
    id: str = "binance"
    sandbox: bool = True


@dataclass
class PaperConfig:
    starting_cash: float = 1000.0
    fee_rate: float = 0.001
    slippage: float = 0.0005


@dataclass
class StrategyConfig:
    name: str = "sma_cross"
    params: dict = field(default_factory=dict)


@dataclass
class RiskConfig:
    risk_per_trade: float = 0.01
    max_position_pct: float = 0.25
    stop_loss_pct: float = 0.03
    take_profit_pct: float = 0.06
    trailing_stop_pct: float = 0.0
    max_daily_loss_pct: float = 0.05
    max_drawdown_pct: float = 0.20
    min_order_value: float = 10.0

    def validate(self) -> None:
        def check(name: str, lo: float, hi: float, allow_zero: bool = False) -> None:
            value = getattr(self, name)
            if allow_zero and value == 0:
                return
            if not lo < value <= hi:
                raise ValueError(f"risk.{name}={value} must be in ({lo}, {hi}]")

        check("risk_per_trade", 0, 0.1)  # >10% risk per trade is reckless
        check("max_position_pct", 0, 1.0)  # spot only: no leverage
        check("stop_loss_pct", 0, 0.5)  # a stop is mandatory
        check("take_profit_pct", 0, 10.0, allow_zero=True)
        check("trailing_stop_pct", 0, 0.5, allow_zero=True)
        check("max_daily_loss_pct", 0, 1.0)
        check("max_drawdown_pct", 0, 1.0)
        if self.min_order_value < 0:
            raise ValueError("risk.min_order_value must be >= 0")


@dataclass
class Config:
    exchange: ExchangeConfig = field(default_factory=ExchangeConfig)
    symbol: str = "BTC/USDT"
    timeframe: str = "1h"
    mode: str = "paper"
    poll_seconds: int = 30
    state_dir: str = "./state"
    paper: PaperConfig = field(default_factory=PaperConfig)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)

    def validate(self) -> None:
        if self.mode not in ("paper", "live"):
            raise ValueError("mode must be 'paper' or 'live'")
        if "/" not in self.symbol:
            raise ValueError("symbol must look like BASE/QUOTE, e.g. BTC/USDT")
        if self.poll_seconds < 1:
            raise ValueError("poll_seconds must be >= 1")
        self.risk.validate()

    @property
    def state_path(self) -> Path:
        return Path(self.state_dir)


_SECTIONS = {
    "exchange": ExchangeConfig,
    "paper": PaperConfig,
    "strategy": StrategyConfig,
    "risk": RiskConfig,
}


def _build(cls, data: dict):
    known = {f.name for f in fields(cls)}
    unknown = set(data) - known
    if unknown:
        raise ValueError(f"unknown {cls.__name__} keys: {sorted(unknown)}")
    return cls(**data)


def config_from_dict(data: dict) -> Config:
    data = dict(data or {})
    kwargs = {}
    for key, cls in _SECTIONS.items():
        if key in data:
            kwargs[key] = _build(cls, data.pop(key) or {})
    cfg = _build(Config, {**data, **kwargs})
    cfg.validate()
    return cfg


def load_config(path: str | Path) -> Config:
    with open(path) as fh:
        return config_from_dict(yaml.safe_load(fh))


@dataclass(frozen=True)
class Credentials:
    api_key: str
    secret: str
    password: str | None = None


def load_credentials() -> Credentials:
    """Read API credentials from the environment (never from the config file)."""
    key = os.environ.get("SAFE_TRADER_API_KEY", "").strip()
    secret = os.environ.get("SAFE_TRADER_API_SECRET", "").strip()
    if not key or not secret:
        raise RuntimeError(
            "live mode needs SAFE_TRADER_API_KEY and SAFE_TRADER_API_SECRET in the environment"
        )
    password = os.environ.get("SAFE_TRADER_API_PASSWORD", "").strip() or None
    return Credentials(key, secret, password)
