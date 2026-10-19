from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

from .ai import LLMAnalyst, LLMClient, RuleAnalyst
from .analysis import KISMarketData, screen
from .backtest import load_csv_dir, make_synthetic_frames, run_backtest
from .brokers import KISBroker, SimBroker
from .config import Settings, load_settings
from .engine import Plan, TradingEngine
from .journal import Journal

KST = ZoneInfo("Asia/Seoul")
log = logging.getLogger("kis_ai_trader")


def is_market_open(now: datetime | None = None) -> bool:
    now = now or datetime.now(KST)
    # 장 시작 직후 변동성과 종가 동시호가 구간은 피한다.
    return now.weekday() < 5 and dtime(9, 5) <= now.time() <= dtime(15, 15)


def make_analyst(settings: Settings, use_llm: bool = True):
    if use_llm and settings.llm.enabled:
        return LLMAnalyst(LLMClient(settings.llm))
    return RuleAnalyst()


def make_kis(settings: Settings):
    from .kis import KISApi, KISClient

    if not settings.kis.configured:
        sys.exit("KIS_APP_KEY / KIS_APP_SECRET / KIS_ACCOUNT_NO 를 .env 에 설정하세요. (키 없이 체험: backtest --demo)")
    return KISApi(KISClient(settings.kis))


def guard_real(settings: Settings, live: bool) -> None:
    if settings.kis.is_paper:
        return
    import os

    if not live or os.getenv("KIS_CONFIRM_REAL", "").upper() != "YES":
        sys.exit(
            "실전투자(KIS_ENV=real)는 실제 돈이 움직입니다. "
            "충분한 모의투자 검증 후 KIS_CONFIRM_REAL=YES 설정과 --live 플래그를 함께 사용하세요."
        )


def build_engine(settings: Settings, use_llm: bool = True) -> tuple[TradingEngine, KISMarketData]:
    api = make_kis(settings)
    md = KISMarketData(api, settings.trading.watchlist or None, settings.trading.use_volume_rank)
    tc = settings.trading
    if tc.broker == "sim":
        broker = SimBroker(
            md.price, tc.sim_initial_cash, tc.fee_rate, tc.sell_tax_rate, tc.slippage_pct,
            state_path=Path(tc.db_path).with_name("sim_account.json"),
        )
    else:
        broker = KISBroker(api)
    engine = TradingEngine(broker, md, make_analyst(settings, use_llm), settings.risk, Journal(tc.db_path), tc.candidates)
    return engine, md


def print_plan(plan: Plan) -> None:
    acc = plan.account
    print(f"\n=== {datetime.now(KST):%Y-%m-%d %H:%M} | 평가금액 {acc.equity:,.0f}원 | 현금 {acc.cash:,.0f}원 ===")
    for p in acc.positions.values():
        print(f"  보유 {p.code} {p.name:<12} {p.qty:>6}주  평단 {p.avg_price:>10,.0f}  현재 {p.price:>10,.0f}  {p.pnl_pct:+.2%}")
    print("\n[발굴 후보 상위]")
    for c in plan.candidates:
        f = c.features
        print(f"  {c.code} {c.name:<12} 점수 {c.score:5.1f}  20일 {f['ret_20d']:+6.1%}  RSI {f['rsi14']:5.1f}  거래량비 {f['volume_ratio']:.1f}")
    if plan.analysis:
        print(f"\n[AI 판단 - {plan.analysis.source}] {plan.analysis.market_view}")
        for d in plan.analysis.decisions:
            if d.action != "HOLD" or d.code in acc.positions:
                print(f"  {d.action:<4} {d.code} 확신 {d.confidence:.2f}  {d.reason}")
    print("\n[주문 계획]" if plan.orders else "\n[주문 없음]")
    for o in plan.orders:
        print(f"  {o.side.upper():<4} {o.code} {o.name} x{o.qty}  - {o.reason}")
    for n in plan.notes:
        print(f"  * {n}")


def cmd_scan(settings: Settings, args) -> None:
    md = KISMarketData(make_kis(settings), settings.trading.watchlist or None, settings.trading.use_volume_rank)
    cands, _ = screen(md, [], args.top)
    for c in cands:
        f = c.features
        print(f"{c.code} {c.name:<14} 점수 {c.score:5.1f}  종가 {f['close']:>10,.0f}  5일 {f['ret_5d']:+6.1%}  "
              f"20일 {f['ret_20d']:+6.1%}  RSI {f['rsi14']:5.1f}  거래량비 {f['volume_ratio']:.1f}")


