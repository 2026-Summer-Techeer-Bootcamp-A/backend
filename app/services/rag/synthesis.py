"""Synthesis — 도구가 낸 사실만으로 한국어 답변을 합성한다.

정직성 핵심: 숫자는 도구(SQL/graph)가 이미 확정한 것. LLM은 문장으로 옮기기만 하며
새 수치를 지어내면 안 된다. LLM 실패 시 사실을 템플릿으로 엮어 degraded 답을 낸다.
"""

from __future__ import annotations

from app.services.rag.llm import LLMClient

_SYNTH_SYSTEM = (
    "너는 채용시장 데이터 어시스턴트다. 아래에 주어진 '사실'만 근거로 한국어 1~2문장 답을 작성한다. "
    "사실에 없는 수치나 항목을 절대 지어내지 마라. 사실이 비어 있으면 '관련 데이터가 부족해요'라고만 답하라. "
    "담백하고 정확하게, 과장 없이."
)


def _fallback(facts: list[str], passed: bool) -> str:
    if not passed or not facts:
        return "관련 데이터가 부족해요."
    return " ".join(facts)


def synthesize(
    llm: LLMClient, question: str, tool_outputs: list[dict], passed: bool
) -> tuple[str, bool]:
    """(answer, degraded). LLM 실패/미가용 시 사실 템플릿 폴백(degraded=True)."""
    facts = [o["facts"] for o in tool_outputs if o.get("facts")]
    if not passed or not facts:
        return "관련 데이터가 부족해요.", True

    prompt = (
        f"질문: {question}\n\n"
        f"사실(근거):\n- " + "\n- ".join(facts) + "\n\n"
        "위 사실만으로 답을 작성하라."
    )
    text = llm.text(_SYNTH_SYSTEM, prompt, temperature=0.3)
    if text and text.strip():
        return text.strip(), False
    return _fallback(facts, passed), True
