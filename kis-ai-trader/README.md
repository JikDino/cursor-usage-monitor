# KIS AI Trader

한국투자증권(KIS) Open API와 LLM을 이용한 국내주식 AI 자동매매 프로그램입니다.
AI가 **종목 발굴 → 매수/매도 판단 → 모의매매 → 성과 회고**를 직접 수행하고,
코드로 강제되는 리스크 규칙이 AI의 실수로부터 계좌를 보호합니다.

> **주의**: 투자 손실 책임은 사용자에게 있습니다. 반드시 **모의투자(KIS_ENV=paper)와 백테스트로 충분히 검증**한 뒤 실전을 고려하세요.
> 실전 모드는 `KIS_CONFIRM_REAL=YES` 와 `--live` 플래그가 모두 있어야만 동작합니다.

## 동작 구조

```
 ┌──────────── 매 사이클 (기본 30분, 장중 09:05~15:15) ────────────┐
 │ 1. 계좌 조회        KIS 잔고 / 로컬 가상계좌                     │
 │ 2. 종목 발굴        관심종목 + 거래량 상위 → 일봉 지표 → 퀀트 점수 │
 │ 3. 강제 청산        손절 / 익절 / 트레일링 스탑 (AI 무시 불가)     │
 │ 4. AI 판단          후보·보유종목·계좌·과거 성과(회고) → JSON 결정 │
 │ 5. 검증·포지션 산정  허용 종목/범위 검증, 비중·위험·현금 한도       │
 │ 6. 주문 실행        매도 먼저, 확신도 높은 매수 순                │
 │ 7. 매매일지 기록     판단 근거·주문·청산 손익 → 다음 회고에 반영    │
 └─────────────────────────────────────────────────────────────────┘
```

| 모듈 | 역할 |
| --- | --- |
| `kis/` | 토큰 발급·디스크 캐시(24h), 호출 속도 제한, 시세/일봉/거래량순위/잔고/주문 API |
| `analysis/` | 기술지표(이동평균, RSI, MACD, ATR, 볼린저, 거래량비), 추세·모멘텀 점수, 종목 스크리너 |
| `ai/` | LLM 클라이언트(OpenAI 호환·Anthropic), 프롬프트, 응답 검증, 규칙 기반 대체 분석가 |
| `risk.py` | 손절/익절/트레일링, 일일 손실 한도, 거래 횟수 한도, 포지션 사이징 |
| `brokers/` | `KISBroker`(한투 모의/실전 계좌 주문), `SimBroker`(수수료·세금·슬리피지 반영 로컬 가상계좌) |
| `engine.py` | 위 단계를 묶은 매매 사이클 |
| `backtest.py` | 동일 엔진으로 과거 데이터 검증 (t일 종가로 판단, t+1일 시가 체결) |
| `journal.py` | SQLite 매매일지 및 성과 요약(AI 회고 입력) |

## 설치

```bash
cd kis-ai-trader
pip install -r requirements.txt
cp .env.example .env   # 앱키, 계좌번호, LLM 키 입력
```

