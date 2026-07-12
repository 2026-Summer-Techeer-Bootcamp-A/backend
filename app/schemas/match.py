from typing import Literal

from pydantic import BaseModel, Field


Pool = Literal["global", "domestic"] #국내,해외 합산 금지


#gap_top5안에 들어가는
class GapSkillOut(BaseModel):
    canonical: str #표준기술명
    freq: float = Field(ge=0, le=1) #채용공고 요구 빈도 0.41 ->41%
    category: str


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

class CoverageFilterOut(BaseModel):
    position: str | None = None
    career_min: int | None = None
    career_max: int | None = None

#커버리지 계산에서 상위 요구 기술 하나를 표현
class CoverageSkillOut(BaseModel):
    canonical: str
    freq: float = Field(ge=0, le=1) #시장에서 요구되는 빈도
    owned: bool #내가 가진 기술인지 여부


class MatchCoverageResponse(BaseModel):
    pool: Pool
    filter: CoverageFilterOut
    coverage_score: float = Field(ge=0, le=100)
    top_skills: list[CoverageSkillOut]
    owned_count: int
    as_of: str
    sample_size: int
    sample_warning: bool

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