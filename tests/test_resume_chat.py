"""K1 — 이력서 첨부 RAG 챗(resume_gap/resume_coverage) 테스트.

기존 매치 엔진(app/services/match.py)을 그대로 재사용하는지, 이력서 미첨부 시
조기 안내로 빠지는지, "skill_ranking" 오분류 버그가 고쳐졌는지를 확인한다.
GEMINI_API_KEY가 없는 테스트 환경에서는 app.services.rag.llm.get_llm()이 NullClient를
반환해 router.plan()/synthesis.synthesize()가 항상 결정론적 휴리스틱/템플릿 경로를 타므로,
대부분의 테스트는 네트워크 없이도 안정적으로 재현된다.

단, router._heuristic()이 내부적으로 쓰는 _detect_skill/_detect_skills_multi는 raw SQL에
ILIKE를 쓴다(Postgres 전용 — sqlite에는 없는 연산자). router.plan()을 거치는 테스트(휴리스틱
직접 호출, run_chat_events, /chat 엔드포인트)는 그래서 @pytest.mark.integration으로 표시해
DATABASE_URL이 있는 환경에서만 돈다 — resume_tool/dispatch 단위 테스트는 ILIKE 경로를
타지 않으므로 fast tier(sqlite)에서 그대로 돈다.
"""

from collections.abc import Iterator
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.db import Base, get_session
from app.core.security import create_access_token
from app.main import app
from app.models import Posting, PostingTech, Resume, ResumeSkill, Skill, User
from app.services.rag import pipeline as rag_pipeline
from app.services.rag import router as rag_router
from app.services.rag.schemas import Plan
from app.services.rag.tools import resume_tool



@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)
    with testing_session() as s:
        python = Skill(canonical="Python", category="language")
        react = Skill(canonical="React", category="frontend")
        s.add_all([python, react])
        s.flush()

        # p1: python+react 요구, p2: python만 요구 -> python 보유 시 커버리지 100%, react가 갭.
        # region_district는 resume_recommend의 지역 필터 테스트용 — p1은 강남, p2는 부산.
        p1 = Posting(
            source="t", source_uid="1", pool="domestic", title="백엔드", company="회사A",
            post_date=date(2026, 1, 1), region_district="강남구",
        )
        p2 = Posting(
            source="t", source_uid="2", pool="domestic", title="백엔드2", company="회사B",
            post_date=date(2026, 1, 2), region_district="부산",
        )
        s.add_all([p1, p2])
        s.flush()
        s.add_all(
            [
                PostingTech(posting_id=p1.id, skill_id=python.id),
                PostingTech(posting_id=p1.id, skill_id=react.id),
                PostingTech(posting_id=p2.id, skill_id=python.id),
            ]
        )
        s.commit()
        s.info["python_id"] = python.id
        s.info["react_id"] = react.id
        yield s
    engine.dispose()


