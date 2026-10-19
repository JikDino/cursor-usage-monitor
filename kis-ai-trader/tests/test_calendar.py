from datetime import date, datetime

import pandas as pd

from kis_ai_trader.cli import seconds_until_next_check
from kis_ai_trader.market_calendar import KST, MarketCalendar


def kst(*args):
    return datetime(*args, tzinfo=KST)


class FakeApi:
    def __init__(self, last_bar: date, trading_day=None):
        self.last_bar = last_bar
        self.trading_day = trading_day
        self.daily_calls = 0
        self.holiday_calls = 0

    def is_trading_day(self, ymd):
        self.holiday_calls += 1
        return self.trading_day

    def get_daily_ohlcv(self, code, days=3):
        self.daily_calls += 1
        return pd.DataFrame({"close": [1.0]}, index=pd.to_datetime([self.last_bar]))


def test_weekend_and_builtin_holidays():
    cal = MarketCalendar()
    assert cal.status(kst(2026, 10, 17, 10))[0] is False  # 토요일
    ok, why = cal.status(kst(2026, 10, 5, 10))
    assert not ok and "개천절" in why
    assert cal.status(kst(2026, 9, 25, 10))[1].endswith("(추석)")
    assert cal.status(kst(2026, 10, 19, 10)) == (True, "장 운영 중")
    assert not cal.status(kst(2026, 10, 19, 8, 59))[0]
    assert not cal.status(kst(2026, 10, 19, 15, 20))[0]


def test_extra_holidays_from_settings():
    cal = MarketCalendar(extra_holidays=["2026-10-19"])
    assert "사용자 지정" in cal.status(kst(2026, 10, 19, 10))[1]


def test_next_open_skips_chuseok_and_weekend():
    cal = MarketCalendar()
    # 2026-09-23(수) 장 마감 후 → 추석(24,25), 주말, 추석 대체(28) 건너뛰고 29일
    assert cal.next_open(kst(2026, 9, 23, 16)) == kst(2026, 9, 29, 9, 5)
    assert cal.next_open(kst(2026, 10, 19, 7)) == kst(2026, 10, 19, 9, 5)
    now = kst(2026, 10, 19, 11)
    assert cal.next_open(now) == now


def test_naive_datetime_is_treated_as_kst():
    assert MarketCalendar().status(datetime(2026, 10, 19, 10))[0]


def test_unexpected_closure_detected_by_missing_today_bar():
    api = FakeApi(last_bar=date(2026, 10, 16))
    cal = MarketCalendar(api)
    ok, why = cal.status(kst(2026, 10, 19, 9, 30))
    assert not ok and "체결 데이터 없음" in why
    # 개장 지연일 대비: 닫힘 결과는 캐시하지 않고 다시 확인한다
    api.last_bar = date(2026, 10, 19)
    assert cal.status(kst(2026, 10, 19, 10, 1))[0]
    calls = api.daily_calls
    assert cal.status(kst(2026, 10, 19, 11))[0] and api.daily_calls == calls  # 열림 확인 후엔 재호출 안 함


def test_kis_holiday_api_is_cached_per_day():
    api = FakeApi(last_bar=date(2026, 10, 20), trading_day=False)
    cal = MarketCalendar(api)
    for h in (9, 10, 11):
        assert "KIS 휴장일" in cal.status(kst(2026, 10, 20, h, 30))[1]
    assert api.holiday_calls == 1 and api.daily_calls == 0


def test_wait_logic():
    cal = MarketCalendar()
    assert seconds_until_next_check(cal, kst(2026, 10, 19, 10), True, 30) == 1800
    assert seconds_until_next_check(cal, kst(2026, 10, 19, 9, 0), False, 30) == 300  # 09:05 까지 5분
    assert seconds_until_next_check(cal, kst(2026, 10, 3, 12), False, 30) == 1800  # 연휴 중엔 30분 간격 확인
    assert seconds_until_next_check(cal, kst(2026, 10, 19, 9, 30), False, 30) == 300  # 개장 지연 재확인
