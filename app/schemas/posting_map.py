from pydantic import BaseModel


class MapPin(BaseModel):
    """개별 공고 마커."""

    id: int
    lat: float
    lng: float
    title: str
    company: str | None = None


class HeatmapEntry(BaseModel):
    """자치구별 공고 밀도."""

    region_district: str
    posting_count: int


class PostingsMapResponse(BaseModel):
    """F16: 국내 채용 공고 지도 — 핀 + 히트맵."""

    pins: list[MapPin]
    heatmap: list[HeatmapEntry]
    as_of: str
