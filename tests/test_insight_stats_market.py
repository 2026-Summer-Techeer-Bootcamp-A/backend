"""stats/skill-share, stats/cooccurrence 엔드포인트 테스트.

mv_skill_share/mv_cooccurrence는 실서비스에서 Postgres MATERIALIZED VIEW로 생성되지만
(app/main.py lifespan), 테스트는 SQLite를 쓰고 lifespan을 실행하지 않으므로
동일한 컬럼 스키마의 일반 테이블로 직접 시딩해 크루드가 읽는 형태를 재현한다.
"""

from collections.abc import Iterator
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.db import Base, get_session
from app.main import app
from app.models import Posting, Skill


@pytest.fixture
def client() -> Iterator[TestClient]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE mv_skill_share (
                    pool TEXT,
                    position TEXT,
                    skill_id INTEGER,
                    skill_canonical TEXT,
                    posting_count INTEGER,
                    total_postings INTEGER,
                    share FLOAT
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE mv_cooccurrence (
                    pool TEXT,
                    skill_id_1 INTEGER,
                    skill_id_2 INTEGER,
                    co_count INTEGER,
                    co_rate FLOAT
                )
                """
            )
        )

    with testing_session() as seed:
        python = Skill(canonical="Python", category="language")
        java = Skill(canonical="Java", category="language")
        spring = Skill(canonical="Spring", category="framework")
        aws = Skill(canonical="AWS", category="cloud")
        seed.add_all([python, java, spring, aws])
        seed.flush()

        toss = Posting(
            source="jumpit",
            source_uid="jumpit-1",
            pool="domestic",
            company="Toss",
            title="Backend Engineer",
            post_date=date(2024, 3, 15),
        )
        kakao = Posting(
            source="jumpit",
            source_uid="jumpit-2",
            pool="domestic",
            company="Kakao",
            title="Senior Backend Engineer",
            post_date=date(2023, 8, 10),
        )
        naver = Posting(
            source="wanted",
            source_uid="wanted-1",
            pool="domestic",
            company="Naver",
            title="Cloud Engineer",
            post_date=date(2024, 1, 5),
        )
        stripe = Posting(
            source="himalayas",
            source_uid="himalayas-1",
            pool="global",
            company="Stripe",
            title="Remote Backend Engineer",
            post_date=date(2026, 6, 1),
        )
        seed.add_all([toss, kakao, naver, stripe])
        seed.commit()

        seed.execute(
            text(
                """
                INSERT INTO mv_skill_share
                    (pool, position, skill_id, skill_canonical, posting_count, total_postings, share)
                VALUES (:pool, :position, :skill_id, :canonical, :posting_count, :total_postings, :share)
                """
            ),
            [
                {
                    "pool": "domestic",
                    "position": "backend",
                    "skill_id": python.id,
                    "canonical": "Python",
                    "posting_count": 12,
                    "total_postings": 20,
                    "share": 0.6,
                },
                {
                    "pool": "domestic",
                    "position": "backend",
                    "skill_id": spring.id,
                    "canonical": "Spring",
                    "posting_count": 8,
                    "total_postings": 20,
                    "share": 0.4,
                },
                {
                    "pool": "domestic",
                    "position": "backend",
                    "skill_id": java.id,
                    "canonical": "Java",
                    "posting_count": 5,
                    "total_postings": 20,
                    "share": 0.25,
                },
                {
                    "pool": "domestic",
                    "position": "frontend",
                    "skill_id": aws.id,
                    "canonical": "AWS",
                    "posting_count": 3,
                    "total_postings": 10,
                    "share": 0.3,
                },
                {
                    "pool": "global",
                    "position": "backend",
                    "skill_id": python.id,
                    "canonical": "Python",
                    "posting_count": 4,
                    "total_postings": 6,
                    "share": 0.6667,
                },
            ],
        )

        seed.execute(
            text(
                """
                INSERT INTO mv_cooccurrence (pool, skill_id_1, skill_id_2, co_count, co_rate)
                VALUES (:pool, :id1, :id2, :co_count, :co_rate)
                """
            ),
            [
                {"pool": "domestic", "id1": python.id, "id2": spring.id, "co_count": 8, "co_rate": 0.67},
                {"pool": "domestic", "id1": spring.id, "id2": python.id, "co_count": 8, "co_rate": 0.8},
                {"pool": "domestic", "id1": python.id, "id2": java.id, "co_count": 3, "co_rate": 0.25},
                {"pool": "domestic", "id1": java.id, "id2": python.id, "co_count": 3, "co_rate": 0.6},
                {"pool": "domestic", "id1": java.id, "id2": spring.id, "co_count": 2, "co_rate": 0.4},
                {"pool": "domestic", "id1": spring.id, "id2": java.id, "co_count": 2, "co_rate": 0.2},
                {"pool": "global", "id1": python.id, "id2": aws.id, "co_count": 4, "co_rate": 0.5},
                {"pool": "global", "id1": aws.id, "id2": python.id, "co_count": 4, "co_rate": 1.0},
            ],
        )
        seed.commit()

    def override_get_session() -> Iterator[Session]:
        with testing_session() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_skill_share_requires_pool(client: TestClient) -> None:
    resp = client.get("/api/v1/stats/skill-share")

    assert resp.status_code == 422


def test_skill_share_filters_by_position_and_top_k(client: TestClient) -> None:
    resp = client.get(
        "/api/v1/stats/skill-share",
        params={"pool": "domestic", "position": "backend", "top_k": 2},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["sample_size"] == 20
    assert [item["canonical"] for item in body["items"]] == ["Python", "Spring"]
    python_item = body["items"][0]
    assert python_item["category"] == "language"
    assert python_item["posting_count"] == 12
    assert python_item["share"] == 0.6


def test_skill_share_without_position_aggregates_across_positions(client: TestClient) -> None:
    resp = client.get("/api/v1/stats/skill-share", params={"pool": "domestic"})

    assert resp.status_code == 200
    body = resp.json()
    # domestic posting 총 3건(toss,kakao,naver) — position 미지정 시 base posting 테이블 기준.
    assert body["sample_size"] == 3
    canonicals = [item["canonical"] for item in body["items"]]
    assert canonicals[0] == "Python"
    python_item = body["items"][0]
    assert python_item["posting_count"] == 12
    assert python_item["share"] == round(12 / 3, 4)


def test_cooccurrence_requires_pool(client: TestClient) -> None:
    resp = client.get("/api/v1/stats/cooccurrence")

    assert resp.status_code == 422


def test_cooccurrence_focused_on_skill_returns_neighbor_links(client: TestClient) -> None:
    resp = client.get(
        "/api/v1/stats/cooccurrence",
        params={"pool": "domestic", "skill": "Python"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert [link["target"] for link in body["links"]] == ["Spring", "Java"]
    assert body["links"][0]["co_count"] == 8
    python_node = next(n for n in body["nodes"] if n["canonical"] == "Python")
    assert python_node["freq"] == 11


def test_cooccurrence_unknown_skill_returns_422(client: TestClient) -> None:
    resp = client.get(
        "/api/v1/stats/cooccurrence",
        params={"pool": "domestic", "skill": "NotARealSkill"},
    )

    assert resp.status_code == 422


def test_cooccurrence_without_skill_dedupes_pairs(client: TestClient) -> None:
    resp = client.get("/api/v1/stats/cooccurrence", params={"pool": "domestic"})

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["links"]) == 3
    pairs = {(link["source"], link["target"]) for link in body["links"]}
    assert ("Spring", "Python") not in pairs
    assert ("Python", "Spring") in pairs
    spring_node = next(n for n in body["nodes"] if n["canonical"] == "Spring")
    assert spring_node["freq"] == 10
