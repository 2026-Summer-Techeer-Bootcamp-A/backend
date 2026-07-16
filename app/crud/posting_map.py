"""F16: 국내 채용 공고 지도 — 핀(좌표) + 히트맵(자치구별 밀도) + 구 단위 클러스터."""

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.posting import Posting, PostingTech

# posting_id IN (...) 절이 한 번에 너무 커지지 않도록 청크로 나눠 조회한다
# (app/crud/posting.py의 _IN_CLAUSE_CHUNK_SIZE와 동일 기준).
_IN_CLAUSE_CHUNK_SIZE = 5000

# 지도 핀은 화면에 그릴 수 있는 개수 이상은 의미가 없다. 데이터가 늘어나도
# 요청 하나가 무제한으로 커지지 않도록 상한을 둔다.
_MAX_MAP_PINS = 5000


def _posting_skill_counts(
    session: Session, posting_ids: list[int], owned_skill_ids: set[int]
) -> dict[int, tuple[int, int]]:
    """posting_id -> (matched_count, required_count). owned_skill_ids가 비어있어도 required_count는 계산한다."""
    if not posting_ids:
        return {}

    per_posting_skills: dict[int, set[int]] = {}
    for i in range(0, len(posting_ids), _IN_CLAUSE_CHUNK_SIZE):
        batch = posting_ids[i : i + _IN_CLAUSE_CHUNK_SIZE]
        rows = session.execute(
            select(PostingTech.posting_id, PostingTech.skill_id).where(
                PostingTech.posting_id.in_(batch),
                PostingTech.is_deleted.is_(False),
            )
        ).all()
        for posting_id, skill_id in rows:
            per_posting_skills.setdefault(posting_id, set()).add(skill_id)

    counts: dict[int, tuple[int, int]] = {}
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
        .order_by(Posting.post_date.desc())
        .limit(_MAX_MAP_PINS)
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

    # 구 개수(수십 개)만 필요한 count/평균좌표는 postgres GROUP BY/AVG로 계산한다.
    # 예전엔 좌표가 있는 공고 행 전체(수천~수만 건)를 애플리케이션까지 가져와
    # 파이썬 for-loop로 평균을 냈는데, 결과 크기에 비해 낭비가 컸다.
    agg_rows = session.execute(
        select(
            Posting.region_district,
            func.count().label("count"),
            func.avg(Posting.lat).label("avg_lat"),
            func.avg(Posting.lng).label("avg_lng"),
        )
        .where(*filters)
        .group_by(Posting.region_district)
    ).all()

    # avg_match_pct(보유 기술 매칭률)는 posting 단위 skill 매칭이 필요해 SQL
    # AVG만으로는 계산 못 함 — owned_skill_ids가 있을 때만 별도로 posting_id만
    # 가볍게 조회해서 구한다(좌표는 이미 위에서 집계했으니 다시 안 가져옴).
    avg_match_by_district: dict[str, float] = {}
    if owned_skill_ids is not None:
        id_rows = session.execute(select(Posting.id, Posting.region_district).where(*filters)).all()
        skill_counts = _posting_skill_counts(session, [row.id for row in id_rows], owned_skill_ids)

        pcts_by_district: dict[str, list[float]] = {}
        for posting_id, district in id_rows:
            matched, required = skill_counts.get(posting_id, (0, 0))
            if required:
                pcts_by_district.setdefault(district, []).append(matched / required * 100)

        for district in {row.region_district for row in agg_rows}:
            pcts = pcts_by_district.get(district, [])
            avg_match_by_district[district] = round(sum(pcts) / len(pcts), 1) if pcts else 0.0

    clusters = [
        {
            "district": row.region_district,
            "count": row.count,
            "lat": round(float(row.avg_lat), 6),
            "lng": round(float(row.avg_lng), 6),
            "avg_match_pct": avg_match_by_district.get(row.region_district) if owned_skill_ids is not None else None,
        }
        for row in agg_rows
    ]

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