@pytest.fixture(autouse=True)
def _patch_skill_detection(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """router._detect_skill/_detect_skills_multi는 raw SQL ILIKE(Postgres 전용)를 쓴다.

    이 테스트들은 sqlite 픽스처로 돌아 ILIKE에서 문법 에러가 났다. 시드된 skill 목록을
    파이썬 부분 문자열 매칭으로 원래 SQL과 같게 흉내 내는 스텁으로 갈아끼워, 이 파일이
    검증하려는 인텐트 분류와 파이프라인 흐름만 DB 종류와 무관하게 결정론적으로 확인한다."""
    canonicals = [c for (c,) in session.execute(select(Skill.canonical)).all()]

    def fake_detect(_s: Session, q: str) -> str | None:
        low = q.lower()
        cands = [c for c in canonicals if len(c) >= 2 and c.lower() in low]
        return max(cands, key=len) if cands else None

    def fake_detect_multi(_s: Session, q: str) -> list[str]:
        low = q.lower()
        cands = [c for c in canonicals if len(c) >= 2 and c.lower() in low]
        return sorted(set(cands), key=len, reverse=True)[:5]

    monkeypatch.setattr(rag_router, "_detect_skill", fake_detect)
    monkeypatch.setattr(rag_router, "_detect_skills_multi", fake_detect_multi)


# --- resume_tool 단위 테스트 ---------------------------------------------------


def test_resume_gap_returns_none_without_owned_skills(session: Session) -> None:
    assert resume_tool.resume_gap(session, set(), pool="domestic") is None
    assert resume_tool.resume_gap(session, None, pool="domestic") is None


def test_resume_coverage_returns_none_without_owned_skills(session: Session) -> None:
    assert resume_tool.resume_coverage(session, set(), pool="domestic") is None


def test_resume_gap_lists_missing_skill_not_owned_one(session: Session) -> None:
    owned = {session.info["python_id"]}
    result = resume_tool.resume_gap(session, owned, pool="domestic")
    assert result is not None
    assert result["tool"] == "resume"
    names = [it["name"] for it in result["tool_result"]["items"]]
    assert "React" in names
    assert "Python" not in names  # 이미 보유한 기술은 갭 목록에 없어야 한다
    assert result["n"] == 2  # sample_size = posting 2건
    assert result["citation"]["type"] == "resume"


def test_resume_gap_defaults_pool_to_domestic_when_missing(session: Session) -> None:
    """pool이 None으로 오면(RAG 챗은 pool 없이도 질문 가능) domestic으로 기본 처리한다."""
    owned = {session.info["python_id"]}
    result = resume_tool.resume_gap(session, owned, pool=None)
    assert result is not None
    assert result["n"] == 2


def test_resume_coverage_reports_score_and_matched_postings(session: Session) -> None:
    owned = {session.info["python_id"]}
    result = resume_tool.resume_coverage(session, owned, pool="domestic")
    assert result is not None
    items = {it["name"]: it["metric"] for it in result["tool_result"]["items"]}
    assert items["Python"] == "보유"
    assert items["React"] == "미보유"
    # python을 보유하면 p1,p2 둘 다 매칭되므로 지원 가능 공고 2건이 facts에 드러나야 한다.
    assert "2건" in result["facts"]


# --- router 휴리스틱: resume 인텐트 분류 + "얼마나" 오탐 회귀 테스트 -----------------


def test_heuristic_classifies_resume_gap(session: Session) -> None:
    plan = rag_router._heuristic(session, "내 이력서 기준 부족한 스킬 뭐야?", None)
    assert plan.intent == "resume_gap"


def test_heuristic_classifies_resume_coverage(session: Session) -> None:
    plan = rag_router._heuristic(session, "내 이력서로 지원 가능한 공고 얼마나 돼?", None)
    assert plan.intent == "resume_coverage"


def test_heuristic_ambiguous_resume_reference_defaults_to_market(session: Session) -> None:
    # "내 이력서 어때?"처럼 본인 이력서를 두루뭉술하게 묻는 질문은 부족 스킬만도,
    # 커버리지만도 아닌 종합 분석에 가까우므로 resume_market(레이더+갭+커버리지)으로 답한다.
    plan = rag_router._heuristic(session, "내 이력서 어때?", None)
    assert plan.intent == "resume_market"


def test_heuristic_does_not_misfire_on_generic_question_with_common_word(session: Session) -> None:
    """"얼마나"는 매우 흔한 단어라 "내 이력서" 같은 본인 지칭 없이 단독으로 오면
    resume_coverage로 오분류되면 안 된다(일반 skill_demand 질문으로 남아야 한다)."""
    plan = rag_router._heuristic(session, "React를 요구하는 공고가 얼마나 있어?", None)
    assert plan.intent not in ("resume_gap", "resume_coverage")
    assert plan.intent == "skill_demand"


# --- pipeline._dispatch: skill_ranking 오분류(대체됨 오판) 버그 수정 확인 --------------


def test_dispatch_skill_ranking_no_longer_marked_as_fallback(session: Session) -> None:
    plan = Plan(intent="skill_ranking", tools=["sql"], pool="domestic", entities={}, subqueries=["아무 기술이나 많이 쓰나요"])
    tool_outputs, fell_back = rag_pipeline._dispatch(session, plan)
    assert tool_outputs  # top_skills 결과가 실제로 채워졌는지
    assert fell_back is False  # 이전 버그: 전용 분기가 없어 True로 오판됐었다


def test_dispatch_resume_gap_uses_resume_tool(session: Session) -> None:
    owned = {session.info["python_id"]}
    plan = Plan(intent="resume_gap", tools=["resume"], pool="domestic", entities={}, subqueries=["q"])
    tool_outputs, fell_back = rag_pipeline._dispatch(session, plan, owned_skill_ids=owned)
    assert len(tool_outputs) == 1
    assert tool_outputs[0]["tool"] == "resume"
    assert fell_back is False


# --- pipeline.run_chat_events: 이력서 미첨부 조기 안내 -------------------------------


def test_run_chat_events_early_returns_when_resume_missing(session: Session) -> None:
    events = list(
        rag_pipeline.run_chat_events(
            session, "내 이력서 기준 부족한 스킬 뭐야?", pool="domestic", owned_skill_ids=None
        )
    )
    kinds = [e["type"] for e in events]
    assert kinds == ["plan", "final"]  # tool/eval/synth 단계 없이 바로 종료돼야 한다
    final = events[-1]
    assert "이력서를 먼저 첨부" in final["answer"]
    assert final["confidence"]["level"] == 0
    assert final["degraded"] is True


def test_run_chat_events_answers_when_resume_attached(session: Session) -> None:
    owned = {session.info["python_id"]}
    events = list(
        rag_pipeline.run_chat_events(
            session, "내 이력서 기준 부족한 스킬 뭐야?", pool="domestic", owned_skill_ids=owned
        )
    )
    kinds = [e["type"] for e in events]
    assert "result" in kinds
    assert kinds[-1] == "final"
    final = events[-1]
    assert "이력서를 먼저 첨부" not in final["answer"]


def test_run_chat_events_resume_text_with_single_posting_bypasses_missing_resume_guard(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """세션 범위 이력서 원문(resume_text)과 공고 1개가 함께 오면, 저장된 이력서가 없어
    owned_skill_ids가 비어 있어도 미첨부 가드가 조기 종료하면 안 된다 — _dispatch의
    resume_posting_llm_compare 분기(첨부 우선 설계)가 원문을 직접 읽고 판정하기
    때문이다. 회귀 대상: 이 조합에서 가드가 무조건 "이력서를 먼저 첨부해 주세요"로
    조기 종료해 실제 비교 도구가 아예 실행되지 못했던 버그.

    compare_tool.resume_posting_llm_compare는 실제 Gemini 호출이 필요해 이 단위
    테스트에서는 _dispatch를 스텁으로 갈아끼우고, 가드를 통과해 _dispatch가 정확한
    인자(resume_text/posting_ids/owned_skill_ids)로 호출되는지만 확인한다."""
    posting_id = session.info["python_id"]  # 유효한 id이기만 하면 되므로 스킬 id를 재사용
    calls: list[dict] = []

    def fake_dispatch(sess: Session, plan: Plan, **kwargs: object) -> tuple[list[dict], bool]:
        calls.append(kwargs)
        return (
            [
                {
                    "tool": "compare",
                    "tool_result": {
                        "kind": "resume_posting_llm",
                        "label": "이력서 대비 공고 요구사항(LLM 판정)",
                        "items": [],
                        "compare": {
                            "posting_title": "백엔드",
                            "score": 50.0,
                            "counts": {"met": 1, "partial": 0, "gap": 1},
                            "summary": "요약",
                            "requirements": [],
                            "degraded": False,
                        },
                    },
                    "citation": {
                        "type": "compare",
                        "ref": "이력서 vs 백엔드",
                        "label": "이력서 원문 대비 공고 요구사항 LLM 판정",
                    },
                    "n": 2,
                    "facts": "met 1건, gap 1건",
                }
            ],
            False,
        )

    monkeypatch.setattr(rag_pipeline, "_dispatch", fake_dispatch)

    events = list(
        rag_pipeline.run_chat_events(
            session,
            "내 이력서로 지원 가능한 공고 얼마나 돼?",  # 휴리스틱상 resume_coverage로 분류됨
            pool="domestic",
            owned_skill_ids=None,
            posting_ids=[posting_id],
            resume_text="지원 직무: 백엔드 개발자, 보유 기술: Python, FastAPI",
        )
    )

    assert len(calls) == 1  # 가드에서 조기 종료됐다면 _dispatch는 호출조차 안 된다
    assert calls[0]["resume_text"] == "지원 직무: 백엔드 개발자, 보유 기술: Python, FastAPI"
    assert calls[0]["posting_ids"] == [posting_id]
    assert not calls[0]["owned_skill_ids"]  # None(저장된 이력서 미첨부) 그대로 전달돼야 한다

    kinds = [e["type"] for e in events]
    assert "result" in kinds  # 조기 종료(plan, final만)가 아니라 도구 결과가 있어야 한다
    final = events[-1]
    assert final["type"] == "final"
    assert "이력서를 먼저 첨부" not in final["answer"]


def test_run_chat_events_attachment_driven_compare_route_change_is_not_fell_back(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """계획 라우트는 resume_coverage(도구=resume)인데, 공고를 첨부해 _dispatch가 compare
    도구로 정당하게 갈아탈 때(K2 첨부 우선 설계) route 불일치를 대체(fell_back)로
    오판하면 안 된다. 고치기 전에는 route != collect['route']("compare" != "resume")만
    보고 fell_back=True로 오판해 degraded=True와 "질문이 겨냥한 대상을 찾지 못해 일반
    기술 랭킹으로 대체됐어요" 사유가 붙었다 — 실제로는 첨부가 의도한 그대로 답한
    경우인데도 대체된 것처럼 보이는 오탐이었다.

    plan()/get_llm() 둘 다 스텁으로 갈아끼워, 이 테스트가 검증하려는 route/fell_back
    결합 로직만 다른 잡음(휴리스틱 폴백, LLM 합성 미가용 등) 없이 확인한다."""
    posting_id = session.info["python_id"]  # 유효한 id이기만 하면 되므로 스킬 id를 재사용

    def fake_plan(sess: Session, llm: object, question: str, pool: str | None) -> tuple[Plan, bool]:
        return (
            Plan(
                intent="resume_coverage",
                tools=["resume"],
                pool="domestic",
                entities={},
                subqueries=[question],
            ),
            False,  # plan_degraded=False — 계획 단계 자체는 성공했다고 가정
        )

    def fake_dispatch(sess: Session, plan: Plan, **kwargs: object) -> tuple[list[dict], bool]:
        return (
            [
                {
                    "tool": "compare",
                    "tool_result": {
                        "kind": "resume_posting_llm",
                        "label": "이력서 대비 공고 요구사항(LLM 판정)",
                        "items": [],
                        "compare": {
                            "posting_title": "백엔드",
                            "score": 100.0,
                            "counts": {"met": 2, "partial": 0, "gap": 0},
                            "summary": "요약",
                            "requirements": [],
                            "degraded": False,
                        },
                    },
                    "citation": {
                        "type": "compare",
                        "ref": "이력서 vs 백엔드",
                        "label": "이력서 원문 대비 공고 요구사항 LLM 판정",
                    },
                    "n": 2,
                    "facts": "met 2건, gap 0건",
                }
            ],
            False,  # _dispatch 자체는 대상을 정확히 찾았으므로 fell_back=False
        )

    class _FakeSynthLLM:
        """synthesize()가 실제 답을 만들도록(=synth_degraded=False) text()가 값을 낸다.
        json()은 이 테스트에서 쓰이지 않지만(plan을 스텁으로 우회) 인터페이스상 필요하다."""

        def __init__(self) -> None:
            self.last_debug: dict | None = None
            self.call_count = 0

        def json(self, *a: object, **k: object) -> dict | None:
            return None

        def text(self, *a: object, **k: object) -> str:
            return "이 공고는 이력서 기준 met 2건으로 잘 맞아요."

    monkeypatch.setattr(rag_pipeline, "make_plan", fake_plan)
    monkeypatch.setattr(rag_pipeline, "_dispatch", fake_dispatch)
    monkeypatch.setattr(rag_pipeline, "get_llm", lambda: _FakeSynthLLM())

    events = list(
        rag_pipeline.run_chat_events(
            session,
            "내 이력서로 지원 가능한 공고 얼마나 돼?",  # 휴리스틱상 resume_coverage로 분류됨
            pool="domestic",
            owned_skill_ids=None,
            posting_ids=[posting_id],
            resume_text="지원 직무: 백엔드 개발자, 보유 기술: Python, FastAPI",
        )
    )

    final = events[-1]
    assert final["type"] == "final"
    assert final["degraded"] is False
    assert "일반 기술 랭킹으로 대체" not in " ".join(final["degraded_reasons"])


def test_run_chat_events_non_resume_question_ignores_owned_skill_ids(session: Session) -> None:
    """일반 질문 경로는 owned_skill_ids를 완전히 무시해야 한다(byte-for-byte 동일 동작)."""
    without = list(rag_pipeline.run_chat_events(session, "요즘 뭐가 제일 인기야?", pool="domestic"))
    with_owned = list(
        rag_pipeline.run_chat_events(
            session, "요즘 뭐가 제일 인기야?", pool="domestic", owned_skill_ids={session.info["python_id"]}
        )
    )
    assert [e["type"] for e in without] == [e["type"] for e in with_owned]
    assert without[-1]["answer"] == with_owned[-1]["answer"]


# --- /chat 엔드포인트 통합 테스트(TestClient) ----------------------------------------


@pytest.fixture
def client(session: Session) -> Iterator[TestClient]:
    user = User(email="resume-chat@example.com", password_hash="unused")
    session.add(user)
    session.commit()
    resume = Resume(user_id=user.id, title="내 이력서", pool="domestic")
    session.add(resume)
    session.commit()
    session.add(ResumeSkill(resume_id=resume.resume_id, skill_id=session.info["python_id"]))
    session.commit()

    def override_get_session() -> Iterator[Session]:
        yield session

    app.dependency_overrides[get_session] = override_get_session
    test_client = TestClient(app)
    test_client.resume_id = resume.resume_id  # type: ignore[attr-defined]
    test_client.token = create_access_token(str(user.id))  # type: ignore[attr-defined]
    yield test_client
    app.dependency_overrides.clear()


def test_chat_endpoint_with_resume_id_requires_auth(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/chat",
        json={"question": "내 이력서 기준 부족한 스킬 뭐야?", "pool": "domestic", "resume_id": client.resume_id},
    )
    assert resp.status_code == 401


def test_chat_endpoint_with_resume_id_and_auth_answers_resume_grounded(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/chat",
        json={"question": "내 이력서 기준 부족한 스킬 뭐야?", "pool": "domestic", "resume_id": client.resume_id},
        headers={"Authorization": f"Bearer {client.token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "이력서를 먼저 첨부" not in body["answer"]
    assert body["route"] == "resume"


def test_chat_endpoint_without_resume_id_unaffected(client: TestClient) -> None:
    """resume_id를 아예 안 보내는 기존 호출 경로는 그대로 동작해야 한다."""
    resp = client.post(
        "/api/v1/chat",
        json={"question": "요즘 뭐가 제일 인기야?", "pool": "domestic"},
    )
    assert resp.status_code == 200


# --- K3: resume_recommend — router 인텐트 분류 ----------------------------------------


def test_heuristic_classifies_resume_recommend(session: Session) -> None:
    """"넣어볼만한"/"추천" + 이력서 지칭이 결합되면 resume_recommend로 분류돼야 한다 —
    이전에는 resume_coverage(통계)로 새어나가 구체적인 공고 목록을 못 돌려줬다."""
    plan = rag_router._heuristic(session, "이 이력서로 넣어볼만한 공고 추천해줘", None)
    assert plan.intent == "resume_recommend"


def test_heuristic_resume_recommend_extracts_region_entity(session: Session) -> None:
    plan = rag_router._heuristic(
        session, "이 이력서로 넣어볼만한 공고 추천해줘. 강남에 있는 곳이면 좋겠어", None
    )
    assert plan.intent == "resume_recommend"
    assert plan.entities.get("region") == "강남"


def test_heuristic_resume_recommend_without_region_has_no_region_entity(session: Session) -> None:
    plan = rag_router._heuristic(session, "내 스킬에 맞는 공고 추천해줘", None)
    assert plan.intent == "resume_recommend"
    assert "region" not in plan.entities


def test_heuristic_still_classifies_resume_coverage_without_recommend_action(
    session: Session,
) -> None:
    """resume_recommend 추가가 기존 resume_coverage 분류를 건드리지 않아야 한다(회귀)."""
    plan = rag_router._heuristic(session, "내 이력서로 지원 가능한 공고 얼마나 돼?", None)
    assert plan.intent == "resume_coverage"


# --- K3: resume_recommend tool — 스킬 겹침 랭킹 + 지역 필터 -----------------------------


def test_resume_recommend_returns_none_without_owned_skills(session: Session) -> None:
    assert resume_tool.resume_recommend(session, set(), pool="domestic") is None
    assert resume_tool.resume_recommend(session, None, pool="domestic") is None


def test_resume_recommend_ranks_by_skill_overlap(session: Session) -> None:
    """python+react 둘 다 보유하면 p1(overlap=2)이 p2(overlap=1)보다 먼저 나와야 한다."""
    owned = {session.info["python_id"], session.info["react_id"]}
    result = resume_tool.resume_recommend(session, owned, pool="domestic")
    assert result is not None
    assert result["tool"] == "resume"
    assert result["tool_result"]["kind"] == "posting_list"
    items = result["tool_result"]["items"]
    assert items[0]["name"] == "백엔드"  # p1: overlap 2건 -> 1위
    assert items[0]["pct"] == 100.0  # 2/2 * 100
    assert items[0]["company"] == "회사A"
    assert items[0]["id"] is not None
    assert items[1]["name"] == "백엔드2"  # p2: overlap 1건 -> 2위
    assert items[1]["pct"] == 50.0  # 1/2 * 100


def test_resume_recommend_includes_matched_missing_skills_and_region(session: Session) -> None:
    """python만 보유한 상태로 추천을 받으면, python+react를 요구하는 p1에서
    matched_skills=["Python"]/missing_skills=["React"]로 갈라져야 하고(카드 배지용),
    지역(region_district 우선)도 함께 실려야 한다."""
    owned = {session.info["python_id"]}
    result = resume_tool.resume_recommend(session, owned, pool="domestic")
    assert result is not None
    items = {it["name"]: it for it in result["tool_result"]["items"]}

    p1 = items["백엔드"]  # python+react 요구
    assert p1["matched_skills"] == ["Python"]
    assert p1["missing_skills"] == ["React"]
    assert p1["region"] == "강남구"

    p2 = items["백엔드2"]  # python만 요구 -> 부족 기술 없음
    assert p2["matched_skills"] == ["Python"]
    assert p2["missing_skills"] == []
    assert p2["region"] == "부산"


def test_resume_recommend_filters_by_region(session: Session) -> None:
    """강남으로 필터하면 region_district="강남구"인 p1만 나와야 한다(부분 문자열 ILIKE)."""
    owned = {session.info["python_id"], session.info["react_id"]}
    result = resume_tool.resume_recommend(session, owned, pool="domestic", region="강남")
    assert result is not None
    items = result["tool_result"]["items"]
    assert len(items) == 1
    assert items[0]["name"] == "백엔드"
    assert "강남" in result["facts"]


def test_resume_recommend_falls_back_when_region_has_no_match(session: Session) -> None:
    """일치하는 지역이 없으면 빈 결과 대신 지역 없이 전체를 돌려주고 facts에 남긴다."""
    owned = {session.info["python_id"], session.info["react_id"]}
    result = resume_tool.resume_recommend(session, owned, pool="domestic", region="제주")
    assert result is not None
    items = result["tool_result"]["items"]
    assert len(items) == 2  # region 필터 없이 p1, p2 둘 다 돌아온다
    assert "일치하는 공고가 없어" in result["facts"]


# --- K3: pipeline._dispatch/run_chat_events — resume_recommend 라우팅 -------------------


def test_dispatch_resume_recommend_uses_resume_tool_and_threads_region(session: Session) -> None:
    owned = {session.info["python_id"], session.info["react_id"]}
    plan = Plan(
        intent="resume_recommend",
        tools=["resume"],
        pool="domestic",
        entities={"region": "강남"},
        subqueries=["q"],
    )
    tool_outputs, fell_back = rag_pipeline._dispatch(session, plan, owned_skill_ids=owned)
    assert len(tool_outputs) == 1
    assert tool_outputs[0]["tool"] == "resume"
    assert tool_outputs[0]["tool_result"]["kind"] == "posting_list"
    assert fell_back is False
    items = tool_outputs[0]["tool_result"]["items"]
    assert len(items) == 1  # 강남 필터로 p1만


def test_run_chat_events_early_returns_for_resume_recommend_without_resume(
    session: Session,
) -> None:
    events = list(
        rag_pipeline.run_chat_events(
            session, "이 이력서로 넣어볼만한 공고 추천해줘", pool="domestic", owned_skill_ids=None
        )
    )
    kinds = [e["type"] for e in events]
    assert kinds == ["plan", "final"]
    final = events[-1]
    assert "이력서를 먼저 첨부" in final["answer"]
    assert final["degraded"] is True


def test_run_chat_events_answers_resume_recommend_when_resume_attached(session: Session) -> None:
    owned = {session.info["python_id"], session.info["react_id"]}
    events = list(
        rag_pipeline.run_chat_events(
            session, "이 이력서로 넣어볼만한 공고 추천해줘", pool="domestic", owned_skill_ids=owned
        )
    )
    kinds = [e["type"] for e in events]
    assert "result" in kinds
    result_event = next(e for e in events if e["type"] == "result")
    assert result_event["result"]["kind"] == "posting_list"


# --- 시장 모수 3년 윈도우 회귀 테스트: "마감 전 공고만"에서 "최근 3년 이내(마감 포함)"로 --


@pytest.fixture
def window_session() -> Iterator[Session]:
    """resume_coverage/resume_recommend가 새 3년 윈도우 필터를 쓰는지 전용으로 검증하는
    독립 세션 — 위 `session` 픽스처는 sample_size=2 등 기존 테스트들이 정확한 값에
    의존하므로 새 포스팅을 더 심으면 그 테스트들이 깨진다."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)
    with testing_session() as s:
        python = Skill(canonical="Python", category="language")
        s.add(python)
        s.flush()

        # 최근(3년 이내) 게시 + 열려 있음 — 항상 포함.
        recent_open = Posting(
            source="t", source_uid="recent_open", pool="domestic", title="최근 오픈", company="회사A",
            post_date=date(2026, 1, 1), close_date=None,
        )
        # 최근(3년 이내) 게시 + 마감됨 — 예전엔 빠졌지만 이제는 포함되어야 한다.
        recent_closed = Posting(
            source="t", source_uid="recent_closed", pool="domestic", title="최근 마감", company="회사B",
            post_date=date(2026, 1, 2), close_date=date(2026, 2, 1),
        )
        # 3년보다 오래전 게시(2020) — 마감 여부와 무관하게 이제는 제외되어야 한다.
        old_posting = Posting(
            source="t", source_uid="old", pool="domestic", title="오래된 공고", company="회사C",
            post_date=date(2020, 1, 1), close_date=None,
        )
        s.add_all([recent_open, recent_closed, old_posting])
        s.flush()
        s.add_all(
            [
                PostingTech(posting_id=recent_open.id, skill_id=python.id),
                PostingTech(posting_id=recent_closed.id, skill_id=python.id),
                PostingTech(posting_id=old_posting.id, skill_id=python.id),
            ]
        )
        s.commit()
        s.info["python_id"] = python.id
        s.info["recent_open_id"] = recent_open.id
        s.info["recent_closed_id"] = recent_closed.id
        s.info["old_id"] = old_posting.id
        yield s
    engine.dispose()


def test_resume_coverage_matched_postings_includes_recent_closed_excludes_old(
    window_session: Session,
) -> None:
    owned = {window_session.info["python_id"]}
    result = resume_tool.resume_coverage(window_session, owned, pool="domestic")
    assert result is not None
    # recent_open + recent_closed 2건만 표본에 잡혀야 한다(old_posting은 2020년 게시라 제외).
    assert result["n"] == 2
    assert "2건" in result["facts"]
    assert "최근 3년" in result["facts"]
    assert "마감 포함" in result["facts"]


def test_resume_recommend_includes_recent_closed_excludes_old_and_marks_closed(
    window_session: Session,
) -> None:
    owned = {window_session.info["python_id"]}
    result = resume_tool.resume_recommend(window_session, owned, pool="domestic")
    assert result is not None
    items = result["tool_result"]["items"]
    ids = {it["id"] for it in items}
    # old_posting(2020년 게시)은 3년 윈도우 밖이라 추천 대상에서 빠져야 한다.
    assert window_session.info["old_id"] not in ids
    assert window_session.info["recent_open_id"] in ids
    assert window_session.info["recent_closed_id"] in ids

    closed_item = next(it for it in items if it["id"] == window_session.info["recent_closed_id"])
    open_item = next(it for it in items if it["id"] == window_session.info["recent_open_id"])
    # 마감된 공고를 지원 가능한 것처럼 보이지 않게 metric에 마감 표시가 붙어야 한다.
    assert "(마감)" in closed_item["metric"]
    assert "(마감)" not in open_item["metric"]
