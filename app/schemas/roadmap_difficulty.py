from typing import Literal

from pydantic import BaseModel, Field

DifficultyTier = Literal["입문", "초급", "중급", "고급"]


class RoadmapDifficultyNodeIn(BaseModel):
    """난이도 보정 요청 노드 하나. 프론트가 로드맵에 이미 그려둔 노드 정보를 그대로 전달한다."""

    node_id: str
    label: str
    type: Literal["skill", "concept", "cert"]
    prereq_depth: int = Field(ge=0)


class RoadmapDifficultyRequest(BaseModel):
    nodes: list[RoadmapDifficultyNodeIn] = Field(min_length=1)


class RoadmapDifficultyItemOut(BaseModel):
    node_id: str
    tier: DifficultyTier
    avg_career: float | None = None
    demand: int
    basis: str


class RoadmapDifficultyResponse(BaseModel):
    """로드맵 난이도 보정 응답. LLM 실패 시에도 동일 스키마의 결정적 폴백을 돌려준다."""

    items: list[RoadmapDifficultyItemOut]
