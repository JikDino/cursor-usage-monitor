"""KIS AI Trader 실행 파일.

    python start.py            # 자동매매 시작 (처음이면 키 입력 마법사 실행)
    python start.py --setup    # 키/설정 다시 입력
    python start.py --dry-run  # 주문 없이 AI 판단만 출력

Windows 는 start.bat 을 더블클릭해도 된다.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENV_FILE = ROOT / ".env"
ENV_EXAMPLE = ROOT / ".env.example"
REQUIRED_MODULES = ("requests", "pandas", "numpy", "dotenv", "tzdata")


def ensure_python() -> None:
    if sys.version_info < (3, 10):
        sys.exit(f"Python 3.10 이상이 필요합니다. 현재 {sys.version.split()[0]}")


def ensure_packages() -> None:
    import importlib.util

    missing = [m for m in REQUIRED_MODULES if importlib.util.find_spec(m) is None]
    if not missing:
        return
    print(f"필요한 패키지를 설치합니다: {', '.join(missing)}")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "-r", str(ROOT / "requirements.txt")])


def read_env() -> dict[str, str]:
    if not ENV_FILE.exists():
        return {}
    values = {}
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            values[k.strip()] = v.strip()
    return values


def write_env(updates: dict[str, str]) -> None:
    """.env.example 의 주석/순서를 유지한 채 값만 채워 .env 를 만든다."""
    base = ENV_FILE if ENV_FILE.exists() else ENV_EXAMPLE
    lines = base.read_text(encoding="utf-8").splitlines()
    done = set()
    for i, line in enumerate(lines):
        if "=" in line and not line.lstrip().startswith("#"):
            key = line.split("=", 1)[0].strip()
            if key in updates:
                lines[i] = f"{key}={updates[key]}"
                done.add(key)
    lines += [f"{k}={v}" for k, v in updates.items() if k not in done]
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def ask(prompt: str, default: str = "", required: bool = False) -> str:
    while True:
        suffix = f" [{default}]" if default else ""
        value = input(f"{prompt}{suffix}: ").strip() or default
        if value or not required:
            return value
        print("  값을 입력해야 합니다.")


def parse_account(raw: str) -> tuple[str, str]:
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) == 10:
        return digits[:8], digits[8:]
    if len(digits) == 8:
        return digits, "01"
    raise ValueError("계좌번호는 '12345678-01' 형식(숫자 10자리) 또는 앞 8자리로 입력하세요.")


def verify_kis(values: dict[str, str]) -> str | None:
    """키로 토큰을 발급받고 잔고를 조회해 본다. 실패 시 오류 메시지 반환."""
    sys.path.insert(0, str(ROOT))
    from kis_ai_trader.config import KISConfig
    from kis_ai_trader.kis import KISApi, KISClient

    cfg = KISConfig(values["KIS_APP_KEY"], values["KIS_APP_SECRET"], values["KIS_ACCOUNT_NO"],
                    values["KIS_ACCOUNT_PRODUCT"], values["KIS_ENV"])
    try:
        bal = KISApi(KISClient(cfg, token_cache=ROOT / "data" / ".kis_token.json")).get_balance()
    except Exception as exc:  # noqa: BLE001 - 사용자에게 원인을 그대로 보여준다
        return str(exc)
    print(f"  연결 성공! 예수금 {bal['cash']:,.0f}원, 보유 종목 {len(bal['holdings'])}개")
    return None


def setup_wizard() -> None:
    cur = read_env()
    print(
        "\n================ 초기 설정 ================\n"
        "입력한 값은 이 폴더의 .env 파일에 저장됩니다. (나중에 메모장으로 직접 수정 가능)\n\n"
        "[한국투자증권 키 발급 방법]\n"
        " 1. 한국투자증권 계좌 개설 후 https://apiportal.koreainvestment.com 로그인\n"
        " 2. 모의투자로 시작하려면 먼저 한투 홈페이지/앱에서 '모의투자 신청'\n"
        " 3. KIS Developers > API 신청 에서 모의투자(또는 실전) 앱키·앱시크릿 발급\n"
        "    * 모의투자 키와 실전 키는 서로 다릅니다. 계좌번호도 각 계좌의 번호를 넣으세요.\n"
    )
    mode = ask("투자 모드 선택 (1=모의투자, 2=실전투자)", "2" if cur.get("KIS_ENV") == "real" else "1")
    values = {"KIS_ENV": "real" if mode == "2" else "paper", "KIS_CONFIRM_REAL": "NO"}
    if values["KIS_ENV"] == "real":
        print("\n  실전투자는 실제 돈으로 주문합니다. 모의투자로 충분히 검증한 뒤 사용하세요.")
        if ask("  계속하려면 '실전' 이라고 입력하세요") == "실전":
            values["KIS_CONFIRM_REAL"] = "YES"
        else:
            print("  모의투자로 설정합니다.")
            values["KIS_ENV"] = "paper"

    while True:
        values["KIS_APP_KEY"] = ask("앱키 (APP KEY)", cur.get("KIS_APP_KEY", ""), required=True)
        values["KIS_APP_SECRET"] = ask("앱시크릿 (APP SECRET)", cur.get("KIS_APP_SECRET", ""), required=True)
        acct_default = f"{cur['KIS_ACCOUNT_NO']}-{cur.get('KIS_ACCOUNT_PRODUCT', '01')}" if cur.get("KIS_ACCOUNT_NO") else ""
        while True:
            try:
                values["KIS_ACCOUNT_NO"], values["KIS_ACCOUNT_PRODUCT"] = parse_account(
                    ask("계좌번호 (예: 50123456-01)", acct_default, required=True)
                )
                break
            except ValueError as exc:
                print(f"  {exc}")
        print("  한국투자증권 연결 확인 중...")
        err = verify_kis(values)
        if not err:
            break
        print(f"  연결 실패: {err}")
        if ask("  다시 입력할까요? (y/n)", "y").lower() != "y":
            break

    print(
        "\n[AI 설정] AI 키가 없으면 규칙 기반 전략으로 매매합니다.\n"
        " 1=OpenAI (https://platform.openai.com/api-keys)\n"
        " 2=Anthropic Claude (https://console.anthropic.com)\n"
        " 3=사용 안 함"
    )
    provider = {"1": "openai", "2": "anthropic"}.get(ask("선택", {"openai": "1", "anthropic": "2"}.get(cur.get("LLM_PROVIDER", ""), "3")), "none")
    values["LLM_PROVIDER"] = provider
    if provider != "none":
        default_model = {"openai": "gpt-4o-mini", "anthropic": "claude-sonnet-4-5"}[provider]
        values["LLM_API_KEY"] = ask("AI API 키", cur.get("LLM_API_KEY", ""), required=True)
        values["LLM_MODEL"] = ask("모델 이름", cur.get("LLM_MODEL") or default_model)

    write_env(values)
    print(f"\n설정 저장 완료: {ENV_FILE}\n")


def needs_setup() -> bool:
    env = read_env()
    return not (env.get("KIS_APP_KEY") and env.get("KIS_APP_SECRET") and env.get("KIS_ACCOUNT_NO"))


def main() -> None:
    ensure_python()
    os.chdir(ROOT)
    ensure_packages()
    args = sys.argv[1:]
    if "--setup" in args or needs_setup():
        args = [a for a in args if a != "--setup"]
        setup_wizard()

    sys.path.insert(0, str(ROOT))
    from kis_ai_trader.cli import main as cli_main

    run_args = ["--env-file", str(ENV_FILE), "run", *args]
    if read_env().get("KIS_CONFIRM_REAL", "").upper() == "YES" and "--live" not in run_args:
        run_args.append("--live")
    cli_main(run_args)


def _pause_if_double_clicked() -> None:
    # start.py 를 탐색기에서 직접 더블클릭하면 오류 메시지를 읽기 전에 창이 닫힌다.
    if os.name == "nt" and not os.getenv("KIS_FROM_BAT"):
        input("엔터를 누르면 창이 닫힙니다...")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n종료합니다.")
    except SystemExit as exc:
        if exc.code not in (None, 0):
            print(exc.code if isinstance(exc.code, str) else f"종료 코드 {exc.code}")
            _pause_if_double_clicked()
            sys.exit(1)
    except Exception:
        import traceback

        traceback.print_exc()
        _pause_if_double_clicked()
        sys.exit(1)
