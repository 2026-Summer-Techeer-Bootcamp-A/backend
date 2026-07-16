import json
from collections.abc import Iterable
from datetime import date, timedelta

from fastapi import HTTPException, status
from sqlalchemy import case, func, literal, or_, select
from sqlalchemy.orm import Session

from app.models import Cert, Posting, PostingCategory, PostingCert, PostingTech, RawPosting, Resume, ResumeSkill, Skill
from app.services.posting_description import normalize_jobkorea_sections


def get_resume_skill_ids(session: Session, *, resume_id: int, user_id: int) -> set[int]:
    resume = session.execute(
        select(Resume.resume_id).where(
            Resume.resume_id == resume_id,
            Resume.user_id == user_id,
            Resume.is_deleted.is_(False),
        )
    ).first()
    if resume is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="resume not found")

    rows = session.execute(
        select(ResumeSkill.skill_id).where(
            ResumeSkill.resume_id == resume_id,
            ResumeSkill.skill_id.is_not(None),
            ResumeSkill.is_deleted.is_(False),
            ResumeSkill.is_out_of_dict.is_(False),
        )
    ).scalars()
    return set(rows)


def get_resume_career_max(session: Session, *, resume_id: int, user_id: int) -> int | None:
    """이력서의 career_max(현재까지 경력 연차 상한)를 조회한다. min_match 필터에서
    공고의 career_min(요구 경력 하한)과 비교해 경력 미달 공고를 걸러내는 데 쓰인다."""
    resume = session.execute(
        select(Resume.career_max).where(
            Resume.resume_id == resume_id,
            Resume.user_id == user_id,
            Resume.is_deleted.is_(False),
        )
    ).first()
    if resume is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="resume not found")

    return resume[0]


def list_posting_cards(
    session: Session,
    *,
    pool: str | None,
    position: str | None,
    sort: str,
    match_only: bool,
    resume_id: int | None,
    user_id: int | None,
    page: int,
    page_size: int,
    district: str | None = None,
    deadline_within_days: int | None = None,
    min_match: float | None = None,
    q: str | None = None,
    skills: list[str] | None = None,
    industry: str | None = None,
    rich_only: bool = False,
) -> tuple[list[dict], int]:
    owned_skill_ids = (
        get_resume_skill_ids(session, resume_id=resume_id, user_id=user_id)
        if resume_id is not None and user_id is not None
        else None
    )
    candidate_career_max = (
        get_resume_career_max(session, resume_id=resume_id, user_id=user_id)
        if resume_id is not None and user_id is not None
        else None
    )

    total = _count_filtered_postings(
        session=session,
        pool=pool,
        position=position,
        district=district,
        deadline_within_days=deadline_within_days,
        q=q,
        skills=skills,
        industry=industry,
        rich_only=rich_only,
        match_only=match_only,
        min_match=min_match,
        owned_skill_ids=owned_skill_ids,
        candidate_career_max=candidate_career_max,
    )
    postings = _get_filtered_postings(
        session=session,
        pool=pool,
        position=position,
        sort=sort,
        district=district,
        deadline_within_days=deadline_within_days,
        q=q,
        skills=skills,
        industry=industry,
        rich_only=rich_only,
        match_only=match_only,
        min_match=min_match,
        owned_skill_ids=owned_skill_ids,
        candidate_career_max=candidate_career_max,
        limit=page_size,
        offset=(page - 1) * page_size,
    )
    posting_ids = [posting.id for posting in postings]
    skill_map, skill_id_map = _get_posting_skills(session, posting_ids)
    url_map = _get_posting_urls(session, posting_ids)

    cards = []
    for posting in postings:
        required_ids = skill_id_map.get(posting.id, set())

        card = {
            "id": posting.id,
            "title": posting.title,
            "company": posting.company,
            "post_date": posting.post_date,
            "close_date": posting.close_date,
            "skills": skill_map.get(posting.id, []),
            "url": url_map.get(posting.id, ""),
            "logo_url": posting.logo_url,
        }
        if owned_skill_ids is not None:
            card["matched_count"] = len(required_ids & owned_skill_ids)
        cards.append(card)

    return cards, total


