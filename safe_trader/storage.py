from __future__ import annotations

import csv
import json
import os
from dataclasses import asdict
from pathlib import Path

from safe_trader.models import Fill, Position
from safe_trader.risk import RiskState


class StateStore:
    """Persists bot state so a restart does not forget an open position."""

    def __init__(self, directory: str | Path):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.state_file = self.dir / "state.json"
        self.journal_file = self.dir / "trades.csv"

    def load(self) -> dict:
        if not self.state_file.exists():
            return {}
        return json.loads(self.state_file.read_text())

    def save(
        self,
        position: Position | None,
        risk_state: RiskState,
        last_candle_ts: int | None,
        paper_balances: tuple[float, float] | None = None,
    ) -> None:
        data = {
            "position": position.to_dict() if position else None,
            "risk": asdict(risk_state),
            "last_candle_ts": last_candle_ts,
            "paper_balances": list(paper_balances) if paper_balances else None,
        }
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        os.replace(tmp, self.state_file)  # atomic: never leave a half-written state file

    @staticmethod
    def position_from(data: dict) -> Position | None:
        raw = data.get("position")
        return Position.from_dict(raw) if raw else None

    @staticmethod
    def risk_state_from(data: dict) -> RiskState:
        return RiskState(**data["risk"]) if data.get("risk") else RiskState()

    def journal(self, fill: Fill) -> None:
        new = not self.journal_file.exists()
        with self.journal_file.open("a", newline="") as fh:
            writer = csv.writer(fh)
            if new:
                writer.writerow(["timestamp", "symbol", "side", "amount", "price", "fee", "reason"])
            writer.writerow([fill.timestamp, fill.symbol, fill.side, f"{fill.amount:.8f}",
                             f"{fill.price:.8f}", f"{fill.fee:.8f}", fill.reason])
