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


class SkillShareItem(BaseModel):
    canonical: str
    category: str | None
    posting_count: int
    share: float


class SkillShareResponse(BaseModel):
    """기술 점유율 — mv_skill_share 마트 노출. position 미지정 시 skill별 posting_count 합산."""

    items: list[SkillShareItem]
    as_of: str
    sample_size: int


class CoocNode(BaseModel):
    canonical: str
    category: str | None
    freq: int


class CoocLink(BaseModel):
    source: str
    target: str
    co_count: int
    co_rate: float


class CooccurrenceResponse(BaseModel):
    """기술 co-occurrence 네트워크 — mv_cooccurrence 마트 노출."""

    nodes: list[CoocNode]
    links: list[CoocLink]
    as_of: str


class PostingTimelineDay(BaseModel):
    date: str
    total: int
    matched: int | None = None


class PostingTimelineResponse(BaseModel):
    """최신 공고 타임라인(일별 건수). resume_id/session_id 지정 시 보유기술과 겹치는 공고 수도 함께 반환."""

    daily: list[PostingTimelineDay]
    as_of: str


class ResponseRateLevel(BaseModel):
    level: str  # "0-20" 등 20포인트 폭 버킷
    n: int


class ResponseRateCompany(BaseModel):
    company: str
    rate: float
    n: int


class ResponseRateResponse(BaseModel):
    """응답률 분포 + 회사별 응답률. posting.response_rate가 있는 공고만(현재 wanted 소스만 적재)."""

    pool: Literal["global", "domestic"]
    median_rate: float
    levels: list[ResponseRateLevel]
    companies: list[ResponseRateCompany]
    as_of: str
    sample_size: int


class SkillTrendYearlySeries(BaseModel):
    canonical: str
    shares: list[float]  # years와 동일 길이, 연도별 점유율(%)
    delta: float  # 마지막 연도 - 첫 연도


class SkillTrendMover(BaseModel):
    canonical: str
    delta: float


class SkillTrendMovers(BaseModel):
    rising: list[SkillTrendMover]
    falling: list[SkillTrendMover]


class SkillTrendYearlyResponse(BaseModel):
    """연도별 기술 점유율 추이 + 무버스(급상승/급하락)."""

    pool: Literal["global", "domestic"]
    years: list[int]
    series: list[SkillTrendYearlySeries]
    movers: SkillTrendMovers
    as_of: str
    sample_size: int


class HotCompanyItem(BaseModel):
    company: str
    posting_count: int


class HotCompaniesResponse(BaseModel):
    """최근 N일간 신규 공고가 많은 활발 기업."""

    pool: Literal["global", "domestic"]
    days: int
    items: list[HotCompanyItem]
    as_of: str


class RegionDensityItem(BaseModel):
    region_district: str
    posting_count: int


class RegionDensityResponse(BaseModel):
    """지역(구/동)별 공고 밀도. region_district는 domestic 공고에만 적재됨."""

    pool: Literal["global", "domestic"]
    items: list[RegionDensityItem]
    as_of: str


class SkillUnlockFunnel(BaseModel):
    apply: int  # 미보유 기술 0개(바로 지원 가능)
    near1: int  # 미보유 기술 1개
    near2_3: int  # 미보유 기술 2~3개
    far: int  # 미보유 기술 4개 이상


class SkillUnlockCandidate(BaseModel):
    canonical: str
    req_count: int  # 이 기술을 요구하면서 아직 apply 단계가 아닌 공고 수
    marginal_apply: int  # 이 기술 하나만 추가하면 apply로 넘어가는 공고 수(near1 중 유일한 미보유 기술)


class SkillUnlockResponse(BaseModel):
    """한계 해금 — 기술 하나를 더 배우면 지원 가능해지는 공고가 얼마나 늘어나는지."""

    pool: Literal["global", "domestic"]
    funnel: SkillUnlockFunnel
    candidates: list[SkillUnlockCandidate]
    as_of: str
    sample_size: int
    sample_warning: bool | None = None


class GroupShareItem(BaseModel):
    canonical: str
    count: int
    share: float  # percent, 그룹 union 대비(절대 전체 공고 대비 아님)


class GroupShareResponse(BaseModel):
    """프레임워크/DB 그룹 내 상대 점유율. share=count/union_count*100(그룹 union 공고 기준, 대략치)."""

    group: Literal["frontend_fw", "backend_fw", "database"]
    pool: Literal["global", "domestic"]
    union_count: int
    items: list[GroupShareItem]
    as_of: str


class ConceptTechNode(BaseModel):
    name: str
    type: Literal["concept", "tech"]


class ConceptTechLink(BaseModel):
    source: str
    target: str
    value: int


class ConceptTechResponse(BaseModel):
    """개념→기술 Sankey. posting_concept×posting_tech 공동출현 상위 개념 × 개념당 상위 기술."""

    pool: Literal["global", "domestic"]
    nodes: list[ConceptTechNode]
    links: list[ConceptTechLink]
    as_of: str


class SkillCountDistBucket(BaseModel):
    k: int  # 공고당 요구 스킬 개수
    count: int  # 그 개수를 가진 공고 수


class SkillCountDistResponse(BaseModel):
    """공고당 요구 스킬 개수 분포 + 평균/중앙값."""

    pool: Literal["global", "domestic"]
    histogram: list[SkillCountDistBucket]
    avg: float
    median: float
    as_of: str


class GlobalDomesticLagPoint(BaseModel):
    year: int
    share: float  # percent


class GlobalDomesticLagItem(BaseModel):
    canonical: str
    lag_years: int  # 0~3, 글로벌이 국내를 이만큼(년) 선행한다고 추정
    global_series: list[GlobalDomesticLagPoint]
    domestic_series: list[GlobalDomesticLagPoint]


class GlobalDomesticLagResponse(BaseModel):
    """글로벌 연도 점유율 추이가 국내를 선행하는 근사 시차(교차상관, lag 0~3년).

    표본 부족·시계열 짧은 기술은 제외. 정확도 한계가 있는 근사치이며 참고용.
    """

    items: list[GlobalDomesticLagItem]
    as_of: str
    note: str
