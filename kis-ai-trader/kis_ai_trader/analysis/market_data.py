from __future__ import annotations

import logging
from datetime import date
from typing import Protocol

import pandas as pd

from ..kis import KISApi, KISError
from .universe import DEFAULT_UNIVERSE

log = logging.getLogger(__name__)


class MarketData(Protocol):
    def daily(self, code: str) -> pd.DataFrame: ...

    def price(self, code: str) -> float: ...

    def discover(self) -> list[tuple[str, str]]: ...

    def name(self, code: str) -> str: ...


class KISMarketData:
    """KIS 시세 기반 데이터. 일봉은 하루 단위로 캐시하고 장중에는 현재가로 마지막 봉을 갱신한다."""

    def __init__(self, api: KISApi, watchlist: list[str] | None = None, use_volume_rank: bool = True):
        self.api = api
        self.names = dict(DEFAULT_UNIVERSE)
        self.watchlist = watchlist or list(DEFAULT_UNIVERSE)
        self.use_volume_rank = use_volume_rank and not api.cfg.is_paper
        self._daily: dict[str, tuple[date, pd.DataFrame]] = {}
        self._price: dict[str, float] = {}

    def name(self, code: str) -> str:
        return self.names.get(code, code)

    def reset_intraday(self) -> None:
        self._price.clear()

    def price(self, code: str) -> float:
        if code not in self._price:
            try:
                self._price[code] = self.api.get_price(code)["price"]
            except KISError as exc:
                log.warning("현재가 조회 실패 %s: %s", code, exc)
                return 0.0
        return self._price[code]

    def daily(self, code: str) -> pd.DataFrame:
        today = date.today()
        cached = self._daily.get(code)
        if not cached or cached[0] != today:
            try:
                df = self.api.get_daily_ohlcv(code, days=150)
            except KISError as exc:
                log.warning("일봉 조회 실패 %s: %s", code, exc)
                return pd.DataFrame()
            self._daily[code] = (today, df)
        return self._daily[code][1]

    def discover(self) -> list[tuple[str, str]]:
        found: dict[str, str] = {c: self.name(c) for c in self.watchlist}
        if self.use_volume_rank:
            try:
                for code, nm in self.api.get_volume_rank(30):
                    # ETF/ETN/스팩 등은 제외 (보통주 6자리 숫자 코드만)
                    if code.isdigit() and not any(k in nm for k in ("KODEX", "TIGER", "ETN", "스팩", "레버리지", "인버스")):
                        found.setdefault(code, nm)
                        self.names.setdefault(code, nm)
            except KISError as exc:
                log.info("거래량 순위 조회 불가(%s) - 관심종목만 사용", exc)
        return list(found.items())


class HistoricalMarketData:
    """백테스트용: 주어진 날짜까지의 데이터만 노출해 미래 정보 누출을 막는다."""

    def __init__(self, frames: dict[str, pd.DataFrame], names: dict[str, str] | None = None):
        self.frames = frames
        self.names = names or {}
        self.current: pd.Timestamp | None = None
        self.price_field = "close"

    def set_time(self, ts: pd.Timestamp, price_field: str = "close") -> None:
        self.current, self.price_field = ts, price_field

    def name(self, code: str) -> str:
        return self.names.get(code, code)

    def daily(self, code: str) -> pd.DataFrame:
        df = self.frames.get(code)
        if df is None:
            return pd.DataFrame()
        return df.loc[: self.current] if self.current is not None else df

    def price(self, code: str) -> float:
        df = self.daily(code)
        if df.empty:
            return 0.0
        return float(df[self.price_field].iloc[-1])

    def discover(self) -> list[tuple[str, str]]:
        return [(c, self.name(c)) for c in self.frames]
