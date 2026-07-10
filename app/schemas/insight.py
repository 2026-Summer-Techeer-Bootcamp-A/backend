"""Stats/Trend 확장 인사이트 — 프론트 widgets 갤러리 전용이었던 지표들의 정식 API화."""

from typing import Literal

from pydantic import BaseModel


class HypeVsHireQuarter(BaseModel):
    quarter: str  # "2026Q3"
    interest_value: float
    posting_count: int


class HypeVsHireResponse(BaseModel):
    """관심(HN 언급) vs 실수요(공고) 괴리, 분기별. himalayas 제외(F14 규칙과 동일)."""

    skill: str
    quarters: list[HypeVsHireQuarter]
    as_of: str
    sample_size: int
    note: str


class NewcomerGateItem(BaseModel):
    canonical: str
    postings: int
    newcomer_postings: int
    open_rate: float = 0  # percent


class NewcomerGateResponse(BaseModel):
    """신입 진입장벽 — career_min<=0을 신입 가능 근사치로 사용(국내 전용)."""

    pool: Literal["domestic"] = "domestic"
    items: list[NewcomerGateItem]
    as_of: str
    sample_size: int
    sample_warning: bool | None = None
    note: str


class PoolGapItem(BaseModel):
    canonical: str
    category: str
    global_pct: float
    domestic_pct: float
    diff: float
    global_n: int
    domestic_n: int


class GlobalDomesticGapResponse(BaseModel):
    """국내/해외 각 풀 내 점유율 비교. global_favored는 diff 내림차순, domestic_favored는 diff 오름차순."""

    global_favored: list[PoolGapItem]
    domestic_favored: list[PoolGapItem]
    as_of: str
    sample_size: dict[str, int]


class HiringSeasonMonth(BaseModel):
    month: int
    global_idx: float
    domestic_idx: float
    global_n: int
    domestic_n: int


class HiringSeasonResponse(BaseModel):
    """월별 채용 지수(=월별 건수/월평균). himalayas 제외, 진행 중인 올해는 제외."""

    months: list[HiringSeasonMonth]
    as_of: str
    sample_size: dict[str, int]
    note: str


class IndustrySkillSignature(BaseModel):
    canonical: str
    index: float
    share_pct: float
    n: int


class IndustryFingerprintEntry(BaseModel):
    name: str
    n: int
    signature: list[IndustrySkillSignature]


class IndustryFingerprintResponse(BaseModel):
    """산업별 기술 지문. index = 산업 내 비중 / 전 산업 평균 비중. 국내 전용, industry 분류 품질 낮음(참고용)."""

    pool: Literal["domestic"] = "domestic"
    industries: list[IndustryFingerprintEntry]
    as_of: str
    sample_size: int
    sample_warning: bool | None = None
    note: str


class RoleCategoryOut(BaseModel):
    name: str
    n: int


class RoleStackFitResponse(BaseModel):
    """직군간 요구 기술 벡터 유사도(가중 자카드/Ruzicka, 0~100). 기술직(job_category.is_tech)만 대상."""

    categories: list[RoleCategoryOut]
    matrix: list[list[float]]
    as_of: str
    sample_size: int
