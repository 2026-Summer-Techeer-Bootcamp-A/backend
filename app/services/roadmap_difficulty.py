"""학습 로드맵 노드 난이도 보정 — 우리 주관(선행 깊이)이 아니라 시장 실데이터에
앵커링해 난이도를 매긴다.

데모 안정성이 최우선이다. LLM 호출 실패, 타임아웃, 파싱 실패, 스키마 불일치, DB 조회
실패 어느 경우에도 예외를 던지지 않고 avg_career/demand/prereq_depth로 정한 결정적
티어와 템플릿 근거 문장으로 구성한 폴백을 돌려준다. LLM은 이 결정적 티어를 뒤집을 수
없다 — 근거 문장(basis)만 자연스럽게 다듬는 역할이다.
"""

from __future__ import annotations

import json
import logging

from pydantic import BaseModel, Field
from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from app.models.concept import Concept
from app.models.posting import Posting, PostingConcept, PostingTech
from app.models.skill import Skill, SkillAlias
from app.schemas.roadmap_difficulty import (
    DifficultyTier,
    RoadmapDifficultyItemOut,
    RoadmapDifficultyNodeIn,
    RoadmapDifficultyRequest,
    RoadmapDifficultyResponse,
)
from app.services.match import market_pool_cutoff_date
from app.services.rag.llm import LLMClient

logger = logging.getLogger(__name__)

# 임계값 근거: 국내 채용 공고가 흔히 쓰는 경력 구간 표기(신입, 1~3년, 3~5년, 5년 이상)를
# 참고해 신입/주니어 경계를 1년, 주니어/미드 경계를 2년, 미드/시니어 경계를 4년으로
# 잡는다. avg_career만으로는 "선행 개념이 전혀 없는 입문"과 "경력 요구는 낮지만 선행
# 개념이 있는 항목"을 구분할 수 없어 입문 판정에는 prereq_depth == 0도 함께 요구한다.
_ENTRY_MAX_CAREER = 1.0
_BEGINNER_MAX_CAREER = 2.0
_INTERMEDIATE_MAX_CAREER = 4.0

_NODE_TYPE_LABEL = {
    "skill": "기술",
    "concept": "개념",
    "cert": "자격증",
}

ROADMAP_DIFFICULTY_SYSTEM = (
    "너는 학습 로드맵 난이도를 시장 데이터에 근거해 설명하는 편집자다. 입력으로 각 "
    "노드의 avg_career(공고 평균 요구 경력, 년 단위), demand(수요 공고 수), "
    "prereq_depth(선행 개념 깊이), tier(이미 결정된 난이도)가 JSON 배열로 주어진다. "
    "tier는 절대 바꾸지 않고 입력값 그대로 복사한다. 각 노드마다 담백한 근거 문장 "
    "하나(basis)만 새로 만든다. 반드시 아래 JSON 스키마 하나만 출력하고 다른 설명이나 "
    "마크다운, 코드블록을 덧붙이지 않는다.\n"
    '{"items": [{"node_id": string, "tier": string, "basis": string}]}\n'
    "규칙: basis는 40자에서 90자 사이 한국어 평서체 한 문장으로, avg_career와 demand가 "
    "있으면 그 수치를 그대로 인용한다. avg_career가 null이면 수치 없이 prereq_depth "
    "기준으로 설명한다. 상투적인 표현이나 과장, 이모지, 줄표, 화살표를 쓰지 않는다."
)


class _LLMDifficultyItem(BaseModel):
    node_id: str
    tier: str
    basis: str


class _LLMDifficultyResponse(BaseModel):
    items: list[_LLMDifficultyItem] = Field(default_factory=list)


def _find_skill(session: Session, label: str) -> Skill | None:
    """label을 taxonomy의 canonical 또는 alias와 대소문자 무시하고 매칭한다."""
    skill = session.execute(
        select(Skill).where(
            func.lower(Skill.canonical) == label.lower(),
            Skill.is_deleted.is_(False),
        )
    ).scalar_one_or_none()
    if skill is not None:
        return skill

    return session.execute(
        select(Skill)
        .join(SkillAlias, SkillAlias.skill_id == Skill.id)
        .where(
            func.lower(SkillAlias.alias) == label.lower(),
            SkillAlias.is_deleted.is_(False),
            Skill.is_deleted.is_(False),
        )
    ).scalar_one_or_none()


