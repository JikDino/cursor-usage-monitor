from __future__ import annotations

from dataclasses import dataclass

from .indicators import compute_features, quant_score
from .market_data import MarketData


@dataclass
class Candidate:
    code: str
    name: str
    score: float
    features: dict

    def to_prompt(self) -> dict:
        return {"code": self.code, "name": self.name, "quant_score": self.score, **self.features}


def screen(md: MarketData, held: list[str], top_n: int = 10, min_price: float = 1000) -> tuple[list[Candidate], dict[str, Candidate]]:
    """유니버스 전체를 점수화해 상위 top_n 신규 후보와 보유 종목 분석을 함께 반환."""
    universe = dict(md.discover())
    for code in held:
        universe.setdefault(code, md.name(code))

    scored: list[Candidate] = []
    held_info: dict[str, Candidate] = {}
    for code, name in universe.items():
        feats = compute_features(md.daily(code))
        if feats is None:
            continue
        cand = Candidate(code, name, quant_score(feats), feats)
        if code in held:
            held_info[code] = cand
        elif feats["close"] >= min_price:
            scored.append(cand)
    scored.sort(key=lambda c: c.score, reverse=True)
    return scored[:top_n], held_info
