import json
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
from app.models.posting import Posting, PostingCategory, PostingCert, PostingConcept, PostingTech
from app.models.skill import Skill
from app.schemas.feed import FeedMatch, FeedPostingItem

_DESCRIPTION_SNIPPET_MAX_LEN = 300
# 첫 섹션 텍스트가 이 길이보다 짧으면 다음 섹션도 이어붙여 스니펫을 채운다.
_SHORT_SECTION_THRESHOLD = 80
# 줄바꿈(\n)은 불릿 구조를 살리기 위해 보존하고, 한 줄 내부의 연속 공백/탭만 정리한다.
_INLINE_WHITESPACE_RE = re.compile(r"[ \t\r\f\v]+")


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


def _clean_section_text(text: str) -> str:
    """섹션 텍스트를 정리한다. 각 줄 내부의 연속 공백/탭만 하나로 줄이고 앞뒤를
    strip하되, 줄바꿈(\\n) 자체는 원문의 불릿 구조(예: '• 항목1\\n• 항목2')를
    살리기 위해 보존한다. 빈 줄은 제거한다."""
    lines = [_INLINE_WHITESPACE_RE.sub(" ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def _build_description_snippet(description: str | None) -> str | None:
    """Posting.description(JSON 섹션 문자열)에서 피드 카드용 요약 스니펫을 뽑는다.

    형식은 get_posting_detail의 desc_sections 파싱과 동일하다:
    `[{"title": .., "text": ..}, ...]`. 값이 없거나 JSON 파싱이 실패하거나
    기대한 형태가 아니면 피드 응답 전체가 죽지 않도록 None을 반환한다.
    """
    if not description:
        return None
    try:
        sections = json.loads(description)
    except (TypeError, ValueError):
        return None
    if not isinstance(sections, list) or not sections:
        return None

    parts: list[str] = []
    collected_len = 0
    for section in sections:
        if not isinstance(section, dict):
            continue
        text = section.get("text")
        if not isinstance(text, str):
            continue
        cleaned = _clean_section_text(text)
        if not cleaned:
            continue
        parts.append(cleaned)
        collected_len += len(cleaned)
        # 첫 섹션이 짧으면(예: 한두 문장짜리 "소개") 다음 섹션까지 이어붙여
        # 스니펫이 너무 빈약해지지 않게 한다. 최대 두 섹션까지만 합친다.
        if collected_len >= _SHORT_SECTION_THRESHOLD or len(parts) >= 2:
            break

    if not parts:
        return None

    snippet = "\n".join(parts)
    if len(snippet) > _DESCRIPTION_SNIPPET_MAX_LEN:
        snippet = snippet[:_DESCRIPTION_SNIPPET_MAX_LEN].rstrip() + "…"
    return snippet


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
                description_snippet=_build_description_snippet(p.description),
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