def _find_concept(session: Session, label: str) -> Concept | None:
    """label을 개념 사전의 name과 대소문자 무시하고 매칭한다. 개념은 별칭 테이블 없이
    마트 적재 시 이미 정규명으로 해소되어 있다(app/models/concept.py 참고)."""
    return session.execute(
        select(Concept).where(
            func.lower(Concept.name) == label.lower(),
            Concept.is_deleted.is_(False),
        )
    ).scalar_one_or_none()


def _skill_demand_and_avg_career(session: Session, skill_id: int) -> tuple[int, float | None]:
    """최근 3년(market_pool_cutoff_date) 이내 이 기술을 요구한 공고 수(demand)와 그 중
    career_min이 있는 공고들의 평균 경력(avg_career). posting_tech는 (posting_id,
    skill_id) unique라 공고당 행이 하나뿐이지만, roadmap_node_content.py와 동일하게
    distinct로 명시해 의도를 드러낸다."""
    cutoff = market_pool_cutoff_date()
    demand, avg_career = session.execute(
        select(
            func.count(distinct(PostingTech.posting_id)),
            func.avg(Posting.career_min),
        )
        .select_from(PostingTech)
        .join(Posting, Posting.id == PostingTech.posting_id)
        .where(
            PostingTech.skill_id == skill_id,
            PostingTech.is_deleted.is_(False),
            Posting.is_deleted.is_(False),
            Posting.post_date.is_(None) | (Posting.post_date >= cutoff),
        )
    ).one()
    return demand or 0, float(avg_career) if avg_career is not None else None


def _concept_demand_and_avg_career(session: Session, concept_id: int) -> tuple[int, float | None]:
    """skill과 동일한 계산을 posting_concept 기준으로 수행한다."""
    cutoff = market_pool_cutoff_date()
    demand, avg_career = session.execute(
        select(
            func.count(distinct(PostingConcept.posting_id)),
            func.avg(Posting.career_min),
        )
        .select_from(PostingConcept)
        .join(Posting, Posting.id == PostingConcept.posting_id)
        .where(
            PostingConcept.concept_id == concept_id,
            PostingConcept.is_deleted.is_(False),
            Posting.is_deleted.is_(False),
            Posting.post_date.is_(None) | (Posting.post_date >= cutoff),
        )
    ).one()
    return demand or 0, float(avg_career) if avg_career is not None else None


def _demand_and_avg_career(
    session: Session, node: RoadmapDifficultyNodeIn
) -> tuple[int, float | None]:
    """노드 타입별로 시장 신호(demand, avg_career)를 찾는다. skill은 posting_tech,
    concept은 posting_concept으로 매칭한다. cert는 공고 자격증 태그가 요구 사항이라기보다
    부가 우대조건으로 쓰이는 경우가 많아 이 객관 신호의 대상에서 제외하고, 항상
    prereq_depth 기반 결정적 폴백으로 처리한다. taxonomy에서 라벨을 찾지 못해도
    데이터 부족으로 보고 (0, None)을 돌려줄 뿐 예외를 던지지 않는다."""
    if node.type == "skill":
        skill = _find_skill(session, node.label)
        if skill is None:
            return 0, None
        return _skill_demand_and_avg_career(session, skill.id)
    if node.type == "concept":
        concept = _find_concept(session, node.label)
        if concept is None:
            return 0, None
        return _concept_demand_and_avg_career(session, concept.id)
    return 0, None


def _compute_deterministic_tier(avg_career: float | None, prereq_depth: int) -> DifficultyTier:
    """avg_career가 있으면 그것을 우선 근거로, 없으면(데이터 부족) prereq_depth로만
    폴백한다. 임계값 근거는 모듈 상단 주석 참고."""
    if avg_career is not None:
        if avg_career < _ENTRY_MAX_CAREER and prereq_depth == 0:
            return "입문"
        if avg_career < _BEGINNER_MAX_CAREER:
            return "초급"
        if avg_career < _INTERMEDIATE_MAX_CAREER:
            return "중급"
        return "고급"

    if prereq_depth <= 0:
        return "입문"
    if prereq_depth == 1:
        return "초급"
    if prereq_depth == 2:
        return "중급"
    return "고급"


def _fallback_basis(avg_career: float | None, demand: int, prereq_depth: int) -> str:
    """LLM 미가용/실패 시 채우는 결정적 템플릿 문장."""
    if avg_career is not None:
        return f"공고 평균 요구 경력 {avg_career:.1f}년, 수요 {demand:,}건."
    return f"시장 수요 데이터가 부족해 선행 개념 깊이 {prereq_depth}단계를 기준으로 판단한다."


