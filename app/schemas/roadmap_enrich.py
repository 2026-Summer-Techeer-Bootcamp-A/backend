from typing import Literal

from pydantic import BaseModel, Field


class RoadmapEnrichRequest(BaseModel):
    """로드맵 AI 보강 요청. 프론트가 이미 계산한 격차를 그대로 전달한다."""

    goal_company: str
    goal_title: str
    owned_skills: list[str] = Field(default_factory=list)
    missing_skills: list[str] = Field(default_factory=list)
    concepts: list[str] = Field(default_factory=list)
    certs: list[str] = Field(default_factory=list)
    career_required: int | None = None
    career_mine: int | None = None


class RoadmapEnrichStepOut(BaseModel):
    order: int
    label: str
    type: Literal["skill", "concept", "cert", "career"]
    effort: str
    priority: Literal["high", "medium", "low"]
    reason: str
    project: str


class RoadmapEnrichResponse(BaseModel):
    """로드맵 AI 보강 응답. LLM 실패 시에도 동일 스키마의 결정적 폴백을 돌려준다."""

    headline: str
    summary: str
    quick_win: str
    steps: list[RoadmapEnrichStepOut]
