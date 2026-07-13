import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.crud.posting import (
    _count_filtered_postings,
    _format_region,
    _get_filtered_postings,
    _get_posting_urls,
)
from app.models.cert import Cert
from app.models.concept import Concept
from app.models.posting import Posting, PostingCategory, PostingCert, PostingConcept, PostingTech, RawPosting
from app.models.skill import Skill
from app.schemas.feed import FeedMatch, FeedPostingItem

_DESCRIPTION_SNIPPET_KEYS = ("description", "description_ko", "body", "content", "intro")
_DESCRIPTION_SNIPPET_MAX_LEN = 300
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")


def _get_feed_skills(session: Session, posting_ids: list[int]) -> dict[int, list[tuple[int, str]]]:
    """posting_id -> [(skill_id, canonical), ...] (canonical 오름차순)"""
    if not posting_ids:
        return {}
    rows = session.execute(
        select(PostingTech.posting_id, Skill.id, Skill.canonical)
        .join(Skill, Skill.id == PostingTech.skill_id)
        .where(
            PostingTech.posting_id.in_(posting_ids),
            PostingTech.is_deleted.is_(False),
            Skill.is_deleted.is_(False),
        )
        .order_by(Skill.canonical)
    ).all()
    out: dict[int, list[tuple[int, str]]] = {}
    for posting_id, skill_id, canonical in rows:
        out.setdefault(posting_id, []).append((skill_id, canonical))
    return out


def _get_feed_categories(session: Session, posting_ids: list[int]) -> dict[int, list[str]]:
    if not posting_ids:
        return {}
    rows = session.execute(
        select(PostingCategory.posting_id, PostingCategory.category)
        .where(
            PostingCategory.posting_id.in_(posting_ids),
            PostingCategory.is_deleted.is_(False),
        )
        .order_by(PostingCategory.category)
    ).all()
    out: dict[int, list[str]] = {}
    for posting_id, category in rows:
        out.setdefault(posting_id, []).append(category)
    return out


def _get_feed_concepts(session: Session, posting_ids: list[int]) -> dict[int, list[str]]:
    if not posting_ids:
        return {}
    rows = session.execute(
        select(PostingConcept.posting_id, Concept.name)
        .join(Concept, Concept.id == PostingConcept.concept_id)
        .where(
            PostingConcept.posting_id.in_(posting_ids),
            PostingConcept.is_deleted.is_(False),
            Concept.is_deleted.is_(False),
        )
        .order_by(Concept.name)
    ).all()
    out: dict[int, list[str]] = {}
    for posting_id, name in rows:
        out.setdefault(posting_id, []).append(name)
    return out


def _get_feed_certs(session: Session, posting_ids: list[int]) -> dict[int, list[str]]:
    if not posting_ids:
        return {}
    rows = session.execute(
        select(PostingCert.posting_id, Cert.name)
        .join(Cert, Cert.id == PostingCert.cert_id)
        .where(
            PostingCert.posting_id.in_(posting_ids),
            PostingCert.is_deleted.is_(False),
            Cert.is_deleted.is_(False),
        )
        .order_by(Cert.name)
    ).all()
    out: dict[int, list[str]] = {}
    for posting_id, name in rows:
        out.setdefault(posting_id, []).append(name)
    return out


def _extract_description_snippet(payload: dict | None) -> str | None:
    if not payload:
        return None
    for key in _DESCRIPTION_SNIPPET_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            text = _HTML_TAG_RE.sub(" ", value)
            text = _WHITESPACE_RE.sub(" ", text).strip()
            return text[:_DESCRIPTION_SNIPPET_MAX_LEN] if text else None
    return None


