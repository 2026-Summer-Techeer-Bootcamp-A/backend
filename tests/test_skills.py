from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.db import Base, get_session
from app.main import app
from app.models import Skill, SkillAlias


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
        python = Skill(canonical="Python", category="language", is_ambiguous=False)
        docker = Skill(canonical="Docker", category="devops", is_ambiguous=False)
        postgres = Skill(canonical="PostgreSQL", category="database", is_ambiguous=False)
        seed.add_all([python, docker, postgres])
        seed.flush()
        seed.add_all(
            [
                SkillAlias(skill_id=python.id, alias="python", is_korean=False),
                SkillAlias(skill_id=python.id, alias="파이썬", is_korean=True),
                SkillAlias(skill_id=docker.id, alias="도커", is_korean=True),
                SkillAlias(skill_id=postgres.id, alias="postgres", is_korean=False),
            ]
        )
        seed.commit()

    def override_get_session() -> Iterator[Session]:
        with testing_session() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_get_skills_searches_canonical_name(client: TestClient) -> None:
    response = client.get("/skills", params={"q": "Pyth"})

    assert response.status_code == 200
    assert response.json() == {
        "skills": [
            {
                "canonical": "Python",
                "category": "language",
                "aliases": ["python", "파이썬"],
            }
        ]
    }


def test_get_skills_searches_korean_alias(client: TestClient) -> None:
    response = client.get("/skills", params={"q": "파이"})

    assert response.status_code == 200
    assert response.json()["skills"][0]["canonical"] == "Python"


def test_get_skills_filters_by_category(client: TestClient) -> None:
    response = client.get("/skills", params={"q": "도", "category": "language"})

    assert response.status_code == 200
    assert response.json() == {"skills": []}


def test_get_skills_limits_results(client: TestClient) -> None:
    response = client.get("/skills", params={"limit": 2})

    assert response.status_code == 200
    assert len(response.json()["skills"]) == 2

