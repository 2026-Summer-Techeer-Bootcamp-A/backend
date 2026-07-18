"""공고 요구사항 추출: LLM이 공고 원문을 읽고 요구를 뽑아내고, LLM이 죽으면
태그 교집합 비교에 쓰던 기존 seed_tags로 우아하게 강등한다.
"""

from __future__ import annotations

import json
from typing import TypedDict

from app.services.rag.llm import LLMClient


class Requirement(TypedDict):
    id: str
    text: str
    source_quote: str


_SYSTEM = (
    "너는 채용 공고에서 지원자에게 요구하는 핵심 역량을 골라내는 분석기다. "
    "공고 본문을 읽고 서로 겹치지 않는 요구사항 5개에서 8개를 뽑되, 각 요구마다 "
    "그 근거가 된 공고 원문 문장을 그대로 인용한다. JSON 객체 하나만 출력한다."
)


def _description_to_text(description: str | None) -> str:
    if not description:
        return ""
    try:
        sections = json.loads(description)
        if isinstance(sections, list):
            return "\n".join(
                s.get("text", "") for s in sections if isinstance(s, dict)
            ).strip()
    except (ValueError, TypeError):
        pass
    return description.strip()


def _tag_fallback(seed_tags: list[str]) -> list[Requirement]:
    return [
        {"id": f"R{i + 1}", "text": tag, "source_quote": ""}
        for i, tag in enumerate(seed_tags[:8])
    ]


def extract_requirements(
    description: str | None, seed_tags: list[str], llm: LLMClient
) -> list[Requirement]:
    body = _description_to_text(description)
    if not body:
        return _tag_fallback(seed_tags)
    prompt = (
        f"공고 본문:\n{body}\n\n"
        f"참고 태그(이미 뽑힌 기술): {', '.join(seed_tags) or '없음'}\n\n"
        'items 배열로 답한다. 각 원소는 {"id":"R1","text":"요구 한 줄",'
        '"source_quote":"공고 원문 문장"} 형식이다.'
    )
    out = llm.json(_SYSTEM, prompt, temperature=0.1, max_output_tokens=1536)
    items = (out or {}).get("items")
    if not isinstance(items, list) or not items:
        return _tag_fallback(seed_tags)
    result: list[Requirement] = []
    for i, it in enumerate(items):
        if not isinstance(it, dict) or not it.get("text"):
            continue
        result.append(
            {
                "id": str(it.get("id") or f"R{i + 1}"),
                "text": str(it["text"]),
                "source_quote": str(it.get("source_quote") or ""),
            }
        )
    return result or _tag_fallback(seed_tags)
