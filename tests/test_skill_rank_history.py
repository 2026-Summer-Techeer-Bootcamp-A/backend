"""get_skill_rank_history — 결정적 티브레이커 + top_n 밖 null 처리 검증.

mv_skill_trend_yearly는 Postgres materialized view라 SQLite에선 동일 컬럼의 일반
테이블로 시뮬레이션하고, category 필터용 skill 테이블은 ORM으로 시드한다.
"""

from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.db import Base
from app.crud.insight import get_skill_rank_history
from app.models import Skill

MV_DDL = """
CREATE TABLE mv_skill_trend_yearly (
    pool TEXT NOT NULL,
    year INTEGER NOT NULL,
    canonical TEXT,
    skill_count INTEGER NOT NULL,
    skill_total INTEGER NOT NULL,
    year_total INTEGER NOT NULL
)
"""


def _seed_mv(session: Session, rows: list[tuple]) -> None:
    for pool, year, canonical, skill_count, year_total in rows:
        session.execute(
            text(
                """
                INSERT INTO mv_skill_trend_yearly
                    (pool, year, canonical, skill_count, skill_total, year_total)
                VALUES (:pool, :year, :canonical, :skill_count, 0, :year_total)
                """
            ),
            {
                "pool": pool,
                "year": year,
                "canonical": canonical,
                "skill_count": skill_count,
                "year_total": year_total,
            },
        )
    session.commit()


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text(MV_DDL))
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)
    with testing_session() as seed:
        seed.add_all(
            [
                Skill(canonical="Python", category="language", is_ambiguous=False),
                Skill(canonical="JavaScript", category="language", is_ambiguous=False),
                Skill(canonical="Java", category="language", is_ambiguous=False),
                Skill(canonical="Go", category="language", is_ambiguous=False),
                Skill(canonical="TypeScript", category="language", is_ambiguous=False),
                Skill(canonical="Spring", category="backend", is_ambiguous=False),
            ]
        )
        seed.commit()
    with testing_session() as s:
        yield s
    engine.dispose()


# 언어 카테고리, pool=global, year_total=10 고정. 2022년에 Python·JavaScript가 5로 동점.
LANGUAGE_ROWS = [
    ("global", 2022, "JavaScript", 5, 10),
    ("global", 2022, "Python", 5, 10),
    ("global", 2022, "Java", 3, 10),
    ("global", 2022, "Go", 1, 10),
    ("global", 2023, "Python", 6, 10),
    ("global", 2023, "JavaScript", 5, 10),
    ("global", 2023, "Java", 3, 10),
    ("global", 2023, "Go", 1, 10),
    ("global", 2024, "Python", 7, 10),
    ("global", 2024, "JavaScript", 5, 10),
    ("global", 2024, "Java", 2, 10),
    ("global", 2024, "Go", 1, 10),
    ("global", 2025, "Python", 7, 10),
    ("global", 2025, "JavaScript", 6, 10),
    ("global", 2025, "Java", 2, 10),
    # 2025 Go 없음 → Go는 그 해 결측(null)
    ("global", 2026, "Python", 8, 10),
    ("global", 2026, "JavaScript", 6, 10),
    ("global", 2026, "Go", 3, 10),
    ("global", 2026, "Java", 2, 10),
    # 카테고리 필터 확인용 — backend 스킬은 language 조회에 안 잡혀야 함
    ("global", 2026, "Spring", 9, 10),
]


def test_tiebreaker_is_deterministic_by_name(session: Session) -> None:
    _seed_mv(session, LANGUAGE_ROWS)
    result = get_skill_rank_history(session, category="language", top_n=3, year_from=2022, year_to=2026)

    ranks = {s["name"]: s["ranks"] for s in result["skills"]}
    # 2022년 Python·JavaScript 동점(5) → 이름 오름차순으로 JavaScript가 1위, Python이 2위.
    assert ranks["JavaScript"][0] == 1
    assert ranks["Python"][0] == 2


def test_out_of_top_n_years_are_null(session: Session) -> None:
    _seed_mv(session, LANGUAGE_ROWS)
    result = get_skill_rank_history(session, category="language", top_n=3, year_from=2022, year_to=2026)
    ranks = {s["name"]: s["ranks"] for s in result["skills"]}

    assert result["years"] == [2022, 2023, 2024, 2025, 2026]
    # Go: 2022~2024는 4위(top3 밖)→null, 2025는 결측→null, 2026은 3위로 복귀.
    assert ranks["Go"] == [None, None, None, None, 3]
    # Java: 2022~2025 3위, 2026엔 Go에 밀려 4위→null(선 끊김).
    assert ranks["Java"] == [3, 3, 3, 3, None]
    # Python/JavaScript는 매년 top3 안.
    assert ranks["Python"] == [2, 1, 1, 1, 1]
    assert ranks["JavaScript"] == [1, 2, 2, 2, 2]


def test_series_only_includes_skills_ever_in_top_n_and_orders_by_latest_rank(session: Session) -> None:
    _seed_mv(session, LANGUAGE_ROWS)
    result = get_skill_rank_history(session, category="language", top_n=3, year_from=2022, year_to=2026)

    names = [s["name"] for s in result["skills"]]
    # TypeScript는 데이터가 없어 제외. 최신(2026) 순위 오름차순 정렬 + 결측은 뒤로.
    assert names == ["Python", "JavaScript", "Go", "Java"]


def test_category_filter_excludes_other_categories(session: Session) -> None:
    _seed_mv(session, LANGUAGE_ROWS)
    result = get_skill_rank_history(session, category="language", top_n=5, year_from=2022, year_to=2026)
    names = {s["name"] for s in result["skills"]}
    # Spring은 backend라 language 조회 결과에 없어야 한다(2026 count 9로 컸음에도).
    assert "Spring" not in names


def test_aggregates_counts_across_pools(session: Session) -> None:
    # 같은 기술이 global/domestic에 나뉘어 있으면 합산 순위로 잡혀야 한다.
    _seed_mv(
        session,
        [
            ("global", 2026, "Python", 2, 10),
            ("domestic", 2026, "Python", 6, 5),  # 합산 8
            ("global", 2026, "Java", 5, 10),
            ("domestic", 2026, "Java", 1, 5),  # 합산 6
        ],
    )
    result = get_skill_rank_history(session, category="language", top_n=5, year_from=2026, year_to=2026)
    ranks = {s["name"]: s["ranks"] for s in result["skills"]}
    # Python 합산 8 > Java 합산 6 → Python 1위(단일 pool만 봤다면 Java가 앞섰을 것).
    assert ranks["Python"] == [1]
    assert ranks["Java"] == [2]
