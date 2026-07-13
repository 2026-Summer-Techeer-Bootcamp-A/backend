from sqlalchemy import select
from sqlalchemy.orm import Session

from app.crud.posting import (
    _count_filtered_postings,
    _format_region,
    _get_filtered_postings,
    _get_posting_urls,
)
from app.models.posting import PostingCategory, PostingTech
from app.models.skill import Skill
from app.schemas.feed import FeedMatch, FeedPostingItem


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


def _build_match(
    skills: list[tuple[int, str]], owned_skill_ids: set[int] | None
) -> FeedMatch | None:
    if owned_skill_ids is None or not skills:
        return None
    owned = [name for skill_id, name in skills if skill_id in owned_skill_ids]
    missing = [name for skill_id, name in skills if skill_id not in owned_skill_ids]
    rate = round(100 * len(owned) / len(skills), 1)
    return FeedMatch(rate=rate, owned_skills=owned, missing_skills=missing)


def list_feed_postings(
    *,
    session: Session,
    pool: str | None,
    category: str | None,
    page: int,
    page_size: int,
    owned_skill_ids: set[int] | None,
) -> tuple[list[FeedPostingItem], int]:
    total = _count_filtered_postings(session=session, pool=pool, position=category)
    postings = _get_filtered_postings(
        session=session,
        pool=pool,
        position=category,
        sort="latest",
        limit=page_size,
        offset=(page - 1) * page_size,
    )
    ids = [p.id for p in postings]
    skills_map = _get_feed_skills(session, ids)
    categories_map = _get_feed_categories(session, ids)
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
                url=urls.get(p.id, ""),
                match=_build_match(skills, owned_skill_ids),
            )
        )
    return items, total
