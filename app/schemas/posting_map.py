from pydantic import BaseModel


class MapPin(BaseModel):
    """개별 공고 마커. resume_id/session_id를 넘기면 matched_count/required_count/match_pct가 채워진다."""

    id: int
    lat: float
    lng: float
    title: str
    company: str | None = None
    matched_count: int | None = None
    required_count: int | None = None
    match_pct: float | None = None


class HeatmapEntry(BaseModel):
    """자치구별 공고 밀도."""

    region_district: str
    posting_count: int


class MapCluster(BaseModel):
    """자치구 단위 클러스터 중심좌표. avg_match_pct는 resume_id/session_id를 넘겼을 때만 채워진다."""

    district: str
    count: int
    lat: float
    lng: float
    avg_match_pct: float | None = None


class PostingsMapResponse(BaseModel):
    """F16: 국내 채용 공고 지도 — 핀 + 히트맵 + 구 단위 클러스터."""

    pins: list[MapPin]
    heatmap: list[HeatmapEntry]
    clusters: list[MapCluster]
    as_of: str
