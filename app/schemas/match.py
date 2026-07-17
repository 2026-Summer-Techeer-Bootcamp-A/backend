from typing import Literal

from pydantic import BaseModel, Field


Pool = Literal["global", "domestic"] #국내,해외 합산 금지


#gap_top5안에 들어가는
class GapSkillOut(BaseModel):
    canonical: str #표준기술명
    freq: float = Field(ge=0, le=1) #채용공고 요구 빈도 0.41 ->41%
    category: str


class WeightedGapSkillOut(BaseModel):
    canonical: str
    posting_count: int
    frequency: float = Field(ge=0, le=1)
    weight: float = Field(ge=0, le=1)
    tier: Literal["core", "supporting"]
    score_gain_if_owned: float = Field(ge=0, le=100)
    unlocked_posting_count: int
    reason: str


class RadarOut(BaseModel):
    category: str
    coverage: float = Field(ge=0, le=1) #카테고리에서 시장이 요구하는 기술 중, 내가 가진 기술


#match/gap의 최종 응답
class MatchGapResponse(BaseModel):
    gap_top5: list[GapSkillOut]
    radar: list[RadarOut]
    as_of: str
    sample_size: int
    sample_warning: bool | None = None
    current_score: float = Field(default=0, ge=0, le=100)
    items: list[WeightedGapSkillOut] = Field(default_factory=list)
    formula_version: str = "weighted-v1"
    company: str | None = None  # A-1: 목표 기업으로 모수를 좁혔을 때만 채워지는 컨텍스트

class CoverageFilterOut(BaseModel):
    position: str | None = None
    career_min: int | None = None
    career_max: int | None = None

#커버리지 계산에서 상위 요구 기술 하나를 표현
class CoverageSkillOut(BaseModel):
    canonical: str
    freq: float = Field(ge=0, le=1) #시장에서 요구되는 빈도
    owned: bool #내가 가진 기술인지 여부


    posting_count: int = 0
    frequency: float = Field(default=0, ge=0, le=1)
    weight: float = Field(default=0, ge=0, le=1)
    tier: Literal["core", "supporting"] = "supporting"
    score_contribution: float = Field(default=0, ge=0, le=100)
    penalty_contribution: float = Field(default=0, ge=0, le=100)


class MatchCoverageResponse(BaseModel):
    pool: Pool
    filter: CoverageFilterOut
    coverage_score: float = Field(ge=0, le=100)
    top_skills: list[CoverageSkillOut]
    owned_count: int
    as_of: str
    sample_size: int
    sample_warning: bool
    score: float = Field(default=0, ge=0, le=100)
    base_score: float = Field(default=0, ge=0, le=100)
    core_missing_penalty: float = Field(default=0, ge=0, le=100)
    formula_version: str = "weighted-v1"

class MatchWhatIfResponse(BaseModel):
    add: str
    matched_before: int
    matched_after: int
    delta: int
    as_of: str
    sample_size: int
    sample_warning: bool | None = None


class CoverageHistogramBin(BaseModel):
    range_start: int  # 0, 5, 10 ... 95 (bin_size 폭)
    count: int


class MatchCoverageDistributionResponse(BaseModel):
    """공고별 커버리지 분포 히스토그램 + 내 위치. widgets 'c-coverage-dist' 정식화."""

    pool: Pool
    coverage_score: float = Field(ge=0, le=100)
    histogram: list[CoverageHistogramBin]
    my_percentile: float = Field(ge=0, le=100)
    matched: int
    total: int
    threshold: float
    as_of: str
    sample_size: int
    sample_warning: bool | None = None
    note: str


class RoadmapStepOut(BaseModel):
    step: int
    canonical: str
    category: str
    matched_after: int
    delta: int
    freq: float = Field(ge=0, le=1)


class MatchRoadmapResponse(BaseModel):
    """탐욕적 최적 학습 순서. widgets 'y1-learning-path' 정식화."""

    pool: Pool
    start_matched: int
    total: int
    threshold: float
    steps: list[RoadmapStepOut]
    as_of: str
    sample_size: int
    sample_warning: bool | None = None


#POST /match/roadmap/scoped 요청 본문. 북마크한 공고 id 목록만을 모수로 로드맵을 계산한다(A-5).
class MatchRoadmapScopedRequest(BaseModel):
    resume_id: int | None = None
    session_id: str | None = None
    posting_ids: list[int] = Field(min_length=1, description="모수로 삼을 북마크 공고 id 목록")
    steps: int = Field(default=5, ge=1, le=10, description="추천 학습 순서 단계 수")


class PivotMissingSkillOut(BaseModel):
    canonical: str
    freq: float = Field(ge=0, le=1)


class PivotTargetOut(BaseModel):
    name: str
    kind: Literal["category", "industry"]
    coverage: float = Field(ge=0, le=100)
    missing: list[PivotMissingSkillOut]
    n: int


class MatchPivotMapResponse(BaseModel):
    """직군/산업별 상위 요구기술 대비 내 커버리지. widgets 'y2-pivot-map' 정식화."""

    pool: Pool
    targets: list[PivotTargetOut]
    as_of: str
    sample_size: int