def get_posting_detail(session: Session, *, posting_id: int) -> dict:
    posting = session.execute(
        select(Posting).where(
            Posting.id == posting_id,
            Posting.is_deleted.is_(False),
        )
    ).scalar_one_or_none()
    if posting is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="posting not found")

    skill_map, _skill_id_map = _get_posting_skills(session, [posting.id])
    url_map = _get_posting_urls(session, [posting.id])
    desc_sections = json.loads(posting.description) if posting.description else []
    if posting.source == "jobkorea":
        desc_sections = normalize_jobkorea_sections(
            desc_sections,
            posting_title=posting.title,
        )

    return {
        "id": posting.id,
        "source": posting.source,
        "pool": posting.pool,
        "company": posting.company,
        "title": posting.title,
        "post_date": posting.post_date,
        "close_date": posting.close_date,
        "career_min": posting.career_min,
        "career_max": posting.career_max,
        "region": _format_region(posting),
        "lat": posting.lat,
        "lng": posting.lng,
        "industry": posting.industry,
        "response_rate": posting.response_rate,
        "categories": _get_posting_categories(session, posting.id),
        "skills": skill_map.get(posting.id, []),
        "certs": _get_posting_certs(session, posting.id),
        "url": url_map.get(posting.id, ""),
        "logo_url": posting.logo_url,
        "desc_sections": desc_sections,
    }


def _apply_posting_filters(
    stmt,
    *,
    pool: str | None,
    position: str | None,
    district: str | None,
    deadline_within_days: int | None,
    q: str | None = None,
    skills: list[str] | None = None,
    industry: str | None = None,
    rich_only: bool = False,
    match_only: bool = False,
    min_match: float | None = None,
    owned_skill_ids: set[int] | None = None,
    candidate_career_max: int | None = None,
):
    """공고 목록 조회와 카운트가 공유하는 WHERE 절. 두 쿼리가 어긋나면 total과
    실제 반환 건수가 달라지므로 반드시 한 곳에서만 정의한다."""
    stmt = stmt.where(Posting.is_deleted.is_(False))
    # 마감일이 지난 공고는 기본적으로 목록에서 제외한다(마감일 자체가 없는 상시채용은 유지).
    stmt = stmt.where(Posting.close_date.is_(None) | (Posting.close_date >= date.today()))

    if pool is not None:
        stmt = stmt.where(Posting.pool == pool)

    if q:
        pattern = f"%{q.strip()}%"
        stmt = stmt.where(or_(Posting.title.ilike(pattern), Posting.company.ilike(pattern)))

    if position is not None:
        stmt = stmt.where(
            select(PostingCategory.id)
            .where(
                PostingCategory.posting_id == Posting.id,
                PostingCategory.category == position,
                PostingCategory.is_deleted.is_(False),
            )
            .exists()
        )

    if skills:
        stmt = stmt.where(
            select(PostingTech.id)
            .join(Skill, Skill.id == PostingTech.skill_id)
            .where(
                PostingTech.posting_id == Posting.id,
                PostingTech.is_deleted.is_(False),
                Skill.is_deleted.is_(False),
                Skill.canonical.in_(skills),
            )
            .exists()
        )

    if district is not None:
        stmt = stmt.where(Posting.region_district.ilike(f"%{district}%"))

    if industry is not None:
        stmt = stmt.where(Posting.industry.ilike(f"%{industry}%"))

    if rich_only:
        # 설명 길이 기준은 소스 전체로 보면 유효했지만, 기본 최신순 정렬에서는
        # 다른 소스의 최근 공고도 대부분 길어서 필터 효과가 체감되지 않았다.
        # jumpit은 소량(약 742건)이지만 설명이 항상 충실한 소스라 이를 기준으로 삼는다.
        stmt = stmt.where(Posting.source == "jumpit")

    if deadline_within_days is not None:
        today = date.today()
        stmt = stmt.where(
            Posting.close_date.isnot(None),
            Posting.close_date >= today,
            Posting.close_date <= today + timedelta(days=deadline_within_days),
        )

    if match_only or min_match is not None:
        matched_count = _matched_skill_count(owned_skill_ids or set())
        if match_only:
            stmt = stmt.where(matched_count >= 1)
        if min_match is not None:
            required_count = _required_skill_count()
            match_pct = case(
                (required_count > 0, matched_count * 100.0 / required_count),
                else_=0.0,
            )
            stmt = stmt.where(match_pct >= min_match)
            # "지원 가능"(min_match) 판정에는 경력요건도 반영한다: 이력서의 career_max가
            # 공고의 career_min보다 낮으면 기술 스택이 겹쳐도 지원 가능으로 보지 않는다.
            # 둘 중 하나라도 정보가 없으면(경력무관 공고, 경력 미기재 이력서) 걸러내지 않는다.
            if candidate_career_max is not None:
                stmt = stmt.where(
                    Posting.career_min.is_(None) | (Posting.career_min <= candidate_career_max)
                )

    return stmt


