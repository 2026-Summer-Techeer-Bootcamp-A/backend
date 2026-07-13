import json
from collections.abc import Iterator
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.db import Base, get_session
from app.main import app
from app.models import (
    Cert,
    Concept,
    Posting,
    PostingCategory,
    PostingCert,
    PostingConcept,
    PostingTech,
    RawPosting,
    Resume,
    ResumeSkill,
    Skill,
    User,
)


@pytest.fixture
def client() -> Iterator[TestClient]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)

    today = date.today()

    with testing_session() as seed:
        python = Skill(canonical="python", category="language")
        react = Skill(canonical="react", category="framework")
        aws = Skill(canonical="aws", category="cloud")
        user = User(email="feed@example.com", password_hash="unused")
        user_without_resume = User(email="noresume@example.com", password_hash="unused")
        seed.add_all([python, react, aws, user, user_without_resume])
        seed.flush()

        resume = Resume(user_id=user.id, title="Resume", position="backend", pool="domestic")
        seed.add(resume)
        seed.commit()
        seed.add_all(
            [
                ResumeSkill(resume_id=resume.resume_id, skill_id=python.id),
                ResumeSkill(resume_id=resume.resume_id, skill_id=react.id),
            ]
        )

        # description_snippet은 Posting.description(JSON 섹션 문자열)에서 뽑는다.
        # 실제 포맷: [{"title": .., "text": ..}, ...] (scripts/enrich_postings.py가 채움)
        p1_desc_sections = json.dumps(
            [
                {"title": "소개", "text": "Python, Django 백엔드 개발자를\n   모집합니다."},
                {"title": "우대사항", "text": "우대사항: MSA 경험"},
            ],
            ensure_ascii=False,
        )

        p1 = Posting(
            source="wanted",
            source_uid="p1",
            pool="domestic",
            company="p1 company",
            title="p1 title",
            industry="IT서비스",
            region_city="서울",
            region_district="강남구",
            post_date=today,
            close_date=today + timedelta(days=10),
            career_min=3,
            career_max=7,
            response_rate=82.5,
            seniority_raw="시니어",
            description=p1_desc_sections,
            logo_url="https://static.example.com/logos/p1.png",
        )
        p2 = Posting(
            source="wanted",
            source_uid="p2",
            pool="domestic",
            company="p2 company",
            title="p2 title",
            post_date=today - timedelta(days=1),
            description=None,
            logo_url=None,
        )
        p3 = Posting(
            source="himalayas",
            source_uid="p3",
            pool="global",
            company="p3 company",
            title="p3 title",
            post_date=today - timedelta(days=3),
            # 손상된 JSON: 스니펫 파싱이 실패해도 피드 응답 자체가 죽으면 안 된다.
            description="{not valid json",
        )
        seed.add_all([p1, p2, p3])
        seed.commit()

        msa = Concept(name="MSA", category="architecture")
        cicd = Concept(name="CI/CD", category="process")
        infoproc = Cert(name="정보처리기사")
        seed.add_all([msa, cicd, infoproc])
        seed.flush()

        seed.add_all(
            [
                PostingCategory(posting_id=p1.id, category="백엔드"),
                PostingCategory(posting_id=p2.id, category="프론트엔드"),
                PostingTech(posting_id=p1.id, skill_id=python.id),
                PostingTech(posting_id=p1.id, skill_id=aws.id),
                PostingTech(posting_id=p2.id, skill_id=react.id),
                PostingConcept(posting_id=p1.id, concept_id=msa.id),
                PostingConcept(posting_id=p1.id, concept_id=cicd.id),
                PostingCert(posting_id=p1.id, cert_id=infoproc.id),
                RawPosting(posting_id=p1.id, payload={"url": "https://example.com/p1"}),
                RawPosting(posting_id=p2.id, payload={"url": "https://example.com/p2"}),
                RawPosting(posting_id=p3.id, payload={"url": "https://example.com/p3"}),
            ]
        )
        seed.commit()

    def override_get_session() -> Iterator[Session]:
        with testing_session() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_feed_anonymous_returns_cards_without_match(client):
    res = client.get("/api/v1/feed/postings", params={"page": 1, "page_size": 20})
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 3
    first = body["items"][0]
    assert first["title"] == "p1 title"  # post_date 내림차순
    assert first["industry"] == "IT서비스"
    assert first["region"] == "서울 강남구"
    assert first["categories"] == ["백엔드"]
    assert sorted(first["skills"]) == ["aws", "python"]
    assert sorted(first["concepts"]) == ["CI/CD", "MSA"]
    assert first["certs"] == ["정보처리기사"]
    assert first["seniority"] == "시니어"
    # 줄바꿈은 보존되고(불릿 구조 유지), 줄 내부의 연속 공백만 정리된다.
    assert first["description_snippet"] == (
        "Python, Django 백엔드 개발자를\n모집합니다.\n우대사항: MSA 경험"
    )
    assert first["logo_url"] == "https://static.example.com/logos/p1.png"
    assert first["match"] is None
    assert first["career_min"] == 3
    assert first["career_max"] == 7
    assert first["response_rate"] == 82.5

    second = body["items"][1]  # p2: career_min/max/response_rate 미지정, 개념/자격증/설명/로고 없음
    assert second["career_min"] is None
    assert second["career_max"] is None
    assert second["response_rate"] is None
    assert second["concepts"] == []
    assert second["certs"] == []
    assert second["seniority"] is None
    assert second["description_snippet"] is None  # description이 없음(NULL)
    assert second["logo_url"] is None

    third = body["items"][2]  # p3: description이 손상된 JSON -> 파싱 실패해도 죽지 않고 None
    assert third["title"] == "p3 title"
    assert third["description_snippet"] is None


