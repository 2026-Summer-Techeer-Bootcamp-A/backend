from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.models import Resume, ResumeSkill, Skill
from app.schemas.resume import (
    ParsedSkill,
    ResumeCreateRequest,
    ResumeDetailResponse,
    ResumeListItem,
    ResumeUpdateRequest,
)


def create_resume(
    session: Session,
    *,
    user_id: int,
    resume_in: ResumeCreateRequest,
) -> Resume:
    resume = Resume(
        user_id=user_id,
        title=resume_in.title,
        position=resume_in.position,
        career_min=resume_in.career_min,
        career_max=resume_in.career_max,
        pool=resume_in.pool,
    )
    session.add(resume)
    session.flush()

    _add_resume_skills(session, resume_id=resume.resume_id, skills=resume_in.skills)

    session.commit()
    session.refresh(resume)
    return resume


def update_resume(
    session: Session,
    *,
    resume_id: int,
    user_id: int,
    resume_in: ResumeUpdateRequest,
) -> Resume | None:
    resume = session.scalar(
        select(Resume).where(
            Resume.resume_id == resume_id,
            Resume.user_id == user_id,
            Resume.is_deleted.is_(False),
        )
    )
    if resume is None:
        return None

    resume.title = resume_in.title
    resume.position = resume_in.position
    resume.career_min = resume_in.career_min
    resume.career_max = resume_in.career_max
    resume.pool = resume_in.pool

    session.execute(
        update(ResumeSkill)
        .where(
            ResumeSkill.resume_id == resume.resume_id,
            ResumeSkill.is_deleted.is_(False),
        )
        .values(is_deleted=True, deleted_at=func.now())
    )
    _add_resume_skills(session, resume_id=resume.resume_id, skills=resume_in.skills)

    session.commit()
    session.refresh(resume)
    return resume


def get_resume_list(
    session: Session,
    *,
    user_id: int,
) -> list[ResumeListItem]:
    stmt = (
        select(Resume.resume_id, Resume.title, Resume.position)
        .where(
            Resume.user_id == user_id,
            Resume.is_deleted.is_(False),
        )
        .order_by(Resume.updated_at.desc(), Resume.resume_id.desc())
    )
    return [
        ResumeListItem(
            resume_id=resume_id,
            title=title,
            position=position,
        )
        for resume_id, title, position in session.execute(stmt).all()
    ]


def get_resume_detail(
    session: Session,
    *,
    resume_id: int,
    user_id: int,
) -> ResumeDetailResponse | None:
    resume = session.scalar(
        select(Resume).where(
            Resume.resume_id == resume_id,
            Resume.user_id == user_id,
            Resume.is_deleted.is_(False),
        )
    )
    if resume is None:
        return None

    stmt = (
        select(ResumeSkill, Skill)
        .outerjoin(Skill, ResumeSkill.skill_id == Skill.id)
        .where(
            ResumeSkill.resume_id == resume.resume_id,
            ResumeSkill.is_deleted.is_(False),
        )
        .order_by(ResumeSkill.id)
    )
    skills = [
        _to_parsed_skill(resume_skill, skill)
        for resume_skill, skill in session.execute(stmt).all()
    ]

    return ResumeDetailResponse(
        resume_id=resume.resume_id,
        title=resume.title,
        skills=skills,
        position=resume.position,
        career_min=resume.career_min,
        career_max=resume.career_max,
        pool=resume.pool,
    )


def _get_skills_by_canonical(session: Session, canonicals: set[str]) -> dict[str, Skill]:
    if not canonicals:
        return {}

    stmt = select(Skill).where(
        Skill.canonical.in_(canonicals),
        Skill.is_deleted.is_(False),
    )
    return {skill.canonical: skill for skill in session.scalars(stmt).all()}


def _add_resume_skills(
    session: Session,
    *,
    resume_id: int,
    skills: list[ParsedSkill],
) -> None:
    skills_by_canonical = _get_skills_by_canonical(
        session,
        {skill.canonical for skill in skills if skill.in_dict},
    )

    for skill in skills:
        dictionary_skill = skills_by_canonical.get(skill.canonical) if skill.in_dict else None
        session.add(
            ResumeSkill(
                resume_id=resume_id,
                skill_id=dictionary_skill.id if dictionary_skill else None,
                raw_label=None if dictionary_skill else skill.canonical,
                is_out_of_dict=dictionary_skill is None,
            )
        )


def _to_parsed_skill(resume_skill: ResumeSkill, skill: Skill | None) -> ParsedSkill:
    if skill is not None and not skill.is_deleted:
        return ParsedSkill(
            canonical=skill.canonical,
            category=skill.category,
            in_dict=True,
        )

    return ParsedSkill(
        canonical=resume_skill.raw_label or "",
        category="unknown",
        in_dict=False,
    )