def _matched_skill_count(owned_skill_ids: set[int]):
    if not owned_skill_ids:
        return literal(0)
    # posting_tech는 (posting_id, skill_id) 유니크 제약이 있어, 특정 posting_id로
    # 좁혀놓은 이 서브쿼리 안에서는 skill_id가 중복될 수 없다. DISTINCT는 아무
    # 것도 걸러내지 못하면서 정렬 비용만 만든다(app/crud/posting.py의
    # _count_filtered_postings와 같은 종류의 문제).
    return (
        select(func.count(PostingTech.skill_id))
        .where(
            PostingTech.posting_id == Posting.id,
            PostingTech.skill_id.in_(owned_skill_ids),
            PostingTech.is_deleted.is_(False),
        )
        .correlate(Posting)
        .scalar_subquery()
    )


def _required_skill_count():
    return (
        select(func.count(PostingTech.skill_id))
        .where(PostingTech.posting_id == Posting.id, PostingTech.is_deleted.is_(False))
        .correlate(Posting)
        .scalar_subquery()
    )


def _count_filtered_postings(
    session: Session,
    *,
    pool: str | None,
    position: str | None,
    district: str | None = None,
    deadline_within_days: int | None = None,
    q: str | None = None,
    skills: list[str] | None = None,
    industry: str | None = None,
    rich_only: bool = False,
    match_only: bool = False,
    min_match: float | None = None,
    owned_skill_ids: set[int] | None = None,
    candidate_career_max: int | None = None,
) -> int:
    # id는 posting의 기본키(_apply_posting_filters는 JOIN 없이 FROM posting만 사용)라
    # 이미 유일함. DISTINCT는 postgres가 dedup을 위해 매칭 행 전체를 정렬하게 만들어
    # 실측 기준 쿼리 시간의 90%+를 차지하는 불필요한 external sort를 유발한다.
    stmt = _apply_posting_filters(
        select(func.count(Posting.id)),
        pool=pool,
        position=position,
        district=district,
        deadline_within_days=deadline_within_days,
        q=q,
        skills=skills,
        industry=industry,
        rich_only=rich_only,
        match_only=match_only,
        min_match=min_match,
        owned_skill_ids=owned_skill_ids,
        candidate_career_max=candidate_career_max,
    )
    return session.execute(stmt).scalar_one()


def _get_filtered_postings(
    session: Session,
    *,
    pool: str | None,
    position: str | None,
    sort: str,
    district: str | None = None,
    deadline_within_days: int | None = None,
    limit: int | None = None,
    offset: int | None = None,
    q: str | None = None,
    skills: list[str] | None = None,
    industry: str | None = None,
    rich_only: bool = False,
    match_only: bool = False,
    min_match: float | None = None,
    owned_skill_ids: set[int] | None = None,
    candidate_career_max: int | None = None,
) -> list[Posting]:
    stmt = _apply_posting_filters(
        select(Posting),
        pool=pool,
        position=position,
        district=district,
        deadline_within_days=deadline_within_days,
        q=q,
        skills=skills,
        industry=industry,
        rich_only=rich_only,
        match_only=match_only,
        min_match=min_match,
        owned_skill_ids=owned_skill_ids,
        candidate_career_max=candidate_career_max,
    )

    if sort == "match" and owned_skill_ids is not None:
        matched_count = _matched_skill_count(owned_skill_ids)
        required_count = _required_skill_count()
        match_pct = case(
            (required_count > 0, matched_count * 100.0 / required_count),
            else_=0.0,
        )
        stmt = stmt.order_by(
            match_pct.desc(), Posting.post_date.is_(None), Posting.post_date.desc(), Posting.id.desc()
        )
    elif sort == "deadline":
        stmt = stmt.order_by(Posting.close_date.is_(None), Posting.close_date.asc(), Posting.id.asc())
    else:
        stmt = stmt.order_by(Posting.post_date.is_(None), Posting.post_date.desc(), Posting.id.desc())

    if limit is not None:
        stmt = stmt.limit(limit)
    if offset is not None:
        stmt = stmt.offset(offset)

    return list(session.execute(stmt).scalars().unique().all())


