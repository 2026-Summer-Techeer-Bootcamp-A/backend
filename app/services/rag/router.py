"""Router/Planner — 질문을 분해해 intent·tools·entities를 정한다.

LLM(Gemini)로 계획을 뽑되, 실패하면 키워드 휴리스틱으로 폴백(degraded).
정직성: 정량·랭킹 intent는 무조건 sql, 관계 질문만 graph.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.rag.llm import LLMClient
from app.services.rag.schemas import Plan

# intent -> 사용할 도구
INTENT_TOOLS = {
    "cooccurrence": ["graph"],
    "skill_demand": ["sql"],
    "skill_ranking": ["sql"],
    "concept_ranking": ["sql"],
    "cert_ranking": ["sql"],
    "semantic_search": ["vector"],
    "overview": ["sql"],
    "region_distribution": ["sql"],
}

_COOCCUR_KW = ("같이", "함께", "동반", "궁합", "짝", "with", "together", "pair", "combo")
_SEMANTIC_KW = ("찾아", "추천", "비슷", "유사", "관련 공고", "같은 공고", "어떤 공고", "공고 있", "recommend", "similar")
_CONCEPT_KW = ("개념", "패러다임", "트렌드", "msa", "마이크로서비스", "생성형", "대규모", "아키텍처", "devops", "ci/cd")
_CERT_KW = ("자격증", "자격", "cert", "토익", "정보처리")
_RANK_KW = ("순위", "많이", "상위", "top", "인기", "가장", "수요")
_REGION_KW = ("어디", "위치", "지역", "몰려", "밀집")

_PLANNER_SYSTEM = (
    "You are a query planner for a Korean job-market RAG. "
    "Classify the user question into exactly one intent and extract entities. "
    "Return ONLY JSON: {\"intent\": one of "
    "[cooccurrence, skill_demand, skill_ranking, concept_ranking, cert_ranking, "
    "semantic_search, overview, region_distribution], "
    "\"skill\": <a single tech name mentioned or null>, "
    "\"pool\": <domestic|global|null>}. "
    "cooccurrence = which techs go together with X. "
    "skill_demand = how many postings want X. "
    "skill_ranking = most demanded techs. concept_ranking = paradigms/concepts. "
    "cert_ranking = certifications. "
    "semantic_search = find/recommend postings similar to a free-form description. "
    "region_distribution = where postings are concentrated geographically (region/location). "
    "overview = general market summary."
)


def _detect_skill(session: Session, q: str) -> str | None:
    """폴백용: 질문에 등장하는 가장 긴 기술 정규명(2자 이상)을 찾는다."""
    row = session.execute(
        text(
            "SELECT canonical FROM skill "
            "WHERE length(canonical) >= 2 AND :q ILIKE '%' || canonical || '%' "
            "ORDER BY length(canonical) DESC LIMIT 1"
        ),
        {"q": q},
    ).first()
    return row.canonical if row else None


def _heuristic(session: Session, q: str, pool: str | None) -> Plan:
    low = q.lower()
    skill = _detect_skill(session, q)
    if skill and any(k in low for k in _COOCCUR_KW):
        intent = "cooccurrence"
    elif any(k in q for k in _SEMANTIC_KW):
        intent = "semantic_search"
    elif any(k in low for k in _CERT_KW):
        intent = "cert_ranking"
    elif any(k in low for k in _CONCEPT_KW):
        intent = "concept_ranking"
    elif any(k in low for k in _REGION_KW):
        intent = "region_distribution"
    elif skill:
        intent = "skill_demand"
    elif any(k in low for k in _RANK_KW):
        intent = "skill_ranking"
    else:
        intent = "overview"
    return Plan(
        intent=intent,
        tools=INTENT_TOOLS[intent],
        pool=pool,
        entities={"skill": skill} if skill else {},
        subqueries=[q],
    )


def plan(session: Session, llm: LLMClient, question: str, pool: str | None) -> tuple[Plan, bool]:
    """(Plan, degraded). LLM 성공 시 degraded=False, 폴백 시 True."""
    raw = llm.json(_PLANNER_SYSTEM, question, temperature=0.0)
    if not raw or raw.get("intent") not in INTENT_TOOLS:
        return _heuristic(session, question, pool), True

    intent = raw["intent"]
    skill = raw.get("skill") or None
    llm_pool = raw.get("pool") if raw.get("pool") in ("domestic", "global") else None
    # cooccurrence/skill_demand인데 기술을 못 뽑았으면 질문에서 재탐지
    if intent in ("cooccurrence", "skill_demand") and not skill:
        skill = _detect_skill(session, question)
        if not skill:
            intent = "skill_ranking"  # 대상 없으면 랭킹으로 강등
    return (
        Plan(
            intent=intent,
            tools=INTENT_TOOLS[intent],
            pool=pool or llm_pool,
            entities={"skill": skill} if skill else {},
            subqueries=[question],
        ),
        False,
    )
