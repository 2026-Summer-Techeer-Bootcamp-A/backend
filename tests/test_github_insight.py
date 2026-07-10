"""GitHub 레포 단위 인사이트 엔드포인트 테스트 (t,u,l)."""

from collections.abc import Iterator
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.db import Base, get_session
from app.main import app
from app.models import GithubRepoSnapshot, GithubStarHistory, Posting, PostingTech, Skill


@pytest.fixture
def empty_client() -> Iterator[TestClient]:
    """ETL 미실행 상태 — 빈 결과를 정직하게 돌려주는지 확인."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)

    def override_get_session() -> Iterator[Session]:
        with testing_session() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def seeded_client() -> Iterator[TestClient]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)

    with testing_session() as seed:
        python = Skill(canonical="Python", category="language")
        js = Skill(canonical="JavaScript", category="language")
        seed.add_all([python, js])
        seed.flush()

        p1 = Posting(source="himalayas", source_uid="h1", pool="global", company="A", title="Py Dev")
        p2 = Posting(source="himalayas", source_uid="h2", pool="global", company="B", title="JS Dev")
        seed.add_all([p1, p2])
        seed.commit()
        seed.add_all(
            [
                PostingTech(posting_id=p1.id, skill_id=python.id),
                PostingTech(posting_id=p2.id, skill_id=js.id),
            ]
        )

        snap_date = date(2026, 7, 3)
        seed.add_all(
            [
                GithubRepoSnapshot(
                    full_name="python/cpython", snapshot_date=snap_date, language="Python",
                    stargazers_count=1000, forks_count=200, open_issues_count=50,
                    subscribers_count=10, topics=["python", "interpreter"], pushed_at=date(2026, 7, 1),
                ),
                GithubRepoSnapshot(
                    full_name="django/django", snapshot_date=snap_date, language="Python",
                    stargazers_count=500, forks_count=100, open_issues_count=20,
                    subscribers_count=5, topics=["python", "web"], pushed_at=date(2026, 6, 20),
                ),
                GithubRepoSnapshot(
                    full_name="facebook/react", snapshot_date=snap_date, language="JavaScript",
                    stargazers_count=2000, forks_count=300, open_issues_count=100,
                    subscribers_count=20, topics=["javascript", "ui"], pushed_at=date(2026, 7, 2),
                ),
            ]
        )
        seed.add_all(
            [
                GithubStarHistory(full_name="python/cpython", month=date(2024, 12, 1), stargazers_count=800),
                GithubStarHistory(full_name="python/cpython", month=date(2025, 12, 1), stargazers_count=950),
                GithubStarHistory(full_name="facebook/react", month=date(2024, 12, 1), stargazers_count=1500),
                GithubStarHistory(full_name="facebook/react", month=date(2025, 12, 1), stargazers_count=1900),
            ]
        )
        seed.commit()

    def override_get_session() -> Iterator[Session]:
        with testing_session() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_vitality_empty_db_is_honest_about_missing_etl(empty_client: TestClient) -> None:
    resp = empty_client.get("/api/v1/trend/github-vitality")
    assert resp.status_code == 200
    body = resp.json()
    assert body["languages"] == []
    assert body["sample_size"] == 0
    assert "ingest_github_snapshots" in body["note"]


def test_vitality_aggregates_by_language(seeded_client: TestClient) -> None:
    resp = seeded_client.get("/api/v1/trend/github-vitality")
    assert resp.status_code == 200
    body = resp.json()
    python_entry = next(x for x in body["languages"] if x["lang"] == "Python")
    assert python_entry["repo_n"] == 2
    assert python_entry["in_taxonomy"] is True
    # global 풀 2건 중 1건이 Python 요구 -> 50%
    assert python_entry["job_demand_pct"] == 50.0


def test_topics_matches_taxonomy_only(seeded_client: TestClient) -> None:
    resp = seeded_client.get("/api/v1/trend/github-topics")
    assert resp.status_code == 200
    body = resp.json()
    canonicals = {item["canonical"] for item in body["items"]}
    assert canonicals == {"Python", "JavaScript"}  # "interpreter","web","ui" 등은 taxonomy 밖이라 제외
    python_item = next(x for x in body["items"] if x["canonical"] == "Python")
    assert python_item["repo_reach"] == 2


def test_chronicle_ranks_within_representative_repos(seeded_client: TestClient) -> None:
    resp = seeded_client.get("/api/v1/trend/github-chronicle")
    assert resp.status_code == 200
    body = resp.json()
    assert body["years"] == [2024, 2025]
    react_line = next(line for line in body["lines"] if line["tech"] == "JavaScript")
    assert react_line["repo"] == "facebook/react"
    # 2024: react(1500) > cpython(800) -> rank 1
    point_2024 = next(p for p in react_line["points"] if p["year"] == 2024)
    assert point_2024["rank"] == 1
    assert point_2024["stars"] == 1500
