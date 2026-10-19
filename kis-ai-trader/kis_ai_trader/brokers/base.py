from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class Position:
    code: str
    name: str
    qty: int
    avg_price: float
    price: float

    @property
    def market_value(self) -> float:
        return self.qty * self.price

    @property
    def pnl_pct(self) -> float:
        return (self.price / self.avg_price - 1.0) if self.avg_price else 0.0


@dataclass
class Account:
    cash: float
    positions: dict[str, Position] = field(default_factory=dict)

    @property
    def equity(self) -> float:
        return self.cash + sum(p.market_value for p in self.positions.values())


@dataclass
class OrderResult:
    ok: bool
    side: str
    code: str
    qty: int
    price: float
    order_no: str = ""
    message: str = ""


class Broker(ABC):
    name: str = "broker"

    @abstractmethod
    def get_account(self) -> Account: ...

    @abstractmethod
    def buy(self, code: str, qty: int, name: str = "") -> OrderResult: ...

    @abstractmethod
    def sell(self, code: str, qty: int) -> OrderResult: ...
