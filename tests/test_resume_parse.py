from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.db import Base, get_session
from app.main import app
from app.models.skill import Skill, SkillAlias


@pytest.fixture
def client_with_skill_dictionary() -> Iterator[TestClient]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)

    with testing_session() as seed:
        python = Skill(canonical="Python", category="language")
        react = Skill(canonical="React", category="frontend")
        seed.add_all([python, react])
        seed.flush()
        seed.add_all(
            [
                SkillAlias(skill_id=python.id, alias="파이썬", is_korean=True),
                SkillAlias(skill_id=react.id, alias="리액트", is_korean=True),
            ]
        )
        seed.commit()

    def override_get_session() -> Iterator[Session]:
        with testing_session() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_parse_resume_rejects_non_pdf_upload() -> None:
    client = TestClient(app)
    response = client.post(
        "/api/v1/resume/parse",
        files={"file": ("resume.pdf", b"Python AWS", "application/pdf")},
    )

    assert response.status_code == 415
    assert response.json()["detail"] == "unsupported media type"


def test_parse_resume_returns_skills_position_and_career(
    monkeypatch, client_with_skill_dictionary: TestClient
) -> None:
    from app.services import resume as resume_service

    monkeypatch.setattr(
        resume_service,
        "extract_pdf_text",
        lambda _: "Backend developer with 3-5 years using Python, AWS, and 리액트. Also used MysteryTool.",
    )

    response = client_with_skill_dictionary.post(
        "/api/v1/resume/parse",
        files={"file": ("resume.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )

    assert response.status_code == 200
    assert response.json() == {
        "skills": [
            {"canonical": "Python", "category": "language", "in_dict": True},
            {"canonical": "AWS", "category": "unknown", "in_dict": False},
            {"canonical": "React", "category": "frontend", "in_dict": True},
            {"canonical": "MysteryTool", "category": "unknown", "in_dict": False},
        ],
        "position": "backend",
        "career_min": 3,
        "career_max": 5,
    }


def test_extract_pdf_text_falls_back_to_pdftotext(monkeypatch) -> None:
    from app.services import resume as resume_service

    monkeypatch.setattr(resume_service, "extract_pdf_text_with_pypdf", lambda _: "")
    monkeypatch.setattr(resume_service, "extract_pdf_text_with_pdftotext", lambda _: "Python")

    assert resume_service.extract_pdf_text(b"%PDF-1.4 fake") == "Python"
