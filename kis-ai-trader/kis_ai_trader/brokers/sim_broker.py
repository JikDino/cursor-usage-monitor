from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Callable

from .base import Account, Broker, OrderResult, Position


class SimBroker(Broker):
    """로컬 가상계좌. 수수료/매도세/슬리피지를 반영해 체결한다.

    price_fn 은 현재가를 돌려주는 함수(실시간 KIS 시세 또는 백테스트 데이터).
    state_path 를 주면 계좌 상태를 파일에 저장해 재시작 후에도 이어간다.
    """

    name = "sim"

    def __init__(
        self,
        price_fn: Callable[[str], float],
        initial_cash: float = 10_000_000,
        fee_rate: float = 0.00015,
        sell_tax_rate: float = 0.002,
        slippage_pct: float = 0.001,
        state_path: str | Path | None = None,
    ):
        self.price_fn = price_fn
        self.fee_rate = fee_rate
        self.sell_tax_rate = sell_tax_rate
        self.slippage_pct = slippage_pct
        self.state_path = Path(state_path) if state_path else None
        self.cash = float(initial_cash)
        self.positions: dict[str, Position] = {}
        self._load()

    def _load(self) -> None:
        if self.state_path and self.state_path.exists():
            data = json.loads(self.state_path.read_text())
            self.cash = data["cash"]
            self.positions = {c: Position(**p) for c, p in data["positions"].items()}

    def _save(self) -> None:
        if not self.state_path:
            return
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(
            json.dumps({"cash": self.cash, "positions": {c: vars(p) for c, p in self.positions.items()}}, ensure_ascii=False)
        )

    def get_account(self) -> Account:
        for code, pos in self.positions.items():
            price = self.price_fn(code)
            if price and price > 0:
                pos.price = price
        return Account(cash=self.cash, positions={c: Position(**vars(p)) for c, p in self.positions.items()})

    def buy(self, code: str, qty: int, name: str = "") -> OrderResult:
        price = self.price_fn(code)
        if not price or price <= 0 or qty <= 0:
            return OrderResult(False, "buy", code, qty, 0.0, message="가격 없음")
        fill = price * (1 + self.slippage_pct)
        unit_cost = fill * (1 + self.fee_rate)
        qty = min(qty, math.floor(self.cash / unit_cost))
        if qty <= 0:
            return OrderResult(False, "buy", code, 0, fill, message="현금 부족")
        self.cash -= qty * unit_cost
        pos = self.positions.get(code)
        if pos:
            total = pos.qty + qty
            pos.avg_price = (pos.avg_price * pos.qty + fill * qty) / total
            pos.qty = total
            pos.price = price
        else:
            self.positions[code] = Position(code, name or code, qty, fill, price)
        self._save()
        return OrderResult(True, "buy", code, qty, fill, message="sim filled")

    def sell(self, code: str, qty: int) -> OrderResult:
        pos = self.positions.get(code)
        price = self.price_fn(code)
        if not pos or not price or price <= 0:
            return OrderResult(False, "sell", code, qty, 0.0, message="보유 없음/가격 없음")
        qty = min(qty, pos.qty)
        fill = price * (1 - self.slippage_pct)
        self.cash += qty * fill * (1 - self.fee_rate - self.sell_tax_rate)
        pos.qty -= qty
        if pos.qty == 0:
            del self.positions[code]
        self._save()
        return OrderResult(True, "sell", code, qty, fill, message="sim filled")
