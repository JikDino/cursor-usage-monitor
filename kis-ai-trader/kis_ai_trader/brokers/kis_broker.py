from __future__ import annotations

import logging

from ..kis import KISApi, KISError
from .base import Account, Broker, OrderResult, Position

log = logging.getLogger(__name__)


class KISBroker(Broker):
    """한국투자증권 계좌로 직접 주문 (KIS_ENV=paper 이면 한투 모의투자 계좌)."""

    def __init__(self, api: KISApi):
        self.api = api
        self.name = "kis-paper" if api.cfg.is_paper else "kis-REAL"

    def get_account(self) -> Account:
        bal = self.api.get_balance()
        positions = {
            h["code"]: Position(h["code"], h["name"], h["qty"], h["avg_price"], h["price"]) for h in bal["holdings"]
        }
        return Account(cash=bal["cash"], positions=positions)

    def _order(self, side: str, code: str, qty: int) -> OrderResult:
        try:
            res = self.api.order_cash(side, code, qty)
            return OrderResult(True, side, code, qty, 0.0, res["order_no"], res["msg"])
        except KISError as exc:
            log.error("%s 주문 실패 %s x%d: %s", side, code, qty, exc)
            return OrderResult(False, side, code, qty, 0.0, message=str(exc))

    def buy(self, code: str, qty: int, name: str = "") -> OrderResult:
        return self._order("buy", code, qty)

    def sell(self, code: str, qty: int) -> OrderResult:
        return self._order("sell", code, qty)
