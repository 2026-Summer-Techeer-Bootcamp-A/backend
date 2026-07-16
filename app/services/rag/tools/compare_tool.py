"""compare_tool — 첨부(이력서/공고) 기반 단건 딥 비교(K2).

새 매칭/시장 통계 로직을 만들지 않고 app/services/match.py(=resume_gap/resume_coverage가
쓰는 것과 동일한 계산)를 그대로 재사용한다 — 기준이 갈라지는 사고를 막기 위함이다.

세 함수 모두 대상(공고/이력서)을 찾지 못하면 None을 반환한다. pipeline._dispatch가 None을
그대로 out에 담지 않고 걸러내며, run_chat_events는 그 결과 tool_outputs가 비면 일반 기술
랭킹으로 대체하는 대신 "비교할 공고를 찾지 못했어요" 같은 안내로 조기 종료한다.
"""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.services.match import (
    calculate_coverage_response,
    calculate_gap_response,
    compare_resume_to_posting,
    compare_two_postings,
)

# match.py의 Pool은 Literal["global","domestic"]이라 구체적인 값이 필요하다. resume_tool.py와
# 동일하게, 지정이 없으면 데이터가 가장 두꺼운 국내 채용시장을 기본값으로 둔다.
_DEFAULT_POOL = "domestic"


def _resolve_pool(pool: str | None) -> str:
    return pool if pool in ("domestic", "global") else _DEFAULT_POOL


def resume_posting_compare(
    session: Session,
    owned_skill_ids: set[int] | None,
    posting_id: int,
) -> dict | None:
    """이력서 보유 기술과 공고 요구 기술을 겹침/부족/여분으로 비교한다."""
    if not owned_skill_ids:
        return None

    try:
        compare = compare_resume_to_posting(
            session, owned_skill_ids=owned_skill_ids, posting_id=posting_id
        )
    except HTTPException:
        return None

    matched_n = len(compare["matched_skills"])
    missing_n = len(compare["missing_skills"])
    facts = (
        f"{compare['posting_title']} 대비 이력서 보유 기술 {matched_n}개, 부족 기술 "
        f"{missing_n}개, 커버리지 {compare['coverage_pct']}%"
    )

    return {
        "tool": "compare",
        "tool_result": {
            "kind": "resume_posting",
            "label": "이력서 대비 공고 요구사항",
            "items": [],
            "compare": compare,
        },
        "citation": {
            "type": "compare",
            "ref": f"이력서 vs {compare['posting_title']}",
            "label": "이력서 보유 기술 vs 공고 요구 기술",
        },
        "n": matched_n + missing_n,
        "facts": facts,
    }


def posting_posting_compare(
    session: Session,
    posting_id_a: int,
    posting_id_b: int,
) -> dict | None:
    """두 공고의 요구 기술을 겹침/차이로 비교한다."""
    try:
        compare = compare_two_postings(
            session, posting_id_a=posting_id_a, posting_id_b=posting_id_b
        )
    except HTTPException:
        return None

    shared_n = len(compare["shared"])
    only_a_n = len(compare["onlyA"])
    only_b_n = len(compare["onlyB"])
    facts = (
        f"{compare['postingA']} vs {compare['postingB']} — 공통 기술 {shared_n}개, "
        f"{compare['postingA']}만 요구 {only_a_n}개, {compare['postingB']}만 요구 {only_b_n}개"
    )

    return {
        "tool": "compare",
        "tool_result": {
            "kind": "posting_posting",
            "label": "두 공고 요구 기술 비교",
            "items": [],
            "compare": compare,
        },
        "citation": {
            "type": "compare",
            "ref": f"{compare['postingA']} vs {compare['postingB']}",
            "label": "공고 간 요구 기술 겹침 비교",
        },
        "n": shared_n + only_a_n + only_b_n,
        "facts": facts,
    }


def resume_market(
    session: Session,
    owned_skill_ids: set[int] | None,
    pool: str | None = None,
    category: str | None = None,
) -> dict | None:
    """이력서 보유 기술을 시장 전체 수요와 비교한다(레이더 + 갭 top5 + 커버리지 점수).

    resume_gap/resume_coverage(resume_tool.py)와 같은 계산(match.py)을 재사용하되, 두
    결과를 프론트 비교 화면 하나의 payload로 합쳐서 보여준다."""
    if not owned_skill_ids:
        return None

    resolved_pool = _resolve_pool(pool)
    gap_resp = calculate_gap_response(
        session, pool=resolved_pool, position=category, owned_skill_ids=owned_skill_ids
    )
    coverage_resp = calculate_coverage_response(
        session, pool=resolved_pool, position=category, owned_skill_ids=owned_skill_ids
    )
    pool_label = "국내" if resolved_pool == "domestic" else "해외"

    gap_top5 = [
        {"canonical": g.canonical, "freq": g.freq, "category": g.category}
        for g in gap_resp.gap_top5
    ]
    compare = {
        "coverage_score": coverage_resp.coverage_score,
        "radar": [{"category": r.category, "coverage": r.coverage} for r in gap_resp.radar],
        "gap_top5": gap_top5,
    }

    gap_body = ", ".join(g["canonical"] for g in gap_top5) if gap_top5 else "없음(상위 기술 대부분 보유)"
    facts = (
        f"{pool_label} 채용시장{f'({category})' if category else ''} {gap_resp.sample_size:,}건 기준 "
        f"이력서 커버리지 {coverage_resp.coverage_score}%, 부족한 상위 기술 — {gap_body}"
    )

    return {
        "tool": "compare",
        "tool_result": {
            "kind": "resume_market",
            "label": "이력서 대비 시장 적합도",
            "items": [],
            "compare": compare,
        },
        "citation": {
            "type": "compare",
            "ref": "이력서 대비 시장 적합도",
            "label": f"{pool_label} 채용시장 {gap_resp.sample_size:,}건 기준",
        },
        "n": gap_resp.sample_size,
        "facts": facts,
    }
