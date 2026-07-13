import json

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.db import Base
from app.crud.posting import get_posting_detail
from app.models import Posting


def _get_detail(*, source: str, description: list[dict], title: str = "백엔드 개발자 채용") -> dict:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        posting = Posting(
            source=source,
            source_uid=f"{source}-1",
            pool="domestic",
            company="테스트 회사",
            title=title,
            description=json.dumps(description, ensure_ascii=False),
        )
        session.add(posting)
        session.commit()
        posting_id = posting.id

    with Session(engine) as session:
        return get_posting_detail(session, posting_id=posting_id)


def test_jobkorea_flat_description_is_normalized_when_detail_is_requested():
    detail = _get_detail(
        source="jobkorea",
        description=[
            {
                "title": "채용 공고 원문",
                "text": (
                    "잡코리아 회원가입 로그인 채용정보 "
                    "담당업무 API 개발 및 서비스 운영 "
                    "자격요건 Python 개발 경험 "
                    "우대사항 FastAPI 경험 "
                    "기업정보 회사 주소와 매출 정보"
                ),
            }
        ],
    )

    assert detail["desc_sections"] == [
        {"title": "주요 업무", "text": "API 개발 및 서비스 운영"},
        {"title": "자격 요건", "text": "Python 개발 경험"},
        {"title": "우대 사항", "text": "FastAPI 경험"},
    ]


def test_structured_jobkorea_description_is_not_changed():
    sections = [
        {"title": "주요 업무", "text": "API 개발"},
        {"title": "자격 요건", "text": "Python 경험"},
    ]

    detail = _get_detail(source="jobkorea", description=sections)

    assert detail["desc_sections"] == sections


def test_non_jobkorea_description_is_not_changed():
    sections = [{"title": "채용 공고 원문", "text": "담당업무 API 개발 자격요건 Python 경험"}]

    detail = _get_detail(source="wanted", description=sections)

    assert detail["desc_sections"] == sections
