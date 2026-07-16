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
from app.models import Posting, PostingCategory, PostingTech, Skill


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

        # position 필터(ILIKE 기반, app.services.job_category.resolve_job_category)
        # 검증용 실데이터. toss/kakao는 실제 posting_category 값("서버/백엔드 개발자")으로
        # '백엔드' 토큰에 걸리고, naver는 프론트엔드라 걸리지 않는다.
        seed.add_all(
            [
                PostingCategory(posting_id=toss.id, category="서버/백엔드 개발자"),
                PostingCategory(posting_id=kakao.id, category="서버/백엔드 개발자"),
                PostingCategory(posting_id=naver.id, category="프론트엔드 개발자"),
                PostingTech(posting_id=toss.id, skill_id=python.id),
                PostingTech(posting_id=toss.id, skill_id=spring.id),
                PostingTech(posting_id=kakao.id, skill_id=python.id),
                PostingTech(posting_id=naver.id, skill_id=aws.id),
            ]
        )
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
    """position='backend'는 mv_skill_share.position exact-match가 아니라 실제
    posting_category를 ILIKE '%백엔드%'로 필터링해 재계산해야 한다(mv_skill_share.position은
    'Developer' 같은 별개의 글로벌 영어 분류라 'backend' exact match로는 항상 0건이었다).
    toss/kakao 두 건만 "서버/백엔드 개발자" 카테고리라 sample_size=2, naver(프론트엔드)는
    제외된다. python은 toss+kakao 모두에 있어 posting_count=2, spring은 toss에만 있어 1이다.
    """
    resp = client.get(
        "/api/v1/stats/skill-share",
        params={"pool": "domestic", "position": "backend", "top_k": 2},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["sample_size"] == 2
    assert [item["canonical"] for item in body["items"]] == ["Python", "Spring"]
    python_item = body["items"][0]
    assert python_item["category"] == "language"
    assert python_item["posting_count"] == 2
    assert python_item["share"] == 1.0
    spring_item = body["items"][1]
    assert spring_item["posting_count"] == 1
    assert spring_item["share"] == 0.5


def test_skill_share_korean_position_keyword_resolves_same_as_client_token(client: TestClient) -> None:
    """RAG가 쓰는 한국어 키워드('백엔드')와 프론트 클라이언트 토큰('backend')이
    동일한 posting_category ILIKE 토큰으로 해소되어 같은 결과를 내야 한다."""
    resp = client.get(
        "/api/v1/stats/skill-share",
        params={"pool": "domestic", "position": "백엔드", "top_k": 2},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["sample_size"] == 2
    assert [item["canonical"] for item in body["items"]] == ["Python", "Spring"]


def test_skill_share_unknown_position_falls_back_to_unfiltered(client: TestClient) -> None:
    """알 수 없는 position은 0건으로 단정하지 않고 position 미지정 경로(전체 합산)로
    폴백한다 — 빈 결과보다 정직한 동작이다."""
    resp = client.get(
        "/api/v1/stats/skill-share",
        params={"pool": "domestic", "position": "존재하지않는직군"},
    )

    assert resp.status_code == 200
    body = resp.json()
    # position 미지정 테스트(test_skill_share_without_position_aggregates_across_positions)와
    # 동일한 base posting 기준 sample_size여야 한다.
    assert body["sample_size"] == 3
    assert body["items"][0]["canonical"] == "Python"


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


# --- get_skill_share position 경로: "마감 전 공고만"에서 "최근 3년(마감 포함)"로 -------
#
# 위 `client` 픽스처는 sample_size가 정확한 값(2, 3 등)에 의존하는 기존 테스트가 많아
# 새 포스팅을 더 심으면 그 테스트들이 깨진다 — 그래서 이 회귀 검증만 독립된
# 엔진/세션으로 따로 돈다.


@pytest.fixture
def window_client() -> Iterator[TestClient]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)

    with testing_session() as seed:
        python = Skill(canonical="Python", category="language")
        seed.add(python)
        seed.flush()

        # 최근(3년 이내) 게시 + 열려 있음 — 항상 포함.
        recent_open = Posting(
            source="jumpit", source_uid="w-recent-open", pool="domestic", company="Open Co",
            title="최근 오픈", post_date=date(2026, 1, 1), close_date=None,
        )
        # 최근(3년 이내) 게시 + 마감됨 — 예전엔 빠졌지만 이제는 포함되어야 한다.
        recent_closed = Posting(
            source="jumpit", source_uid="w-recent-closed", pool="domestic", company="Closed Co",
            title="최근 마감", post_date=date(2026, 1, 2), close_date=date(2026, 2, 1),
        )
        # 3년보다 오래전 게시(2020) — 마감 여부와 무관하게 이제는 제외되어야 한다.
        old_posting = Posting(
            source="jumpit", source_uid="w-old", pool="domestic", company="Old Co",
            title="오래된 공고", post_date=date(2020, 1, 1), close_date=None,
        )
        seed.add_all([recent_open, recent_closed, old_posting])
        seed.commit()

        seed.add_all(
            [
                PostingCategory(posting_id=recent_open.id, category="서버/백엔드 개발자"),
                PostingCategory(posting_id=recent_closed.id, category="서버/백엔드 개발자"),
                PostingCategory(posting_id=old_posting.id, category="서버/백엔드 개발자"),
                PostingTech(posting_id=recent_open.id, skill_id=python.id),
                PostingTech(posting_id=recent_closed.id, skill_id=python.id),
                PostingTech(posting_id=old_posting.id, skill_id=python.id),
            ]
        )
        seed.commit()

    def override_get_session() -> Iterator[Session]:
        with testing_session() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_skill_share_position_path_includes_recent_closed_excludes_old(
    window_client: TestClient,
) -> None:
    resp = window_client.get(
        "/api/v1/stats/skill-share",
        params={"pool": "domestic", "position": "backend"},
    )

    assert resp.status_code == 200
    body = resp.json()
    # recent_open + recent_closed 2건만 표본 — old_posting(2020년 게시)은 3년 윈도우 밖.
    assert body["sample_size"] == 2
    python_item = next(item for item in body["items"] if item["canonical"] == "Python")
    assert python_item["posting_count"] == 2
