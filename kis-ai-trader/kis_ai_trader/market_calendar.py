"""KRX 휴장일/장 운영시간 판별.

세 단계로 확인한다.
1. 주말 + 내장 휴장일 표 + EXTRA_HOLIDAYS(.env)
2. KIS 국내휴장일조회 API (실전 서버만 지원, 하루 1회 캐시)
3. 장중 실제 데이터 확인: 대표 종목에 오늘 일봉이 생겼는지 (임시휴장·개장 지연 대응)
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

KST = ZoneInfo("Asia/Seoul")
# 장 시작 직후 변동성과 종가 동시호가 구간은 피한다.
TRADE_START = time(9, 5)
TRADE_END = time(15, 15)

KRX_HOLIDAYS: dict[str, str] = {
    # 2025
    "2025-01-01": "신정", "2025-01-27": "임시공휴일", "2025-01-28": "설날", "2025-01-29": "설날",
    "2025-01-30": "설날", "2025-03-03": "삼일절 대체", "2025-05-01": "근로자의날", "2025-05-05": "어린이날·부처님오신날",
    "2025-05-06": "대체공휴일", "2025-06-03": "대통령선거", "2025-06-06": "현충일", "2025-08-15": "광복절",
    "2025-10-03": "개천절", "2025-10-06": "추석", "2025-10-07": "추석", "2025-10-08": "추석 대체",
    "2025-10-09": "한글날", "2025-12-25": "성탄절", "2025-12-31": "연말휴장",
    # 2026
    "2026-01-01": "신정", "2026-02-16": "설날", "2026-02-17": "설날", "2026-02-18": "설날",
    "2026-03-02": "삼일절 대체", "2026-05-01": "노동절", "2026-05-05": "어린이날", "2026-05-25": "부처님오신날 대체",
    "2026-06-03": "지방선거", "2026-08-17": "광복절 대체", "2026-09-24": "추석", "2026-09-25": "추석",
    "2026-09-28": "추석 대체", "2026-10-05": "개천절 대체", "2026-10-09": "한글날", "2026-12-25": "성탄절",
    "2026-12-31": "연말휴장",
    # 2027
    "2027-01-01": "신정", "2027-02-08": "설날", "2027-02-09": "설날 대체", "2027-03-01": "삼일절",
    "2027-05-05": "어린이날", "2027-05-13": "부처님오신날", "2027-08-16": "광복절 대체", "2027-09-14": "추석",
    "2027-09-15": "추석", "2027-09-16": "추석", "2027-10-04": "개천절 대체", "2027-10-11": "한글날 대체",
    "2027-12-27": "성탄절 대체", "2027-12-31": "연말휴장",
}


class MarketCalendar:
    def __init__(self, api=None, extra_holidays: list[str] | None = None, probe_code: str = "005930"):
        self.api = api
        self.holidays = dict(KRX_HOLIDAYS)
        for d in extra_holidays or []:
            self.holidays[d] = "사용자 지정 휴장일"
        self.probe_code = probe_code
        self._api_day: dict[date, bool | None] = {}
        self._traded: set[date] = set()

    def holiday_reason(self, d: date) -> str | None:
        """API 호출 없이 알 수 있는 휴장 사유."""
        if d.weekday() >= 5:
            return "주말"
        return self.holidays.get(d.isoformat())

    def _api_says_closed(self, d: date) -> bool:
        if self.api is None:
            return False
        if d not in self._api_day:
            try:
                self._api_day[d] = self.api.is_trading_day(d.strftime("%Y%m%d"))
            except Exception as exc:
                log.warning("휴장일 API 조회 실패: %s", exc)
                return False
        return self._api_day[d] is False

    def _traded_today(self, d: date) -> bool:
        if self.api is None or d in self._traded:
            return True
        try:
            df = self.api.get_daily_ohlcv(self.probe_code, days=3)
        except Exception as exc:
            log.warning("개장 여부 확인 실패(%s) - 진행", exc)
            return True
        if len(df) and df.index[-1].date() == d:
            self._traded.add(d)
            return True
        return False

    def status(self, now: datetime | None = None) -> tuple[bool, str]:
        """(지금 매매 가능 여부, 사유)."""
        now = _kst(now)
        d = now.date()
        reason = self.holiday_reason(d)
        if reason:
            return False, f"휴장일 ({reason})"
        if self._api_says_closed(d):
            return False, "휴장일 (KIS 휴장일 조회)"
        if not (TRADE_START <= now.time() <= TRADE_END):
            return False, "장 운영시간 아님 (09:05~15:15)"
        if not self._traded_today(d):
            # 캐시하지 않는다: 개장 지연일(수능 등)은 잠시 뒤 다시 확인하면 열려 있다.
            return False, "오늘 체결 데이터 없음 (임시휴장 또는 개장 지연)"
        return True, "장 운영 중"

    def next_open(self, now: datetime | None = None) -> datetime:
        """다음 매매 가능 시각 (이미 장중이면 now). 내장 휴장일 표 기준."""
        now = _kst(now)
        d = now.date()
        for _ in range(30):
            if not self.holiday_reason(d):
                start = datetime.combine(d, TRADE_START, tzinfo=KST)
                if start > now:
                    return start
                if d == now.date() and now.time() <= TRADE_END:
                    return now
            d += timedelta(days=1)
        return datetime.combine(d, TRADE_START, tzinfo=KST)


def _kst(now: datetime | None) -> datetime:
    """naive datetime 은 KST 로 간주한다."""
    if now is None:
        return datetime.now(KST)
    return now.astimezone(KST) if now.tzinfo else now.replace(tzinfo=KST)