def _get_feed_description_snippets(session: Session, posting_ids: list[int]) -> dict[int, str | None]:
    """posting_id -> 최신 RawPosting.payload에서 뽑은 설명 스니펫 (없으면 None)."""
    if not posting_ids:
        return {}
    rows = session.execute(
        select(RawPosting.posting_id, RawPosting.payload, RawPosting.captured_at)
        .where(
            RawPosting.posting_id.in_(posting_ids),
            RawPosting.is_deleted.is_(False),
        )
        .order_by(RawPosting.captured_at.desc())
    ).all()
    out: dict[int, str | None] = {}
    for posting_id, payload, _captured_at in rows:
        if posting_id not in out:
            out[posting_id] = _extract_description_snippet(payload)
    return out


def _build_match(
    skills: list[tuple[int, str]], owned_skill_ids: set[int] | None
) -> FeedMatch | None:
    if owned_skill_ids is None or not skills:
        return None
    owned = [name for skill_id, name in skills if skill_id in owned_skill_ids]
    missing = [name for skill_id, name in skills if skill_id not in owned_skill_ids]
    rate = round(100 * len(owned) / len(skills), 1)
    return FeedMatch(rate=rate, owned_skills=owned, missing_skills=missing)


def _build_feed_items(
    session: Session,
    postings: list[Posting],
    owned_skill_ids: set[int] | None,
) -> list[FeedPostingItem]:
    ids = [p.id for p in postings]
    skills_map = _get_feed_skills(session, ids)
    categories_map = _get_feed_categories(session, ids)
    concepts_map = _get_feed_concepts(session, ids)
    certs_map = _get_feed_certs(session, ids)
    snippets_map = _get_feed_description_snippets(session, ids)
    urls = _get_posting_urls(session, ids)

    items: list[FeedPostingItem] = []
    for p in postings:
        skills = skills_map.get(p.id, [])
        items.append(
            FeedPostingItem(
                id=p.id,
                title=p.title,
                company=p.company,
                industry=p.industry,
                region=_format_region(p),
                pool=p.pool,
                post_date=p.post_date,
                close_date=p.close_date,
                categories=categories_map.get(p.id, []),
                skills=[name for _, name in skills],
                concepts=concepts_map.get(p.id, []),
                certs=certs_map.get(p.id, []),
                seniority=p.seniority_raw,
                description_snippet=snippets_map.get(p.id),
                url=urls.get(p.id, ""),
                career_min=p.career_min,
                career_max=p.career_max,
                response_rate=float(p.response_rate) if p.response_rate is not None else None,
                match=_build_match(skills, owned_skill_ids),
            )
        )
    return items


def list_feed_postings(
    *,
    session: Session,
    pool: str | None,
    category: str | None,
    page: int,
    page_size: int,
    owned_skill_ids: set[int] | None,
    district: str | None = None,
    deadline_within_days: int | None = None,
    min_match: int | None = None,
) -> tuple[list[FeedPostingItem], int]:
    if min_match is None:
        total = _count_filtered_postings(
            session=session,
            pool=pool,
            position=category,
            district=district,
            deadline_within_days=deadline_within_days,
        )
        postings = _get_filtered_postings(
            session=session,
            pool=pool,
            position=category,
            sort="latest",
            district=district,
            deadline_within_days=deadline_within_days,
            limit=page_size,
            offset=(page - 1) * page_size,
        )
        return _build_feed_items(session, postings, owned_skill_ids), total

    # min_match는 페이지를 정하기 전에 매치율을 계산해야 하므로 DB 레벨
    # LIMIT/OFFSET을 쓸 수 없다. 필터된 공고 전체를 가져와 매치율로 거른 뒤
    # 파이썬에서 페이지를 자른다. (list_posting_cards의 min_match 분기와 동일 방식)
    postings = _get_filtered_postings(
        session=session,
        pool=pool,
        position=category,
        sort="latest",
        district=district,
        deadline_within_days=deadline_within_days,
    )
    items = _build_feed_items(session, postings, owned_skill_ids)
    filtered = [
        item
        for item in items
        if (item.match.rate if item.match is not None else 0.0) >= min_match
    ]
    total = len(filtered)
    offset = (page - 1) * page_size
    return filtered[offset : offset + page_size], total
