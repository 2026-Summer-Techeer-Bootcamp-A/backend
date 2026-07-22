"""Gemini Flash 기반 이력서 실시간 LLM 파싱 — SSE 이벤트 스트리밍.

이벤트 순서:
  1. start          - 텍스트 추출 완료, 원문 일부와 함께 전송
  2. pii_detected   - 개인정보(이름·연락처·주소) 감지, 마스킹 지시
  3. skill_detected - 기술 스택 하나씩 (근거 문장 포함)
  4. cert_detected  - 자격증 하나씩
  5. memo_detected  - 메모로 쓸 문장 하나씩 (어필 포인트·맥락 요약)
  6. meta_detected  - 직무·경력 연차
  7. complete       - 전체 분석 완료 (full_data 포함)
  8. error          - 실패 (message 포함)
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Iterator
from typing import Any

from app.core.config import settings
from app.services.resume import extract_pdf_text

# ── 개인정보 정규식 패턴 ────────────────────────────────────────
_PII_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("phone",   re.compile(r"0\d{1,2}[-\s]?\d{3,4}[-\s]?\d{4}")),
    ("email",   re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")),
    ("address", re.compile(r"(?:서울|경기|인천|부산|대구|대전|광주|수원|용인|성남|고양)\s*(?:시|도)?\s*\S+")),
    ("url",     re.compile(r"https?://\S+")),
    ("jumin",   re.compile(r"\d{6}[-\s]\d{7}")),
]

# ── Gemini REST 직접 호출 (GeminiClient 재사용) ───────────────────
def _call_gemini(prompt: str, *, temperature: float = 0.1) -> str | None:
    """settings에서 API 키와 모델을 읽어 Gemini REST 호출."""
    if not settings.gemini_api_key:
        return None

    import urllib.request, urllib.error

    # 이력서 파싱에는 Flash(빠른 모델)를 우선 사용한다.
    # 설정된 모델이 Flash 계열이면 그대로, 아니면 gemini-2.0-flash-lite로 폴백.
    model = settings.gemini_model
    if "flash" not in model.lower():
        model = "gemini-2.0-flash-lite"

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": 4096,
            "responseMimeType": "application/json",
        },
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode(),
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": settings.gemini_api_key,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
        for cand in data.get("candidates", []):
            for part in cand.get("content", {}).get("parts", []):
                if isinstance(part.get("text"), str):
                    return part["text"]
    except Exception:
        pass
    return None


def _clean_json(raw: str) -> dict | None:
    """LLM 응답에서 JSON을 추출한다."""
    s = raw.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?", "", s).rstrip("`").strip()
    try:
        obj = json.loads(s)
        return obj if isinstance(obj, dict) else None
    except Exception:
        start, end = s.find("{"), s.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(s[start:end + 1])
            except Exception:
                pass
    return None


def _detect_pii_in_text(text: str) -> list[dict[str, str]]:
    """정규식으로 PII를 감지해 [{type, value, masked}] 반환."""
    found: list[dict[str, str]] = []
    seen: set[str] = set()
    for pii_type, pat in _PII_PATTERNS:
        for m in pat.finditer(text):
            val = m.group(0)
            if val not in seen:
                seen.add(val)
                # 마스킹: 앞 2자리 남기고 *** 처리
                masked = val[:2] + "*" * max(3, len(val) - 2) if len(val) > 2 else "***"
                found.append({"type": pii_type, "value": val, "masked": masked})
    return found


def parse_resume_llm_stream(
    pdf_bytes: bytes | None,
    raw_text: str | None = None,
) -> Iterator[dict[str, Any]]:
    """이력서를 Gemini Flash로 분석하며 SSE 이벤트를 순차 방출한다."""

    # ── 1. 텍스트 추출 ──────────────────────────────────────────
    text = ""
    if pdf_bytes:
        text = extract_pdf_text(pdf_bytes)
    if not text and raw_text:
        text = raw_text
    if not text:
        yield {"type": "error", "message": "추출 가능한 텍스트가 없습니다."}
        return

    yield {"type": "start", "total_chars": len(text), "preview_text": text[:400]}

    # ── 2. 개인정보 감지 (정규식 — 즉시 방출) ───────────────────
    pii_items = _detect_pii_in_text(text)
    for pii in pii_items:
        yield {"type": "pii_detected", **pii}
        time.sleep(0.08)

    # ── 3. LLM 분석 호출 ────────────────────────────────────────
    prompt = f"""You are an expert Korean resume analyzer. Analyze the resume text below and return a single JSON object with EXACTLY these keys:

{{
  "position": "<string: job title in Korean, e.g. 백엔드 개발자>",
  "career_years": <integer or null: years of experience>,
  "skills": [
    {{"canonical": "<tech name>", "category": "<backend|frontend|data|devops|mobile|other>", "evidence": "<exact sentence from resume where this skill appears>"}}
  ],
  "certs": [
    {{"name": "<certificate name>", "evidence": "<exact sentence>"}}
  ],
  "memo_sentences": [
    "<A meaningful sentence from the resume that describes career goals, strengths, project context, or self-introduction — useful as AI context. Extract 2-5 sentences.>"
  ],
  "pii_names": ["<full name if found>"]
}}

RESUME TEXT:
{text}

Return ONLY the JSON object. No explanation."""

    raw = _call_gemini(prompt, temperature=0.1)
    if not raw:
        # Gemini 없으면 에러 — 빈 complete로 종료
        yield {"type": "error", "message": "LLM API를 호출할 수 없습니다. (API 키 확인 필요)"}
        return

    data = _clean_json(raw)
    if not data:
        yield {"type": "error", "message": "LLM 응답 파싱에 실패했습니다."}
        return

    # ── 4. 이름 PII 추가 감지 (LLM이 찾은 이름) ─────────────────
    for name in data.get("pii_names", []):
        if name and len(name) >= 2:
            masked = name[0] + "○" * (len(name) - 1)
            yield {"type": "pii_detected", "type_": "name", "value": name, "masked": masked}
            time.sleep(0.1)

    # ── 5. 직무·경력 ──────────────────────────────────────────────
    yield {
        "type": "meta_detected",
        "position": data.get("position", ""),
        "career_years": data.get("career_years"),
    }
    time.sleep(0.15)

    # ── 6. 기술 스택 — 하나씩 방출 ──────────────────────────────
    for skill in data.get("skills", []):
        yield {
            "type": "skill_detected",
            "canonical": skill.get("canonical", ""),
            "category": skill.get("category", "other"),
            "evidence": skill.get("evidence", ""),
        }
        time.sleep(0.12)

    # ── 7. 자격증 ─────────────────────────────────────────────────
    for cert in data.get("certs", []):
        yield {
            "type": "cert_detected",
            "name": cert.get("name", ""),
            "evidence": cert.get("evidence", ""),
        }
        time.sleep(0.1)

    # ── 8. 메모 문장 — 하나씩 방출 ──────────────────────────────
    for sentence in data.get("memo_sentences", []):
        yield {"type": "memo_sentence", "text": sentence}
        time.sleep(0.18)

    # ── 9. 완료 ───────────────────────────────────────────────────
    yield {"type": "complete", "full_data": data, "raw_text": text}
