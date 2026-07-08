from pydantic import BaseModel, Field

from app.schemas.match import Pool


class SkillShareItem(BaseModel):
    canonical: str
    share: float = Field(ge=0, le=1)
    posting_count: int


class SkillShareResponse(BaseModel):
    pool: Pool
    skills: list[SkillShareItem]
    as_of: str
    sample_size: int


class CooccurrenceItem(BaseModel):
    canonical: str
    co_rate: float = Field(ge=0, le=1)
    co_count: int


class CooccurrenceResponse(BaseModel):
    skill: str
    co_occurs: list[CooccurrenceItem]
    as_of: str
