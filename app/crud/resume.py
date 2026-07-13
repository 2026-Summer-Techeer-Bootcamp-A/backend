from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.models import Cert, Resume, ResumeCert, ResumeSkill, Skill
from app.schemas.resume import (
    ParsedCert,
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
    has_existing = session.scalar(
        select(func.count()).select_from(Resume).where(
            Resume.user_id == user_id,
            Resume.is_deleted.is_(False),
        )
    )
    resume = Resume(
        user_id=user_id,
        title=resume_in.title,
        position=resume_in.position,
        career_min=resume_in.career_min,
        career_max=resume_in.career_max,
        pool=resume_in.pool,
        memo=resume_in.memo,
        is_primary=has_existing == 0,
    )
    session.add(resume)
    session.flush()

    _add_resume_skills(session, resume_id=resume.resume_id, skills=resume_in.skills)
    _add_resume_certs(session, resume_id=resume.resume_id, certs=resume_in.certs)

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
    resume.memo = resume_in.memo

    session.execute(
        update(ResumeSkill)
        .where(
            ResumeSkill.resume_id == resume.resume_id,
            ResumeSkill.is_deleted.is_(False),
        )
        .values(is_deleted=True, deleted_at=func.now())
    )
    _add_resume_skills(session, resume_id=resume.resume_id, skills=resume_in.skills)

    session.execute(
        update(ResumeCert)
        .where(
            ResumeCert.resume_id == resume.resume_id,
            ResumeCert.is_deleted.is_(False),
        )
        .values(is_deleted=True, deleted_at=func.now())
    )
    _add_resume_certs(session, resume_id=resume.resume_id, certs=resume_in.certs)

    session.commit()
    session.refresh(resume)
    return resume


def delete_resume(
    session: Session,
    *,
    resume_id: int,
    user_id: int,
) -> bool:
    target = session.scalar(
        select(Resume).where(
            Resume.resume_id == resume_id,
            Resume.user_id == user_id,
            Resume.is_deleted.is_(False),
        )
    )
    if target is None:
        return False
    was_primary = target.is_primary

    result = session.execute(
        update(Resume)
        .where(
            Resume.resume_id == resume_id,
            Resume.user_id == user_id,
            Resume.is_deleted.is_(False),
        )
        .values(is_deleted=True, deleted_at=func.now(), is_primary=False)
    )
    if result.rowcount == 0:
        return False

    session.execute(
        update(ResumeSkill)
        .where(
            ResumeSkill.resume_id == resume_id,
            ResumeSkill.is_deleted.is_(False),
        )
        .values(is_deleted=True, deleted_at=func.now())
    )
    session.execute(
        update(ResumeCert)
        .where(
            ResumeCert.resume_id == resume_id,
            ResumeCert.is_deleted.is_(False),
        )
        .values(is_deleted=True, deleted_at=func.now())
    )

    if was_primary:
        successor = session.scalar(
            select(Resume)
            .where(
                Resume.user_id == user_id,
                Resume.is_deleted.is_(False),
            )
            .order_by(Resume.updated_at.desc(), Resume.resume_id.desc())
            .limit(1)
        )
        if successor is not None:
            successor.is_primary = True

    session.commit()
    return True


def set_primary_resume(
    session: Session,
    *,
    resume_id: int,
    user_id: int,
) -> list[ResumeListItem] | None:
    target = session.scalar(
        select(Resume).where(
            Resume.resume_id == resume_id,
            Resume.user_id == user_id,
            Resume.is_deleted.is_(False),
        )
    )
    if target is None:
        return None

    session.execute(
        update(Resume)
        .where(Resume.user_id == user_id, Resume.is_primary.is_(True))
        .values(is_primary=False)
    )
    target.is_primary = True
    session.commit()
    return get_resume_list(session, user_id=user_id)


def get_resume_list(
    session: Session,
    *,
    user_id: int,
) -> list[ResumeListItem]:
    stmt = (
        select(Resume.resume_id, Resume.title, Resume.position, Resume.is_primary)
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
            is_primary=is_primary,
        )
        for resume_id, title, position, is_primary in session.execute(stmt).all()
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

    stmt_certs = (
        select(ResumeCert, Cert)
        .outerjoin(Cert, ResumeCert.cert_id == Cert.id)
        .where(
            ResumeCert.resume_id == resume.resume_id,
            ResumeCert.is_deleted.is_(False),
        )
        .order_by(ResumeCert.id)
    )
    certs = [
        _to_parsed_cert(resume_cert, cert)
        for resume_cert, cert in session.execute(stmt_certs).all()
    ]

    return ResumeDetailResponse(
        resume_id=resume.resume_id,
        title=resume.title,
        skills=skills,
        certs=certs,
        position=resume.position,
        career_min=resume.career_min,
        career_max=resume.career_max,
        pool=resume.pool,
        memo=resume.memo,
        is_primary=resume.is_primary,
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


def _get_certs_by_name(session: Session, names: set[str]) -> dict[str, Cert]:
    if not names:
        return {}

    stmt = select(Cert).where(
        Cert.name.in_(names),
        Cert.is_deleted.is_(False),
    )
    return {cert.name: cert for cert in session.scalars(stmt).all()}


def _add_resume_certs(
    session: Session,
    *,
    resume_id: int,
    certs: list[ParsedCert],
) -> None:
    certs_by_name = _get_certs_by_name(
        session,
        {cert.name for cert in certs if cert.in_dict},
    )

    for cert in certs:
        dictionary_cert = certs_by_name.get(cert.name) if cert.in_dict else None
        session.add(
            ResumeCert(
                resume_id=resume_id,
                cert_id=dictionary_cert.id if dictionary_cert else None,
                raw_label=None if dictionary_cert else cert.name,
                is_out_of_dict=dictionary_cert is None,
            )
        )


def _to_parsed_cert(resume_cert: ResumeCert, cert: Cert | None) -> ParsedCert:
    if cert is not None and not cert.is_deleted:
        return ParsedCert(name=cert.name, in_dict=True)

    return ParsedCert(name=resume_cert.raw_label or "", in_dict=False)
