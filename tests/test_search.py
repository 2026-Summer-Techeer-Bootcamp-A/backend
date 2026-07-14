from collections.abc import Iterator
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.db import Base, get_session
from app.main import app
from app.models import Posting, Skill, SkillAlias


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    from app.services import search_cache

    class FakeRedis:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        def get(self, key: str) -> str | None:
            return self.store.get(key)

        def setex(self, key: str, ttl: int, value: str) -> None:
            self.store[key] = value

    monkeypatch.setattr(search_cache, "redis_client", FakeRedis())

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)

    with testing_session() as seed:
        python = Skill(canonical="Python", category="language", is_ambiguous=False)
        docker = Skill(canonical="Docker", category="devops", is_ambiguous=False)
        seed.add_all([python, docker])
        seed.flush()
        seed.add(SkillAlias(skill_id=python.id, alias="파이썬", is_korean=True))

        postings = [
            Posting(
                source="wanted",
                source_uid="wanted-1",
                pool="domestic",
                company="Pythonic Corp",
                title="Backend Engineer",
                post_date=date(2026, 7, 1),
            ),
            Posting(
                source="jumpit",
                source_uid="jumpit-1",
                pool="domestic",
                company="Kakao",
                title="Python Developer",
                post_date=date(2026, 7, 5),
            ),
            Posting(
                source="wanted",
                source_uid="wanted-2",
                pool="domestic",
                company="Kakao",
                title="Frontend Engineer",
                post_date=date(2026, 7, 7),
            ),
            Posting(
                source="wanted",
                source_uid="wanted-3",
                pool="domestic",
                company="Python Zombie",
                title="Deleted Posting",
                post_date=date(2026, 7, 10),
                is_deleted=True,
            ),
        ]
        seed.add_all(postings)
        seed.commit()

    def override_get_session() -> Iterator[Session]:
        with testing_session() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_search_returns_matching_postings_by_title(client: TestClient) -> None:
    response = client.get("/api/v1/search", params={"q": "Python"})

    assert response.status_code == 200
    posting_titles = [item["title"] for item in response.json()["postings"]]
    assert "Python Developer" in posting_titles


def test_search_returns_matching_postings_by_company(client: TestClient) -> None:
    response = client.get("/api/v1/search", params={"q": "Python"})

    assert response.status_code == 200
    posting_companies = [item["company"] for item in response.json()["postings"]]
    assert "Pythonic Corp" in posting_companies


def test_search_excludes_soft_deleted_postings(client: TestClient) -> None:
    response = client.get("/api/v1/search", params={"q": "Python"})

    assert response.status_code == 200
    posting_titles = [item["title"] for item in response.json()["postings"]]
    assert "Deleted Posting" not in posting_titles


def test_search_postings_are_sorted_by_latest_post_date(client: TestClient) -> None:
    response = client.get("/api/v1/search", params={"q": "Python"})

    assert response.status_code == 200
    posting_titles = [item["title"] for item in response.json()["postings"]]
    assert posting_titles == ["Python Developer", "Backend Engineer"]


def test_search_posting_item_includes_pool(client: TestClient) -> None:
    response = client.get("/api/v1/search", params={"q": "Python"})

    assert response.status_code == 200
    for item in response.json()["postings"]:
        assert item["pool"] == "domestic"


def test_search_returns_matching_skill_by_canonical(client: TestClient) -> None:
    response = client.get("/api/v1/search", params={"q": "Pyth"})

    assert response.status_code == 200
    assert response.json()["skills"] == [{"canonical": "Python", "category": "language"}]


def test_search_returns_matching_skill_by_alias(client: TestClient) -> None:
    response = client.get("/api/v1/search", params={"q": "파이"})

    assert response.status_code == 200
    assert response.json()["skills"][0]["canonical"] == "Python"


def test_search_returns_matching_companies_with_posting_count(client: TestClient) -> None:
    response = client.get("/api/v1/search", params={"q": "Kakao"})

    assert response.status_code == 200
    assert response.json()["companies"] == [{"company": "Kakao", "posting_count": 2}]


def test_search_respects_limit(client: TestClient) -> None:
    response = client.get("/api/v1/search", params={"q": "e", "limit": 1})

    assert response.status_code == 200
    body = response.json()
    assert len(body["postings"]) <= 1
    assert len(body["skills"]) <= 1
    assert len(body["companies"]) <= 1


def test_search_missing_q_returns_422(client: TestClient) -> None:
    response = client.get("/api/v1/search")

    assert response.status_code == 422


def test_search_blank_q_returns_422(client: TestClient) -> None:
    response = client.get("/api/v1/search", params={"q": ""})

    assert response.status_code == 422


def test_search_whitespace_only_q_returns_422(client: TestClient) -> None:
    response = client.get("/api/v1/search", params={"q": "   "})

    assert response.status_code == 422


def test_search_rejects_query_longer_than_100_characters(client: TestClient) -> None:
    response = client.get("/api/v1/search", params={"q": "a" * 101})

    assert response.status_code == 422


def test_repeated_normalized_search_uses_cached_result(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.routers import search as search_router

    original_search_all = search_router.search_all
    calls = 0

    def counted_search_all(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original_search_all(*args, **kwargs)

    monkeypatch.setattr(search_router, "search_all", counted_search_all)

    first = client.get("/api/v1/search", params={"q": " Python "})
    second = client.get("/api/v1/search", params={"q": "python"})

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["postings"] == second.json()["postings"]
    assert second.json()["query"] == "python"
    assert calls == 1


def test_search_no_match_returns_empty_lists(client: TestClient) -> None:
    response = client.get("/api/v1/search", params={"q": "zzzznomatch"})

    assert response.status_code == 200
    assert response.json() == {
        "postings": [],
        "skills": [],
        "companies": [],
        "query": "zzzznomatch",
    }
