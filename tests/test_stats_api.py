from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.db import Base, get_session
from app.main import app
from app.models import JobCategory, Posting, PostingCategory, PostingTech, Skill


@pytest.fixture
def client() -> Iterator[TestClient]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)
    with testing_session() as seed:
        python = Skill(canonical="Python", category="language")
        aws = Skill(canonical="AWS", category="platform")
        seed.add_all([python, aws])
        # 기술직/비기술직 통제 어휘. 직무 미지정 집계는 is_tech=True 공고만 대상으로 한다.
        seed.add_all(
            [
                JobCategory(name="Developer", is_tech=True),
                JobCategory(name="Sales", is_tech=False),
            ]
        )
        seed.commit()

        posting_a = Posting(
            source="wanted",
            source_uid="wanted-1",
            pool="domestic",
            title="Backend Developer A",
        )
        posting_b = Posting(
            source="jumpit",
            source_uid="jumpit-1",
            pool="domestic",
            title="Backend Developer B",
        )
        # 비기술 직군 공고. 직무 미지정 집계에서 제외되어야 한다.
        posting_c = Posting(
            source="wanted",
            source_uid="wanted-2",
            pool="domestic",
            title="Sales Manager C",
        )
        seed.add_all([posting_a, posting_b, posting_c])
        seed.commit()

        seed.add_all(
            [
                PostingCategory(posting_id=posting_a.id, category="Developer"),
                PostingCategory(posting_id=posting_b.id, category="Developer"),
                PostingCategory(posting_id=posting_c.id, category="Sales"),
                PostingTech(posting_id=posting_a.id, skill_id=python.id),
                PostingTech(posting_id=posting_b.id, skill_id=python.id),
                PostingTech(posting_id=posting_a.id, skill_id=aws.id),
                # 비기술 공고의 기술은 직무 미지정 집계에 반영되면 안 된다.
                PostingTech(posting_id=posting_c.id, skill_id=aws.id),
            ]
        )
        seed.commit()

    def override_get_session() -> Iterator:
        with testing_session() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_get_stats_skills_returns_share_ranked_desc(client: TestClient) -> None:
    response = client.get("/api/v1/stats/skills?pool=domestic&position=Developer")

    assert response.status_code == 200
    assert response.json() == {
        "pool": "domestic",
        "skills": [
            {"canonical": "Python", "share": 1.0, "posting_count": 2},
            {"canonical": "AWS", "share": 0.5, "posting_count": 1},
        ],
        "as_of": response.json()["as_of"],
        "sample_size": 2,
    }


def test_get_stats_skills_allows_missing_position(client: TestClient) -> None:
    response = client.get("/api/v1/stats/skills?pool=domestic")

    assert response.status_code == 200
    body = response.json()
    # 비기술 직군(Sales) 공고는 제외 → 기술직 공고 2건만 모수가 된다.
    assert body["sample_size"] == 2
    # posting_c(Sales)의 AWS 요구가 반영되면 count 2/share 1.0로 부풀려진다. 제외되어야 한다.
    skills = {row["canonical"]: row for row in body["skills"]}
    assert skills["Python"] == {"canonical": "Python", "share": 1.0, "posting_count": 2}
    assert skills["AWS"] == {"canonical": "AWS", "share": 0.5, "posting_count": 1}


def test_get_stats_skills_respects_limit(client: TestClient) -> None:
    response = client.get("/api/v1/stats/skills?pool=domestic&limit=1")

    assert response.status_code == 200
    assert len(response.json()["skills"]) == 1


def test_get_stats_skills_returns_empty_for_no_matching_postings(client: TestClient) -> None:
    response = client.get("/api/v1/stats/skills?pool=global")

    assert response.status_code == 200
    assert response.json()["skills"] == []
    assert response.json()["sample_size"] == 0


def test_get_stats_skills_requires_pool(client: TestClient) -> None:
    response = client.get("/api/v1/stats/skills")

    assert response.status_code == 422


def test_get_stats_skills_rejects_invalid_pool(client: TestClient) -> None:
    response = client.get("/api/v1/stats/skills?pool=invalid")

    assert response.status_code == 422


def test_get_stats_cooccurrence_returns_co_rate_ranked_desc(client: TestClient) -> None:
    response = client.get("/api/v1/stats/cooccurrence?skill=Python&pool=domestic")

    assert response.status_code == 200
    assert response.json() == {
        "skill": "Python",
        "co_occurs": [{"canonical": "AWS", "co_rate": 0.5, "co_count": 1}],
        "as_of": response.json()["as_of"],
    }


def test_get_stats_cooccurrence_respects_limit(client: TestClient) -> None:
    response = client.get("/api/v1/stats/cooccurrence?skill=Python&pool=domestic&limit=0")

    assert response.status_code == 422


def test_get_stats_cooccurrence_returns_empty_for_unknown_skill(client: TestClient) -> None:
    response = client.get("/api/v1/stats/cooccurrence?skill=Nope&pool=domestic")

    assert response.status_code == 200
    assert response.json() == {
        "skill": "Nope",
        "co_occurs": [],
        "as_of": response.json()["as_of"],
    }


def test_get_stats_cooccurrence_requires_skill_and_pool(client: TestClient) -> None:
    response = client.get("/api/v1/stats/cooccurrence")

    assert response.status_code == 422


def test_get_stats_cooccurrence_rejects_invalid_pool(client: TestClient) -> None:
    response = client.get("/api/v1/stats/cooccurrence?skill=Python&pool=invalid")

    assert response.status_code == 422
