from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.db import Base
from app.models import Posting, PostingTech, Skill
from app.services.rag.tools import sql_tool


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)
    with testing_session() as s:
        react = Skill(canonical="React", category="frontend", is_ambiguous=False)
        vue = Skill(canonical="Vue", category="frontend", is_ambiguous=False)
        s.add_all([react, vue])
        s.flush()
        p1 = Posting(source="t", source_uid="1", pool="domestic", title="프론트 개발자")
        p2 = Posting(source="t", source_uid="2", pool="domestic", title="프론트 개발자2")
        s.add_all([p1, p2])
        s.flush()
        s.add_all(
            [
                PostingTech(posting_id=p1.id, skill_id=react.id),
                PostingTech(posting_id=p2.id, skill_id=react.id),
                PostingTech(posting_id=p2.id, skill_id=vue.id),
            ]
        )
        s.commit()
        yield s
    engine.dispose()


def test_top_skills_verbose_false_has_no_debug(session: Session) -> None:
    result = sql_tool.top_skills(session, pool="domestic", verbose=False)
    assert result["tool_result"].get("debug") is None


def test_top_skills_verbose_true_exposes_real_sql(session: Session) -> None:
    result = sql_tool.top_skills(session, pool="domestic", verbose=True)
    debug = result["tool_result"]["debug"]
    assert "posting_tech" in debug["sql"]
    assert "GROUP BY s.canonical" in debug["sql"]
    assert debug["params"]["pool"] == "domestic"


def test_skill_demand_verbose_true_exposes_real_sql(session: Session) -> None:
    result = sql_tool.skill_demand(session, "React", pool="domestic", verbose=True)
    assert result is not None
    debug = result["tool_result"]["debug"]
    assert "posting_tech" in debug["sql"]
    assert debug["params"]["sid"] is not None


def test_multi_skill_compare_verbose_true_exposes_real_sql(session: Session) -> None:
    result = sql_tool.multi_skill_compare(session, ["React", "Vue"], pool="domestic", verbose=True)
    assert result is not None
    debug = result["tool_result"]["debug"]
    assert "posting_tech" in debug["sql"]
