"""build_stack_insight — 조합 집계 정확성 + LLM은 숫자만 조립(폴백 포함) 검증.

mv_cooccurrence는 Postgres materialized view라 SQLite에선 동일 컬럼 테이블로 시뮬레이션하고,
get_cooccurrence가 조인하는 skill 테이블은 ORM으로 시드한다. LLM은 가짜 클라이언트로 주입.
"""

from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.db import Base
from app.models import Skill
from app.services.stack_insight import build_stack_insight

MV_DDL = """
CREATE TABLE mv_cooccurrence (
    pool TEXT NOT NULL,
    skill_id_1 INTEGER NOT NULL,
    skill_id_2 INTEGER NOT NULL,
    co_count INTEGER NOT NULL,
    co_rate REAL NOT NULL
)
"""


class FakeLLM:
    """호출 프롬프트를 기록하고 지정한 텍스트를 반환하는 가짜 LLM."""

    def __init__(self, reply: str | None) -> None:
        self.reply = reply
        self.calls: list[tuple[str, str]] = []

    def json(self, system: str, prompt: str, temperature: float = 0.2):
        return None

    def text(self, system: str, prompt: str, temperature: float = 0.4) -> str | None:
        self.calls.append((system, prompt))
        return self.reply


class NullLLM:
    def json(self, system: str, prompt: str, temperature: float = 0.2):
        return None

    def text(self, system: str, prompt: str, temperature: float = 0.4):
        return None


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
        skills = [
            Skill(canonical="Python", category="language", is_ambiguous=False),
            Skill(canonical="Docker", category="devops", is_ambiguous=False),
            Skill(canonical="AWS", category="cloud", is_ambiguous=False),
            Skill(canonical="SQL", category="data_db", is_ambiguous=False),
        ]
        seed.add_all(skills)
        seed.flush()
        ids = {s.canonical: s.id for s in skills}
        # Python(base) 기준: Docker 64%, AWS 41%, SQL 30% (co_rate는 0~1 분수)
        seed.execute(
            text(
                """
                INSERT INTO mv_cooccurrence (pool, skill_id_1, skill_id_2, co_count, co_rate)
                VALUES
                    ('domestic', :py, :docker, 640, 0.64),
                    ('domestic', :py, :aws, 410, 0.41),
                    ('domestic', :py, :sql, 300, 0.30)
                """
            ),
            {"py": ids["Python"], "docker": ids["Docker"], "aws": ids["AWS"], "sql": ids["SQL"]},
        )
        seed.commit()
    with testing_session() as s:
        yield s
    engine.dispose()


def test_combos_are_conditional_rates_sorted_desc(session: Session) -> None:
    result = build_stack_insight(
        session, base_skill="Python", pool="domestic", owned_skills=[], llm=NullLLM(), top_k=5
    )
    combos = result["combos"]
    assert [c["skill"] for c in combos] == ["Docker", "AWS", "SQL"]
    # co_rate(0~1)가 %로 정규화되어야 한다.
    assert combos[0] == {"skill": "Docker", "co_rate": 64.0, "co_count": 640}
    assert combos[1]["co_rate"] == 41.0


def test_fallback_sentence_uses_db_numbers_when_llm_unavailable(session: Session) -> None:
    result = build_stack_insight(
        session, base_skill="Python", pool="domestic", owned_skills=["SQL"], llm=NullLLM()
    )
    assert result["ai_generated"] is False
    # 폴백 문장도 DB 숫자를 그대로 인용해야 한다.
    assert "64%가 Docker" in result["insight"]
    assert "41%가 AWS" in result["insight"]


def test_llm_sentence_is_used_and_receives_db_numbers_in_prompt(session: Session) -> None:
    fake = FakeLLM(reply="Python 공고의 64%가 Docker를 함께 요구합니다.")
    result = build_stack_insight(
        session, base_skill="Python", pool="domestic", owned_skills=["SQL"], llm=fake
    )
    assert result["ai_generated"] is True
    assert result["insight"] == "Python 공고의 64%가 Docker를 함께 요구합니다."
    # 숫자는 프롬프트로 주입된다(LLM이 지어내지 않도록) — 프롬프트에 집계 수치가 들어있어야 한다.
    _system, prompt = fake.calls[0]
    assert "64" in prompt and "Docker" in prompt
    assert "SQL" in prompt  # owned_skills 맥락도 전달


def test_top_k_limits_combos(session: Session) -> None:
    result = build_stack_insight(
        session, base_skill="Python", pool="domestic", owned_skills=[], llm=NullLLM(), top_k=2
    )
    assert [c["skill"] for c in result["combos"]] == ["Docker", "AWS"]
