from collections.abc import Iterable

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Posting, PostingCategory, PostingTech, RawPosting, Resume, ResumeSkill, Skill


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
) -> tuple[list[dict], int]:
    owned_skill_ids: set[int] = set()
    if match_only and resume_id is not None and user_id is not None:
        owned_skill_ids = get_resume_skill_ids(
            session,
            resume_id=resume_id,
            user_id=user_id,
        )

    postings = _get_filtered_postings(
        session=session,
        pool=pool,
        position=position,
        sort=sort,
    )
    posting_ids = [posting.id for posting in postings]
    skill_map, skill_id_map = _get_posting_skills(session, posting_ids)
    url_map = _get_posting_urls(session, posting_ids)

    cards = []
    for posting in postings:
        matched_count = None
        if match_only:
            matched_count = len(skill_id_map.get(posting.id, set()) & owned_skill_ids)
            if matched_count < 1:
                continue

        card = {
            "id": posting.id,
            "title": posting.title,
            "company": posting.company,
            "post_date": posting.post_date,
            "close_date": posting.close_date,
            "skills": skill_map.get(posting.id, []),
            "url": url_map.get(posting.id, ""),
        }
        if matched_count is not None:
            card["matched_count"] = matched_count
        cards.append(card)

    total = len(cards)
    offset = (page - 1) * page_size
    return cards[offset : offset + page_size], total


def _get_filtered_postings(
    session: Session,
    *,
    pool: str | None,
    position: str | None,
    sort: str,
) -> list[Posting]:
    stmt = select(Posting).where(Posting.is_deleted.is_(False))

    if pool is not None:
        stmt = stmt.where(Posting.pool == pool)

    if position is not None:
        stmt = stmt.join(PostingCategory, PostingCategory.posting_id == Posting.id).where(
            PostingCategory.category == position,
            PostingCategory.is_deleted.is_(False),
        )

    if sort == "deadline":
        stmt = stmt.order_by(Posting.close_date.is_(None), Posting.close_date.asc(), Posting.id.asc())
    else:
        stmt = stmt.order_by(Posting.post_date.is_(None), Posting.post_date.desc(), Posting.id.desc())

    return list(session.execute(stmt).scalars().unique().all())


def _get_posting_skills(
    session: Session,
    posting_ids: Iterable[int],
) -> tuple[dict[int, list[str]], dict[int, set[int]]]:
    ids = list(posting_ids)
    if not ids:
        return {}, {}

    rows = session.execute(
        select(PostingTech.posting_id, Skill.id, Skill.canonical)
        .join(Skill, Skill.id == PostingTech.skill_id)
        .where(
            PostingTech.posting_id.in_(ids),
            PostingTech.is_deleted.is_(False),
            Skill.is_deleted.is_(False),
        )
        .order_by(Skill.canonical.asc())
    ).all()

    skill_map: dict[int, list[str]] = {}
    skill_id_map: dict[int, set[int]] = {}
    for posting_id, skill_id, canonical in rows:
        skill_map.setdefault(posting_id, []).append(canonical)
        skill_id_map.setdefault(posting_id, set()).add(skill_id)

    return skill_map, skill_id_map


def _get_posting_urls(session: Session, posting_ids: Iterable[int]) -> dict[int, str]:
    ids = list(posting_ids)
    if not ids:
        return {}

    rows = session.execute(
        select(RawPosting.posting_id, RawPosting.payload, RawPosting.captured_at)
        .where(
            RawPosting.posting_id.in_(ids),
            RawPosting.is_deleted.is_(False),
        )
        .order_by(RawPosting.captured_at.desc())
    ).all()

    url_map: dict[int, str] = {}
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
