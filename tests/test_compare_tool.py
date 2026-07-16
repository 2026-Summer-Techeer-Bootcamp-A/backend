"""K2 딥 비교(이력서 vs 공고, 공고 vs 공고, 이력서 vs 시장) 단위/스모크 테스트.

집합 연산(_build_resume_posting_compare/_build_posting_posting_compare/_dedupe_sorted_ci)은
DB 세션이 필요 없는 순수 함수라 fake 데이터로 바로 검증한다. get_posting_skill_names/
compare_resume_to_posting/compare_two_postings는 ILIKE 없이 등가 비교만 쓰므로(app/crud/
posting.py get_similar_postings와 동일한 posting_tech -> skill 조인 패턴) SQLite 픽스처로도
스모크 테스트가 가능하다 — 별도 통합(@pytest.mark.integration) 마킹 없이 fast tier에서 돈다.
"""

from collections.abc import Iterator

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.db import Base
from app.models import Posting, PostingTech, Skill
from app.services.match import (
    _build_posting_posting_compare,
    _build_resume_posting_compare,
    _dedupe_sorted_ci,
    compare_resume_to_posting,
    compare_two_postings,
    get_posting_skill_names,
)


# ---- 순수 집합 연산 단위테스트(DB 불필요) ----


def test_dedupe_sorted_ci_keeps_first_casing_and_sorts() -> None:
    result = _dedupe_sorted_ci(["python", "Python", "AWS", "aws", "Kubernetes"])
    assert result == ["AWS", "Kubernetes", "python"]


def test_dedupe_sorted_ci_empty() -> None:
    assert _dedupe_sorted_ci([]) == []


def test_build_resume_posting_compare_set_math() -> None:
    result = _build_resume_posting_compare(
        resume_title="내 이력서",
        posting_title="백엔드 개발자",
        owned_names=["Python", "AWS", "Docker"],
        posting_skills=["python", "Kubernetes", "AWS"],
    )
    assert result["resume_title"] == "내 이력서"
    assert result["posting_title"] == "백엔드 개발자"
    # posting_skills 표기(소문자 "python")가 아니라 posting 쪽 표기를 채택한다.
    assert result["matched_skills"] == ["AWS", "python"]
    assert result["missing_skills"] == ["Kubernetes"]
    assert result["extra_skills"] == ["Docker"]
    # matched 2개 / posting 요구 2개(python, Kubernetes, AWS 중 python·AWS 겹침, 분모는 3) = 66.7%
    assert result["coverage_pct"] == round(100 * 2 / 3, 1)


def test_build_resume_posting_compare_no_posting_skills_is_zero_coverage() -> None:
    result = _build_resume_posting_compare(
        resume_title="내 이력서",
        posting_title="공고",
        owned_names=["Python"],
        posting_skills=[],
    )
    assert result["coverage_pct"] == 0.0
    assert result["matched_skills"] == []
    assert result["missing_skills"] == []
    assert result["extra_skills"] == ["Python"]


def test_build_posting_posting_compare_set_math() -> None:
    result = _build_posting_posting_compare(
        title_a="공고 A",
        skills_a=["Python", "AWS", "Docker"],
        title_b="공고 B",
        skills_b=["python", "Kubernetes", "AWS"],
    )
    assert result["postingA"] == "공고 A"
    assert result["postingB"] == "공고 B"
    assert result["shared"] == ["AWS", "Python"]
    assert result["onlyA"] == ["Docker"]
    assert result["onlyB"] == ["Kubernetes"]


def test_build_posting_posting_compare_case_insensitive_dedupe() -> None:
    # 두 공고가 같은 기술을 대소문자만 다르게 등록해도 shared로 하나만 잡혀야 한다.
    result = _build_posting_posting_compare(
        title_a="공고 A", skills_a=["React"], title_b="공고 B", skills_b=["react"]
    )
    assert result["shared"] == ["React"]
    assert result["onlyA"] == []
    assert result["onlyB"] == []


# ---- SQLite 스모크 테스트(get_posting_skill_names 등가 조인 경로) ----


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)
    with testing_session() as db:
        yield db
    engine.dispose()


def _seed_posting_with_skills(db: Session, *, source_uid: str, title: str, skill_names: list[str]) -> int:
    posting = Posting(source="test", source_uid=source_uid, title=title, pool="domestic")
    db.add(posting)
    db.flush()

    for name in skill_names:
        skill = db.query(Skill).filter(Skill.canonical == name).one_or_none()
        if skill is None:
            skill = Skill(canonical=name, category="language")
            db.add(skill)
            db.flush()
        db.add(PostingTech(posting_id=posting.id, skill_id=skill.id))
    db.flush()
    return posting.id


def test_get_posting_skill_names_returns_title_and_deduped_skills(session: Session) -> None:
    posting_id = _seed_posting_with_skills(
        session, source_uid="p1", title="백엔드 개발자", skill_names=["Python", "AWS", "python"]
    )
    title, skills = get_posting_skill_names(session, posting_id)
    assert title == "백엔드 개발자"
    assert skills == ["AWS", "Python"]


def test_get_posting_skill_names_missing_posting_raises_404(session: Session) -> None:
    with pytest.raises(HTTPException) as exc_info:
        get_posting_skill_names(session, 999999)
    assert exc_info.value.status_code == 404


def test_compare_two_postings_end_to_end(session: Session) -> None:
    posting_a = _seed_posting_with_skills(
        session, source_uid="a", title="공고 A", skill_names=["Python", "AWS", "Docker"]
    )
    posting_b = _seed_posting_with_skills(
        session, source_uid="b", title="공고 B", skill_names=["Python", "Kubernetes"]
    )
    result = compare_two_postings(session, posting_id_a=posting_a, posting_id_b=posting_b)
    assert result["postingA"] == "공고 A"
    assert result["postingB"] == "공고 B"
    assert result["shared"] == ["Python"]
    assert sorted(result["onlyA"]) == ["AWS", "Docker"]
    assert result["onlyB"] == ["Kubernetes"]


def test_compare_two_postings_missing_posting_raises_404(session: Session) -> None:
    posting_a = _seed_posting_with_skills(session, source_uid="a", title="공고 A", skill_names=["Python"])
    with pytest.raises(HTTPException) as exc_info:
        compare_two_postings(session, posting_id_a=posting_a, posting_id_b=999999)
    assert exc_info.value.status_code == 404


def test_compare_resume_to_posting_end_to_end(session: Session) -> None:
    posting_id = _seed_posting_with_skills(
        session, source_uid="p1", title="백엔드 채용", skill_names=["Python", "AWS", "Kubernetes"]
    )
    python_skill = session.query(Skill).filter(Skill.canonical == "Python").one()
    docker_skill = Skill(canonical="Docker", category="devops")
    session.add(docker_skill)
    session.flush()

    result = compare_resume_to_posting(
        session, owned_skill_ids={python_skill.id, docker_skill.id}, posting_id=posting_id
    )
    assert result["resume_title"] == "내 이력서"
    assert result["posting_title"] == "백엔드 채용"
    assert result["matched_skills"] == ["Python"]
    assert result["missing_skills"] == ["AWS", "Kubernetes"]
    assert result["extra_skills"] == ["Docker"]
    assert result["coverage_pct"] == round(100 * 1 / 3, 1)
