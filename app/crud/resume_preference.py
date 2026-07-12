from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Resume, ResumePreference
from app.schemas.resume_preference import LocationPrefs, ResumePreferences


def get_resume_preferences(
    session: Session,
    *,
    resume_id: int,
    user_id: int,
) -> ResumePreferences | None:
    resume = _get_owned_resume(session, resume_id=resume_id, user_id=user_id)
    if resume is None:
        return None

    preference = session.get(ResumePreference, resume_id)
    if preference is None:
        return None

    return _to_schema(preference)


def upsert_resume_preferences(
    session: Session,
    *,
    resume_id: int,
    user_id: int,
    preferences_in: ResumePreferences,
) -> ResumePreferences | None:
    resume = _get_owned_resume(session, resume_id=resume_id, user_id=user_id)
    if resume is None:
        return None

    preference = session.get(ResumePreference, resume_id)
    extra = {
        "companyStagePrefs": preferences_in.companyStagePrefs,
        "sectorInterests": preferences_in.sectorInterests,
        "location": preferences_in.location.model_dump(),
    }

    if preference is None:
        preference = ResumePreference(
            resume_id=resume_id,
            level=preferences_in.level,
            job_search_status=preferences_in.jobSearchStatus,
            preferences_extra=extra,
        )
        session.add(preference)
    else:
        preference.level = preferences_in.level
        preference.job_search_status = preferences_in.jobSearchStatus
        preference.preferences_extra = extra

    session.commit()
    session.refresh(preference)
    return _to_schema(preference)


def _get_owned_resume(session: Session, *, resume_id: int, user_id: int) -> Resume | None:
    return session.scalar(
        select(Resume).where(
            Resume.resume_id == resume_id,
            Resume.user_id == user_id,
            Resume.is_deleted.is_(False),
        )
    )


def _to_schema(preference: ResumePreference) -> ResumePreferences:
    extra = preference.preferences_extra or {}
    location = extra.get("location") or {}
    return ResumePreferences(
        level=preference.level,
        jobSearchStatus=preference.job_search_status,
        companyStagePrefs=extra.get("companyStagePrefs") or {},
        sectorInterests=extra.get("sectorInterests") or [],
        location=LocationPrefs(**location) if location else LocationPrefs(),
    )
