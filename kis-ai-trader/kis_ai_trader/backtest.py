"""백테스트: 실거래와 동일한 엔진/리스크/분석가를 과거 데이터에 적용.

t일 종가까지의 데이터로 판단하고 t+1일 시가에 체결해 미래 정보 누출(look-ahead)을 막는다.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from .analysis.indicators import MIN_BARS
from .analysis.market_data import HistoricalMarketData
from .brokers.sim_broker import SimBroker
from .config import Settings
from .engine import TradingEngine
from .journal import Journal

log = logging.getLogger(__name__)


@dataclass
class BacktestResult:
    equity: pd.Series
    benchmark: pd.Series
    trades: list[dict]
    metrics: dict

    def summary(self) -> str:
        m = self.metrics
        lines = [
            f"기간            {m['start']} ~ {m['end']} ({m['days']}거래일)",
            f"총수익률        {m['total_return']:+.2%}   (동일비중 보유 벤치마크 {m['benchmark_return']:+.2%})",
            f"연환산(CAGR)    {m['cagr']:+.2%}",
            f"최대낙폭(MDD)   {m['max_drawdown']:.2%}   (벤치마크 {m['benchmark_mdd']:.2%})",
            f"샤프지수        {m['sharpe']:.2f}",
            f"청산 거래       {m['trades']}건, 승률 {m['win_rate']:.1%}, 평균 {m['avg_trade']:+.2%}",
        ]
        return "\n".join(lines)


def _max_drawdown(s: pd.Series) -> float:
    return float((s / s.cummax() - 1).min()) if len(s) else 0.0


def compute_metrics(equity: pd.Series, benchmark: pd.Series, trades: list[dict]) -> dict:
    rets = equity.pct_change().dropna()
    years = max(len(equity) / 252, 1e-9)
    total = float(equity.iloc[-1] / equity.iloc[0] - 1)
    pnls = [t["pnl_pct"] for t in trades]
    return {
        "start": str(equity.index[0].date()),
        "end": str(equity.index[-1].date()),
        "days": len(equity),
        "total_return": total,
        "cagr": (1 + total) ** (1 / years) - 1 if total > -1 else -1.0,
        "max_drawdown": _max_drawdown(equity),
        "sharpe": float(rets.mean() / rets.std() * math.sqrt(252)) if len(rets) > 1 and rets.std() > 0 else 0.0,
        "benchmark_return": float(benchmark.iloc[-1] / benchmark.iloc[0] - 1),
        "benchmark_mdd": _max_drawdown(benchmark),
        "trades": len(pnls),
        "win_rate": sum(p > 0 for p in pnls) / len(pnls) if pnls else 0.0,
        "avg_trade": float(np.mean(pnls)) if pnls else 0.0,
    }


def run_backtest(
    frames: dict[str, pd.DataFrame],
    analyst,
    settings: Settings,
    names: dict[str, str] | None = None,
    initial_cash: float | None = None,
) -> BacktestResult:
    md = HistoricalMarketData(frames, names)
    tc = settings.trading
    broker = SimBroker(
        md.price,
        initial_cash or tc.sim_initial_cash,
        fee_rate=tc.fee_rate,
        sell_tax_rate=tc.sell_tax_rate,
        slippage_pct=tc.slippage_pct,
    )
    journal = Journal(":memory:")
    engine = TradingEngine(broker, md, analyst, settings.risk, journal, top_n=tc.candidates)

    dates = sorted(set().union(*(df.index for df in frames.values())))
    if len(dates) <= MIN_BARS + 1:
        raise ValueError(f"백테스트에는 최소 {MIN_BARS + 2}거래일 데이터가 필요합니다.")

    curve: dict[pd.Timestamp, float] = {}
    start = MIN_BARS - 1
    md.set_time(dates[start])
    curve[dates[start]] = broker.get_account().equity
    for i in range(start, len(dates) - 1):
        today, nxt = dates[i], dates[i + 1]
        md.set_time(today, "close")
        plan = engine.plan(now=today.to_pydatetime())
        md.set_time(nxt, "open")
        engine.execute(plan, now=nxt.to_pydatetime())
        md.set_time(nxt, "close")
        curve[nxt] = broker.get_account().equity

    equity = pd.Series(curve).sort_index()
    closes = pd.DataFrame({c: df["close"] for c, df in frames.items()}).reindex(equity.index).ffill().bfill()
    benchmark = (closes / closes.iloc[0]).mean(axis=1) * equity.iloc[0]
    trades = journal.closed_trades()
    return BacktestResult(equity, benchmark, trades, compute_metrics(equity, benchmark, trades))


def make_synthetic_frames(n_codes: int = 20, days: int = 500, seed: int = 7) -> dict[str, pd.DataFrame]:
    """오프라인 데모/테스트용 가상 시세. 국면(상승/횡보/하락)이 바뀌는 랜덤워크."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2023-01-02", periods=days)
    frames = {}
    for k in range(n_codes):
        drift = np.zeros(days)
        t = 0
        while t < days:
            length = int(rng.integers(30, 120))
            drift[t : t + length] = rng.choice([0.0025, 0.0, -0.002], p=[0.4, 0.35, 0.25])
            t += length
        vol = rng.uniform(0.012, 0.03)
        rets = drift + rng.normal(0, vol, days)
        close = 20000 * np.exp(np.cumsum(rets))
        open_ = close * (1 + rng.normal(0, vol / 3, days))
        high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, vol / 2, days)))
        low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, vol / 2, days)))
        volume = rng.lognormal(13, 0.4, days) * (1 + 5 * np.clip(rets, 0, None) / vol / 10)
        frames[f"{900000 + k:06d}"] = pd.DataFrame(
            {"open": open_, "high": high, "low": low, "close": close, "volume": volume}, index=idx
        ).round(0)
    return frames


def load_csv_dir(path: str | Path) -> dict[str, pd.DataFrame]:
    """CSV 디렉터리 로드: 파일명=종목코드.csv, 컬럼 date,open,high,low,close,volume."""
    frames = {}
    for f in sorted(Path(path).glob("*.csv")):
        df = pd.read_csv(f, parse_dates=["date"]).set_index("date").sort_index()
        frames[f.stem] = df[["open", "high", "low", "close", "volume"]].astype(float)
    return frames
