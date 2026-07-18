"""커리어 적합도 Split Diff — /chat이 resume_session_id를 받아 LLM 비교로 라우팅하는지,
세션이 없으면 기존 태그 기반 비교로 강등하는지 확인한다.

GEMINI_API_KEY가 없는 테스트 환경에서는 app.services.rag.llm.get_llm()이 NullClient를
반환한다. 그래도 requirements 추출은 seed_tags(공고 요구 기술)로 태그 폴백해 비어있지
않은 요구 목록을 만들고, 판정은 LLM이 죽어 전부 gap으로 채워지긴 하지만 여전히
tool_result.kind는 resume_posting_llm으로 나온다 — 이 테스트가 확인하려는 것은 판정
품질이 아니라 라우팅(세션 유무에 따라 어떤 kind로 가는지)이라 이걸로 충분하다.
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


def test_chat_with_resume_session_id_routes_to_llm_compare(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.routers.chat.get_resume_text_from_session",
        lambda session_id: "Python 백엔드 개발자, 4년차." if session_id == "sess-1" else None,
    )

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
