from datetime import datetime

import pytest

from kis_ai_trader.ai import Decision, LLMAnalyst, RuleAnalyst, extract_json, validate_decisions
from kis_ai_trader.analysis import Candidate, HistoricalMarketData, compute_features, quant_score
from kis_ai_trader.backtest import make_synthetic_frames, run_backtest
from kis_ai_trader.brokers import Account, Position, SimBroker
from kis_ai_trader.config import RiskConfig, Settings
from kis_ai_trader.engine import TradingEngine
from kis_ai_trader.journal import Journal
from kis_ai_trader.risk import RiskManager


@pytest.fixture
def frames():
    return make_synthetic_frames(n_codes=6, days=160, seed=1)


def test_features_and_score(frames):
    df = next(iter(frames.values()))
    assert compute_features(df.head(30)) is None
    f = compute_features(df)
    assert f is not None and 0 <= f["rsi14"] <= 100
    assert 0 <= quant_score(f) <= 100


def test_uptrend_scores_higher_than_downtrend():
    import numpy as np
    import pandas as pd

    idx = pd.bdate_range("2024-01-01", periods=120)

    def frame(slope):
        close = 10000 * np.exp(slope * np.arange(120) + 0.003 * np.sin(np.arange(120)))
        return pd.DataFrame({"open": close, "high": close * 1.01, "low": close * 0.99, "close": close, "volume": 1e5}, index=idx)

    up, down = quant_score(compute_features(frame(0.004))), quant_score(compute_features(frame(-0.004)))
    assert up > 65 > 35 > down


def test_extract_json_handles_fences_and_prose():
    assert extract_json('설명\n```json\n{"a": 1}\n```')["a"] == 1
    assert extract_json('결과: {"a": {"b": 2}} 끝')["a"]["b"] == 2


def test_validate_decisions_rejects_invalid():
    risk = RiskConfig(max_position_pct=0.2, stop_loss_pct=0.05)
    cands = [Candidate("000001", "A", 80, {}), Candidate("000002", "B", 70, {})]
    acc = Account(1_000_000, {"000003": Position("000003", "C", 10, 1000, 1000)})
    raw = {
        "decisions": [
            {"code": "000001", "action": "BUY", "confidence": 1.5, "weight": 0.9, "stop_loss_pct": 0.3},
            {"code": "999999", "action": "BUY", "confidence": 0.9},  # 후보에 없음
            {"code": "000002", "action": "SELL", "confidence": 0.9},  # 미보유 매도
            {"code": "1", "action": "YOLO"},
        ]
    }
    ds = {d.code: d for d in validate_decisions(raw, cands, acc, risk)}
    assert set(ds) == {"000001", "000003"}
    assert ds["000001"].confidence == 1.0 and ds["000001"].weight == 0.2 and ds["000001"].stop_loss_pct == 0.05
    assert ds["000003"].action == "HOLD"


def test_risk_forced_exits_and_sizing():
    rm = RiskManager(RiskConfig(stop_loss_pct=0.05, take_profit_pct=0.15, trailing_stop_pct=0.08))
    acc = Account(
        0,
        {
            "A": Position("A", "A", 10, 100, 94),
            "B": Position("B", "B", 10, 100, 116),
            "C": Position("C", "C", 10, 100, 101),
            "D": Position("D", "D", 10, 100, 104),
        },
    )
    metas = {"C": {"entry_price": 100, "peak_price": 112}, "D": {"entry_price": 100, "peak_price": 105}}
    exits = dict(rm.forced_exits(acc, metas))
    assert exits["A"].startswith("손절") and exits["B"].startswith("익절") and exits["C"].startswith("트레일링")
    assert "D" not in exits

    rm = RiskManager(RiskConfig(max_position_pct=0.2, risk_per_trade_pct=0.01, min_cash_pct=0.1))
    d = Decision("X", "BUY", 0.9, weight=0.5, stop_loss_pct=0.05)
    qty = rm.size_buy(d, equity=10_000_000, cash=10_000_000, price=10_000)
    assert qty * 10_000 <= 2_000_000 and qty > 190
    assert rm.size_buy(d, equity=10_000_000, cash=900_000, price=10_000) == 0


def test_daily_loss_limit_blocks_buys():
    rm = RiskManager(RiskConfig(daily_loss_limit_pct=0.03, max_trades_per_day=5))
    assert not rm.can_open_new(Account(960), 1000, 0)[0]
    assert not rm.can_open_new(Account(1000), 1000, 5)[0]
    assert rm.can_open_new(Account(990), 1000, 0)[0]


def test_sim_broker_costs():
    prices = {"A": 10_000.0}
    b = SimBroker(lambda c: prices[c], 1_000_000, fee_rate=0.001, sell_tax_rate=0.002, slippage_pct=0.0)
    assert b.buy("A", 50).ok
    assert b.cash == pytest.approx(1_000_000 - 50 * 10_000 * 1.001)
    assert b.buy("A", 10_000).qty == 49  # 남은 현금 한도 내로 축소
    prices["A"] = 11_000
    res = b.sell("A", 1000)
    assert res.qty == 99 and "A" not in b.positions
    assert b.get_account().equity > 1_000_000


class FakeLLM:
    class cfg:
        model = "fake"

    def __init__(self, response):
        self.response = response
        self.prompts = []

    def complete_json(self, system, user):
        self.prompts.append(user)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _engine(frames, analyst, cash=10_000_000):
    md = HistoricalMarketData(frames)
    md.set_time(next(iter(frames.values())).index[-1])
    broker = SimBroker(md.price, cash)
    return TradingEngine(broker, md, analyst, RiskConfig(max_positions=3), Journal(":memory:"), top_n=5), broker


def test_engine_executes_llm_buy_and_journals(frames):
    code = next(iter(frames))
    llm = FakeLLM({"market_view": "강세", "decisions": [
        {"code": code, "action": "BUY", "confidence": 0.9, "weight": 0.15, "stop_loss_pct": 0.04, "reason": "테스트"}
    ]})
    engine, broker = _engine({code: frames[code]}, LLMAnalyst(llm))
    plan, results = engine.run_cycle(now=datetime(2024, 1, 2, 10))
    assert [o.side for o in plan.orders] == ["buy"] and results[0].ok
    assert code in broker.positions
    meta = engine.journal.get_meta()[code]
    assert meta["stop_loss_pct"] == 0.04 and "테스트" in meta["entry_reason"]
    assert '"candidates"' in llm.prompts[0]

    # 다음 사이클: 보유 종목 매도 결정 → 청산 기록
    llm.response = {"decisions": [{"code": code, "action": "SELL", "confidence": 0.8, "reason": "추세이탈"}]}
    plan, results = engine.run_cycle(now=datetime(2024, 1, 3, 10))
    assert results and results[0].side == "sell" and results[0].ok
    assert engine.journal.closed_trades()[0]["exit_reason"].startswith("AI매도")


def test_llm_failure_falls_back_to_rules(frames):
    from kis_ai_trader.ai import LLMError

    engine, _ = _engine(frames, LLMAnalyst(FakeLLM(LLMError("down"))))
    plan, _ = engine.run_cycle(now=datetime(2024, 1, 2, 10), dry_run=True)
    assert plan.analysis.source == "rule(fallback)"


def test_backtest_runs_without_lookahead(frames):
    res = run_backtest(frames, RuleAnalyst(), Settings())
    assert len(res.equity) == 160 - 59
    assert res.equity.iloc[0] == Settings().trading.sim_initial_cash
    assert set(res.metrics) >= {"total_return", "max_drawdown", "sharpe", "win_rate"}
