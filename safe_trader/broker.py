from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from safe_trader.config import Credentials, ExchangeConfig, PaperConfig
from safe_trader.models import Fill

log = logging.getLogger(__name__)


def split_symbol(symbol: str) -> tuple[str, str]:
    base, quote = symbol.split("/", 1)
    return base, quote.split(":", 1)[0]


class Broker(ABC):
    @abstractmethod
    def balances(self, symbol: str) -> tuple[float, float]:
        """Return (free quote balance, free base balance)."""

    @abstractmethod
    def market_buy(self, symbol: str, amount: float, price: float, ts: int, reason: str) -> Fill:
        """Buy ``amount`` base. ``price`` is the reference price (used by paper fills)."""

    @abstractmethod
    def market_sell(self, symbol: str, amount: float, price: float, ts: int, reason: str) -> Fill:
        """Sell ``amount`` base."""


class PaperBroker(Broker):
    """Simulated fills with fees and slippage. No exchange connection needed."""

    def __init__(self, config: PaperConfig, quote_balance: float | None = None,
                 base_balance: float = 0.0):
        self.fee_rate = config.fee_rate
        self.slippage = config.slippage
        self.quote = config.starting_cash if quote_balance is None else quote_balance
        self.base = base_balance

    def balances(self, symbol: str) -> tuple[float, float]:
        return self.quote, self.base

    def market_buy(self, symbol, amount, price, ts, reason):
        fill_price = price * (1 + self.slippage)
        cost = amount * fill_price
        fee = cost * self.fee_rate
        if cost + fee > self.quote + 1e-9:
            raise RuntimeError(f"paper: insufficient {symbol} quote balance")
        self.quote -= cost + fee
        self.base += amount
        return Fill(symbol, "buy", amount, fill_price, fee, ts, reason)

    def market_sell(self, symbol, amount, price, ts, reason):
        amount = min(amount, self.base)
        fill_price = price * (1 - self.slippage)
        proceeds = amount * fill_price
        fee = proceeds * self.fee_rate
        self.base -= amount
        self.quote += proceeds - fee
        return Fill(symbol, "sell", amount, fill_price, fee, ts, reason)


def make_exchange(config: ExchangeConfig, credentials: Credentials | None = None):
    import ccxt

    try:
        cls = getattr(ccxt, config.id)
    except AttributeError:
        raise ValueError(f"unknown ccxt exchange id {config.id!r}") from None
    params = {"enableRateLimit": True, "options": {"defaultType": "spot"}}
    if credentials:
        params.update(apiKey=credentials.api_key, secret=credentials.secret)
        if credentials.password:
            params["password"] = credentials.password
    exchange = cls(params)
    if config.sandbox:
        exchange.set_sandbox_mode(True)
    return exchange


class LiveBroker(Broker):
    """Real orders through ccxt. Spot market orders only."""

    def __init__(self, exchange):
        self.exchange = exchange
        self.exchange.load_markets()

    def balances(self, symbol):
        base, quote = split_symbol(symbol)
        free = self.exchange.fetch_balance().get("free", {})
        return float(free.get(quote) or 0.0), float(free.get(base) or 0.0)

    def _check_limits(self, symbol: str, amount: float, price: float) -> float:
        market = self.exchange.market(symbol)
        amount = float(self.exchange.amount_to_precision(symbol, amount))
        limits = market.get("limits", {})
        min_amount = (limits.get("amount") or {}).get("min")
        min_cost = (limits.get("cost") or {}).get("min")
        if amount <= 0 or (min_amount and amount < min_amount):
            raise ValueError(f"order amount {amount} below exchange minimum {min_amount}")
        if min_cost and amount * price < min_cost:
            raise ValueError(f"order value {amount * price:.2f} below exchange minimum {min_cost}")
        return amount

    def _to_fill(self, symbol, side, order, fallback_price, ts, reason) -> Fill:
        filled = float(order.get("filled") or order.get("amount") or 0.0)
        price = float(order.get("average") or order.get("price") or fallback_price)
        fee = 0.0
        _, quote = split_symbol(symbol)
        for f in order.get("fees") or ([order["fee"]] if order.get("fee") else []):
            if f and f.get("cost"):
                # Fees charged in base currency are converted to quote for reporting.
                fee += float(f["cost"]) * (1 if f.get("currency") == quote else price)
        return Fill(symbol, side, filled, price, fee, ts, reason)

    def market_buy(self, symbol, amount, price, ts, reason):
        amount = self._check_limits(symbol, amount, price)
        log.info("LIVE BUY %s %s (~%s) reason=%s", amount, symbol, price, reason)
        order = self.exchange.create_order(symbol, "market", "buy", amount)
        return self._to_fill(symbol, "buy", self._settle(order, symbol), price, ts, reason)

    def market_sell(self, symbol, amount, price, ts, reason):
        _, free_base = self.balances(symbol)
        amount = self._check_limits(symbol, min(amount, free_base), price)
        log.info("LIVE SELL %s %s (~%s) reason=%s", amount, symbol, price, reason)
        order = self.exchange.create_order(symbol, "market", "sell", amount)
        return self._to_fill(symbol, "sell", self._settle(order, symbol), price, ts, reason)

    def _settle(self, order: dict, symbol: str) -> dict:
        """Some exchanges return a bare order id; fetch full details when possible."""
        if order.get("filled") is None and order.get("id") and self.exchange.has.get("fetchOrder"):
            try:
                return self.exchange.fetch_order(order["id"], symbol)
            except Exception:  # noqa: BLE001 - reporting only; the order already executed
                log.warning("could not fetch order %s details", order["id"], exc_info=True)
        return order