def _get_posting_categories(session: Session, posting_id: int) -> list[str]:
    rows = session.execute(
        select(PostingCategory.category)
        .where(
            PostingCategory.posting_id == posting_id,
            PostingCategory.is_deleted.is_(False),
        )
        .order_by(PostingCategory.category.asc())
    ).scalars()
    return list(rows)


def _get_posting_certs(session: Session, posting_id: int) -> list[str]:
    rows = session.execute(
        select(Cert.name)
        .join(PostingCert, PostingCert.cert_id == Cert.id)
        .where(
            PostingCert.posting_id == posting_id,
            PostingCert.is_deleted.is_(False),
            Cert.is_deleted.is_(False),
        )
        .order_by(Cert.name.asc())
    ).scalars()
    return list(rows)


def _format_region(posting: Posting) -> str | None:
    city, district = posting.region_city, posting.region_district
    # region_city가 "서울"처럼 시/도 단위로만 있고 region_district(구/군)가 더 상세한 경우가
    # 있다(예: wanted). district가 이미 city 문자열 안에 포함돼 있으면(jumpit 등 원래
    # 상세 주소가 city에 통째로 들어있는 경우) 중복 표기하지 않는다.
    if city and district and district not in city:
        return f"{city} {district}"
    return city or district or posting.region_country


# Postgres는 한 쿼리에 바인딩할 수 있는 파라미터가 65,535개로 제한된다. 필터에
# 걸리는 공고가 그 이상이면(예: 필터 없는 전체 조회) IN 절 하나로 다 묶는 순간
# OperationalError로 쿼리 자체가 거부된다. 이를 피하기 위해 항상 이 크기 이하로
# 청크를 나눠 여러 번 조회한다. posting_id는 청크 사이에서 겹치지 않으므로,
# 각 공고의 결과는 정확히 하나의 청크에서만 채워진다.
_IN_CLAUSE_CHUNK_SIZE = 5000


def _chunked(items: list[int], size: int) -> Iterable[list[int]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _get_posting_skills(
    session: Session,
    posting_ids: Iterable[int],
) -> tuple[dict[int, list[str]], dict[int, set[int]]]:
    ids = list(posting_ids)
    if not ids:
        return {}, {}

    skill_map: dict[int, list[str]] = {}
    skill_id_map: dict[int, set[int]] = {}
    for batch in _chunked(ids, _IN_CLAUSE_CHUNK_SIZE):
        rows = session.execute(
            select(PostingTech.posting_id, Skill.id, Skill.canonical)
            .join(Skill, Skill.id == PostingTech.skill_id)
            .where(
                PostingTech.posting_id.in_(batch),
                PostingTech.is_deleted.is_(False),
                Skill.is_deleted.is_(False),
            )
            .order_by(Skill.canonical.asc())
        ).all()
        for posting_id, skill_id, canonical in rows:
            skill_map.setdefault(posting_id, []).append(canonical)
            skill_id_map.setdefault(posting_id, set()).add(skill_id)

    return skill_map, skill_id_map


def _get_posting_urls(session: Session, posting_ids: Iterable[int]) -> dict[int, str]:
    ids = list(posting_ids)
    if not ids:
        return {}

    url_map: dict[int, str] = {}
    for batch in _chunked(ids, _IN_CLAUSE_CHUNK_SIZE):
        rows = session.execute(
            select(RawPosting.posting_id, RawPosting.payload, RawPosting.captured_at)
            .where(
                RawPosting.posting_id.in_(batch),
                RawPosting.is_deleted.is_(False),
            )
            .order_by(RawPosting.captured_at.desc())
        ).all()
        for posting_id, payload, _captured_at in rows:
            if posting_id not in url_map:
                url_map[posting_id] = _extract_url(payload)

    return url_map


def _extract_url(payload: dict) -> str:
    for key in ("url", "link", "source_url", "apply_url", "job_url"):
        value = payload.get(key)
        if isinstance(value, str):
            return value

    return ""


def _get_posting_or_404(session: Session, posting_id: int) -> Posting:
    posting = session.execute(
        select(Posting).where(Posting.id == posting_id, Posting.is_deleted.is_(False))
    ).scalar_one_or_none()
    if posting is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="posting not found")
    return posting


