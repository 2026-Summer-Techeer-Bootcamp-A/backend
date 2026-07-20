"""학습 로드맵 노드 학습 콘텐츠 — 노드 클릭 시 RAG로 짧은 학습 가이드를 만든다.

데모 안정성이 최우선이다. LLM 호출 실패, 타임아웃, 파싱 실패, 스키마 불일치 어느 경우에도
예외를 던지지 않고 node_label/node_type/section만으로 구성한 결정적 폴백을 같은 스키마로
돌려준다. 노드 기술이 taxonomy에 있으면 최근 3년 공고 요구 건수를 세어 why/citations에
실제 숫자를 실어 근거를 붙인다.
"""

from __future__ import annotations

import json
import logging

from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from app.models.posting import Posting, PostingTech
from app.models.skill import Skill, SkillAlias
from app.schemas.roadmap_node_content import (
    RoadmapNodeContentRequest,
    RoadmapNodeContentResponse,
)
from app.services.match import market_pool_cutoff_date
from app.services.rag.llm import LLMClient

logger = logging.getLogger(__name__)

ROADMAP_NODE_CONTENT_SYSTEM = (
    "너는 한국 IT 취업 준비생에게 학습 로드맵의 한 항목을 짧게 설명하는 코치다. "
    "반드시 아래 JSON 스키마 하나만 출력하고 다른 설명이나 마크다운, 코드블록을 "
    "덧붙이지 않는다.\n"
    "{"
    '"why": string, '
    '"summary": string, '
    '"resources": [{"label": string, "kind": "guide"|"doc"|"project"|"video"}], '
    '"project": string, '
    '"citations": [string]'
    "}\n"
    "규칙: why는 이 항목을 왜 배우는지 한두 문장(80자 이내)으로 쓰고, 주어진 근거 "
    "데이터(공고 요구 건수 등)가 있으면 반드시 숫자를 그대로 인용한다. summary는 "
    "핵심 개념을 3문장 이내(문장당 40자 내외)로 담백하게 요약한다. resources는 "
    "2개에서 4개 사이로, 각 label은 15자 이내의 구체적인 학습 자료 이름이다. "
    "project는 손에 잡히는 미니 프로젝트를 한 줄(50자 이내)로 제시한다. citations는 "
    "근거로 쓴 출처를 0개에서 3개 사이 짧은 라벨로 담되(예: '공고 512건', 문서 제목), "
    "근거가 없으면 빈 배열로 둔다. 모든 텍스트는 자연스러운 한국어 평서체로 쓰고, "
    "상투적인 표현이나 과장, 이모지, 줄표, 화살표를 쓰지 않는다."
)

_NODE_TYPE_LABEL = {
    "skill": "기술",
    "concept": "개념",
    "cert": "자격증",
}


def _build_prompt(request: RoadmapNodeContentRequest, demand_count: int | None) -> str:
    payload = {
        "node_label": request.node_label,
        "node_type": request.node_type,
        "section": request.section,
        "goal_company": request.goal_company,
        "goal_title": request.goal_title,
        "posting_demand_count": demand_count,
    }
    return (
        "다음은 학습 로드맵의 한 항목이다(JSON). 이 정보만 사용해 학습 콘텐츠를 만들어라. "
        "posting_demand_count가 숫자로 주어지면 최근 3년 국내외 공고 중 이 기술을 요구한 "
        "공고 수이니 why/citations에 그대로 활용하고, null이면 근거 수치 없이 일반적으로 "
        "설명해라.\n" + json.dumps(payload, ensure_ascii=False)
    )


def _find_skill(session: Session, node_label: str) -> Skill | None:
    """node_label을 taxonomy의 canonical 또는 alias와 대소문자 무시하고 매칭한다."""
    skill = session.execute(
        select(Skill).where(
            func.lower(Skill.canonical) == node_label.lower(),
            Skill.is_deleted.is_(False),
        )
    ).scalar_one_or_none()
    if skill is not None:
        return skill

    return session.execute(
        select(Skill)
        .join(SkillAlias, SkillAlias.skill_id == Skill.id)
        .where(
            func.lower(SkillAlias.alias) == node_label.lower(),
            SkillAlias.is_deleted.is_(False),
            Skill.is_deleted.is_(False),
        )
    ).scalar_one_or_none()


def _count_postings_requiring_skill(session: Session, skill_id: int) -> int:
    """최근 3년(market_pool_cutoff_date) 이내 게시된 공고 중 이 기술을 요구한 건수.
    국내/해외 pool을 굳이 나누지 않는다 — 여기서는 비율이 아니라 근거로 인용할 절대
    건수 하나만 필요하기 때문이다."""
    cutoff = market_pool_cutoff_date()
    count = session.scalar(
        select(func.count(distinct(PostingTech.posting_id)))
        .join(Posting, Posting.id == PostingTech.posting_id)
        .where(
            PostingTech.skill_id == skill_id,
            PostingTech.is_deleted.is_(False),
            Posting.is_deleted.is_(False),
            Posting.post_date.is_(None) | (Posting.post_date >= cutoff),
        )
    )
    return count or 0


