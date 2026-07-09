"""F16: 국내 채용 공고 지도 — 핀(좌표) + 히트맵(자치구별 밀도)."""

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.posting import Posting


def get_map_pins(
    session: Session,
    region: str | None = None,
    bbox: tuple[float, float, float, float] | None = None,
) -> tuple[list[dict], date]:
    """좌표가 있는 국내 공고를 핀 목록으로 반환한다.

    Returns:
        (pins, as_of)
        각 pin = {"id": int, "lat": float, "lng": float, "title": str, "company": str|None}
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

    pins = [
        {
            "id": row.id,
            "lat": float(row.lat),
            "lng": float(row.lng),
            "title": row.title,
            "company": row.company,
        }
        for row in rows
    ]

    # as_of: 국내 공고 중 최신 post_date
    as_of_stmt = (
        select(func.max(Posting.post_date))
        .where(Posting.is_deleted.is_(False))
        .where(Posting.pool == "domestic")
    )
    as_of = session.scalar(as_of_stmt) or date.today()

    return pins, as_of


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
