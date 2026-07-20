"""POST /match/roadmap/node-content 테스트 — LLM 미가용 폴백 경로가 200 유효 JSON을 주는지 확인."""

from collections.abc import Iterator
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.db import Base, get_session
from app.main import app
from app.models import Posting, PostingTech, Skill


class _NullLLM:
    """항상 None을 반환하는 가짜 LLM — 폴백 경로를 결정적으로 재현한다."""

    def json(self, system: str, prompt: str, temperature: float = 0.2, **kwargs):
        return None

    def text(self, system: str, prompt: str, temperature: float = 0.4):
        return None


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)

    with testing_session() as seed:
        skill = Skill(canonical="Kubernetes", category="devops")
        seed.add(skill)
        seed.flush()

        posting = Posting(
            source="jumpit",
            source_uid="j1",
            pool="domestic",
            company="Toss",
            title="Backend Engineer",
            industry="fintech",
            post_date=date(2026, 1, 1),
        )
        seed.add(posting)
        seed.commit()

        seed.add(PostingTech(posting_id=posting.id, skill_id=skill.id))
        seed.commit()

    def override_get_session() -> Iterator[Session]:
        with testing_session() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    monkeypatch.setattr("app.routers.match.get_llm", lambda: _NullLLM())
    test_client = TestClient(app)
    yield test_client
    app.dependency_overrides.clear()


def _assert_valid_schema(body: dict) -> None:
    assert isinstance(body["why"], str) and body["why"]
    assert isinstance(body["summary"], str) and body["summary"]
    assert 2 <= len(body["resources"]) <= 4
    for resource in body["resources"]:
        assert resource["kind"] in ("guide", "doc", "project", "video")
        assert resource["label"]
    assert isinstance(body["project"], str) and body["project"]
    assert 0 <= len(body["citations"]) <= 3


def test_node_content_fallback_for_skill_with_demand(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/match/roadmap/node-content",
        json={
            "node_id": "n1",
            "node_label": "Kubernetes",
            "node_type": "skill",
            "section": "학습 순서",
            "goal_company": "카카오페이증권",
            "goal_title": "백엔드 엔지니어",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    _assert_valid_schema(body)
    # 공고 1건이 Kubernetes를 요구하도록 시딩했으니 근거 수치가 실린다.
    assert body["citations"] == ["공고 1건"]
    assert "1건" in body["why"]


def test_node_content_fallback_for_concept_without_demand(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/match/roadmap/node-content",
        json={
            "node_id": "n2",
            "node_label": "MSA",
            "node_type": "concept",
            "section": "학습 순서",
            "goal_company": None,
            "goal_title": "백엔드 엔지니어",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    _assert_valid_schema(body)
    assert body["citations"] == []


def test_node_content_fallback_for_unknown_skill_label(client: TestClient) -> None:
    """taxonomy에 없는 라벨(node_type=skill)도 500 없이 일반 설명 폴백으로 처리된다."""
    resp = client.post(
        "/api/v1/match/roadmap/node-content",
        json={
            "node_id": "n3",
            "node_label": "존재하지않는기술",
            "node_type": "skill",
            "section": "학습 순서",
            "goal_company": "카카오페이증권",
            "goal_title": "백엔드 엔지니어",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    _assert_valid_schema(body)
    assert body["citations"] == []
