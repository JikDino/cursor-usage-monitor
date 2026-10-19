from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

REAL_URL = "https://openapi.koreainvestment.com:9443"
PAPER_URL = "https://openapivts.koreainvestment.com:29443"


def _env(name: str, default: str = "") -> str:
    value = os.getenv(name)
    return default if value is None or value.strip() == "" else value.strip()


def _float(name: str, default: float) -> float:
    return float(_env(name, str(default)))


def _int(name: str, default: int) -> int:
    return int(_env(name, str(default)))


def _bool(name: str, default: bool) -> bool:
    return _env(name, str(default)).lower() in {"1", "true", "yes", "y", "on"}


@dataclass
class KISConfig:
    app_key: str = ""
    app_secret: str = ""
    account_no: str = ""
    account_product: str = "01"
    env: str = "paper"

    @property
    def is_paper(self) -> bool:
        return self.env != "real"

    @property
    def base_url(self) -> str:
        return PAPER_URL if self.is_paper else REAL_URL

    @property
    def configured(self) -> bool:
        return bool(self.app_key and self.app_secret and self.account_no)


@dataclass
class LLMConfig:
    provider: str = "none"
    model: str = ""
    api_key: str = ""
    base_url: str = ""
    temperature: float | None = None
    timeout: int = 90

    @property
    def enabled(self) -> bool:
        return self.provider in {"openai", "anthropic"} and bool(self.api_key or self.base_url)


@dataclass
class RiskConfig:
    max_positions: int = 5
    max_position_pct: float = 0.20
    min_cash_pct: float = 0.10
    risk_per_trade_pct: float = 0.01
    stop_loss_pct: float = 0.05
    take_profit_pct: float = 0.15
    trailing_stop_pct: float = 0.07
    daily_loss_limit_pct: float = 0.03
    min_confidence: float = 0.6
    max_trades_per_day: int = 10


@dataclass
class TradingConfig:
    broker: str = "kis"
    sim_initial_cash: float = 10_000_000
    watchlist: list[str] = field(default_factory=list)
    use_volume_rank: bool = True
    candidates: int = 10
    loop_interval_min: int = 30
    db_path: str = "data/trader.db"
    fee_rate: float = 0.00015
    sell_tax_rate: float = 0.002
    slippage_pct: float = 0.001


@dataclass
class Settings:
    kis: KISConfig = field(default_factory=KISConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    trading: TradingConfig = field(default_factory=TradingConfig)


def load_settings(env_file: str | None = ".env") -> Settings:
    if env_file:
        load_dotenv(env_file, override=False)

    kis = KISConfig(
        app_key=_env("KIS_APP_KEY"),
        app_secret=_env("KIS_APP_SECRET"),
        account_no=_env("KIS_ACCOUNT_NO"),
        account_product=_env("KIS_ACCOUNT_PRODUCT", "01"),
        env=_env("KIS_ENV", "paper").lower(),
    )
    temperature = _env("LLM_TEMPERATURE")
    llm = LLMConfig(
        provider=_env("LLM_PROVIDER", "none").lower(),
        model=_env("LLM_MODEL"),
        api_key=_env("LLM_API_KEY"),
        base_url=_env("LLM_BASE_URL"),
        temperature=float(temperature) if temperature else None,
    )
    risk = RiskConfig(
        max_positions=_int("MAX_POSITIONS", 5),
        max_position_pct=_float("MAX_POSITION_PCT", 0.20),
        min_cash_pct=_float("MIN_CASH_PCT", 0.10),
        risk_per_trade_pct=_float("RISK_PER_TRADE_PCT", 0.01),
        stop_loss_pct=_float("STOP_LOSS_PCT", 0.05),
        take_profit_pct=_float("TAKE_PROFIT_PCT", 0.15),
        trailing_stop_pct=_float("TRAILING_STOP_PCT", 0.07),
        daily_loss_limit_pct=_float("DAILY_LOSS_LIMIT_PCT", 0.03),
        min_confidence=_float("MIN_CONFIDENCE", 0.6),
        max_trades_per_day=_int("MAX_TRADES_PER_DAY", 10),
    )
    watchlist = [c.strip() for c in _env("WATCHLIST").split(",") if c.strip()]
    trading = TradingConfig(
        broker=_env("BROKER", "kis").lower(),
        sim_initial_cash=_float("SIM_INITIAL_CASH", 10_000_000),
        watchlist=watchlist,
        use_volume_rank=_bool("USE_VOLUME_RANK", True),
        candidates=_int("CANDIDATES", 10),
        loop_interval_min=_int("LOOP_INTERVAL_MIN", 30),
        db_path=_env("DB_PATH", "data/trader.db"),
        fee_rate=_float("FEE_RATE", 0.00015),
        sell_tax_rate=_float("SELL_TAX_RATE", 0.002),
        slippage_pct=_float("SLIPPAGE_PCT", 0.001),
    )
    return Settings(kis=kis, llm=llm, risk=risk, trading=trading)