def _build_cards(session: Session, postings: list[Posting]) -> list[dict]:
    posting_ids = [p.id for p in postings]
    skill_map, _skill_id_map = _get_posting_skills(session, posting_ids)
    url_map = _get_posting_urls(session, posting_ids)

    return [
        {
            "id": p.id,
            "title": p.title,
            "company": p.company,
            "post_date": p.post_date,
            "close_date": p.close_date,
            "skills": skill_map.get(p.id, []),
            "url": url_map.get(p.id, ""),
            "logo_url": p.logo_url,
        }
        for p in postings
    ]


def get_nearby_postings(session: Session, *, posting_id: int, limit: int = 10) -> list[dict]:
    """자기 자신을 제외한, 같은 region_district의 최신 공고."""
    posting = _get_posting_or_404(session, posting_id)

    if posting.region_district is None:
        return []

    rows = (
        session.execute(
            select(Posting)
            .where(
                Posting.id != posting_id,
                Posting.region_district == posting.region_district,
                Posting.is_deleted.is_(False),
                Posting.close_date.is_(None) | (Posting.close_date >= date.today()),
            )
            .order_by(Posting.post_date.is_(None), Posting.post_date.desc(), Posting.id.desc())
            .limit(limit)
        )
        .scalars()
        .unique()
        .all()
    )

    return _build_cards(session, list(rows))


def get_similar_postings(session: Session, *, posting_id: int, limit: int = 10) -> list[dict]:
    """자기 자신을 제외한, 요구 기술 겹침(overlap_count)이 많은 순 공고."""
    _get_posting_or_404(session, posting_id)

    skill_ids = list(
        session.scalars(
            select(PostingTech.skill_id).where(
                PostingTech.posting_id == posting_id,
                PostingTech.is_deleted.is_(False),
            )
        ).all()
    )
    if not skill_ids:
        return []

    # (posting_id, skill_id) 유니크 제약 덕분에 posting_id로 GROUP BY한 안에서는
    # skill_id가 중복되지 않는다. DISTINCT 없이도 결과는 같고 정렬 비용만 없앤다.
    overlap_rows = session.execute(
        select(PostingTech.posting_id, func.count(PostingTech.skill_id).label("overlap"))
        .where(
            PostingTech.skill_id.in_(skill_ids),
            PostingTech.posting_id != posting_id,
            PostingTech.is_deleted.is_(False),
        )
        .group_by(PostingTech.posting_id)
        .order_by(func.count(PostingTech.skill_id).desc())
        .limit(limit)
    ).all()

    overlap_map = {row.posting_id: row.overlap for row in overlap_rows}
    if not overlap_map:
        return []

    postings = (
        session.execute(
            select(Posting).where(
                Posting.id.in_(overlap_map.keys()),
                Posting.is_deleted.is_(False),
                Posting.close_date.is_(None) | (Posting.close_date >= date.today()),
            )
        )
        .scalars()
        .unique()
        .all()
    )

    cards = _build_cards(session, list(postings))
    for card in cards:
        card["overlap_count"] = overlap_map[card["id"]]
    cards.sort(key=lambda c: c["overlap_count"], reverse=True)

    return cards