def test_feed_authed_includes_match(client, monkeypatch):
    monkeypatch.setattr("app.routers.match.is_token_blocklisted", lambda token: False)
    from app.core.security import create_access_token

    token = create_access_token(1)
    res = client.get(
        "/api/v1/feed/postings",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    first = res.json()["items"][0]  # p1: skills=[python, aws], 보유=[python, react]
    assert first["match"]["rate"] == 50.0
    assert first["match"]["owned_skills"] == ["python"]
    assert first["match"]["missing_skills"] == ["aws"]


def test_feed_posting_without_skills_has_null_match_when_authed(client, monkeypatch):
    monkeypatch.setattr("app.routers.match.is_token_blocklisted", lambda token: False)
    from app.core.security import create_access_token

    token = create_access_token(1)
    res = client.get("/api/v1/feed/postings", headers={"Authorization": f"Bearer {token}"})
    p3 = [i for i in res.json()["items"] if i["skills"] == []][0]
    assert p3["match"] is None


def test_feed_pool_filter(client):
    res = client.get("/api/v1/feed/postings", params={"pool": "global"})
    assert res.json()["total"] == 1


def test_feed_category_filter(client):
    res = client.get("/api/v1/feed/postings", params={"category": "백엔드"})
    body = res.json()
    assert body["total"] == 1
    assert body["items"][0]["categories"] == ["백엔드"]


def test_feed_pagination(client):
    res = client.get("/api/v1/feed/postings", params={"page": 2, "page_size": 1})
    body = res.json()
    assert body["total"] == 3
    assert len(body["items"]) == 1
    assert body["page"] == 2


def test_feed_district_filter(client):
    res = client.get("/api/v1/feed/postings", params={"district": "강남"})
    body = res.json()
    assert body["total"] == 1
    assert body["items"][0]["title"] == "p1 title"

    res = client.get("/api/v1/feed/postings", params={"district": "해운대"})
    assert res.json()["total"] == 0


def test_feed_deadline_within_days_filter(client):
    # p1만 close_date(오늘+10일)를 가진다
    res = client.get("/api/v1/feed/postings", params={"deadline_within_days": 15})
    body = res.json()
    assert body["total"] == 1
    assert body["items"][0]["title"] == "p1 title"

    res = client.get("/api/v1/feed/postings", params={"deadline_within_days": 5})
    assert res.json()["total"] == 0


def test_feed_min_match_filters_by_match_rate(client, monkeypatch):
    monkeypatch.setattr("app.routers.match.is_token_blocklisted", lambda token: False)
    from app.core.security import create_access_token

    token = create_access_token(1)
    headers = {"Authorization": f"Bearer {token}"}

    # 보유=[python, react]. p1=50%(python/aws), p2=100%(react), p3=0%(스킬 없음)
    res = client.get("/api/v1/feed/postings", params={"min_match": 60}, headers=headers)
    body = res.json()
    assert res.status_code == 200
    assert body["total"] == 1
    assert body["items"][0]["title"] == "p2 title"
    assert body["items"][0]["match"]["rate"] == 100.0

    res = client.get("/api/v1/feed/postings", params={"min_match": 50}, headers=headers)
    body = res.json()
    assert body["total"] == 2
    assert [i["title"] for i in body["items"]] == ["p1 title", "p2 title"]


def test_feed_min_match_pagination_slices_after_filtering(client, monkeypatch):
    monkeypatch.setattr("app.routers.match.is_token_blocklisted", lambda token: False)
    from app.core.security import create_access_token

    token = create_access_token(1)
    res = client.get(
        "/api/v1/feed/postings",
        params={"min_match": 50, "page": 2, "page_size": 1},
        headers={"Authorization": f"Bearer {token}"},
    )
    body = res.json()
    assert body["total"] == 2
    assert len(body["items"]) == 1
    assert body["items"][0]["title"] == "p2 title"


def test_feed_min_match_anonymous_rejected(client):
    res = client.get("/api/v1/feed/postings", params={"min_match": 50})
    assert res.status_code == 422


def test_feed_min_match_without_resume_rejected(client, monkeypatch):
    monkeypatch.setattr("app.routers.match.is_token_blocklisted", lambda token: False)
    from app.core.security import create_access_token

    token = create_access_token(2)  # noresume@example.com
    res = client.get(
        "/api/v1/feed/postings",
        params={"min_match": 50},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 422


def test_feed_min_match_range_validated(client):
    res = client.get("/api/v1/feed/postings", params={"min_match": 101})
    assert res.status_code == 422
    res = client.get("/api/v1/feed/postings", params={"min_match": -1})
    assert res.status_code == 422


def test_feed_sort_match_orders_by_match_rate_desc_for_authed_user_with_resume(client, monkeypatch):
    monkeypatch.setattr("app.routers.match.is_token_blocklisted", lambda token: False)
    from app.core.security import create_access_token

    token = create_access_token(1)
    # 보유=[python, react]. p1=50%(python/aws), p2=100%(react), p3=0%(스킬 없음 -> match=None)
    res = client.get(
        "/api/v1/feed/postings",
        params={"sort": "match"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    body = res.json()
    titles = [item["title"] for item in body["items"]]
    assert titles == ["p2 title", "p1 title", "p3 title"]
    assert body["items"][0]["match"]["rate"] == 100.0
    assert body["items"][1]["match"]["rate"] == 50.0
    assert body["items"][2]["match"] is None


def test_feed_sort_match_without_auth_falls_back_to_latest(client):
    res = client.get("/api/v1/feed/postings", params={"sort": "match"})
    assert res.status_code == 200
    body = res.json()
    # 폴백: 인증/이력서 컨텍스트가 없으면 에러 없이 최신순(post_date desc)을 유지한다.
    titles = [item["title"] for item in body["items"]]
    assert titles == ["p1 title", "p2 title", "p3 title"]
    assert all(item["match"] is None for item in body["items"])


def test_feed_sort_match_without_resume_falls_back_to_latest(client, monkeypatch):
    monkeypatch.setattr("app.routers.match.is_token_blocklisted", lambda token: False)
    from app.core.security import create_access_token

    token = create_access_token(2)  # noresume@example.com
    res = client.get(
        "/api/v1/feed/postings",
        params={"sort": "match"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    titles = [item["title"] for item in res.json()["items"]]
    assert titles == ["p1 title", "p2 title", "p3 title"]


def test_feed_industry_filter_partial_match(client):
    res = client.get("/api/v1/feed/postings", params={"industry": "IT"})
    body = res.json()
    assert body["total"] == 1
    assert body["items"][0]["title"] == "p1 title"

    res = client.get("/api/v1/feed/postings", params={"industry": "제조"})
    assert res.json()["total"] == 0


def test_feed_skills_filter_matches_any(client):
    # p1: aws+python, p2: react. skills=react,aws는 둘 다 매칭(OR).
    res = client.get("/api/v1/feed/postings", params={"skills": "react,aws"})
    body = res.json()
    assert body["total"] == 2
    assert {item["title"] for item in body["items"]} == {"p1 title", "p2 title"}

    res = client.get("/api/v1/feed/postings", params={"skills": "react"})
    body = res.json()
    assert body["total"] == 1
    assert body["items"][0]["title"] == "p2 title"


def test_build_description_snippet_edge_cases():
    from app.crud.feed import _build_description_snippet

    # None/빈 문자열/빈 리스트/손상된 JSON -> None (피드가 죽으면 안 된다)
    assert _build_description_snippet(None) is None
    assert _build_description_snippet("") is None
    assert _build_description_snippet("[]") is None
    assert _build_description_snippet("not valid json") is None
    assert _build_description_snippet("{}") is None  # 리스트가 아님
    assert _build_description_snippet(json.dumps([{"title": "소개"}])) is None  # text 없음
    assert _build_description_snippet(json.dumps([{"title": "소개", "text": "   "}])) is None

    # 첫 섹션이 짧으면 다음 섹션까지 이어붙여 스니펫을 채운다.
    # 줄 내부의 연속 공백/탭은 정리되지만 줄바꿈 자체는 보존되어 섹션은 "\n"으로 이어진다.
    short_sections = json.dumps(
        [
            {"title": "소개", "text": "짧은 소개\n   문구입니다."},
            {"title": "주요 업무", "text": "업무 내용입니다."},
        ],
        ensure_ascii=False,
    )
    assert _build_description_snippet(short_sections) == "짧은 소개\n문구입니다.\n업무 내용입니다."

    # 불릿마다 줄바꿈이 있는 실제 포맷: 개행이 유지되어 불릿 구조가 살아 있어야 한다.
    bulleted = json.dumps(
        [{"title": "주요 업무", "text": "• 항목1\n• 항목2\n• 항목3"}],
        ensure_ascii=False,
    )
    assert _build_description_snippet(bulleted) == "• 항목1\n• 항목2\n• 항목3"

    # 한 줄 내부의 연속 공백/탭만 하나로 정리된다 (줄바꿈과는 별개)
    inline_whitespace = json.dumps([{"title": "소개", "text": "a   b\tc"}], ensure_ascii=False)
    assert _build_description_snippet(inline_whitespace) == "a b c"

    # 첫 섹션이 충분히 길면 두 번째 섹션은 합치지 않는다
    long_first = "가" * 100
    long_sections = json.dumps(
        [{"title": "소개", "text": long_first}, {"title": "주요 업무", "text": "안 보여야 함"}],
        ensure_ascii=False,
    )
    assert _build_description_snippet(long_sections) == long_first

    # 300자를 넘으면 잘리고 말줄임표가 붙는다
    very_long = json.dumps([{"title": "소개", "text": "나" * 350}], ensure_ascii=False)
    snippet = _build_description_snippet(very_long)
    assert snippet == ("나" * 300) + "…"
    assert len(snippet) == 301