def _build_prompt(
    signals: dict[str, tuple[RoadmapDifficultyNodeIn, float | None, int, DifficultyTier]],
) -> str:
    payload = [
        {
            "node_id": node_id,
            "label": node.label,
            "type": node.type,
            "prereq_depth": node.prereq_depth,
            "avg_career": avg_career,
            "demand": demand,
            "tier": tier,
        }
        for node_id, (node, avg_career, demand, tier) in signals.items()
    ]
    return (
        "다음은 학습 로드맵 노드별 시장 데이터다(JSON 배열). 각 노드의 tier는 이미 "
        "결정되어 있으니 그대로 두고, avg_career/demand/prereq_depth를 근거로 삼아 "
        "간결한 basis 문장만 만들어라.\n" + json.dumps(payload, ensure_ascii=False)
    )


# 노드 시그니처(node_id + label + type + prereq_depth)를 키로 하는 얇은 인메모리
# 캐시. 같은 로드맵을 다시 요청했을 때 DB 조회와 LLM 호출을 반복하지 않기 위함이다.
# 프로세스 재시작 시 비워지는 것으로 충분해 Redis까지는 쓰지 않는다.
_CACHE: dict[tuple, RoadmapDifficultyItemOut] = {}


def _cache_key(node: RoadmapDifficultyNodeIn) -> tuple:
    return (node.node_id, node.label, node.type, node.prereq_depth)


def build_roadmap_difficulty(
    request: RoadmapDifficultyRequest,
    llm: LLMClient,
    session: Session,
) -> RoadmapDifficultyResponse:
    """배치로 들어온 노드마다 시장 신호를 조회해 결정적 티어를 정하고, LLM으로 근거
    문장만 자연스럽게 다듬는다. 어떤 실패든 결정적 폴백으로 흡수해 항상 200을 낸다."""
    resolved: dict[str, RoadmapDifficultyItemOut] = {}
    signals: dict[str, tuple[RoadmapDifficultyNodeIn, float | None, int, DifficultyTier]] = {}

    for node in request.nodes:
        cached = _CACHE.get(_cache_key(node))
        if cached is not None:
            resolved[node.node_id] = cached
            continue

        try:
            demand, avg_career = _demand_and_avg_career(session, node)
        except Exception as exc:  # noqa: BLE001 — 신호 조회 실패도 절대 요청을 깨면 안 된다
            logger.warning("roadmap difficulty signal query failed for %s: %s", node.node_id, exc)
            demand, avg_career = 0, None

        tier = _compute_deterministic_tier(avg_career, node.prereq_depth)
        signals[node.node_id] = (node, avg_career, demand, tier)

    fallback_items = {
        node_id: RoadmapDifficultyItemOut(
            node_id=node_id,
            tier=tier,
            avg_career=avg_career,
            demand=demand,
            basis=_fallback_basis(avg_career, demand, node.prereq_depth),
        )
        for node_id, (node, avg_career, demand, tier) in signals.items()
    }
    resolved.update(fallback_items)

    if signals:
        raw = None
        try:
            raw = llm.json(
                ROADMAP_DIFFICULTY_SYSTEM,
                _build_prompt(signals),
                temperature=0.2,
            )
        except Exception as exc:  # noqa: BLE001 — 데모 안정성: LLM 호출은 절대 요청을 깨면 안 된다
            logger.warning("roadmap difficulty llm call failed: %s", exc)

        if raw:
            try:
                parsed = _LLMDifficultyResponse.model_validate(raw)
            except Exception as exc:  # noqa: BLE001 — 스키마 불일치도 폴백으로 흡수한다
                logger.warning("roadmap difficulty llm response failed validation: %s", exc)
                parsed = None

            if parsed is not None:
                for llm_item in parsed.items:
                    signal = signals.get(llm_item.node_id)
                    if signal is None:
                        continue
                    _, avg_career, demand, tier = signal
                    basis = llm_item.basis.strip()
                    # tier는 시장 데이터로 이미 결정된 값이라 LLM이 뒤집으면(오류거나
                    # 임의 변경이거나) 신뢰하지 않고 해당 노드만 폴백을 유지한다.
                    if llm_item.tier != tier or not basis:
                        continue
                    resolved[llm_item.node_id] = RoadmapDifficultyItemOut(
                        node_id=llm_item.node_id,
                        tier=tier,
                        avg_career=avg_career,
                        demand=demand,
                        basis=basis,
                    )

    for node in request.nodes:
        item = resolved.get(node.node_id)
        if item is not None:
            _CACHE[_cache_key(node)] = item

    return RoadmapDifficultyResponse(items=[resolved[node.node_id] for node in request.nodes])
