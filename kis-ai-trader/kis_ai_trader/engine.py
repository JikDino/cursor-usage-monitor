"""한 번의 매매 사이클: 계좌 조회 → 종목 발굴 → 리스크 청산 → AI 판단 → 주문 → 기록."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

from .ai.analyst import AnalysisResult, Decision
from .analysis.market_data import MarketData
from .analysis.screener import Candidate, screen
from .brokers.base import Account, Broker, OrderResult
from .config import RiskConfig
from .journal import Journal
from .risk import COST_BUFFER, RiskManager

log = logging.getLogger(__name__)

SELL_MIN_CONFIDENCE = 0.5


@dataclass
class PlannedOrder:
    side: str
    code: str
    name: str
    qty: int
    reason: str
    decision: Decision | None = None


@dataclass
class Plan:
    account: Account
    candidates: list[Candidate]
    analysis: AnalysisResult | None
    orders: list[PlannedOrder] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


class TradingEngine:
    def __init__(
        self,
        broker: Broker,
        market: MarketData,
        analyst,
        risk_cfg: RiskConfig,
        journal: Journal,
        top_n: int = 10,
    ):
        self.broker = broker
        self.market = market
        self.analyst = analyst
        self.risk_cfg = risk_cfg
        self.risk = RiskManager(risk_cfg)
        self.journal = journal
        self.top_n = top_n

    def _sync_meta(self, account: Account, now) -> dict[str, dict]:
        metas = self.journal.get_meta()
        for code in set(metas) - set(account.positions):
            self.journal.drop_meta(code)
        for code, pos in account.positions.items():
            if code not in metas:
                self.journal.open_position(
                    now, code, pos.name, pos.avg_price, self.risk_cfg.stop_loss_pct, self.risk_cfg.take_profit_pct,
                    "기존 보유 종목 편입",
                )
            elif pos.price > 0:
                self.journal.update_peak(code, pos.price)
        return self.journal.get_meta()

    def plan(self, now: datetime | None = None) -> Plan:
        now = now or datetime.now()
        account = self.broker.get_account()
        self.journal.record_equity(now, account.equity, account.cash, len(account.positions))
        metas = self._sync_meta(account, now)

        sells: dict[str, PlannedOrder] = {}
        for code, reason in self.risk.forced_exits(account, metas):
            pos = account.positions[code]
            sells[code] = PlannedOrder("sell", code, pos.name, pos.qty, reason)

        candidates, held = screen(self.market, list(account.positions), self.top_n)
        analysis = self.analyst.analyze(candidates, held, account, self.risk_cfg, self.journal.performance_summary())
        self.journal.record_analysis(now, analysis)
        plan = Plan(account, candidates, analysis)

        for d in analysis.decisions:
            if d.action == "SELL" and d.code not in sells and d.confidence >= SELL_MIN_CONFIDENCE:
                pos = account.positions[d.code]
                sells[d.code] = PlannedOrder("sell", d.code, pos.name, pos.qty, f"AI매도: {d.reason}", d)
        plan.orders.extend(sells.values())

        ok, why = self.risk.can_open_new(account, self.journal.reference_equity(now), self.journal.trades_today(now))
        if not ok:
            plan.notes.append(f"신규 매수 중단 - {why}")
            return plan

        cash = account.cash + sum(account.positions[o.code].market_value * (1 - COST_BUFFER) for o in sells.values())
        slots = self.risk_cfg.max_positions - (len(account.positions) - len(sells))
        names = {c.code: c.name for c in candidates}
        buys = sorted(
            (d for d in analysis.decisions if d.action == "BUY" and d.code not in sells),
            key=lambda d: d.confidence,
            reverse=True,
        )
        for d in buys:
            if slots <= 0:
                plan.notes.append("최대 보유 종목 수 도달")
                break
            if d.confidence < self.risk_cfg.min_confidence:
                plan.notes.append(f"{d.code} 확신도 {d.confidence:.2f} < {self.risk_cfg.min_confidence} → 보류")
                continue
            price = self.market.price(d.code)
            qty = self.risk.size_buy(d, account.equity, cash, price)
            if qty <= 0:
                plan.notes.append(f"{d.code} 매수 가능 수량 0 (현금/비중 한도)")
                continue
            cash -= qty * price * (1 + COST_BUFFER)
            slots -= 1
            plan.orders.append(PlannedOrder("buy", d.code, names.get(d.code, d.code), qty, f"AI매수: {d.reason}", d))
        return plan

    def execute(self, plan: Plan, now: datetime | None = None) -> list[OrderResult]:
        now = now or datetime.now()
        results = []
        for o in [o for o in plan.orders if o.side == "sell"] + [o for o in plan.orders if o.side == "buy"]:
            if o.side == "sell":
                res = self.broker.sell(o.code, o.qty)
                if res.ok:
                    exit_price = res.price or self.market.price(o.code)
                    pos = plan.account.positions.get(o.code)
                    self.journal.close_position(now, o.code, exit_price, res.qty, o.reason, pos.avg_price if pos else 0)
            else:
                res = self.broker.buy(o.code, o.qty, o.name)
                if res.ok:
                    d = o.decision
                    self.journal.open_position(
                        now, o.code, o.name, res.price or self.market.price(o.code),
                        d.stop_loss_pct or self.risk_cfg.stop_loss_pct,
                        d.take_profit_pct or self.risk_cfg.take_profit_pct,
                        o.reason,
                    )
            self.journal.record_order(now, res, o.reason)
            log.info("%s %s %s x%d → %s %s", o.side.upper(), o.code, o.name, o.qty, "OK" if res.ok else "FAIL", res.message)
            results.append(res)
        return results

    def run_cycle(self, now: datetime | None = None, dry_run: bool = False) -> tuple[Plan, list[OrderResult]]:
        plan = self.plan(now)
        results = [] if dry_run else self.execute(plan, now)
        return plan, results
