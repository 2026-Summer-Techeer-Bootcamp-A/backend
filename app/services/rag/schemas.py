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
    # 로그인 사용자가 찜해둔 이력서를 첨부해 질문하면(예: "내 이력서 기준 부족한 스킬 뭐야?")
    # 라우터가 resume_gap/resume_coverage 인텐트로 기존 매치 엔진(match.py)을 재사용한다.
    resume_id: int | None = None
    # 공고를 1개 이상 첨부하면(예: 공고 상세에서 "이 공고와 비교" 버튼) 딥 비교 채널로
    # 쓰인다 — resume_id와 함께 오면 이력서 vs 공고, 단독으로 2개 오면 공고 vs 공고
    # 비교로 라우팅된다(app/services/rag/pipeline.py _dispatch 참고).
    posting_ids: list[int] | None = None


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
    duration_ms: float | None = None
    debug: dict[str, Any] | None = None  # kind="synth"일 때만 채워짐 — LLM 모델/temperature/토큰/재시도 실측값


class ToolResultItem(BaseModel):
    name: str
    metric: str | None = None
    pct: float | None = None
    # K3: 공고를 목록으로 반환하는 kind="posting_list"에서만 채워진다 — 프론트가 이
    # 필드들로 클릭 가능한 공고 카드(상세보기 링크, 북마크)를 렌더링한다. 그 외 kind
    # (list/stat/...)에서는 항상 None이라 기존 계약을 깨지 않는다.
    id: int | None = None
    company: str | None = None
    pool: str | None = None
    # posting_list 카드에 보유(초록)/부족(빨강) 스킬 배지를 붙이기 위한 필드 — 이력서가
    # 붙은 resume_recommend에서만 채워지고(오너드 스킬 집합이 있어야 matched/missing을
    # 가를 수 있다), 이력서 없이 도는 semantic_search에서는 항상 None으로 둔다.
    matched_skills: list[str] | None = None
    missing_skills: list[str] | None = None
    # posting_list 카드에 지역 텍스트를 보여주기 위한 필드 — region_district(구/군)가
    # 있으면 그걸, 없으면 region_city를 쓴다. resume_recommend/semantic_search 둘 다 채운다.
    region: str | None = None


class ToolResult(BaseModel):
    kind: Literal[
        "list",
        "stat",
        "trend",
        "graph",
        "compare",
        # K2: 첨부(이력서/공고) 기반 단건 딥 비교 — 프론트 비교 화면 계약과 이름을 맞춘
        # 별도 kind. 기존 "compare"(여러 기술 수요 비교, kind=list 아이템)와는 별개다.
        "resume_posting",
        "posting_posting",
        "resume_market",
        # K3: 실제 공고 목록(이력서 추천, 의미 유사 검색) — items 각각이 posting id를
        # 들고 있어 프론트가 통계 막대그래프 대신 클릭 가능한 공고 카드로 렌더링한다.
        "posting_list",
    ]
    label: str
    items: list[ToolResultItem] = []
    value: float | int | str | None = None
    unit: str | None = None
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    # K2: 딥 비교 결과 페이로드(프론트 계약 그대로) — kind가 resume_posting/posting_posting/
    # resume_market일 때만 채워진다. 자유 형식 dict로 두어 프론트 계약이 바뀌어도 이
    # 스키마를 매번 고치지 않아도 되게 한다.
    compare: dict[str, Any] | None = None
    debug: dict[str, Any] | None = None
    facts: str | None = None  # synthesize()에 실제로 먹인 근거 문장 — verbose 로그에서 "무엇을 근거로 답했는지" 보여주는 용도


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
    degraded_reasons: list[str] = []
    total_duration_ms: float | None = None
