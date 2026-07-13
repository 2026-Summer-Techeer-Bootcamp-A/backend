"""Synthesis — 도구가 낸 사실만으로 한국어 답변을 합성한다.

정직성 핵심: 숫자는 도구(SQL/graph)가 이미 확정한 것. LLM은 문장으로 옮기기만 하며
새 수치를 지어내면 안 된다. LLM 실패 시 사실을 템플릿으로 엮어 degraded 답을 낸다.
"""

from __future__ import annotations

from app.services.rag.llm import LLMClient

_SYNTH_SYSTEM = (
    "너는 채용시장 데이터 어시스턴트다. 아래에 주어진 '사실'만 근거로 한국어 2~3문장 답을 작성한다. "
    "사실에 없는 수치나 항목을 절대 지어내지 마라. "
    "주어진 집계가 질문과 정확히 일치하지 않아도(예: 질문은 특정 직군·조건을 물었는데 "
    "사실은 전체 기준 집계뿐이어도), 관련된 수치가 있으면 그것으로 최대한 답하고 "
    "정확히 짚어내지 못한 부분은 한 줄로 솔직하게 덧붙여라. "
    "사실이 하나라도 있으면 '데이터가 부족하다'는 취지의 문장을 쓰지 마라 — "
    "근거(사실)가 정말 하나도 없을 때만 '관련 데이터가 부족해요'라고 답하라. "
    "담백하고 정확하게, 과장 없이."
)

_BAIL_MARKERS = ("데이터가 부족", "정보가 부족", "근거가 부족", "자료가 부족")


def _is_bail(text: str) -> bool:
    """LLM이 사실이 있음에도 개선된 프롬프트를 무시하고 부족 문구를 냈는지 감지."""
    return any(m in text for m in _BAIL_MARKERS)


def _fallback(facts: list[str]) -> str:
    return " ".join(facts)


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
