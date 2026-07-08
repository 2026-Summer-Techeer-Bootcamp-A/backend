"""F16: 국내 채용 공고 지도 (핀 + 히트맵). 국내 전용 — pool=global이면 422."""

from fastapi import APIRouter, HTTPException, Query

from app.core.deps import SessionDep
from app.crud.posting_map import get_heatmap, get_map_pins
from app.schemas.posting_map import HeatmapEntry, MapPin, PostingsMapResponse

router = APIRouter()


def _parse_bbox(bbox_str: str) -> tuple[float, float, float, float]:
    """'min_lng,min_lat,max_lng,max_lat' 문자열을 파싱한다."""
    parts = bbox_str.split(",")
    if len(parts) != 4:
        raise HTTPException(
            status_code=422,
            detail="bbox must be 'min_lng,min_lat,max_lng,max_lat'",
        )
    try:
        return tuple(float(p.strip()) for p in parts)  # type: ignore[return-value]
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="bbox values must be numeric",
        )


@router.get("/postings/map", response_model=PostingsMapResponse)
def postings_map(
    session: SessionDep,
    region: str | None = Query(None, description="행정구역 필터 (예: 서울)"),
    bbox: str | None = Query(
        None, description="지도 영역 경계 상자 (min_lng,min_lat,max_lng,max_lat)"
    ),
    pool: str | None = Query(None, description="국내 전용. global이면 422"),
) -> PostingsMapResponse:
    """국내 공고의 위치를 지도 핀과 자치구별 히트맵으로 돌려줍니다.

    국내 전용 API — pool=global이면 422를 반환합니다.
    """
    # 국내 전용: global 명시 시 422
    if pool == "global":
        raise HTTPException(
            status_code=422,
            detail="지도는 국내(domestic) 공고 전용이에요. pool=global은 지원하지 않습니다.",
        )

    parsed_bbox = _parse_bbox(bbox) if bbox else None

    pins, as_of = get_map_pins(session=session, region=region, bbox=parsed_bbox)
    heatmap = get_heatmap(session=session, region=region, bbox=parsed_bbox)

    return PostingsMapResponse(
        pins=[MapPin(**pin) for pin in pins],
        heatmap=[HeatmapEntry(**entry) for entry in heatmap],
        as_of=as_of.isoformat(),
    )
