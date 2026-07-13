"""GET /stats/skill-unlock 테스트."""

from collections.abc import Iterator
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.db import Base, get_session
from app.core.security import create_access_token
from app.main import app
from app.models import Posting, PostingTech, Resume, ResumeSkill, Skill, User


@pytest.fixture
def client() -> Iterator[TestClient]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)

    with testing_session() as seed:
        python = Skill(canonical="Python", category="language")
        java = Skill(canonical="Java", category="language")
        spring = Skill(canonical="Spring", category="framework")
        aws = Skill(canonical="AWS", category="cloud")
        docker = Skill(canonical="Docker", category="tool")
        go = Skill(canonical="Go", category="language")
        user = User(email="unlock@example.com", password_hash="unused")
        seed.add_all([python, java, spring, aws, docker, go, user])
        seed.flush()

        resume = Resume(user_id=user.id, title="Backend", position="backend", pool="domestic")
        seed.add(resume)
        seed.commit()
        seed.add(ResumeSkill(resume_id=resume.resume_id, skill_id=python.id))
        seed.commit()

        p1 = Posting(source="jumpit", source_uid="u1", pool="domestic", company="A", title="X",
                      post_date=date(2026, 7, 1))
        p2 = Posting(source="jumpit", source_uid="u2", pool="domestic", company="B", title="X",
                      post_date=date(2026, 7, 1))
        p3 = Posting(source="jumpit", source_uid="u3", pool="domestic", company="C", title="X",
                      post_date=date(2026, 7, 1))
        p4 = Posting(source="jumpit", source_uid="u4", pool="domestic", company="D", title="X",
                      post_date=date(2026, 7, 1))
        p5 = Posting(source="jumpit", source_uid="u5", pool="domestic", company="E", title="X",
                      post_date=date(2026, 7, 1))
        p6 = Posting(source="jumpit", source_uid="u6", pool="domestic", company="F", title="X",
                      post_date=date(2026, 7, 1))
        seed.add_all([p1, p2, p3, p4, p5, p6])
        seed.commit()

        seed.add_all(
            [
                # p1: Python only -> apply
                PostingTech(posting_id=p1.id, skill_id=python.id),
                # p2: Python + Java -> missing Java -> near1
                PostingTech(posting_id=p2.id, skill_id=python.id),
                PostingTech(posting_id=p2.id, skill_id=java.id),
                # p3: Python + Spring -> missing Spring -> near1
                PostingTech(posting_id=p3.id, skill_id=python.id),
                PostingTech(posting_id=p3.id, skill_id=spring.id),
                # p4: Python + Java + AWS -> missing Java,AWS -> near2_3
                PostingTech(posting_id=p4.id, skill_id=python.id),
                PostingTech(posting_id=p4.id, skill_id=java.id),
                PostingTech(posting_id=p4.id, skill_id=aws.id),
                # p5: Python + Java + AWS + Docker -> missing 3 -> near2_3
                PostingTech(posting_id=p5.id, skill_id=python.id),
                PostingTech(posting_id=p5.id, skill_id=java.id),
                PostingTech(posting_id=p5.id, skill_id=aws.id),
                PostingTech(posting_id=p5.id, skill_id=docker.id),
                # p6: Java+AWS+Docker+Spring+Go (5, no python) -> missing 5 -> far
                PostingTech(posting_id=p6.id, skill_id=java.id),
                PostingTech(posting_id=p6.id, skill_id=aws.id),
                PostingTech(posting_id=p6.id, skill_id=docker.id),
                PostingTech(posting_id=p6.id, skill_id=spring.id),
                PostingTech(posting_id=p6.id, skill_id=go.id),
            ]
        )
        seed.commit()
        resume_id = resume.resume_id
        user_id = user.id

    def override_get_session() -> Iterator[Session]:
        with testing_session() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    test_client = TestClient(app)
    test_client.resume_id = resume_id  # type: ignore[attr-defined]
    test_client.token = create_access_token(str(user_id))  # type: ignore[attr-defined]
    yield test_client
    app.dependency_overrides.clear()


def test_skill_unlock_requires_resume_or_session(client: TestClient) -> None:
    resp = client.get("/api/v1/stats/skill-unlock", params={"pool": "domestic"})
    assert resp.status_code == 400


def test_skill_unlock_funnel_buckets(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.routers.match.is_token_blocklisted", lambda token: False)
    resp = client.get(
        "/api/v1/stats/skill-unlock",
        params={"pool": "domestic", "resume_id": client.resume_id},
        headers={"Authorization": f"Bearer {client.token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["funnel"] == {"apply": 1, "near1": 2, "near2_3": 2, "far": 1}


def test_skill_unlock_candidates_marginal_apply(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.routers.match.is_token_blocklisted", lambda token: False)
    resp = client.get(
        "/api/v1/stats/skill-unlock",
        params={"pool": "domestic", "resume_id": client.resume_id},
        headers={"Authorization": f"Bearer {client.token}"},
    )
    body = resp.json()
    candidates = {c["canonical"]: c for c in body["candidates"]}
    assert candidates["Java"]["marginal_apply"] == 1
    assert candidates["Java"]["req_count"] == 4
    assert candidates["Spring"]["marginal_apply"] == 1
    assert candidates["Spring"]["req_count"] == 2
    assert candidates["AWS"]["marginal_apply"] == 0
    assert candidates["AWS"]["req_count"] == 3
    # sorted by marginal_apply desc, then req_count desc
    order = [c["canonical"] for c in body["candidates"]]
    assert order.index("Java") < order.index("AWS")
    assert order.index("Spring") < order.index("AWS")
