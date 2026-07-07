from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from app.models import Skill, SkillAlias


def search_skills(
    session: Session,
    q: str | None = None,
    category: str | None = None,
    limit: int = 20,
) -> list[Skill]:
    stmt = (
        select(Skill)
        .options(selectinload(Skill.aliases))
        .where(Skill.is_deleted.is_(False))
        .order_by(Skill.canonical)
        .limit(limit)
    )

    if category:
        stmt = stmt.where(Skill.category == category)

    if q:
        pattern = f"%{q}%"
        stmt = (
            stmt.outerjoin(SkillAlias)
            .where(
                or_(
                    Skill.canonical.ilike(pattern),
                    SkillAlias.alias.ilike(pattern),
                )
            )
            .distinct()
        )

    return list(session.scalars(stmt).all())

