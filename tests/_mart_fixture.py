"""load_mart 통합 테스트용 인메모리 mart/target 픽스처와 소형 taxonomy 상수."""

import sqlite3

TAXO = {
    "_meta": {"version": "test"},
    "language": {"Python": ["python", "파이썬"], "JavaScript": ["javascript", "js"]},
    "frontend": {"React": ["react", "리액트"]},
    "_ambiguous_llm_fallback": {
        "_comment": "일반명사 충돌",
        "Go": ["go", "golang"],
        "React": ["react"],
    },
}

CERTS = {
    "_comment": "테스트용 자격증 사전",
    "국가기술자격": {"정보처리기사": ["정보처리기사", "정처기"]},
}


def make_mart() -> sqlite3.Connection:
    """mart.db 스키마를 미러링한 인메모리 SQLite + 소형 픽스처 데이터."""
    m = sqlite3.connect(":memory:")
    m.row_factory = sqlite3.Row
    m.executescript(
        """
        CREATE TABLE fact_posting (
            posting_id TEXT PRIMARY KEY, source TEXT, company TEXT, title TEXT,
            post_date TEXT, close_date TEXT, career_min INTEGER, career_max INTEGER,
            region TEXT, industry TEXT, industry_method TEXT, seniority TEXT);
        CREATE TABLE fact_posting_tech (posting_id TEXT, tech TEXT);
        CREATE TABLE fact_posting_cert (posting_id TEXT, cert TEXT);
        CREATE TABLE fact_posting_category (posting_id TEXT, category TEXT);
        CREATE TABLE raw_posting (posting_id TEXT PRIMARY KEY, source TEXT, captured TEXT, json TEXT);
        """
    )
    m.execute(
        "INSERT INTO fact_posting VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        ("jumpit:111", "jumpit", "토스", "백엔드 개발자", "2026-07-01", None, 3, 7,
         "서울 강남구 논현로65길22", "금융", "rule", "Senior"),
    )
    m.execute(
        "INSERT INTO fact_posting VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        ("himalayas:222", "himalayas", "Acme", "Backend Engineer", "2026-06-15", None,
         None, None, "Remote", "Software", None, "Mid-level"),
    )
    m.executemany(
        "INSERT INTO fact_posting_tech VALUES (?,?)",
        [("jumpit:111", "Python"), ("jumpit:111", "AWS"), ("himalayas:222", "Python")],
    )
    m.execute("INSERT INTO fact_posting_cert VALUES (?,?)", ("jumpit:111", "정보처리기사"))
    m.executemany(
        "INSERT INTO fact_posting_category VALUES (?,?)",
        [("jumpit:111", "backend"), ("himalayas:222", "backend")],
    )
    m.execute(
        "INSERT INTO raw_posting VALUES (?,?,?,?)",
        ("jumpit:111", "jumpit", "2026-07-01T09:00:00+00:00", '{"salary": "6000"}'),
    )
    m.execute(
        "INSERT INTO raw_posting VALUES (?,?,?,?)",
        ("himalayas:222", "himalayas", None, '{"tags": ["remote"]}'),
    )
    m.commit()
    return m


def make_target():
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    from app.core.db import Base
    import app.models  # noqa: F401 - ensure all models are registered with Base

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return engine
