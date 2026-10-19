"""AI 판단과 무관하게 항상 적용되는 하드 리스크 규칙."""
from __future__ import annotations

import math

from .ai.analyst import Decision
from .brokers.base import Account
from .config import RiskConfig

COST_BUFFER = 0.003


class RiskManager:
    def __init__(self, cfg: RiskConfig):
        self.cfg = cfg

    def forced_exits(self, account: Account, metas: dict[str, dict]) -> list[tuple[str, str]]:
        exits = []
        for code, pos in account.positions.items():
            if pos.price <= 0:
                continue
            meta = metas.get(code, {})
            entry = meta.get("entry_price") or pos.avg_price
            peak = max(meta.get("peak_price") or entry, pos.price)
            stop = meta.get("stop_loss_pct") or self.cfg.stop_loss_pct
            take = meta.get("take_profit_pct") or self.cfg.take_profit_pct
            pnl = pos.price / entry - 1
            if pnl <= -stop:
                exits.append((code, f"손절: {pnl:+.2%} (한도 -{stop:.1%})"))
            elif pnl >= take:
                exits.append((code, f"익절: {pnl:+.2%} (목표 +{take:.1%})"))
            elif (
                peak / entry - 1 >= self.cfg.trailing_stop_pct / 2
                and pos.price <= peak * (1 - self.cfg.trailing_stop_pct)
            ):
                exits.append((code, f"트레일링스탑: 고점 {peak:,.0f} 대비 {pos.price / peak - 1:+.2%}"))
        return exits

    def can_open_new(self, account: Account, reference_equity: float | None, trades_today: int) -> tuple[bool, str]:
        if reference_equity and account.equity / reference_equity - 1 <= -self.cfg.daily_loss_limit_pct:
            return False, f"일일 손실 한도 도달 ({account.equity / reference_equity - 1:+.2%})"
        if trades_today >= self.cfg.max_trades_per_day:
            return False, f"일일 거래 횟수 한도({self.cfg.max_trades_per_day}) 도달"
        return True, ""

    def size_buy(self, decision: Decision, equity: float, cash: float, price: float) -> int:
        """목표 비중, 손절폭 기반 위험 한도, 최소 현금 비중 중 가장 작은 금액으로 수량 산정."""
        if price <= 0 or equity <= 0:
            return 0
        weight = min(decision.weight or self.cfg.max_position_pct, self.cfg.max_position_pct)
        stop = decision.stop_loss_pct or self.cfg.stop_loss_pct
        by_weight = weight * equity
        by_risk = equity * self.cfg.risk_per_trade_pct / stop
        spendable = cash - self.cfg.min_cash_pct * equity
        value = min(by_weight, by_risk, spendable)
        if value <= 0:
            return 0
        return max(0, math.floor(value / (price * (1 + COST_BUFFER))))
