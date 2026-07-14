from pydantic import BaseModel


class SearchPostingItem(BaseModel):
    id: int
    title: str
    company: str
    pool: str


class SearchSkillItem(BaseModel):
    canonical: str
    category: str | None


class SearchCompanyItem(BaseModel):
    company: str
    posting_count: int


class SearchCachePayload(BaseModel):
    """검색어 원문을 제외한, Redis에 저장 가능한 통합 검색 결과."""

    postings: list[SearchPostingItem]
    skills: list[SearchSkillItem]
    companies: list[SearchCompanyItem]


class SearchResponse(SearchCachePayload):
    """통합 검색 응답 — 공고 · 기술 · 기업을 한 번에 반환."""

    query: str
