from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass

from ..analysis.screener import Candidate
from ..brokers.base import Account
from ..config import RiskConfig
from .llm import LLMClient, LLMError

log = logging.getLogger(__name__)

ACTIONS = {"BUY", "SELL", "HOLD"}


@dataclass
class Decision:
    code: str
    action: str
    confidence: float
    weight: float = 0.0
    stop_loss_pct: float = 0.0
    take_profit_pct: float = 0.0
    reason: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AnalysisResult:
    market_view: str
    decisions: list[Decision]
    source: str


SYSTEM_PROMPT = """너는 한국 주식시장(KOSPI/KOSDAQ) 스윙 트레이딩 펀드매니저다.
목표: 위험 대비 수익(샤프지수)을 극대화하며 계좌를 꾸준히 불리는 것. 큰 손실 회피가 최우선이다.

판단 원칙:
- 주어진 정량 지표(추세, 모멘텀, RSI, MACD, 거래량, 변동성)와 계좌 상태, 과거 매매 성과만 근거로 판단한다.
- 추세가 살아있고(종가>20일선>60일선) 거래량이 동반된 종목을 선호한다. 과열(RSI>75)이나 추세 이탈 종목 추격매수는 피한다.
- 보유 종목은 추세가 꺾였거나 더 나은 후보로 교체할 근거가 있을 때만 SELL 한다. 잦은 매매는 수수료/세금으로 수익을 깎는다.
- 확신이 낮으면 HOLD. 억지로 매수할 필요 없다(현금도 포지션이다).
- 과거 매매 성과(reflection)에서 반복된 실수를 찾아 이번 판단에 반영한다.

반드시 아래 JSON 하나만 출력한다:
{
  "market_view": "시장/후보군에 대한 2~3문장 요약",
  "decisions": [
    {"code": "005930", "action": "BUY|SELL|HOLD", "confidence": 0.0~1.0,
     "weight": 목표 비중 0.0~1.0 (BUY만), "stop_loss_pct": 손절폭 예 0.05, "take_profit_pct": 익절폭 예 0.15,
     "reason": "한국어 1~2문장 근거"}
  ]
}
보유 종목은 모두 decisions 에 포함해야 한다(HOLD 또는 SELL)."""


def build_prompt(
    candidates: list[Candidate],
    held: dict[str, Candidate],
    account: Account,
    risk: RiskConfig,
    performance: dict | None,
) -> str:
    equity = account.equity
    holdings = []
    for code, pos in account.positions.items():
        info = held.get(code)
        holdings.append(
            {
                "code": code,
                "name": pos.name,
                "qty": pos.qty,
                "avg_price": round(pos.avg_price),
                "price": pos.price,
                "pnl_pct": round(pos.pnl_pct, 4),
                "weight": round(pos.market_value / equity, 4) if equity else 0,
                "analysis": info.to_prompt() if info else None,
            }
        )
    payload = {
        "account": {
            "equity": round(equity),
            "cash": round(account.cash),
            "cash_pct": round(account.cash / equity, 4) if equity else 1,
            "positions": len(account.positions),
        },
        "risk_limits": {
            "max_positions": risk.max_positions,
            "max_position_pct": risk.max_position_pct,
            "min_cash_pct": risk.min_cash_pct,
            "max_stop_loss_pct": risk.stop_loss_pct,
            "min_confidence_to_trade": risk.min_confidence,
        },
        "holdings": holdings,
        "candidates": [c.to_prompt() for c in candidates],
        "reflection": performance or {},
        "feature_guide": {
            "ret_Nd": "N일 수익률",
            "macd_hist": "MACD 히스토그램/종가",
            "bb_pos": "볼린저밴드 내 위치(0=하단,1=상단)",
            "atr_pct": "ATR/종가(일간 변동폭)",
            "volume_ratio": "당일 거래량/20일 평균",
            "dist_from_high": "120일 고점 대비",
            "quant_score": "규칙 기반 점수 0~100",
        },
    }
    return "다음 데이터를 분석해 매매 결정을 JSON으로 내려라.\n" + json.dumps(payload, ensure_ascii=False)


def _clip(value, lo: float, hi: float, default: float) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


