from typing import Literal

from pydantic import BaseModel, Field


class RoadmapNodeContentRequest(BaseModel):
    """로드맵 노드 클릭 시 학습 콘텐츠 요청. 프론트가 클릭한 노드 정보를 그대로 전달한다."""

    node_id: str
    node_label: str
    node_type: Literal["skill", "concept", "cert"]
    section: str
    goal_company: str | None = None
    goal_title: str | None = None


class RoadmapNodeResourceOut(BaseModel):
    label: str
    kind: Literal["guide", "doc", "project", "video"]


class RoadmapNodeContentResponse(BaseModel):
    """로드맵 노드 학습 콘텐츠 응답. LLM 실패 시에도 동일 스키마의 결정적 폴백을 돌려준다."""

    why: str
    summary: str
    resources: list[RoadmapNodeResourceOut] = Field(min_length=2, max_length=4)
    project: str
    citations: list[str] = Field(default_factory=list, max_length=3)
