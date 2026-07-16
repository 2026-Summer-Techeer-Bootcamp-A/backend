"""resume_tool — 이력서 기준 갭·커버리지 질문에 기존 매치 엔진을 그대로 재사용해 답한다.

새 매칭 로직을 만들지 않고 app/services/match.py(=/match API가 쓰는 계산)를 그대로 불러
"내 이력서 기준 부족한 스킬 뭐야?"/"내 이력서로 지원 가능한 공고 얼마나 돼?" 질문에
답한다 — 매칭 기준이 두 곳으로 갈라져 결과가 어긋나는 사고를 막기 위함이다.

owned_skill_ids가 비어 있으면(이력서 미첨부 혹은 기술 미추출) None을 반환한다 —
그 경우는 pipeline.run_chat_events가 도구 실행 전에 이미 "이력서를 먼저 첨부해 주세요"로
처리하므로, 여기서는 방어적으로 한 번 더 걸러내는 역할만 한다.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.services.match import (
    calculate_coverage_response,
    calculate_gap_response,
    count_matched_postings,
)

# match.py의 Pool은 Literal["global","domestic"]이라 구체적인 값이 필요하다. RAG 챗은
# pool 없이(전체 대상) 질문할 수 있지만 매치 엔진은 국내/해외 중 하나를 요구하므로,
# 지정이 없으면 데이터가 가장 두꺼운 국내 채용시장을 기본값으로 둔다.
_DEFAULT_POOL = "domestic"


def _resolve_pool(pool: str | None) -> str:
    return pool if pool in ("domestic", "global") else _DEFAULT_POOL


def resume_gap(
    session: Session,
    owned_skill_ids: set[int] | None,
    pool: str | None = None,
    category: str | None = None,
) -> dict | None:
    if not owned_skill_ids:
        return None

    resolved_pool = _resolve_pool(pool)
    resp = calculate_gap_response(
        session,
        pool=resolved_pool,
        position=category,
        owned_skill_ids=owned_skill_ids,
    )
    pool_label = "국내" if resolved_pool == "domestic" else "해외"

    items = [
        {
            "name": g.canonical,
            "metric": f"{round(g.freq * 100, 1)}% 공고 요구",
            "pct": round(g.freq * 100, 1),
        }
        for g in resp.gap_top5
    ]

    if items:
        facts_body = "; ".join(f"{it['name']} {it['metric']}" for it in items)
        facts = (
            f"{pool_label} 채용시장{f'({category})' if category else ''} {resp.sample_size:,}건 기준 "
            f"이력서에 없는 요구 기술 상위 — {facts_body}"
        )
    else:
        # gap_top5가 비어 있는 건 실패가 아니라 "시장이 요구하는 상위 기술을 이미 다 갖고
        # 있다"는 정직한 결과 — 데이터 부족으로 오인되지 않게 facts를 채워둔다.
        facts = (
            f"{pool_label} 채용시장{f'({category})' if category else ''} {resp.sample_size:,}건 기준 "
            "이력서가 시장 요구 상위 기술을 이미 대부분 커버하고 있어 부족한 기술이 두드러지지 않아요"
        )

    return {
        "tool": "resume",
        "tool_result": {
            "kind": "list",
            "label": f"이력서 대비 부족 기술{f' ({category})' if category else ''}",
            "items": items,
        },
        "citation": {
            "type": "resume",
            "ref": "이력서 대비 시장 요구기술 갭",
            "label": f"{pool_label} 채용시장 {resp.sample_size:,}건 기준",
        },
        "n": resp.sample_size,
        "facts": facts,
    }


def resume_coverage(
    session: Session,
    owned_skill_ids: set[int] | None,
    pool: str | None = None,
    category: str | None = None,
) -> dict | None:
    if not owned_skill_ids:
        return None

    resolved_pool = _resolve_pool(pool)
    resp = calculate_coverage_response(
        session,
        pool=resolved_pool,
        position=category,
        owned_skill_ids=owned_skill_ids,
        top_k=20,
    )
    pool_label = "국내" if resolved_pool == "domestic" else "해외"

    # "지원 가능한 공고 얼마나 돼?" 질문에 정확히 답하려면 커버리지 점수(상위 20개 요구기술
    # 대비 비율)만으로는 부족하다 — 실제로 내 보유 기술이 하나라도 걸리는 공고 수를
    # count_matched_postings로 별도 집계해 함께 보여준다.
    matched_postings = count_matched_postings(
        session,
        pool=resolved_pool,
        position=category,
        skill_ids=owned_skill_ids,
    )

    items = [
        {
            "name": s.canonical,
            "metric": "보유" if s.owned else "미보유",
            "pct": round(s.freq * 100, 1),
        }
        for s in resp.top_skills
    ]
    facts = (
        f"{pool_label} 채용시장{f'({category})' if category else ''} 상위 20개 요구기술 중 "
        f"{resp.owned_count}개 보유, 커버리지 {resp.coverage_score}%, "
        f"보유 기술이 하나라도 걸리는 지원 가능 공고 {matched_postings:,}건"
        f"(표본 {resp.sample_size:,}건)"
    )

    return {
        "tool": "resume",
        "tool_result": {
            "kind": "list",
            "label": f"이력서 커버리지{f' ({category})' if category else ''}",
            "items": items,
        },
        "citation": {
            "type": "resume",
            "ref": "이력서 커버리지",
            "label": f"{pool_label} 채용시장 {resp.sample_size:,}건 기준",
        },
        "n": resp.sample_size,
        "facts": facts,
    }
