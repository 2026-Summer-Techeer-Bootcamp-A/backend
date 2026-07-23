"""Synthesis — 도구가 낸 사실만으로 한국어 답변을 합성한다.

정직성 핵심: 숫자는 도구(SQL/graph)가 이미 확정한 것. LLM은 문장으로 옮기기만 하며
새 수치를 지어내면 안 된다. LLM 실패 시 사실을 템플릿으로 엮어 degraded 답을 낸다.
"""

from __future__ import annotations

import re
from app.services.rag.llm import LLMClient

_SYNTH_SYSTEM = (
    "너는 전문적인 채용시장 데이터 어시스턴트다. 아래에 주어진 '사실'과 데이터를 바탕으로 "
    "사용자가 요청한 기술 스택, 직군, 채용 동향에 대해 풍부하고 유용한 인사이트와 분석 답변을 작성한다. "
    "사실에 없는 수치나 통계 숫자를 지어내지 않되, 주어진 데이터를 바탕으로 시장 백그라운드, 스택 트렌드, "
    "실무 적용 포인트 및 구직자/개발자를 위한 구체적인 지원 전략과 커리어 인사이트를 상세히 전달하라. "
    "주어진 집계가 질문과 정확히 일치하지 않더라도, 관련된 수치가 있으면 그것으로 최대한 명확하게 답하고 "
    "조건의 한계가 있다면 간결하게 덧붙여라. "
    "근거(사실)가 정말 하나도 없을 때만 '관련 데이터가 부족해요'라고 답하라."
    "\n\n표현 규칙:\n"
    "- '제공된 데이터에 따르면', '주어진 사실에는', '제공된 사실에 따르면' 같은 "
    "메타적(자기지시적) 표현은 쓰지 마라. 대신 '국내 채용 공고 데이터를 종합 분석한 결과,' 또는 "
    "'개발자 채용 시장 동향을 분석해 보면,' 같이 자연스럽고 전문적인 어조로 전달하라.\n"
    "- 이 서비스는 IT·개발자 채용 공고를 기반으로 하므로 불필요한 자격 조항 단서는 줄이고 핵심 인사이트에 집중하라."
    "\n\n출력 및 구성 규칙(마크다운):\n"
    "- 단순 수치 나열에 그치지 않고, 수치 분석과 함께 채용 시장에서의 의미, 실무 활용 트렌드, 대비 전략 등의 인사이트를 풍부하게 서술하라.\n"
    "- 강조할 핵심 수치, 주요 기술명, 핵심 키워드는 **굵게** 표시한다.\n"
    "- 소제목이나 요약 문장을 볼드(**bold**)로 강조한 경우, 바로 아래 본문 문단에서 동일한 문장을 중복하여 똑같이 반복하지 마라.\n"
    "- 항목 나열이 필요한 경우 `- **기술명**: 수치 (비율) — 상세 특징 및 인사이트` 형식의 불릿 리스트를 활용하라.\n"
    "- 코드블록(```)이나 표(|)는 쓰지 않는다."
)

_BAIL_MARKERS = ("데이터가 부족", "정보가 부족", "근거가 부족", "자료가 부족")


def _is_bail(text: str) -> bool:
    """LLM이 사실이 있음에도 개선된 프롬프트를 무시하고 부족 문구를 냈는지 감지."""
    return any(m in text for m in _BAIL_MARKERS)


def _format_single_fact(fact: str) -> str:
    """기계식 파라미터 표기를 제거하고 세미콜론 나열 텍스트를 자연스러운 마크다운 불릿으로 가공."""
    # 내부 디버그 라벨 청소
    cleaned = re.sub(r"pool=\S+\s*", "", fact)
    cleaned = re.sub(r"직군=\S+\s*", "", cleaned)
    cleaned = re.sub(r"신입=\S+\s*", "", cleaned)
    cleaned = cleaned.strip()

    # 헤더와 데이터 본문 분리 (예: "전체 채용 공고 (백엔드 직군) 총 11,106건 기준 수요 상위 기술 — Java 4833건...")
    delim = " — " if " — " in cleaned else (": " if ": " in cleaned else None)
    if delim and delim in cleaned:
        header, body = cleaned.split(delim, 1)
        header = header.strip()
        body = body.strip()

        if ";" in body:
            items = [it.strip() for it in body.split(";") if it.strip()]
            bullets = []
            for it in items:
                # 'Java 4833건(43.5%)' -> '- **Java**: 4,833건 (43.5%)' 나열 다듬기
                parts = it.split(" ", 1)
                if len(parts) == 2:
                    name, val = parts[0], parts[1]
                    bullets.append(f"- **{name}**: {val}")
                else:
                    bullets.append(f"- {it}")
            return f"{header}:\n\n" + "\n".join(bullets)
        return f"{header}: {body}"

    return cleaned


def _fallback(facts: list[str]) -> str:
    if not facts:
        return ""
    formatted_list = [_format_single_fact(f) for f in facts]
    return "\n\n".join(formatted_list)


def synthesize(
    llm: LLMClient, question: str, tool_outputs: list[dict], passed: bool
) -> tuple[str, bool, bool]:
    """(answer, degraded, answered).

    degraded: LLM 미가용/실패로 사실 템플릿을 그대로 이어붙인 답이면 True.
    answered: 근거(사실)로 실제 답을 냈으면 True, 근거가 아예 없어 못 낸 경우만 False.
    confidence는 answered를 기준으로 계산해야 '부족' 답변에 높은 신뢰도가 붙는 모순을 막는다.
    """
    facts = [o["facts"] for o in tool_outputs if o.get("facts")]
    if not passed or not facts:
        return "관련 데이터가 부족해요.", True, False

    prompt = (
        f"질문: {question}\n\n"
        f"사실(근거):\n- " + "\n- ".join(facts) + "\n\n"
        "위 사실만으로 답을 작성하라."
    )
    text = llm.text(_SYNTH_SYSTEM, prompt, temperature=0.3)
    if text and text.strip() and not _is_bail(text.strip()):
        return text.strip(), False, True
    # LLM이 미가용이거나, 사실이 있는데도 부족 문구로 답했다면 사실 템플릿으로 덮어써
    # 실제 데이터를 보여준다(허위 '부족' 응답 방지).
    return _fallback(facts), True, True
