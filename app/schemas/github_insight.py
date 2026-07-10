"""GitHub 레포 단위 인사이트(t,u,l) — github_repo_snapshot/github_star_history 기반."""

from pydantic import BaseModel


class GithubVitalityLanguage(BaseModel):
    lang: str
    repo_n: int
    fork_ratio: float
    issue_per_1k_star: float
    median_days_since_push: int | None
    job_demand_pct: float | None
    in_taxonomy: bool


class GithubVitalityResponse(BaseModel):
    """언어별 GitHub 활력도. widgets 't-github-vitality' 정식화."""

    languages: list[GithubVitalityLanguage]
    as_of: str
    sample_size: int
    note: str


class GithubTopicItem(BaseModel):
    canonical: str
    category: str
    repo_reach: int
    reach_pct: float
    job_demand_pct: float | None
    owned: bool | None = None


class GithubTopicsResponse(BaseModel):
    """GitHub topics 태그 기반 관심 vs 채용수요. widgets 'u-github-topics' 정식화."""

    items: list[GithubTopicItem]
    as_of: str
    sample_size: int
    note: str


class GithubChronicleYearPoint(BaseModel):
    year: int
    rank: int
    stars: int


class GithubChronicleLine(BaseModel):
    tech: str
    repo: str
    points: list[GithubChronicleYearPoint]


class GithubChronicleResponse(BaseModel):
    """기술별 대표 레포의 연도별 스타 순위 변천사. widgets 'l-github-chronicle' 정식화."""

    years: list[int]
    lines: list[GithubChronicleLine]
    as_of: str
    sample_size: int
    note: str
