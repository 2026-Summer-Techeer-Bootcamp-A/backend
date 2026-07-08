from typing import Literal

from pydantic import BaseModel, Field


Pool = Literal["global", "domestic"]


class GapSkillOut(BaseModel):
    canonical: str #표준기술명
    freq: float = Field(ge=0, le=1) #채용공고 요구 빈도
    category: str


class RadarOut(BaseModel):
    category: str
    coverage: float = Field(ge=0, le=1) #카테고리에서 시장이 요구하는 기술 중, 내가 가진 기술


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