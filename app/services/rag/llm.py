"""LLM 프로바이더 추상화.

현재 배선은 Gemini(REST, SDK 미사용 — resume_feedback.py 패턴 재사용). 설계상 나중에
Claude로 교체 가능하도록 인터페이스를 좁게 둔다. 키가 없거나 호출이 실패하면 None을
반환해 호출부가 degraded 폴백을 타게 한다(정직성 원칙).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Protocol

from app.core.config import settings

# 표준 Gemini generateContent 엔드포인트. {model}에 settings.gemini_model이 들어간다.
GEMINI_URL_TMPL = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)


class LLMClient(Protocol):
    """좁은 LLM 인터페이스. 실패 시 None(폴백 유도)."""

    def json(self, system: str, prompt: str, temperature: float = 0.2) -> dict | None: ...

    def text(self, system: str, prompt: str, temperature: float = 0.4) -> str | None: ...


def _extract_text(body: dict[str, Any]) -> str:
    """Gemini interactions 응답에서 텍스트를 추출(여러 스키마 형태 대응)."""
    output_text = body.get("output_text")
    if isinstance(output_text, str):
        return output_text
    texts: list[str] = []
    for step in body.get("steps", []):
        for part in step.get("content", []):
            if isinstance(part.get("text"), str):
                texts.append(part["text"])
    if texts:
        return "\n".join(texts)
    for cand in body.get("candidates", []):
        for part in cand.get("content", {}).get("parts", []):
            if isinstance(part.get("text"), str):
                texts.append(part["text"])
    return "\n".join(texts)


def _parse_json_object(text: str) -> dict[str, Any] | None:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.lower().startswith("json"):
            stripped = stripped[4:]
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        obj = json.loads(stripped[start : end + 1])
        return obj if isinstance(obj, dict) else None
    except (ValueError, TypeError):
        return None


class GeminiClient:
    """Gemini REST 클라이언트. 실패는 조용히 None(호출부 degraded 처리)."""

    def _call(self, system: str, prompt: str, temperature: float) -> str | None:
        if not settings.gemini_api_key:
            return None
        body = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "systemInstruction": {"parts": [{"text": system}]},
            "generationConfig": {
                "temperature": temperature,
                # thinkingLevel은 generationConfig 최상위가 아니라 반드시 thinkingConfig
                # 안에 중첩되어야 한다. 최상위에 두면 API가 HTTP 400 "Unknown name"을 반환한다.
                "thinkingConfig": {"thinkingLevel": settings.gemini_thinking_level},
                "maxOutputTokens": settings.gemini_max_output_tokens,
            },
        }
        req = urllib.request.Request(
            GEMINI_URL_TMPL.format(model=settings.gemini_model),
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": settings.gemini_api_key,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=settings.gemini_timeout_seconds) as resp:
                parsed = json.loads(resp.read().decode("utf-8"))
        except (TimeoutError, urllib.error.URLError, urllib.error.HTTPError, ValueError):
            return None
        return _extract_text(parsed) or None

    def json(self, system: str, prompt: str, temperature: float = 0.2) -> dict | None:
        text = self._call(system, prompt, temperature)
        return _parse_json_object(text) if text else None

    def text(self, system: str, prompt: str, temperature: float = 0.4) -> str | None:
        return self._call(system, prompt, temperature)


class NullClient:
    """LLM 미가용 환경용 — 항상 None을 반환해 결정론적 폴백을 강제."""

    def json(self, system: str, prompt: str, temperature: float = 0.2) -> dict | None:
        return None

    def text(self, system: str, prompt: str, temperature: float = 0.4) -> str | None:
        return None


def get_llm() -> LLMClient:
    """설정에 따라 LLM 클라이언트 팩토리. 키 없으면 NullClient(항상 폴백)."""
    if settings.gemini_api_key:
        return GeminiClient()
    return NullClient()
