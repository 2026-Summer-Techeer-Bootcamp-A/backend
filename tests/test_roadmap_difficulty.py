"""POST /match/roadmap/difficulty 테스트.

키 없는 환경(LLM NullClient)에서도 폴백 경로가 200 유효 JSON을 주는지, 그리고 티어
결정 규칙(_compute_deterministic_tier)이 avg_career/prereq_depth 조합별로 올바른
값을 내는지 검증한다.
"""

from collections.abc import Iterator
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.db import Base, get_session
from app.main import app
from app.models import Concept, Posting, PostingConcept, PostingTech, Skill
from app.services.roadmap_difficulty import _compute_deterministic_tier


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
        concept = Concept(name="MSA", category="architecture")
        seed.add_all([skill, concept])
        seed.flush()

        postings = [
            Posting(
                source="jumpit",
                source_uid=f"j{i}",
                pool="domestic",
                company="Toss",
                title="Backend Engineer",
                industry="fintech",
                post_date=date(2026, 1, 1),
                career_min=career_min,
            )
            for i, career_min in enumerate([2, 4, 6])
        ]
        seed.add_all(postings)
        seed.commit()

        for posting in postings:
            seed.add(PostingTech(posting_id=posting.id, skill_id=skill.id))
            seed.add(PostingConcept(posting_id=posting.id, concept_id=concept.id))
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
    assert "items" in body
    for item in body["items"]:
        assert isinstance(item["node_id"], str) and item["node_id"]
        assert item["tier"] in ("입문", "초급", "중급", "고급")
        assert item["avg_career"] is None or isinstance(item["avg_career"], (int, float))
        assert isinstance(item["demand"], int) and item["demand"] >= 0
        assert isinstance(item["basis"], str) and item["basis"]


def test_difficulty_fallback_for_skill_with_market_data(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/match/roadmap/difficulty",
        json={
            "nodes": [
                {
                    "node_id": "n1",
                    "label": "Kubernetes",
                    "type": "skill",
                    "prereq_depth": 2,
                }
            ]
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    _assert_valid_schema(body)
    item = body["items"][0]
    # career_min 2/4/6의 평균은 4.0년 -> 고급(4년 이상)
    assert item["avg_career"] == pytest.approx(4.0)
    assert item["demand"] == 3
    assert item["tier"] == "고급"
    assert "4.0" in item["basis"]
    assert "3" in item["basis"]


def test_difficulty_fallback_for_concept_with_market_data(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/match/roadmap/difficulty",
        json={
            "nodes": [
                {
                    "node_id": "n2",
                    "label": "MSA",
                    "type": "concept",
                    "prereq_depth": 1,
                }
            ]
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    _assert_valid_schema(body)
    item = body["items"][0]
    assert item["avg_career"] == pytest.approx(4.0)
    assert item["demand"] == 3
    assert item["tier"] == "고급"


def test_difficulty_fallback_for_unknown_label_uses_prereq_depth(client: TestClient) -> None:
    """taxonomy에 없는 라벨은 avg_career=None, demand=0으로 depth 기반 폴백을 탄다."""
    resp = client.post(
        "/api/v1/match/roadmap/difficulty",
        json={
            "nodes": [
                {
                    "node_id": "n3",
                    "label": "존재하지않는기술",
                    "type": "skill",
                    "prereq_depth": 3,
                }
            ]
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    _assert_valid_schema(body)
    item = body["items"][0]
    assert item["avg_career"] is None
    assert item["demand"] == 0
    assert item["tier"] == "고급"
    assert "3단계" in item["basis"]


def test_difficulty_batch_processes_multiple_nodes(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/match/roadmap/difficulty",
        json={
            "nodes": [
                {"node_id": "n1", "label": "Kubernetes", "type": "skill", "prereq_depth": 2},
                {"node_id": "n3", "label": "미지의개념", "type": "concept", "prereq_depth": 0},
            ]
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    _assert_valid_schema(body)
    assert [item["node_id"] for item in body["items"]] == ["n1", "n3"]


def test_difficulty_cert_type_skips_market_query_and_falls_back(client: TestClient) -> None:
    """cert 타입은 스펙상 posting_tech/posting_concept 매칭 대상이 아니라 항상
    prereq_depth 기반 결정적 폴백만 탄다."""
    resp = client.post(
        "/api/v1/match/roadmap/difficulty",
        json={
            "nodes": [
                {"node_id": "n4", "label": "정보처리기사", "type": "cert", "prereq_depth": 0}
            ]
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    item = body["items"][0]
    assert item["avg_career"] is None
    assert item["demand"] == 0
    assert item["tier"] == "입문"


@pytest.mark.parametrize(
    ("avg_career", "prereq_depth", "expected"),
    [
        (0.5, 0, "입문"),
        (0.5, 1, "초급"),  # avg_career < 1년이어도 선행 깊이가 있으면 입문이 아니다
        (1.5, 0, "초급"),
        (1.99, 3, "초급"),
        (2.0, 0, "중급"),
        (3.9, 0, "중급"),
        (4.0, 0, "고급"),
        (10.0, 0, "고급"),
    ],
)
def test_compute_deterministic_tier_with_avg_career(
    avg_career: float, prereq_depth: int, expected: str
) -> None:
    assert _compute_deterministic_tier(avg_career, prereq_depth) == expected


@pytest.mark.parametrize(
    ("prereq_depth", "expected"),
    [
        (0, "입문"),
        (1, "초급"),
        (2, "중급"),
        (3, "고급"),
        (5, "고급"),
    ],
)
def test_compute_deterministic_tier_fallback_without_avg_career(
    prereq_depth: int, expected: str
) -> None:
    assert _compute_deterministic_tier(None, prereq_depth) == expected
