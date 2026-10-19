from __future__ import annotations

import math

import numpy as np
import pandas as pd

MIN_BARS = 60


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(100.0)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    prev_close = df["close"].shift()
    tr = pd.concat(
        [df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()], axis=1
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def compute_features(df: pd.DataFrame) -> dict | None:
    """Latest-bar feature snapshot used by both the quant scorer and the LLM prompt."""
    if df is None or len(df) < MIN_BARS:
        return None
    close, vol = df["close"], df["volume"]
    sma5, sma20, sma60 = close.rolling(5).mean(), close.rolling(20).mean(), close.rolling(60).mean()
    ema12, ema26 = close.ewm(span=12, adjust=False).mean(), close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    hist = macd - macd.ewm(span=9, adjust=False).mean()
    bb_std = close.rolling(20).std()
    rets = close.pct_change()
    a = atr(df)

    last = float(close.iloc[-1])

    def ret(n: int) -> float:
        return float(close.iloc[-1] / close.iloc[-1 - n] - 1) if len(close) > n else 0.0

    bb_width = 4 * bb_std.iloc[-1]
    feats = {
        "close": last,
        "ret_1d": ret(1),
        "ret_5d": ret(5),
        "ret_20d": ret(20),
        "ret_60d": ret(59),
        "sma5": float(sma5.iloc[-1]),
        "sma20": float(sma20.iloc[-1]),
        "sma60": float(sma60.iloc[-1]),
        "rsi14": float(rsi(close).iloc[-1]),
        "macd_hist": float(hist.iloc[-1] / last),
        "macd_hist_rising": bool(hist.iloc[-1] > hist.iloc[-2]),
        "bb_pos": float((last - (sma20.iloc[-1] - 2 * bb_std.iloc[-1])) / bb_width) if bb_width else 0.5,
        "atr_pct": float(a.iloc[-1] / last),
        "volatility_20d": float(rets.tail(20).std()),
        "volume_ratio": float(vol.iloc[-1] / vol.tail(20).mean()) if vol.tail(20).mean() else 1.0,
        "dist_from_high": float(last / close.tail(120).max() - 1),
    }
    return {k: (round(v, 5) if isinstance(v, float) and math.isfinite(v) else v) for k, v in feats.items()}


def quant_score(f: dict) -> float:
    """0~100 추세추종+모멘텀 점수. 높을수록 매수 우위."""
    s = 50.0
    s += 8 if f["close"] > f["sma20"] else -8
    s += 8 if f["sma20"] > f["sma60"] else -8
    s += 4 if f["close"] > f["sma60"] else -4
    s += max(-15.0, min(15.0, f["ret_20d"] * 75))
    s += max(-5.0, min(5.0, f["ret_5d"] * 50))
    r = f["rsi14"]
    if 50 <= r <= 68:
        s += 6
    elif r > 75:
        s -= 10
    elif r < 30:
        s -= 5
    s += 5 if f["macd_hist"] > 0 else -5
    s += 3 if f["macd_hist_rising"] else -3
    if f["volume_ratio"] > 1.5 and f["ret_1d"] > 0:
        s += 6
    if f["dist_from_high"] > -0.05:
        s += 4
    if f["volatility_20d"] > 0.05:
        s -= 5
    return round(max(0.0, min(100.0, s)), 1)
