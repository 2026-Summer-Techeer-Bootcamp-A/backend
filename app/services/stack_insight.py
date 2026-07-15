"""스택 조합 인사이트 — co-occurrence 집계 + LLM 한 줄 문장.

핵심 원칙: 숫자는 전부 DB 집계값(mv_cooccurrence)에서 나온다. LLM은 그 숫자로
문장만 조립한다(숫자를 새로 만들지 못하게 프롬프트로 강제 + 실패 시 결정적 폴백).
따라서 환각으로 수치가 틀릴 일이 없다. 절대 건수가 아니라 조건부 비율(co_rate)을
쓰므로 소스별 수집 규모 오염에도 강하다.
"""

from __future__ import annotations

import json
from datetime import date

from sqlalchemy.orm import Session

from app.crud.insight import get_cooccurrence
from app.services.rag.llm import LLMClient

STACK_INSIGHT_SYSTEM = (
    "너는 채용 시장 데이터 분석가다. 반드시 아래 제공된 숫자만 사용해 한국어 한 문장으로 "
    "'함께 요구되는 스택 조합' 인사이트를 만든다. 숫자를 새로 지어내거나 바꾸지 말고, "
    "퍼센트·건수는 제공된 값을 그대로 인용한다. 과장이나 추천 표현 없이 사실만 담는다."
)


def _normalize_rate(co_rate: float) -> float:
    """co_rate가 0~1 분수면 %로 환산. 이미 %(>1)면 그대로 둔다."""
    pct = co_rate * 100 if co_rate <= 1 else co_rate
    return round(pct, 1)


def get_stack_combos(session: Session, *, base_skill: str, pool: str, top_k: int = 5) -> list[dict]:
    """base_skill 공고에서 함께 요구되는 상위 기술을 조건부 비율(co_rate) 순으로 반환.

    get_cooccurrence(skill=base)는 skill_id_1 = base로 필터하므로 각 링크의 source는
    항상 base_skill이고 target이 동반 기술이다.
    """
    _nodes, links = get_cooccurrence(
        session=session, pool=pool, skill=base_skill, top_k=max(top_k * 4, 20)
    )
    combos = [
        {
            "skill": link["target"],
            "co_rate": _normalize_rate(link["co_rate"]),
            "co_count": int(link["co_count"]),
        }
        for link in links
    ]
    # 조건부 비율↓ → 공동 공고 수↓ → 이름↑로 결정적 정렬 후 상위 top_k.
    combos.sort(key=lambda c: (-c["co_rate"], -c["co_count"], c["skill"]))
    return combos[:top_k]


def _fallback_sentence(base_skill: str, combos: list[dict]) -> str:
    """LLM 미가용/실패 시 DB 숫자만으로 만드는 결정적 문장."""
    if not combos:
        return f"{base_skill} 공고와 함께 요구되는 기술 데이터가 아직 충분하지 않아요."
    parts = [f"{c['co_rate']:g}%가 {c['skill']}" for c in combos[:2]]
    return f"{base_skill} 공고의 " + ", ".join(parts) + "를 함께 요구해요."


def build_stack_insight(
    session: Session,
    *,
    base_skill: str,
    pool: str,
    owned_skills: list[str],
    llm: LLMClient,
    top_k: int = 5,
) -> dict:
    """조합 집계 + 한 줄 인사이트. LLM 실패 시 결정적 폴백 문장을 쓴다."""
    combos = get_stack_combos(session, base_skill=base_skill, pool=pool, top_k=top_k)

    insight = _fallback_sentence(base_skill, combos)
    ai_generated = False

    if combos:
        facts = {"base_skill": base_skill, "pool": pool, "owned_skills": owned_skills, "combos": combos}
        prompt = (
            "다음은 DB 집계 사실이다(JSON). 이 숫자만 사용해 한 문장으로 요약해라.\n"
            + json.dumps(facts, ensure_ascii=False)
            + "\n규칙: base_skill 공고에서 함께 요구되는 상위 기술과 그 비율(%)을 언급한다. "
            "owned_skills를 사용자가 이미 보유했다면 그 맥락을 반영하되, 숫자는 위 combos 값만 사용한다."
        )
        text = llm.text(STACK_INSIGHT_SYSTEM, prompt)
        if text and text.strip():
            insight = text.strip()
            ai_generated = True

    return {
        "base_skill": base_skill,
        "pool": pool,
        "combos": combos,
        "insight": insight,
        "ai_generated": ai_generated,
        "as_of": date.today().isoformat(),
    }
