"""/chat v2 구조화 JSON 계약. 설계 문서 5절 그대로.

프론트가 이미 아는 ToolResult union(list|stat|trend|graph)에 맞춰 반환한다.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    pool: Literal["domestic", "global"] | None = None
    verbose: bool = False


class Plan(BaseModel):
    intent: str
    subqueries: list[str] = []
    tools: list[str] = []
    pool: str | None = None
    entities: dict[str, Any] = {}


class Step(BaseModel):
    kind: Literal["plan", "tool", "eval", "synth"]
    tool: str | None = None
    label: str
    detail: str | None = None


class ToolResultItem(BaseModel):
    name: str
    metric: str | None = None
    pct: float | None = None


class ToolResult(BaseModel):
    kind: Literal["list", "stat", "trend", "graph", "compare"]
    label: str
    items: list[ToolResultItem] = []
    value: float | int | str | None = None
    unit: str | None = None
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    debug: dict[str, Any] | None = None


class Citation(BaseModel):
    type: str
    ref: str
    label: str


class Confidence(BaseModel):
    level: int  # 0~5
    n: int  # 근거 표본 수


class ChatResponse(BaseModel):
    answer: str
    route: str
    plan: Plan
    steps: list[Step] = []
    tool_results: list[ToolResult] = []
    citations: list[Citation] = []
    confidence: Confidence
    degraded: bool = False
