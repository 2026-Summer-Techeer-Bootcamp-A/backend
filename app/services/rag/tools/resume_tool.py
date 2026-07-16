"""resume_tool — 이력서 기준 갭·커버리지 질문에 기존 매치 엔진을 그대로 재사용해 답한다.

새 매칭 로직을 만들지 않고 app/services/match.py(=/match API가 쓰는 계산)를 그대로 불러
"내 이력서 기준 부족한 스킬 뭐야?"/"내 이력서로 지원 가능한 공고 얼마나 돼?" 질문에
답한다 — 매칭 기준이 두 곳으로 갈라져 결과가 어긋나는 사고를 막기 위함이다.

owned_skill_ids가 비어 있으면(이력서 미첨부 혹은 기술 미추출) None을 반환한다 —
그 경우는 pipeline.run_chat_events가 도구 실행 전에 이미 "이력서를 먼저 첨부해 주세요"로
처리하므로, 여기서는 방어적으로 한 번 더 걸러내는 역할만 한다.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.posting import Posting, PostingTech
from app.models.skill import Skill
from app.services.match import (
    _build_resume_posting_compare,
    calculate_coverage_response,
    calculate_gap_response,
    count_matched_postings,
    market_pool_cutoff_date,
)

# posting_list 카드 배지(보유/부족 스킬)에 실어보내는 상한 — 요구 기술이 많은 공고라도
# 카드 UI가 감당 못 할 만큼 배지가 늘어지지 않게 한쪽당 8개로 잘라둔다.
_SKILL_BADGE_CAP = 8

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
        f"보유 기술이 하나라도 걸리는 최근 3년 공고(마감 포함) {matched_postings:,}건"
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


def resume_recommend(
    session: Session,
    owned_skill_ids: set[int] | None,
    pool: str | None = None,
    region: str | None = None,
    limit: int = 8,
) -> dict | None:
    """이력서 보유 기술과 겹치는 정도로 랭킹한, 실제로 지원해볼 만한 구체적 공고 목록(K3).

    resume_coverage/resume_gap은 커버리지 %·부족 스킬 같은 통계만 답해 "넣어볼만한 공고
    추천해줘" 류 질문에 실제 공고를 보여주지 못했다 — app/crud/posting.py
    get_similar_postings의 스킬 겹침(overlap_count) 랭킹 패턴을 그대로 재사용해 이력서
    보유 스킬과 posting_tech가 겹치는 공고를 뽑는다. get_similar_postings와 달리 기준이
    되는 단일 공고가 없고(이력서 자체가 기준), region 필터가 추가로 붙는다.
    """
    if not owned_skill_ids:
        return None

    resolved_pool = _resolve_pool(pool)
    pool_label = "국내" if resolved_pool == "domestic" else "해외"
    n_owned = len(owned_skill_ids)

    def _rank(with_region: bool) -> list[tuple[Posting, int]]:
        # (posting_id, skill_id) 유니크 제약 덕분에 posting_id로 GROUP BY한 안에서는
        # skill_id가 중복되지 않는다 — get_similar_postings와 동일한 근거로 DISTINCT 없이도
        # overlap count가 정확하다. pool/region/마감일 필터를 걸기 전에 넉넉히 더 뽑아둬야
        # (vector_tool.semantic_search의 fetch_limit 여유분과 같은 이유) 필터 후에도
        # limit개를 채울 여지가 남는다.
        fetch_limit = min(limit * 20, 200)
        overlap_rows = session.execute(
            select(PostingTech.posting_id, func.count(PostingTech.skill_id).label("overlap"))
            .where(
                PostingTech.skill_id.in_(owned_skill_ids),
                PostingTech.is_deleted.is_(False),
            )
            .group_by(PostingTech.posting_id)
            .order_by(func.count(PostingTech.skill_id).desc())
            .limit(fetch_limit)
        ).all()
        overlap_map = {row.posting_id: row.overlap for row in overlap_rows}
        if not overlap_map:
            return []

        stmt = select(Posting).where(
            Posting.id.in_(overlap_map.keys()),
            Posting.pool == resolved_pool,
            Posting.is_deleted.is_(False),
            # "마감 전 공고만"에서 "최근 3년 이내 게시(마감 포함)"로 추천 모수를 바꿨다
            # — match.py build_posting_pool_query와 동일한 시장 모수 기준으로 맞춘다.
            # post_date가 없는 공고는 조용히 빠지지 않게 포함한다.
            Posting.post_date.is_(None) | (Posting.post_date >= market_pool_cutoff_date()),
        )
        if with_region and region:
            # ORM .ilike()를 써야 sqlite(CI)에서도 컴파일된다 — raw SQL ILIKE는
            # Postgres 전용 연산자라 sqlite에서 문법 에러가 난다.
            stmt = stmt.where(
                Posting.region_city.ilike(f"%{region}%")
                | Posting.region_district.ilike(f"%{region}%")
            )
        postings = session.execute(stmt).scalars().unique().all()
        ranked = [(p, overlap_map[p.id]) for p in postings]
        ranked.sort(key=lambda pair: pair[1], reverse=True)
        return ranked[:limit]

    region_fallback = False
    results = _rank(with_region=True) if region else _rank(with_region=False)
    if region and not results:
        # 지역 필터로 0건이 되면 빈 결과를 그대로 돌려주지 않고 지역 없이 다시 찾아,
        # facts에 "지역 필터를 못 지켰다"는 사실을 정직하게 남긴다.
        results = _rank(with_region=False)
        region_fallback = True

    if not results:
        return None

    # 카드에 보유(초록)/부족(빨강) 스킬 배지를 붙이려면 공고별 요구 기술 목록이 필요하다.
    # posting마다 따로 쿼리하면 N+1이 되므로, 랭킹된 posting id 전체를 한 번에 묶어
    # posting_tech -> skill을 조인해 posting_id별로 모아둔다(get_posting_skill_names와
    # 같은 조인 패턴, 여러 건을 한 번에 가져오는 점만 다르다).
    posting_ids = [posting.id for posting, _ in results]
    skill_rows = session.execute(
        select(PostingTech.posting_id, Skill.canonical)
        .join(Skill, Skill.id == PostingTech.skill_id)
        .where(
            PostingTech.posting_id.in_(posting_ids),
            PostingTech.is_deleted.is_(False),
            Skill.is_deleted.is_(False),
        )
    ).all()
    posting_skills_map: dict[int, list[str]] = {}
    for pid, canonical in skill_rows:
        posting_skills_map.setdefault(pid, []).append(canonical)

    owned_names = list(
        session.scalars(
            select(Skill.canonical).where(
                Skill.id.in_(owned_skill_ids),
                Skill.is_deleted.is_(False),
            )
        ).all()
    )

    items = []
    for posting, overlap in results:
        fit_pct = round(overlap / n_owned * 100, 1) if n_owned else 0.0
        # 추천 모수가 마감 공고까지 포함하도록 넓어졌으므로, 이미 마감된 공고를 "지원
        # 가능"인 것처럼 보이게 두지 않고 metric에 마감 여부를 정직하게 드러낸다.
        is_closed = posting.close_date is not None and posting.close_date < date.today()
        metric = f"적합도 {overlap}개 일치" + ("(마감)" if is_closed else "")
        # matched/missing 계산은 compare_resume_to_posting과 같은 근거(_build_resume_posting_compare의
        # 대소문자 무시 집합 연산)를 그대로 재사용한다 — 매칭 기준이 두 화면(비교 vs 추천)에서
        # 갈라지는 사고를 막기 위함이다. 여기서는 posting마다 다시 쿼리하지 않고 위에서
        # 배치로 가져온 posting_skills_map을 넘겨 같은 함수를 순수 집합 연산으로만 쓴다.
        compare = _build_resume_posting_compare(
            resume_title="내 이력서",
            posting_title=posting.title,
            owned_names=owned_names,
            posting_skills=posting_skills_map.get(posting.id, []),
        )
        region = posting.region_district or posting.region_city
        items.append(
            {
                "name": posting.title,
                "metric": metric,
                "pct": fit_pct,
                "id": posting.id,
                "company": posting.company,
                "pool": posting.pool,
                "matched_skills": compare["matched_skills"][:_SKILL_BADGE_CAP],
                "missing_skills": compare["missing_skills"][:_SKILL_BADGE_CAP],
                "region": region,
            }
        )

    if region and region_fallback:
        region_note = f"{region} 지역에는 일치하는 공고가 없어 전체 지역으로 대신 보여드려요, "
    elif region:
        region_note = f"{region} 지역 기준, "
    else:
        region_note = ""

    facts_body = "; ".join(f"{it['name']} {it['metric']}" for it in items[:5])
    facts = (
        f"{pool_label} 채용시장(최근 3년, 마감 포함) 기준 {region_note}"
        f"이력서 보유 기술과 겹치는 공고 상위 — {facts_body}"
    )

    label_region = f" ({region})" if region and not region_fallback else ""
    return {
        "tool": "resume",
        "tool_result": {
            "kind": "posting_list",
            "label": f"이력서 기반 추천 공고{label_region}",
            "items": items,
        },
        "citation": {
            "type": "resume",
            "ref": "이력서 기반 공고 추천",
            "label": f"{pool_label} 채용시장 기준 스킬 겹침 랭킹",
        },
        "n": len(items),
        "facts": facts,
    }
