"""LLM 프로바이더 추상화.

현재 배선은 Gemini(REST, SDK 미사용 — resume_feedback.py 패턴 재사용). 설계상 나중에
Claude로 교체 가능하도록 인터페이스를 좁게 둔다. 키가 없거나 호출이 실패하면 None을
반환해 호출부가 degraded 폴백을 타게 한다(정직성 원칙).
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any, Protocol

from app.core.config import settings

# 표준 Gemini generateContent 엔드포인트. {model}에 settings.gemini_model이 들어간다.
GEMINI_URL_TMPL = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)


class LLMClient(Protocol):
    """좁은 LLM 인터페이스. 실패 시 None(폴백 유도).

    last_debug는 json()/text() 호출 직후 호출부(router.py/synthesis.py)가 읽어 verbose
    로그에 실어 보내는 사이드채널이다 — 반환 타입(dict|None, str|None)을 그대로 지키면서
    모델명·temperature·시도 횟수·지연시간·토큰 수를 흘려보내기 위해 반환값 대신 인스턴스
    속성으로 노출한다.
    """

    last_debug: dict[str, Any] | None
    call_count: int

    def json(
        self,
        system: str,
        prompt: str,
        temperature: float = 0.2,
        *,
        max_output_tokens: int | None = None,
    ) -> dict | None: ...

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


def _thinking_config(model: str, level: str) -> dict[str, Any] | None:
    """모델 계열에 맞는 thinking 설정을 고른다.

    gemini-2.5 계열은 thinkingLevel을 모르고 thinkingBudget(정수)만 받는다. thinkingLevel을
    보내면 HTTP 400 "Thinking level is not supported for this model."로 모든 호출이 실패한다.
    gemini-3 계열은 thinkingLevel(문자열)을 받는다. 계열을 모르면 thinkingConfig를 아예
    빼서(생략) 어느 모델에서도 400을 내지 않게 안전하게 둔다.
    """
    if model.startswith("gemini-2.5") or model.startswith("gemini-2.0"):
        # minimal은 사고를 끄고(0), 그 외 단계는 동적 예산(-1)으로 모델이 알아서 정하게 둔다.
        return {"thinkingBudget": 0 if level == "minimal" else -1}
    if model.startswith("gemini-3"):
        return {"thinkingLevel": level}
    return None


def _parse_json_object(text: str) -> dict[str, Any] | None:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.lower().startswith("json"):
            stripped = stripped[4:]
    stripped = stripped.strip()

    # extract_requirements/judge_requirements는 프롬프트에서 "items 배열로 답한다"고
    # 요청하는데, 모델이 이를 곧이곧대로 따라 최상위가 객체가 아니라 배열인 응답을
    # 종종 내놓는다. 아래 중괄호 스캔은 배열({로 시작하지 않음)을 다루지 못해 이런
    # 응답을 통째로 놓쳤다 — 먼저 전체를 그대로 파싱해보고, 배열이면 items로 감싸서
    # 호출부(둘 다 "items" 키를 읽는다)가 그대로 쓸 수 있게 한다.
    try:
        whole = json.loads(stripped)
    except (ValueError, TypeError):
        whole = None
    if isinstance(whole, dict):
        return whole
    if isinstance(whole, list):
        return {"items": whole}

    # 통째로는 못 읽었을 때만(앞뒤에 설명 문장이 붙은 경우 등) 중괄호 범위를 긁어 재시도한다.
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

    def __init__(self) -> None:
        self.last_debug: dict[str, Any] | None = None
        # 호출부(pipeline.py)가 "이 단계에서 LLM이 실제로 호출됐는지"를 판별하는 카운터.
        # last_debug 하나만으로는 이전 단계의 값이 남아있는 건지 이번 단계 값인지 구분이 안 된다.
        self.call_count = 0

    def _call(
        self,
        system: str,
        prompt: str,
        temperature: float,
        *,
        max_output_tokens: int | None = None,
        response_mime_type: str | None = None,
    ) -> str | None:
        if not settings.gemini_api_key:
            return None
        self.call_count += 1
        gen_cfg: dict[str, Any] = {
            "temperature": temperature,
            "maxOutputTokens": max_output_tokens or settings.gemini_max_output_tokens,
        }
        thinking = _thinking_config(settings.gemini_model, settings.gemini_thinking_level)
        if thinking is not None:
            gen_cfg["thinkingConfig"] = thinking
        if response_mime_type:
            gen_cfg["responseMimeType"] = response_mime_type
        body = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "systemInstruction": {"parts": [{"text": system}]},
            "generationConfig": gen_cfg,
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

        start = time.perf_counter()
        max_attempts = 1 + settings.gemini_max_retries
        last_error: Exception | None = None
        attempt = 0
        for attempt in range(1, max_attempts + 1):
            try:
                with urllib.request.urlopen(req, timeout=settings.gemini_timeout_seconds) as resp:
                    parsed = json.loads(resp.read().decode("utf-8"))
            except (TimeoutError, urllib.error.URLError, urllib.error.HTTPError, ValueError) as exc:
                last_error = exc
                # 4xx(잘못된 요청/인증 실패/만료 키 등)는 재시도해도 성공할 수 없다 — 지연을 방지하기 위해 즉시 중단한다.
                if isinstance(exc, urllib.error.HTTPError) and 400 <= exc.code < 500:
                    break
                # API 키가 유효하지 않은 경우(INVALID_KEY 등) URLError/HTTPError 발생 시 재시도 없이 중단
                if "INVALID" in str(settings.gemini_api_key).upper() or "EXPIRED" in str(settings.gemini_api_key).upper():
                    break
                continue

            usage = parsed.get("usageMetadata", {})
            self.last_debug = {
                "model": settings.gemini_model,
                "temperature": temperature,
                "attempts": attempt,
                "latency_ms": round((time.perf_counter() - start) * 1000, 1),
                "prompt_tokens": usage.get("promptTokenCount"),
                "output_tokens": usage.get("candidatesTokenCount"),
                "total_tokens": usage.get("totalTokenCount"),
            }
            return _extract_text(parsed) or None

        self.last_debug = {
            "model": settings.gemini_model,
            "temperature": temperature,
            "attempts": attempt,
            "latency_ms": round((time.perf_counter() - start) * 1000, 1),
            "error": str(last_error) if last_error else "알 수 없는 오류",
        }
        return None

    def json(
        self,
        system: str,
        prompt: str,
        temperature: float = 0.2,
        *,
        max_output_tokens: int | None = None,
    ) -> dict | None:
        text = self._call(
            system,
            prompt,
            temperature,
            max_output_tokens=max_output_tokens,
            response_mime_type="application/json",
        )
        return _parse_json_object(text) if text else None

    def text(self, system: str, prompt: str, temperature: float = 0.4) -> str | None:
        return self._call(system, prompt, temperature)


class NullClient:
    """LLM 미가용 환경용 — 항상 None을 반환해 결정론적 폴백을 강제."""

    def __init__(self) -> None:
        self.last_debug: dict[str, Any] | None = None
        self.call_count = 0

    def json(
        self,
        system: str,
        prompt: str,
        temperature: float = 0.2,
        *,
        max_output_tokens: int | None = None,
    ) -> dict | None:
        return None

    def text(self, system: str, prompt: str, temperature: float = 0.4) -> str | None:
        return None


def get_llm() -> LLMClient:
    """설정에 따라 LLM 클라이언트 팩토리. 키 없으면 NullClient(항상 폴백)."""
    if settings.gemini_api_key:
        return GeminiClient()
    return NullClient()