def _demand_count(session: Session, request: RoadmapNodeContentRequest) -> int | None:
    """skill 타입 노드만 taxonomy에서 찾아 공고 요구 건수를 센다. concept/cert는
    공고 기술 스택 태그와 직접 대응되지 않아 None을 돌려주고, 폴백은 일반 설명으로 채운다."""
    if request.node_type != "skill":
        return None
    skill = _find_skill(session, request.node_label)
    if skill is None:
        return None
    return _count_postings_requiring_skill(session, skill.id)


def _fallback_resources(node_type: str, node_label: str) -> list[dict]:
    if node_type == "skill":
        return [
            {"label": f"{node_label} 공식 문서", "kind": "doc"},
            {"label": f"{node_label} 입문 튜토리얼", "kind": "guide"},
        ]
    if node_type == "cert":
        return [
            {"label": f"{node_label} 시험 안내", "kind": "doc"},
            {"label": f"{node_label} 기출문제 풀이", "kind": "guide"},
        ]
    return [
        {"label": f"{node_label} 개념 정리 글", "kind": "doc"},
        {"label": f"{node_label} 설명 영상", "kind": "video"},
    ]


def _fallback_project(node_type: str, node_label: str) -> str:
    if node_type == "skill":
        return f"{node_label}을 활용한 작은 기능을 직접 만들어 이력서에 담아보세요."
    if node_type == "cert":
        return f"{node_label} 시험 일정을 잡고 학습 계획부터 세워보세요."
    return f"{node_label} 개념을 적용한 예제 코드를 작성하고 정리해보세요."


def _fallback_why(request: RoadmapNodeContentRequest, demand_count: int | None) -> str:
    type_label = _NODE_TYPE_LABEL[request.node_type]
    target = request.goal_title or request.goal_company or "목표 직무"
    if demand_count:
        return f"최근 공고 {demand_count}건이 이 {type_label}을 요구해요. {target} 준비에 도움이 돼요."
    return f"{request.section} 영역에서 {target} 준비에 도움이 되는 {type_label}이에요."


def _fallback_summary(request: RoadmapNodeContentRequest) -> str:
    type_label = _NODE_TYPE_LABEL[request.node_type]
    return f"{request.node_label}은 {request.section} 영역의 {type_label}이에요. 핵심 개념부터 차근차근 익히면 실무에 바로 이어져요."


def _fallback_citations(demand_count: int | None) -> list[str]:
    if demand_count:
        return [f"공고 {demand_count}건"]
    return []


def _fallback_response(
    request: RoadmapNodeContentRequest, demand_count: int | None
) -> RoadmapNodeContentResponse:
    """LLM 미가용/실패 시 node_label/node_type/section만으로 구성하는 결정적 폴백."""
    return RoadmapNodeContentResponse.model_validate(
        {
            "why": _fallback_why(request, demand_count),
            "summary": _fallback_summary(request),
            "resources": _fallback_resources(request.node_type, request.node_label),
            "project": _fallback_project(request.node_type, request.node_label),
            "citations": _fallback_citations(demand_count),
        }
    )


# 노드 아이디 + 요청 맥락(목표 기업/직무 등)을 키로 하는 얇은 인메모리 캐시. 데모 중
# 같은 노드를 다시 클릭했을 때 LLM을 또 부르지 않기 위함이다. 프로세스 재시작 시
# 비워지는 것으로 충분해 Redis까지는 쓰지 않는다.
_CACHE: dict[tuple, RoadmapNodeContentResponse] = {}


def _cache_key(request: RoadmapNodeContentRequest) -> tuple:
    return (
        request.node_id,
        request.node_label,
        request.node_type,
        request.section,
        request.goal_company,
        request.goal_title,
    )


def build_roadmap_node_content(
    request: RoadmapNodeContentRequest,
    llm: LLMClient,
    session: Session,
) -> RoadmapNodeContentResponse:
    """LLM으로 노드 학습 콘텐츠를 만들되, 어떤 실패든 결정적 폴백으로 흡수해 항상 200을 낸다."""
    cache_key = _cache_key(request)
    cached = _CACHE.get(cache_key)
    if cached is not None:
        return cached

    try:
        demand_count = _demand_count(session, request)
    except Exception as exc:  # noqa: BLE001 — 통계 조회 실패도 절대 요청을 깨면 안 된다
        logger.warning("roadmap node content demand count failed: %s", exc)
        demand_count = None

    fallback = _fallback_response(request, demand_count)

    try:
        raw = llm.json(
            ROADMAP_NODE_CONTENT_SYSTEM,
            _build_prompt(request, demand_count),
            temperature=0.3,
        )
    except Exception as exc:  # noqa: BLE001 — 데모 안정성: LLM 호출은 절대 요청을 깨면 안 된다
        logger.warning("roadmap node content llm call failed: %s", exc)
        raw = None

    if not raw:
        _CACHE[cache_key] = fallback
        return fallback

    try:
        result = RoadmapNodeContentResponse.model_validate(raw)
    except Exception as exc:  # noqa: BLE001 — 스키마 불일치도 폴백으로 흡수한다
        logger.warning("roadmap node content llm response failed validation: %s", exc)
        _CACHE[cache_key] = fallback
        return fallback

    _CACHE[cache_key] = result
    return result