def cmd_run(settings: Settings, args) -> None:
    guard_real(settings, args.live)
    engine, md = build_engine(settings, not args.no_llm)
    print(f"브로커: {engine.broker.name} | 분석가: {type(engine.analyst).__name__} | dry-run: {args.dry_run}")
    while True:
        if args.force or is_market_open():
            md.reset_intraday()
            try:
                plan, _ = engine.run_cycle(dry_run=args.dry_run)
                print_plan(plan)
            except Exception:
                log.exception("매매 사이클 실패")
                if args.once:
                    raise
        else:
            print(f"{datetime.now(KST):%H:%M} 장 운영시간 아님 (평일 09:05~15:15). --force 로 강제 실행 가능")
        if args.once:
            return
        time.sleep(settings.trading.loop_interval_min * 60)


def cmd_backtest(settings: Settings, args) -> None:
    names = None
    if args.demo:
        frames = make_synthetic_frames(n_codes=args.n_codes, days=args.days, seed=args.seed)
    elif args.csv_dir:
        frames = load_csv_dir(args.csv_dir)
    else:
        from .analysis import DEFAULT_UNIVERSE

        api = make_kis(settings)
        codes = args.codes or settings.trading.watchlist or list(DEFAULT_UNIVERSE)[:20]
        frames = {}
        for c in codes:
            df = api.get_daily_ohlcv(c, days=args.days)
            if len(df):
                frames[c] = df
                print(f"  {c} {len(df)}일 로드")
        names = DEFAULT_UNIVERSE
    analyst = make_analyst(settings, args.use_llm)
    if args.use_llm and isinstance(analyst, LLMAnalyst):
        print("주의: LLM 백테스트는 거래일마다 API를 호출하므로 비용이 발생합니다.")
    res = run_backtest(frames, analyst, settings, names=names)
    print(res.summary())
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        res.equity.rename("equity").to_frame().assign(benchmark=res.benchmark).to_csv(args.out)
        print(f"자산곡선 저장: {args.out}")


def cmd_report(settings: Settings, args) -> None:
    j = Journal(settings.trading.db_path)
    perf = j.performance_summary(recent=args.recent)
    curve = j.equity_curve()
    if curve:
        first, last = curve[0]["equity"], curve[-1]["equity"]
        print(f"평가금액 {first:,.0f} → {last:,.0f} ({last / first - 1:+.2%}), 기록 {len(curve)}회")
    if not perf.get("closed_trades"):
        print(perf.get("note"))
        return
    print(f"청산 {perf['closed_trades']}건 | 승률 {perf['win_rate']:.1%} | 평균이익 {perf['avg_win_pct']:+.2%} | 평균손실 {perf['avg_loss_pct']:+.2%}")
    for k, v in perf["exit_reason_stats"].items():
        print(f"  {k}: {v['n']}건 평균 {v['avg_pnl']:+.2%}")
    for t in perf["recent_trades"]:
        print(f"  {t['code']} {t['name']} {t['pnl_pct']:+.2%} | {t['exit_reason']}")


def cmd_balance(settings: Settings, args) -> None:
    engine, _ = build_engine(settings, use_llm=False)
    acc = engine.broker.get_account()
    print(f"[{engine.broker.name}] 평가금액 {acc.equity:,.0f}원, 현금 {acc.cash:,.0f}원")
    for p in acc.positions.values():
        print(f"  {p.code} {p.name} {p.qty}주 평단 {p.avg_price:,.0f} 현재 {p.price:,.0f} ({p.pnl_pct:+.2%})")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="kis-ai-trader", description="한국투자증권 API 기반 AI 주식 자동매매")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("scan", help="종목 발굴 결과 보기")
    p.add_argument("--top", type=int, default=15)

    p = sub.add_parser("run", help="AI 자동매매 실행")
    p.add_argument("--once", action="store_true", help="한 사이클만 실행")
    p.add_argument("--dry-run", action="store_true", help="주문 없이 판단만 출력")
    p.add_argument("--force", action="store_true", help="장 운영시간 검사 무시")
    p.add_argument("--no-llm", action="store_true", help="규칙 기반 분석가 사용")
    p.add_argument("--live", action="store_true", help="실전투자 허용 (KIS_CONFIRM_REAL=YES 필요)")

    p = sub.add_parser("backtest", help="과거 데이터 백테스트")
    p.add_argument("--demo", action="store_true", help="가상 시세로 오프라인 실행")
    p.add_argument("--csv-dir")
    p.add_argument("--codes", nargs="*")
    p.add_argument("--days", type=int, default=500)
    p.add_argument("--n-codes", type=int, default=20)
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--use-llm", action="store_true")
    p.add_argument("--out", help="자산곡선 CSV 저장 경로")

    p = sub.add_parser("report", help="매매일지 성과 리포트")
    p.add_argument("--recent", type=int, default=10)

    sub.add_parser("balance", help="계좌 잔고")

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = load_settings(args.env_file)
    {"scan": cmd_scan, "run": cmd_run, "backtest": cmd_backtest, "report": cmd_report, "balance": cmd_balance}[args.cmd](
        settings, args
    )


if __name__ == "__main__":
    main()