1. [KIS Developers](https://apiportal.koreainvestment.com)에서 **모의투자 계좌 신청 후 모의투자용 앱키**를 발급받습니다.
2. `.env`에 `KIS_APP_KEY`, `KIS_APP_SECRET`, `KIS_ACCOUNT_NO`(앞 8자리)를 입력합니다.
3. AI 분석을 쓰려면 `LLM_PROVIDER`, `LLM_MODEL`, `LLM_API_KEY`를 입력합니다.
   키가 없으면 규칙 기반 분석가로 동작합니다. OpenAI 호환 서버(Gemini, Ollama 등)는 `LLM_BASE_URL`로 지정합니다.

## 사용법

```bash
# 0) 키 없이 바로: 가상 시세로 전체 파이프라인 백테스트
python3 -m kis_ai_trader backtest --demo

# 1) 종목 발굴 결과 확인
python3 -m kis_ai_trader scan

# 2) 주문 없이 AI 판단만 확인 (장 시간 무시)
python3 -m kis_ai_trader run --once --dry-run --force

# 3) 한투 모의투자 계좌로 자동매매 (장중 30분마다 반복)
python3 -m kis_ai_trader run

# 4) 실제 시세 백테스트 (규칙 기반 / LLM 사용 시 --use-llm, 비용 발생)
python3 -m kis_ai_trader backtest --codes 005930 000660 035420 --days 500 --out data/equity.csv

# 5) 성과 리포트 / 잔고
python3 -m kis_ai_trader report
python3 -m kis_ai_trader balance
```

`BROKER=sim` 으로 두면 한투 모의계좌 대신 로컬 가상계좌(`data/sim_account.json`)로 체결합니다.
시세는 계속 KIS에서 가져오므로 모의투자 계좌 없이도 실시간 모의매매가 가능합니다.

## 수익 개선 방법 (권장 순서)

1. **기준선 측정**: `backtest`로 규칙 기반 전략의 수익률·MDD·샤프를 기록합니다. 동일비중 보유(벤치마크)보다 나은지가 첫 기준입니다.
2. **리스크 파라미터 튜닝**: `.env`의 `STOP_LOSS_PCT`, `TRAILING_STOP_PCT`, `MAX_POSITIONS`, `RISK_PER_TRADE_PCT`를 바꿔 백테스트를 반복합니다. 한 번에 하나씩 바꿉니다.
3. **AI 비교**: 같은 기간을 `--use-llm`으로 돌려 규칙 기반보다 나은지 확인합니다(짧은 기간·적은 종목으로 비용 관리).
4. **모의투자 운용**: 최소 수 주간 `run`으로 모의투자하며 `report`의 청산 사유별 손익을 확인합니다. AI는 이 성과 요약을 매 사이클 프롬프트로 받아 자기 실수를 반영합니다.
5. 과최적화를 피하려면 튜닝 기간과 검증 기간을 나누고(예: 앞 2/3로 튜닝, 뒤 1/3로 검증), 거래비용을 줄이지 마세요.

## 리스크 규칙 (AI가 넘을 수 없음)

- 종목당 최대 비중 `MAX_POSITION_PCT`, 최대 보유 종목 수 `MAX_POSITIONS`, 최소 현금 비중 `MIN_CASH_PCT`
- 거래당 손실 위험 `RISK_PER_TRADE_PCT` (손절폭 기반 수량 산정)
- AI가 제시한 손절폭은 `STOP_LOSS_PCT`보다 넓어질 수 없음
- 전일 대비 `DAILY_LOSS_LIMIT_PCT` 이상 손실 시 신규 매수 중단, 하루 거래 횟수 제한
- 확신도 `MIN_CONFIDENCE` 미만 매수 보류, 후보 목록 밖 종목·미보유 종목 매도 등 잘못된 응답은 폐기
- LLM 장애 시 규칙 기반 분석가로 자동 대체

## 참고 및 한계

- 거래량 순위 API는 모의투자 서버에서 지원되지 않아, 모의투자에서는 관심종목(`WATCHLIST` 또는 기본 대형주 40종목)만 탐색합니다.
- 일봉 기반 스윙 전략입니다. 초단타/호가 매매에는 적합하지 않습니다.
- 매도 거래세(`SELL_TAX_RATE`, 기본 0.20%)와 수수료(`FEE_RATE`)는 증권사/연도별로 다르니 본인 조건에 맞게 조정하세요.
- 공휴일 휴장은 별도로 판별하지 않습니다(휴장일 주문은 KIS가 거부).

## 테스트

```bash
python3 -m pytest -q
```

KIS API는 가짜 HTTP 세션, LLM은 가짜 클라이언트로 대체해 키 없이 실행됩니다.
