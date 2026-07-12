"""F16: 국내 채용 공고 지도 — 핀(좌표) + 히트맵(자치구별 밀도) + 구 단위 클러스터."""

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.posting import Posting, PostingTech


def _posting_skill_counts(
    session: Session, posting_ids: list[int], owned_skill_ids: set[int]
) -> dict[int, tuple[int, int]]:
    """posting_id -> (matched_count, required_count). owned_skill_ids가 비어있어도 required_count는 계산한다."""
    if not posting_ids:
        return {}

    rows = session.execute(
        select(PostingTech.posting_id, PostingTech.skill_id).where(
            PostingTech.posting_id.in_(posting_ids),
            PostingTech.is_deleted.is_(False),
        )
    ).all()

    counts: dict[int, tuple[int, int]] = {}
    per_posting_skills: dict[int, set[int]] = {}
    for posting_id, skill_id in rows:
        per_posting_skills.setdefault(posting_id, set()).add(skill_id)

    for posting_id, skills in per_posting_skills.items():
        matched = len(skills & owned_skill_ids)
        counts[posting_id] = (matched, len(skills))

    return counts


def get_map_pins(
    session: Session,
    region: str | None = None,
    bbox: tuple[float, float, float, float] | None = None,
    owned_skill_ids: set[int] | None = None,
) -> tuple[list[dict], date]:
    """좌표가 있는 국내 공고를 핀 목록으로 반환한다.

    owned_skill_ids가 주어지면 각 pin에 matched_count/required_count/match_pct를 채운다.

    Returns:
        (pins, as_of)
    """
    filters = [
        Posting.is_deleted.is_(False),
        Posting.pool == "domestic",
        Posting.lat.isnot(None),
        Posting.lng.isnot(None),
    ]

    if region:
        filters.append(Posting.region_city.ilike(f"%{region}%"))

    if bbox:
        min_lng, min_lat, max_lng, max_lat = bbox
        filters.extend([
            Posting.lng >= min_lng,
            Posting.lat >= min_lat,
            Posting.lng <= max_lng,
            Posting.lat <= max_lat,
        ])

    stmt = (
        select(
            Posting.id,
            Posting.lat,
            Posting.lng,
            Posting.title,
            Posting.company,
        )
        .where(*filters)
    )

    rows = session.execute(stmt).all()

    skill_counts: dict[int, tuple[int, int]] = {}
    if owned_skill_ids is not None:
        skill_counts = _posting_skill_counts(session, [row.id for row in rows], owned_skill_ids)

    pins = []
    for row in rows:
        pin = {
            "id": row.id,
            "lat": float(row.lat),
            "lng": float(row.lng),
            "title": row.title,
            "company": row.company,
        }
        if row.id in skill_counts:
            matched, required = skill_counts[row.id]
            pin["matched_count"] = matched
            pin["required_count"] = required
            pin["match_pct"] = round(matched / required * 100, 1) if required else 0.0
        pins.append(pin)

    # as_of: 국내 공고 중 최신 post_date
    as_of_stmt = (
        select(func.max(Posting.post_date))
        .where(Posting.is_deleted.is_(False))
        .where(Posting.pool == "domestic")
    )
    as_of = session.scalar(as_of_stmt) or date.today()

    return pins, as_of


def get_clusters(
    session: Session,
    region: str | None = None,
    bbox: tuple[float, float, float, float] | None = None,
    owned_skill_ids: set[int] | None = None,
) -> list[dict]:
    """자치구 단위 클러스터 중심좌표(평균 lat/lng) + 건수 + (옵션) 평균 매칭률."""
    filters = [
        Posting.is_deleted.is_(False),
        Posting.pool == "domestic",
        Posting.lat.isnot(None),
        Posting.lng.isnot(None),
        Posting.region_district.isnot(None),
    ]

    if region:
        filters.append(Posting.region_city.ilike(f"%{region}%"))

    if bbox:
        min_lng, min_lat, max_lng, max_lat = bbox
        filters.extend([
            Posting.lng >= min_lng,
            Posting.lat >= min_lat,
            Posting.lng <= max_lng,
            Posting.lat <= max_lat,
        ])

    rows = session.execute(
        select(Posting.id, Posting.region_district, Posting.lat, Posting.lng).where(*filters)
    ).all()

    skill_counts: dict[int, tuple[int, int]] = {}
    if owned_skill_ids is not None:
        skill_counts = _posting_skill_counts(session, [row.id for row in rows], owned_skill_ids)

    by_district: dict[str, list] = {}
    for row in rows:
        by_district.setdefault(row.region_district, []).append(row)

    clusters = []
    for district, entries in by_district.items():
        count = len(entries)
        avg_lat = sum(float(e.lat) for e in entries) / count
        avg_lng = sum(float(e.lng) for e in entries) / count

        avg_match_pct = None
        if owned_skill_ids is not None:
            pcts = []
            for e in entries:
                matched, required = skill_counts.get(e.id, (0, 0))
                if required:
                    pcts.append(matched / required * 100)
            avg_match_pct = round(sum(pcts) / len(pcts), 1) if pcts else 0.0

        clusters.append(
            {
                "district": district,
                "count": count,
                "lat": round(avg_lat, 6),
                "lng": round(avg_lng, 6),
                "avg_match_pct": avg_match_pct,
            }
        )

    clusters.sort(key=lambda c: c["count"], reverse=True)
    return clusters


def get_heatmap(
    session: Session,
    region: str | None = None,
    bbox: tuple[float, float, float, float] | None = None,
) -> list[dict]:
    """자치구(region_district)별 공고 수를 집계한다."""
    filters = [
        Posting.is_deleted.is_(False),
        Posting.pool == "domestic",
        Posting.region_district.isnot(None),
    ]

    if region:
        filters.append(Posting.region_city.ilike(f"%{region}%"))

    if bbox:
        min_lng, min_lat, max_lng, max_lat = bbox
        filters.extend([
            Posting.lat.isnot(None),
            Posting.lng.isnot(None),
            Posting.lng >= min_lng,
            Posting.lat >= min_lat,
            Posting.lng <= max_lng,
            Posting.lat <= max_lat,
        ])

    stmt = (
        select(
            Posting.region_district,
            func.count().label("posting_count"),
        )
        .where(*filters)
        .group_by(Posting.region_district)
        .order_by(func.count().desc())
    )

    return [
        {"region_district": row.region_district, "posting_count": row.posting_count}
        for row in session.execute(stmt).all()
    ]
