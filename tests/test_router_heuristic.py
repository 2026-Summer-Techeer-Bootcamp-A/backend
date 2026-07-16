"""router._heuristic 인텐트 분류 회귀 테스트.

K2에서 발견된 버그: 이력서를 첨부한 채 시장 관련 일반 질문("React 수요 어때?")을 하면
pipeline._dispatch가 텍스트 인텐트와 무관하게 resume_market으로 가로챘다. 수정 후에는
resume_market이 router에서 텍스트 신호로만(=사용자가 실제로 "내 이력서를 시장과
비교/분석해줘" 류를 물었을 때만) 명시적으로 분류되는 인텐트가 됐다 — 첨부 여부는
router._heuristic 판단에 아무 영향을 주지 않는다(pipeline._dispatch에서 intent가
"resume_market"일 때만 첨부를 컨텍스트로 쓴다). 이 테스트는 router 레이어에서 그
텍스트 분류 자체가 올바른지만 검증한다.

_detect_skill/_detect_skills_multi는 raw SQL에 리터럴 ILIKE를 써서(SQLAlchemy의
.ilike()와 달리 sqlite 방언으로 컴파일되지 않는다) 실 Postgres에서만 동작한다 — 이
인텐트 분류 테스트는 그 기술명 탐지 자체를 검증 대상으로 삼지 않으므로, DB를 안 타게
스텁으로 바꿔 fast tier(sqlite 없이도 도는 순수 유닛 테스트)로 유지한다.
"""

import pytest

from app.services.rag import router as router_module


@pytest.fixture(autouse=True)
def _stub_db_skill_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    """_heuristic의 순수 키워드 로직만 검증하기 위해 DB 의존 헬퍼를 스텁으로 대체한다."""
    monkeypatch.setattr(router_module, "_detect_skill", lambda session, q: None)
    monkeypatch.setattr(router_module, "_detect_skills_multi", lambda session, q: [])


def test_generic_market_question_is_not_resume_market() -> None:
    # 이력서가 첨부돼 있든 없든, router 레이어는 첨부 여부를 아예 모른다 — 텍스트만 보고
    # 판단한다. "React 수요 어때?"는 이력서 언급이 전혀 없으므로 resume_market이면 안 된다.
    plan = router_module._heuristic(None, "React 수요 어때?", None)
    assert plan.intent != "resume_market"


def test_explicit_market_fit_question_is_resume_market() -> None:
    plan = router_module._heuristic(None, "내 이력서 시장 적합도 어때?", None)
    assert plan.intent == "resume_market"
    assert plan.tools == ["resume"]


def test_frontend_default_attachment_phrase_is_resume_market() -> None:
    # frontend/src/rag/useAttachments.ts가 이력서만 첨부됐을 때 기본으로 주입하는 문구.
    plan = router_module._heuristic(None, "내 이력서를 시장과 비교해줘", None)
    assert plan.intent == "resume_market"


def test_my_competitiveness_standalone_is_resume_market() -> None:
    plan = router_module._heuristic(None, "내 경쟁력 어느 정도야?", None)
    assert plan.intent == "resume_market"


def test_gap_question_is_still_resume_gap() -> None:
    plan = router_module._heuristic(None, "내 이력서 기준 부족한 스킬 뭐야?", None)
    assert plan.intent == "resume_gap"


def test_coverage_question_is_still_resume_coverage() -> None:
    plan = router_module._heuristic(None, "내 이력서로 지원 가능한 공고 얼마나 돼?", None)
    assert plan.intent == "resume_coverage"


def test_ambiguous_resume_mention_still_defaults_to_coverage() -> None:
    # 시장/분석/갭/커버리지 키워드가 전혀 없는 포괄적인 이력서 언급은 기존처럼 coverage로
    # 기본 처리한다(회귀 방지).
    plan = router_module._heuristic(None, "내 이력서 봐줘", None)
    assert plan.intent == "resume_coverage"