def validate_decisions(raw: dict, candidates: list[Candidate], account: Account, risk: RiskConfig) -> list[Decision]:
    """LLM 출력을 신뢰하지 않고 허용된 종목/행동/범위로 정규화한다."""
    cand_codes = {c.code for c in candidates}
    held_codes = set(account.positions)
    out: dict[str, Decision] = {}
    for item in raw.get("decisions", []) or []:
        if not isinstance(item, dict):
            continue
        code = str(item.get("code", "")).strip().zfill(6)
        action = str(item.get("action", "HOLD")).upper()
        if action not in ACTIONS:
            continue
        if action == "BUY" and (code not in cand_codes or code in held_codes):
            continue
        if action == "SELL" and code not in held_codes:
            continue
        if action == "HOLD" and code not in held_codes and code not in cand_codes:
            continue
        out[code] = Decision(
            code=code,
            action=action,
            confidence=_clip(item.get("confidence"), 0.0, 1.0, 0.0),
            weight=_clip(item.get("weight"), 0.0, risk.max_position_pct, risk.max_position_pct / 2),
            stop_loss_pct=_clip(item.get("stop_loss_pct"), 0.015, risk.stop_loss_pct, risk.stop_loss_pct),
            take_profit_pct=_clip(item.get("take_profit_pct"), 0.03, 0.5, risk.take_profit_pct),
            reason=str(item.get("reason", ""))[:300],
        )
    for code in held_codes - set(out):
        out[code] = Decision(code, "HOLD", 0.5, reason="AI 응답에 없음 - 유지")
    return list(out.values())


class RuleAnalyst:
    """LLM 없이 동작하는 기준 전략(백테스트 기본값, LLM 장애 시 대체)."""

    source = "rule"

    def __init__(self, buy_score: float = 72, sell_score: float = 40):
        self.buy_score = buy_score
        self.sell_score = sell_score

    def analyze(self, candidates, held, account: Account, risk: RiskConfig, performance=None) -> AnalysisResult:
        decisions: list[Decision] = []
        for code, pos in account.positions.items():
            info = held.get(code)
            if info and (info.score < self.sell_score or info.features["close"] < info.features["sma60"]):
                decisions.append(Decision(code, "SELL", 0.8, reason=f"점수 {info.score} / 추세 이탈"))
            else:
                decisions.append(Decision(code, "HOLD", 0.5, reason="추세 유지"))
        for c in candidates:
            if c.code in account.positions or c.score < self.buy_score:
                continue
            f = c.features
            if f["rsi14"] > 75:
                continue
            stop = min(risk.stop_loss_pct, max(0.02, 2.0 * f["atr_pct"]))
            decisions.append(
                Decision(
                    c.code,
                    "BUY",
                    round(min(0.95, c.score / 100 + 0.1), 3),
                    weight=risk.max_position_pct,
                    stop_loss_pct=round(stop, 4),
                    take_profit_pct=risk.take_profit_pct,
                    reason=f"점수 {c.score}, 20일 {f['ret_20d']:+.1%}, RSI {f['rsi14']:.0f}",
                )
            )
        return AnalysisResult("규칙 기반 추세추종 전략", decisions, self.source)


class LLMAnalyst:
    source = "llm"

    def __init__(self, client: LLMClient, fallback: RuleAnalyst | None = None):
        self.client = client
        self.fallback = fallback or RuleAnalyst()

    def analyze(self, candidates, held, account: Account, risk: RiskConfig, performance=None) -> AnalysisResult:
        prompt = build_prompt(candidates, held, account, risk, performance)
        try:
            raw = self.client.complete_json(SYSTEM_PROMPT, prompt)
        except (LLMError, KeyError, ValueError, OSError) as exc:
            log.error("LLM 분석 실패, 규칙 기반으로 대체: %s", exc)
            res = self.fallback.analyze(candidates, held, account, risk, performance)
            res.source = "rule(fallback)"
            return res
        decisions = validate_decisions(raw, candidates, account, risk)
        return AnalysisResult(str(raw.get("market_view", ""))[:1000], decisions, f"llm:{self.client.cfg.model}")
