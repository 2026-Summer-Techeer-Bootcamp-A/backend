"""커리어 적합도 Split Diff — /chat이 resume_session_id를 받아 LLM 비교로 라우팅하는지,
세션이 없으면 기존 태그 기반 비교로 강등하는지 확인한다.

GEMINI_API_KEY가 없는 테스트 환경에서는 app.services.rag.llm.get_llm()이 기본적으로
NullClient를 반환해 모든 LLM 호출이 None이 된다. extract_requirements/judge_requirements가
LLM 실패를 정직하게 강등 신호(llm_ok=False)로 돌려주게 고친 뒤로는(compare_tool.py 참고)
NullClient로는 resume_posting_llm_compare가 태그 기반 비교로 강등돼 kind가
resume_posting_llm로 나오지 않는다 — 그래서 "LLM 비교로 라우팅"을 검증하는 테스트는
app.services.rag.pipeline.get_llm을 실제로 요구/판정을 만들어내는 가짜 LLM으로 갈아끼워
LLM 경로가 정직하게 성공하는 경우를 재현한다.
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
from app.services.rag import router as rag_router


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)
    with testing_session() as s:
        python = Skill(canonical="Python", category="language")
        s.add(python)
        s.flush()

        posting = Posting(
            source="t", source_uid="1", pool="domestic", title="백엔드", company="회사A",
            post_date=date(2026, 1, 1),
            description='[{"title":"자격요건","text":"Python 백엔드 개발 경험"}]',
        )
        s.add(posting)
        s.flush()
        s.add(PostingTech(posting_id=posting.id, skill_id=python.id))
        s.commit()
        s.info["python_id"] = python.id
        s.info["posting_id"] = posting.id
        yield s
    engine.dispose()


@pytest.fixture(autouse=True)
def _patch_skill_detection(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """router._detect_skill/_detect_skills_multi는 raw SQL ILIKE(Postgres 전용)를 쓴다.
    sqlite 픽스처에서 문법 에러가 나므로 부분 문자열 매칭 스텁으로 갈아끼운다(
    test_resume_chat.py와 동일 패턴)."""
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


@pytest.fixture
def client(session: Session) -> Iterator[TestClient]:
    user = User(email="career-diff@example.com", password_hash="unused")
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
    test_client.posting_id = session.info["posting_id"]  # type: ignore[attr-defined]
    test_client.token = create_access_token(str(user.id))  # type: ignore[attr-defined]
    yield test_client
    app.dependency_overrides.clear()


class _FakeLLM:
    """extract_requirements/judge_requirements 둘 다 실제로 판정을 만들어내는 가짜
    LLM — 프롬프트 내용으로 어느 단계 호출인지 구분한다(공고 본문/요구사항 키워드는
    각 함수의 프롬프트 템플릿에서 고정으로 쓰인다). 플래너 프롬프트처럼 둘 다에
    해당하지 않는 호출은 None을 돌려줘 라우터가 휴리스틱으로 대체되게 둔다 — 이
    테스트가 검증하려는 것은 라우팅 자체가 아니라 LLM 비교 경로의 정직한 성공이다."""

    def __init__(self) -> None:
        self.last_debug: dict | None = None
        self.call_count = 0

    def json(self, system: str, prompt: str, temperature: float = 0.2, *, max_output_tokens: int | None = None) -> dict | None:
        self.call_count += 1
        if "공고 본문" in prompt:
            return {
                "items": [
                    {"id": "R1", "text": "Python 백엔드 개발 경험", "source_quote": "Python 백엔드 개발 경험"},
                ]
            }
        if "요구사항:" in prompt:
            return {
                "items": [
                    {
                        "req_id": "R1",
                        "verdict": "met",
                        "resume_quote": "Python 백엔드 개발자",
                        "rationale": "일치",
                    },
                ]
            }
        return None

    def text(self, system: str, prompt: str, temperature: float = 0.4) -> str | None:
        return None


def test_chat_with_resume_session_id_routes_to_llm_compare(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.routers.chat.get_resume_text_from_session",
        lambda session_id: "Python 백엔드 개발자, 4년차." if session_id == "sess-1" else None,
    )
    monkeypatch.setattr("app.services.rag.pipeline.get_llm", lambda: _FakeLLM())

    resp = client.post(
        "/api/v1/chat",
        json={
            "question": "이 공고랑 내 이력서 비교해줘",
            "pool": "domestic",
            "resume_id": client.resume_id,
            "posting_ids": [client.posting_id],
            "resume_session_id": "sess-1",
        },
        headers={"Authorization": f"Bearer {client.token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["tool_results"][0]["kind"] == "resume_posting_llm"
    assert body["tool_results"][0]["compare"]["posting_title"] == "백엔드"
    assert body["tool_results"][0]["compare"]["degraded"] is False


def test_chat_without_resume_session_id_degrades_to_tag_compare(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/chat",
        json={
            "question": "이 공고랑 내 이력서 비교해줘",
            "pool": "domestic",
            "resume_id": client.resume_id,
            "posting_ids": [client.posting_id],
        },
        headers={"Authorization": f"Bearer {client.token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["tool_results"][0]["kind"] == "resume_posting"
