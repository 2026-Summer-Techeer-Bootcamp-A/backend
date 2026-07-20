"""공고 요구사항 추출: LLM이 공고 원문을 읽고 요구를 뽑아내고, LLM이 죽으면
태그 교집합 비교에 쓰던 기존 seed_tags로 우아하게 강등한다.

각 요구에는 kind("must"|"preferred")가 붙는다 — 공고 원문이 자격 요건 섹션에서
나온 요구인지 우대 사항 섹션에서 나온 요구인지를 프론트가 뱃지로 구분해 보여주기
위함이다(chatContract.ts RequirementKind). kind는 LLM에게 맡기지 않는다 — LLM이
"이건 우대야"라고 지어낼 수 있으므로, source_quote가 description의 어느 섹션
텍스트 안에서 발견되는지를 코드로 되짚어 그 섹션의 제목(normalize_jobkorea_sections가
정리하는 "자격 요건"/"우대 사항" 계열 라벨, 혹은 다른 소스가 쓰는 유사 표현)으로만
kind를 정한다. 섹션을 못 찾거나 라벨이 애매하면 must로 기본 처리한다(과대 주장 금지).
"""

from __future__ import annotations

import json
import re
from typing import Literal, TypedDict

from app.services.rag.llm import LLMClient

RequirementKind = Literal["must", "preferred"]


class Requirement(TypedDict):
    id: str
    text: str
    source_quote: str
    kind: RequirementKind


_SYSTEM = (
    "너는 채용 공고에서 지원자에게 요구하는 핵심 역량을 골라내는 분석기다. "
    "공고 본문을 읽고 서로 겹치지 않는 요구사항 5개에서 8개를 뽑되, 각 요구마다 "
    "그 근거가 된 공고 원문 문장을 그대로 인용한다. JSON 객체 하나만 출력한다."
)

# 우대 사항 계열 섹션 제목 키워드. normalize_jobkorea_sections가 정리하는 "우대 사항"
# 뿐 아니라 정규화를 거치지 않는 다른 소스의 원제목(예: "우대조건", "Plus")도 잡도록
# 넉넉하게 잡는다. must보다 먼저 검사한다 — "우대"는 "필수"류 키워드와 겹치지 않는다.
_PREFERRED_TITLE_KEYWORDS = (
    "우대사항",
    "우대요건",
    "우대조건",
    "우대",
    "plus",
    "가산점",
)
# 자격 요건 계열 섹션 제목 키워드.
_MUST_TITLE_KEYWORDS = (
    "자격요건",
    "지원자격",
    "지원조건",
    "응시자격",
    "필수요건",
    "필수사항",
    "필수",
)


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s).lower()


def _classify_section_title(title: str) -> RequirementKind | None:
    """섹션 제목으로 kind를 정한다. 자격/우대 어느 쪽도 아니면(주요 업무, 근무 조건,
    혜택 및 복지 등) None을 돌려줘 호출부가 must로 기본 처리하게 한다."""
    normalized = _norm(title)
    if not normalized:
        return None
    for kw in _PREFERRED_TITLE_KEYWORDS:
        if _norm(kw) in normalized:
            return "preferred"
    for kw in _MUST_TITLE_KEYWORDS:
        if _norm(kw) in normalized:
            return "must"
    return None


def _parse_sections(description: str | None) -> list[dict]:
    """description JSON([{"title":..,"text":..}])을 섹션 리스트로 파싱한다.
    JSON이 아니거나 리스트가 아니면 빈 리스트를 돌려준다 — 그 경우 원문은 섹션
    구분 없는 평문으로 취급하고(_sectioned_body), kind는 항상 must로 기본 처리된다."""
    if not description:
        return []
    try:
        parsed = json.loads(description)
    except (ValueError, TypeError):
        return []
    if not isinstance(parsed, list):
        return []
    return [
        s
        for s in parsed
        if isinstance(s, dict) and isinstance(s.get("text"), str) and s.get("text", "").strip()
    ]


def _sectioned_body(description: str | None, sections: list[dict]) -> str:
    """LLM 프롬프트에 넘길 본문. 섹션이 있으면 [제목] 마커로 경계를 표시해 LLM이
    어느 섹션 문장을 인용하는지 스스로도 알 수 있게 하고(선택), kind는 그와 무관하게
    source_quote 매칭으로 코드가 별도 판정한다. 섹션이 없으면(비-JSON 소스) 원문을
    그대로 쓴다."""
    if sections:
        parts = []
        for s in sections:
            title = str(s.get("title") or "").strip()
            text = str(s.get("text") or "").strip()
            if not text:
                continue
            parts.append(f"[{title}]\n{text}" if title else text)
        return "\n\n".join(parts).strip()
    return (description or "").strip()


def _kind_for_quote(quote: str, sections: list[dict]) -> RequirementKind:
    """source_quote가 어느 섹션 텍스트에 포함되는지 찾아 그 섹션의 kind를 돌려준다.
    일치하는 섹션이 없거나 섹션 라벨이 자격/우대 어느 쪽도 아니면 must로 기본
    처리한다 — 근거 없이 preferred로 지어내지 않는다."""
    norm_quote = _norm(quote)
    if not norm_quote:
        return "must"
    for s in sections:
        text = str(s.get("text") or "")
        if norm_quote in _norm(text):
            kind = _classify_section_title(str(s.get("title") or ""))
            return kind or "must"
    return "must"


def _tag_fallback(seed_tags: list[str]) -> list[Requirement]:
    return [
        {"id": f"R{i + 1}", "text": tag, "source_quote": "", "kind": "must"}
        for i, tag in enumerate(seed_tags[:8])
    ]


def extract_requirements(
    description: str | None, seed_tags: list[str], llm: LLMClient
) -> tuple[list[Requirement], bool]:
    """공고 본문에서 LLM으로 요구사항을 뽑는다.

    반환하는 bool(llm_ok)은 요구사항 목록이 실제로 LLM 원문 판독에서 나왔는지를
    가리킨다. seed_tags 태그 폴백은 항상 뭔가를 채워 넣어(비어있지 않아) 호출부가
    "성공"으로 착각하기 쉬웠다 — llm_ok를 별도로 반환해 태그 폴백인데도 조용히
    degraded=False로 넘어가는 일을 막는다(compare_tool.py가 이 값으로 강등을 판단).
    """
    sections = _parse_sections(description)
    body = _sectioned_body(description, sections)
    if not body:
        return _tag_fallback(seed_tags), False
    prompt = (
        f"공고 본문(섹션은 [제목]으로 표시됨):\n{body}\n\n"
        f"참고 태그(이미 뽑힌 기술): {', '.join(seed_tags) or '없음'}\n\n"
        'items 배열로 답한다. 각 원소는 {"id":"R1","text":"요구 한 줄",'
        '"source_quote":"공고 원문 문장"} 형식이다. source_quote는 반드시 위 '
        "본문에서 그대로 발췌한다."
    )
    out = llm.json(_SYSTEM, prompt, temperature=0.1, max_output_tokens=1536)
    items = (out or {}).get("items")
    if not isinstance(items, list) or not items:
        return _tag_fallback(seed_tags), False
    result: list[Requirement] = []
    for i, it in enumerate(items):
        if not isinstance(it, dict) or not it.get("text"):
            continue
        source_quote = str(it.get("source_quote") or "")
        result.append(
            {
                "id": str(it.get("id") or f"R{i + 1}"),
                "text": str(it["text"]),
                "source_quote": source_quote,
                "kind": _kind_for_quote(source_quote, sections),
            }
        )
    if result:
        return result, True
    return _tag_fallback(seed_tags), False
