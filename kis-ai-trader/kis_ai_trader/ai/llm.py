from __future__ import annotations

import json
import re

import requests

from ..config import LLMConfig


class LLMError(RuntimeError):
    pass


def extract_json(text: str) -> dict:
    """LLM 응답에서 JSON 객체를 추출 (코드펜스/앞뒤 설명문 허용)."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise LLMError(f"JSON 응답이 아님: {text[:200]}")
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise LLMError(f"JSON 파싱 실패: {exc}") from exc


class LLMClient:
    def __init__(self, cfg: LLMConfig, session: requests.Session | None = None):
        self.cfg = cfg
        self.session = session or requests.Session()

    def complete_json(self, system: str, user: str) -> dict:
        if self.cfg.provider == "anthropic":
            return extract_json(self._anthropic(system, user))
        if self.cfg.provider == "openai":
            return extract_json(self._openai(system, user))
        raise LLMError(f"지원하지 않는 LLM_PROVIDER: {self.cfg.provider}")

    def _openai(self, system: str, user: str) -> str:
        base = (self.cfg.base_url or "https://api.openai.com/v1").rstrip("/")
        payload: dict = {
            "model": self.cfg.model or "gpt-4o-mini",
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": {"type": "json_object"},
        }
        if self.cfg.temperature is not None:
            payload["temperature"] = self.cfg.temperature
        headers = {"Authorization": f"Bearer {self.cfg.api_key}"} if self.cfg.api_key else {}
        resp = self.session.post(f"{base}/chat/completions", json=payload, headers=headers, timeout=self.cfg.timeout)
        if resp.status_code >= 400:
            raise LLMError(f"OpenAI 호환 API 오류 {resp.status_code}: {resp.text[:300]}")
        return resp.json()["choices"][0]["message"]["content"]

    def _anthropic(self, system: str, user: str) -> str:
        base = (self.cfg.base_url or "https://api.anthropic.com").rstrip("/")
        payload: dict = {
            "model": self.cfg.model or "claude-sonnet-4-5",
            "max_tokens": 4000,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        if self.cfg.temperature is not None:
            payload["temperature"] = self.cfg.temperature
        resp = self.session.post(
            f"{base}/v1/messages",
            json=payload,
            headers={"x-api-key": self.cfg.api_key, "anthropic-version": "2023-06-01"},
            timeout=self.cfg.timeout,
        )
        if resp.status_code >= 400:
            raise LLMError(f"Anthropic API 오류 {resp.status_code}: {resp.text[:300]}")
        return "".join(b.get("text", "") for b in resp.json().get("content", []) if b.get("type") == "text")
