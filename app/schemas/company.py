from pydantic import BaseModel


class CompanyEntry(BaseModel):
    """기업별 공고 수 + 응답률. response_rate는 원티드 공고에만 존재."""

    company: str
    posting_count: int
    response_rate: float | None = None


class CompanyBySkillResponse(BaseModel):
    """F7+F11: 특정 기술을 요구한 기업을 과거/현재로 나눠 보여주는 응답."""

    skill: str
    split_date: str
    present: list[CompanyEntry]
    past: list[CompanyEntry]
    as_of: str
    domestic_note: str | None = None
