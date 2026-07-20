"""학습 로드맵 AI 보강 — 격차(기술/개념/자격증/연차)를 LLM으로 구조화된 학습 순서로 정리한다.

데모 안정성이 최우선이다. LLM 호출 실패, 타임아웃, 파싱 실패, 스키마 불일치 어느 경우에도
예외를 던지지 않고 입력만으로 구성한 결정적 폴백을 같은 스키마로 돌려준다.
"""

from __future__ import annotations

import json
import logging

from app.schemas.roadmap_enrich import (
    RoadmapEnrichRequest,
    RoadmapEnrichResponse,
    RoadmapEnrichStepOut,
)
from app.services.rag.llm import LLMClient

logger = logging.getLogger(__name__)

ROADMAP_ENRICH_SYSTEM = (
    "너는 한국 IT 취업 준비생을 코칭하는 커리어 로드맵 코치다. "
    "지원자가 부족한 기술, 개념, 자격증, 연차 정보를 받아 목표 기업/직무에 맞는 "
    "학습 순서를 만든다. 반드시 아래 JSON 스키마 하나만 출력하고 다른 설명이나 "
    "마크다운, 코드블록을 덧붙이지 않는다.\n"
    "{"
    '"headline": string, '
    '"summary": string, '
    '"quick_win": string, '
    '"steps": ['
    '{"order": int, "label": string, '
    '"type": "skill"|"concept"|"cert"|"career", '
    '"effort": string, "priority": "high"|"medium"|"low", '
    '"reason": string, "project": string}'
    "]"
    "}\n"
    "규칙: steps에는 입력으로 주어진 missing_skills, concepts, certs를 빠짐없이 각각 "
    "하나의 step으로 담고, career_mine이 career_required보다 낮으면 career 타입 step을 "
    "하나 추가한다. order는 학습하기 좋은 순서대로 1부터 매긴다. quick_win은 가장 "
    "레버리지가 큰 다음 한 수를 가리키는 label 문자열이어야 한다. summary는 1~2문장으로 "
    "현재 위치와 방향을 담담하게 서술한다. headline은 목표 기업/직무와 대략적인 기간을 "
    "담은 한 문장이다. 모든 텍스트는 자연스러운 한국어로 작성한다."
)


def _build_prompt(request: RoadmapEnrichRequest) -> str:
    payload = {
        "goal_company": request.goal_company,
        "goal_title": request.goal_title,
        "owned_skills": request.owned_skills,
        "missing_skills": request.missing_skills,
        "concepts": request.concepts,
        "certs": request.certs,
        "career_required": request.career_required,
        "career_mine": request.career_mine,
    }
    return (
        "다음은 지원자의 격차 데이터다(JSON). 이 정보만 사용해 학습 로드맵을 만들어라.\n"
        + json.dumps(payload, ensure_ascii=False)
    )


def _effort_for(index: int, step_type: str) -> str:
    if step_type == "career":
        return "지속"
    if step_type == "cert":
        return "1개월"
    if step_type == "concept":
        return "2주"
    # skill: 앞쪽일수록 짧게(빠른 승리), 뒤로 갈수록 길게 잡는다.
    return "1주" if index == 0 else "2주"


def _priority_for(order: int) -> str:
    if order == 1:
        return "high"
    if order <= 3:
        return "medium"
    return "low"


def _reason_for(step_type: str, label: str, request: RoadmapEnrichRequest) -> str:
    if step_type == "skill":
        return f"{request.goal_title} 채용 공고에서 자주 요구되는 기술이에요."
    if step_type == "concept":
        return f"{request.goal_title} 직무에서 실무 적용 이해도를 보여주는 개념이에요."
    if step_type == "cert":
        return f"{request.goal_company} 지원 시 자격 요건 충족을 뒷받침해요."
    return f"{request.goal_company}이 요구하는 연차 조건에 가까워지기 위한 경험이에요."


