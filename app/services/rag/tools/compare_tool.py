"""compare_tool — 첨부(이력서/공고) 기반 단건 딥 비교(K2).

새 매칭/시장 통계 로직을 만들지 않고 app/services/match.py(=resume_gap/resume_coverage가
쓰는 것과 동일한 계산)를 그대로 재사용한다 — 기준이 갈라지는 사고를 막기 위함이다.

세 함수 모두 대상(공고/이력서)을 찾지 못하면 None을 반환한다. pipeline._dispatch가 None을
그대로 out에 담지 않고 걸러내며, run_chat_events는 그 결과 tool_outputs가 비면 일반 기술
랭킹으로 대체하는 대신 "비교할 공고를 찾지 못했어요" 같은 안내로 조기 종료한다.
"""

from __future__ import annotations

import json

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.posting import Posting
from app.services.career.judge import judge_requirements, weighted_score
from app.services.career.requirements import extract_requirements
from app.services.match import (
    calculate_coverage_response,
    calculate_gap_response,
    compare_resume_to_posting,
    compare_two_postings,
    get_posting_skill_names,
)
from app.services.posting_description import normalize_jobkorea_sections
from app.services.rag.llm import LLMClient

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


def _get_posting_description(session: Session, posting_id: int) -> tuple[str | None, str | None]:
    """공고 description 원문(JSON 문자열)과 source를 조회한다. 존재 검증은 호출부가
    get_posting_skill_names로 이미 마쳤으므로 여기서는 그대로 값만 가져온다."""
    row = session.execute(
        select(Posting.description, Posting.source).where(
            Posting.id == posting_id,
            Posting.is_deleted.is_(False),
        )
    ).one_or_none()
    if row is None:
        return None, None
    return row[0], row[1]


def _normalize_description(description: str | None, source: str | None, posting_title: str) -> str | None:
    """잡코리아 소스는 단일 섹션에 원문 전체가 뭉쳐 있어(posting_description.py 참고)
    요구 추출 전에 섹션을 나눠 준다. 그 외 소스는 저장된 형태를 그대로 쓴다."""
    if not description or source != "jobkorea":
        return description
    try:
        sections = json.loads(description)
    except (ValueError, TypeError):
        return description
    if not isinstance(sections, list):
        return description
    normalized = normalize_jobkorea_sections(sections, posting_title=posting_title)
    return json.dumps(normalized, ensure_ascii=False)


def _build_summary(rows: list[dict]) -> str:
    met = next((r for r in rows if r["verdict"] == "met"), None)
    gap = next((r for r in rows if r["verdict"] == "gap"), None)
    parts: list[str] = []
    if met:
        parts.append(f"{met['text']}은(는) 충족됩니다")
    if gap:
        parts.append(f"{gap['text']}이(가) 공백입니다")
    if not parts:
        return "판정 가능한 요구사항이 없습니다."
    return ", ".join(parts) + "."


def _degrade_to_tag_compare(
    session: Session, owned_skill_ids: set[int] | None, posting_id: int
) -> dict | None:
    """LLM 판정 경로가 원문 부재나 빈 결과로 이어지지 못할 때 기존 태그 기반 비교로
    강등한다. degraded 플래그를 실어 프론트 배지가 뜨게 한다(조용한 실패 금지)."""
    base = resume_posting_compare(
        session=session, owned_skill_ids=owned_skill_ids, posting_id=posting_id
    )
    if base is None:
        return None
    base["tool_result"]["compare"]["degraded"] = True
    return base


def resume_posting_llm_compare(
    session: Session,
    resume_text: str | None,
    owned_skill_ids: set[int] | None,
    posting_id: int,
    llm: LLMClient,
) -> dict | None:
    """이력서 원문과 공고 원문을 LLM으로 대조해 요구사항별 met/partial/gap을 판정한다.

    이력서 원문이 없거나(세션 만료 등), 요구사항/판정 목록이 비어있거나, 혹은
    비어있지는 않아도 LLM이 아니라 태그 폴백/기본 gap 채움으로만 채워졌다면(=
    extract_requirements/judge_requirements가 llm_ok=False를 돌려준 경우) 전부
    기존 태그 교집합 비교(resume_posting_compare)로 강등하고 degraded=True를 싣는다.
    """
    if not resume_text:
        return _degrade_to_tag_compare(session, owned_skill_ids, posting_id)

    try:
        posting_title, seed_tags = get_posting_skill_names(session, posting_id)
    except HTTPException:
        return None

    description, source = _get_posting_description(session, posting_id)
    normalized_description = _normalize_description(description, source, posting_title)

    requirements, req_llm_ok = extract_requirements(normalized_description, seed_tags, llm)
    if not requirements:
        return _degrade_to_tag_compare(session, owned_skill_ids, posting_id)
    if not req_llm_ok:
        # 요구사항 목록 자체는 비어있지 않지만(seed_tags 태그 폴백) LLM이 원문을
        # 읽어 뽑아낸 게 아니다 — 그대로 이어가면 태그 텍스트를 요구사항인 척
        # 내보내면서 degraded=False를 붙이는 "근거 없는 확신"이 된다. 정직하게
        # 태그 기반 비교로 강등한다.
        return _degrade_to_tag_compare(session, owned_skill_ids, posting_id)

    judgments, judge_llm_ok = judge_requirements(requirements, resume_text, llm)
    if not judgments:
        return _degrade_to_tag_compare(session, owned_skill_ids, posting_id)
    if not judge_llm_ok:
        # 판정도 마찬가지다 — LLM이 죽으면 judge_requirements는 전부 gap으로
        # 채운 목록을 돌려주는데, 이걸 그대로 "0건 충족" 확정 결과로 보여주면
        # 이 기능 전체가 없애려던 "근거 없는 0%" 문제가 그대로 재현된다.
        return _degrade_to_tag_compare(session, owned_skill_ids, posting_id)

    score = weighted_score(judgments)
    counts = {"met": 0, "partial": 0, "gap": 0}
    for judgment in judgments:
        counts[judgment["verdict"]] = counts.get(judgment["verdict"], 0) + 1

    by_req_id = {req["id"]: req for req in requirements}
    rows = []
    for judgment in judgments:
        req = by_req_id.get(judgment["req_id"], {"id": judgment["req_id"], "text": "", "source_quote": ""})
        rows.append(
            {
                "id": req["id"],
                "text": req["text"],
                "source_quote": req["source_quote"],
                "verdict": judgment["verdict"],
                "resume_quote": judgment["resume_quote"],
                "rationale": judgment["rationale"],
                "next_step": judgment["next_step"],
            }
        )

    summary = _build_summary(rows)
    compare = {
        "posting_title": posting_title,
        "score": score,
        "counts": counts,
        "summary": summary,
        "requirements": rows,
        "degraded": False,
    }
    facts = (
        f"{posting_title} 대비 이력서 원문 LLM 판정 — met {counts['met']}건, "
        f"partial {counts['partial']}건, gap {counts['gap']}건, 가중 점수 {score}"
    )

    return {
        "tool": "compare",
        "tool_result": {
            "kind": "resume_posting_llm",
            "label": "이력서 대비 공고 요구사항(LLM 판정)",
            "items": [],
            "compare": compare,
        },
        "citation": {
            "type": "compare",
            "ref": f"이력서 vs {posting_title}",
            "label": "이력서 원문 대비 공고 요구사항 LLM 판정",
        },
        "n": len(rows),
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
