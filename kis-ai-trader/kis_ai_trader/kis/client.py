from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import requests

from ..config import KISConfig

log = logging.getLogger(__name__)

TOKEN_EXPIRED_CODES = {"EGW00123", "EGW00121"}
RATE_LIMIT_CODES = {"EGW00201"}


class KISError(RuntimeError):
    def __init__(self, msg_cd: str, msg: str, payload: dict | None = None):
        super().__init__(f"[{msg_cd}] {msg}")
        self.msg_cd = msg_cd
        self.msg = msg
        self.payload = payload or {}


class KISClient:
    """Low-level REST client: OAuth token caching, throttling and error handling.

    KIS limits token issuance (about once per minute, and tokens are valid for
    24h), so tokens are cached on disk and reused across process restarts.
    """

    def __init__(
        self,
        cfg: KISConfig,
        token_cache: str | Path = "data/.kis_token.json",
        session: requests.Session | None = None,
        min_interval: float | None = None,
        max_retries: int = 3,
    ):
        if not cfg.configured:
            raise ValueError("KIS_APP_KEY / KIS_APP_SECRET / KIS_ACCOUNT_NO 를 .env 에 설정하세요.")
        self.cfg = cfg
        self.session = session or requests.Session()
        self.token_cache = Path(token_cache)
        # 모의투자 서버는 초당 호출 한도가 훨씬 낮다.
        self.min_interval = min_interval if min_interval is not None else (0.55 if cfg.is_paper else 0.07)
        self.max_retries = max_retries
        self._token: str | None = None
        self._token_exp: datetime | None = None
        self._last_call = 0.0

    # ---------------------------------------------------------------- token
    def _load_cached_token(self) -> bool:
        if not self.token_cache.exists():
            return False
        try:
            data = json.loads(self.token_cache.read_text())
        except (OSError, json.JSONDecodeError):
            return False
        if data.get("app_key") != self.cfg.app_key or data.get("env") != self.cfg.env:
            return False
        exp = datetime.fromisoformat(data["expires_at"])
        if exp - datetime.now() < timedelta(minutes=10):
            return False
        self._token, self._token_exp = data["access_token"], exp
        return True

    def _issue_token(self) -> None:
        resp = self.session.post(
            f"{self.cfg.base_url}/oauth2/tokenP",
            json={
                "grant_type": "client_credentials",
                "appkey": self.cfg.app_key,
                "appsecret": self.cfg.app_secret,
            },
            timeout=10,
        )
        data = resp.json()
        if "access_token" not in data:
            raise KISError(data.get("error_code", "TOKEN"), data.get("error_description", str(data)), data)
        self._token = data["access_token"]
        self._token_exp = datetime.now() + timedelta(seconds=int(data.get("expires_in", 86400)))
        self.token_cache.parent.mkdir(parents=True, exist_ok=True)
        self.token_cache.write_text(
            json.dumps(
                {
                    "app_key": self.cfg.app_key,
                    "env": self.cfg.env,
                    "access_token": self._token,
                    "expires_at": self._token_exp.isoformat(),
                }
            )
        )
        log.info("KIS 접근토큰 발급 완료 (만료 %s)", self._token_exp)

    def token(self, force: bool = False) -> str:
        if force or not self._token or not self._token_exp or self._token_exp - datetime.now() < timedelta(minutes=10):
            if force or not self._load_cached_token():
                self._issue_token()
        assert self._token
        return self._token

    # -------------------------------------------------------------- request
    def _throttle(self) -> None:
        wait = self.min_interval - (time.monotonic() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()

    def request(
        self,
        method: str,
        path: str,
        tr_id: str,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        tr_cont: str = "",
    ) -> tuple[dict, dict]:
        """Returns (json_body, response_headers). Raises KISError when rt_cd != '0'."""
        refreshed = False
        for attempt in range(self.max_retries + 1):
            self._throttle()
            headers = {
                "content-type": "application/json; charset=utf-8",
                "authorization": f"Bearer {self.token()}",
                "appkey": self.cfg.app_key,
                "appsecret": self.cfg.app_secret,
                "tr_id": tr_id,
                "tr_cont": tr_cont,
                "custtype": "P",
            }
            try:
                resp = self.session.request(
                    method, f"{self.cfg.base_url}{path}", headers=headers, params=params, json=body, timeout=15
                )
            except requests.RequestException as exc:
                if attempt >= self.max_retries:
                    raise
                log.warning("KIS 네트워크 오류 %s, 재시도 %d", exc, attempt + 1)
                time.sleep(1.0 * (attempt + 1))
                continue

            try:
                data = resp.json()
            except ValueError:
                data = {"rt_cd": "-1", "msg_cd": str(resp.status_code), "msg1": resp.text[:200]}

            msg_cd = data.get("msg_cd", "")
            if data.get("rt_cd") == "0":
                return data, dict(resp.headers)
            if msg_cd in TOKEN_EXPIRED_CODES and not refreshed:
                self.token(force=True)
                refreshed = True
                continue
            if (msg_cd in RATE_LIMIT_CODES or resp.status_code >= 500) and attempt < self.max_retries:
                time.sleep(1.0 * (attempt + 1))
                continue
            raise KISError(msg_cd, data.get("msg1", "unknown error"), data)
        raise KISError("RETRY", "재시도 한도 초과")