def _project_for(step_type: str, label: str) -> str:
    if step_type == "skill":
        return f"{label}을 활용한 미니 프로젝트를 만들어 이력서에 추가해보세요."
    if step_type == "concept":
        return f"{label} 개념을 적용한 예제 코드를 작성하고 정리해보세요."
    if step_type == "cert":
        return f"{label} 취득 일정을 잡고 학습 계획을 세워보세요."
    return "관련 실무 경험을 쌓을 수 있는 프로젝트나 인턴십을 찾아보세요."


def _fallback_response(request: RoadmapEnrichRequest) -> RoadmapEnrichResponse:
    """LLM 미가용/실패 시 입력만으로 구성하는 결정적 폴백. 항상 유효한 스키마를 만족한다."""
    steps: list[RoadmapEnrichStepOut] = []
    order = 1

    for skill in request.missing_skills:
        steps.append(
            RoadmapEnrichStepOut(
                order=order,
                label=skill,
                type="skill",
                effort=_effort_for(order - 1, "skill"),
                priority=_priority_for(order),
                reason=_reason_for("skill", skill, request),
                project=_project_for("skill", skill),
            )
        )
        order += 1

    for concept in request.concepts:
        steps.append(
            RoadmapEnrichStepOut(
                order=order,
                label=concept,
                type="concept",
                effort=_effort_for(order - 1, "concept"),
                priority=_priority_for(order),
                reason=_reason_for("concept", concept, request),
                project=_project_for("concept", concept),
            )
        )
        order += 1

    for cert in request.certs:
        steps.append(
            RoadmapEnrichStepOut(
                order=order,
                label=cert,
                type="cert",
                effort=_effort_for(order - 1, "cert"),
                priority=_priority_for(order),
                reason=_reason_for("cert", cert, request),
                project=_project_for("cert", cert),
            )
        )
        order += 1

    if (
        request.career_required is not None
        and request.career_mine is not None
        and request.career_mine < request.career_required
    ):
        gap = request.career_required - request.career_mine
        label = f"{gap}년치 실무 경험 채우기"
        steps.append(
            RoadmapEnrichStepOut(
                order=order,
                label=label,
                type="career",
                effort=_effort_for(order - 1, "career"),
                priority=_priority_for(order),
                reason=_reason_for("career", label, request),
                project=_project_for("career", label),
            )
        )
        order += 1

    if steps:
        quick_win = steps[0].label
        summary = (
            f"{request.goal_company} {request.goal_title} 목표까지 부족한 항목이 "
            f"{len(steps)}개 남아있어요. {quick_win}부터 순서대로 채워가면 돼요."
        )
    else:
        quick_win = "현재 보유 스킬 점검"
        summary = (
            f"{request.goal_company} {request.goal_title} 목표에 필요한 격차 데이터가 "
            "충분하지 않아요. 보유 스킬을 먼저 점검해보세요."
        )

    headline = f"{request.goal_company} {request.goal_title}까지, {max(len(steps), 1)}단계 학습 로드맵"

    return RoadmapEnrichResponse(
        headline=headline,
        summary=summary,
        quick_win=quick_win,
        steps=steps,
    )


def build_roadmap_enrichment(
    request: RoadmapEnrichRequest,
    llm: LLMClient,
) -> RoadmapEnrichResponse:
    """LLM으로 로드맵을 보강하되, 어떤 실패든 결정적 폴백으로 흡수해 항상 200을 낸다."""
    fallback = _fallback_response(request)

    try:
        raw = llm.json(ROADMAP_ENRICH_SYSTEM, _build_prompt(request), temperature=0.3)
    except Exception as exc:  # noqa: BLE001 — 데모 안정성: LLM 호출은 절대 요청을 깨면 안 된다
        logger.warning("roadmap enrich llm call failed: %s", exc)
        raw = None

    if not raw:
        return fallback

    try:
        return RoadmapEnrichResponse.model_validate(raw)
    except Exception as exc:  # noqa: BLE001 — 스키마 불일치도 폴백으로 흡수한다
        logger.warning("roadmap enrich llm response failed validation: %s", exc)
        return fallback
