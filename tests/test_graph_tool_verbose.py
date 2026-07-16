from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.db import Base
from app.models import Posting, PostingTech, Skill
from app.services.rag.tools import graph_tool


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)
    with testing_session() as s:
        react = Skill(canonical="React", category="frontend", is_ambiguous=False)
        redux = Skill(canonical="Redux", category="frontend", is_ambiguous=False)
        ts = Skill(canonical="TypeScript", category="language", is_ambiguous=False)
        s.add_all([react, redux, ts])
        s.flush()
        p1 = Posting(source="t", source_uid="1", pool="domestic", title="프론트 개발자")
        p2 = Posting(source="t", source_uid="2", pool="domestic", title="프론트 개발자2")
        s.add_all([p1, p2])
        s.flush()
        s.add_all(
            [
                PostingTech(posting_id=p1.id, skill_id=react.id),
                PostingTech(posting_id=p1.id, skill_id=redux.id),
                PostingTech(posting_id=p1.id, skill_id=ts.id),
                PostingTech(posting_id=p2.id, skill_id=react.id),
                PostingTech(posting_id=p2.id, skill_id=ts.id),
            ]
        )
        s.commit()
        yield s
    engine.dispose()


def test_co_occurring_skills_verbose_false_has_no_debug(session: Session) -> None:
    result = graph_tool.co_occurring_skills(session, "React", pool="domestic", verbose=False)
    assert result is not None
    assert result["tool_result"].get("debug") is None


def test_co_occurring_skills_verbose_true_exposes_formula_and_sql(session: Session) -> None:
    result = graph_tool.co_occurring_skills(session, "React", pool="domestic", verbose=True)
    assert result is not None
    debug = result["tool_result"]["debug"]
    assert debug["base_postings"] == 2
    assert "posting_tech" in debug["sql_1hop"]
    assert "strength" in debug["strength_formula"]
