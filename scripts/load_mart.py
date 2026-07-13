"""mart.db(SQLite) → Postgres 적재(ETL). 순수 변환 함수 계층.

호스트에서 dev-compose Postgres를 대상으로 실행:
    DATABASE_URL=postgresql+psycopg://appuser:change-me@localhost:5432/appdb \
        python -m scripts.load_mart
"""

import sqlite3
import struct
from collections.abc import Iterable
from datetime import date, datetime

from sqlalchemy import select, text

from app.core.db import Base
from app.models import (
    Cert,
    Concept,
    JobCategory,
    Posting,
    PostingCategory,
    PostingCert,
    PostingConcept,
    PostingEmbedding,
    PostingTech,
    RawPosting,
    Skill,
    SkillAlias,
)

DOMESTIC_SOURCES = {"wanted", "jumpit", "jobkorea"}


def split_posting_id(posting_id: str) -> tuple[str, str]:
    """"jumpit:1268163382" → ("jumpit", "1268163382"). 첫 ':'만 분리."""
    source, sep, uid = posting_id.partition(":")
    if not sep:
        raise ValueError(f"posting_id missing ':' separator: {posting_id!r}")
    return source, uid


def derive_pool(source: str) -> str:
    return "domestic" if source in DOMESTIC_SOURCES else "global"


def region_country_for(source: str) -> str | None:
    return "KR" if source in DOMESTIC_SOURCES else None


def parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None


def has_hangul(text: str) -> bool:
    return any("가" <= ch <= "힣" for ch in text)


AMBIGUOUS_KEY = "_ambiguous_llm_fallback"


def build_skill_rows(
    taxonomy: dict, extra_techs: Iterable[str]
) -> tuple[list[dict], list[dict]]:
    skills: dict[str, dict] = {}
    aliases: list[dict] = []
    seen_alias: set[str] = set()

    def add_aliases(canonical: str, alias_list) -> None:
        if not isinstance(alias_list, list):
            return
        for a in alias_list:
            key = a.lower()
            if key in seen_alias:
                continue
            seen_alias.add(key)
            aliases.append(
                {"canonical": canonical, "alias": a, "is_korean": has_hangul(a)}
            )

    for category, entries in taxonomy.items():
        if category.startswith("_") or not isinstance(entries, dict):
            continue
        for canonical, alias_list in entries.items():
            if canonical not in skills:
                skills[canonical] = {
                    "canonical": canonical,
                    "category": category,
                    "is_ambiguous": False,
                }
            add_aliases(canonical, alias_list)

    for canonical, alias_list in taxonomy.get(AMBIGUOUS_KEY, {}).items():
        if canonical.startswith("_") or not isinstance(alias_list, list):
            continue
        if canonical in skills:
            skills[canonical]["is_ambiguous"] = True
        else:
            skills[canonical] = {
                "canonical": canonical,
                "category": "ambiguous",
                "is_ambiguous": True,
            }
        add_aliases(canonical, alias_list)

    for tech in extra_techs:
        if tech and tech not in skills:
            skills[tech] = {
                "canonical": tech,
                "category": "uncategorized",
                "is_ambiguous": False,
            }

    return list(skills.values()), aliases


def build_cert_names(cert_taxonomy: dict, extra_certs: Iterable[str]) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for category, entries in cert_taxonomy.items():
        if category.startswith("_") or not isinstance(entries, dict):
            continue
        for name in entries:
            if name not in seen:
                seen.add(name)
                names.append(name)
    for name in extra_certs:
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    return names


def build_category_names(mart_categories: Iterable[str]) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for name in mart_categories:
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    return names


def build_concept_rows(
    concepts_taxonomy: dict, extra_concepts: Iterable[str]
) -> list[dict]:
    """concepts_taxonomy.json → concept 행. 상위분류(category)를 함께 기록.

    최상위 키는 개념 분류(아키텍처·확장성 등), 각 값은 {개념명: 별칭리스트}.
    '_'로 시작하는 키(_meta, _excluded_soft_skills 등)는 건너뛴다.
    """
    concepts: dict[str, dict] = {}
    for category, entries in concepts_taxonomy.items():
        if category.startswith("_") or not isinstance(entries, dict):
            continue
        for name in entries:
            if name.startswith("_"):
                continue
            concepts.setdefault(name, {"name": name, "category": category})
    for name in extra_concepts:
        if name and name not in concepts:
            concepts[name] = {"name": name, "category": "uncategorized"}
    return list(concepts.values())


def open_mart(path: str) -> sqlite3.Connection:
    """mart.db 파일을 열고 Row 팩토리를 설정해 반환."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_schema(engine) -> None:
    """대상 DB에 전체 ORM 스키마를 생성(idempotent)."""
    Base.metadata.create_all(bind=engine)


def wipe(conn) -> None:
    """대상 DB의 모든 애플리케이션 테이블을 비운다(스키마 유지)."""
    if conn.dialect.name == "postgresql":
        tables = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
        conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    else:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(table.delete())


def _insert_chunked(conn, table, rows: list[dict], size: int = 5000) -> None:
    """행 리스트를 size별로 청크로 나눠 테이블에 삽입."""
    for i in range(0, len(rows), size):
        conn.execute(table.insert(), rows[i : i + size])


def distinct_techs(mart: sqlite3.Connection) -> list[str]:
    """mart에서 모든 고유한 기술 목록을 추출."""
    return [r[0] for r in mart.execute("SELECT DISTINCT tech FROM fact_posting_tech")]


def distinct_certs(mart: sqlite3.Connection) -> list[str]:
    """mart에서 모든 고유한 자격증 목록을 추출."""
    return [r[0] for r in mart.execute("SELECT DISTINCT cert FROM fact_posting_cert")]


def distinct_categories(mart: sqlite3.Connection) -> list[str]:
    """mart에서 모든 고유한 카테고리 목록을 추출."""
    return [
        r[0] for r in mart.execute("SELECT DISTINCT category FROM fact_posting_category")
    ]


def distinct_concepts(mart: sqlite3.Connection) -> list[str]:
    """mart에서 모든 고유한 개념 목록을 추출."""
    return [
        r[0] for r in mart.execute("SELECT DISTINCT concept FROM fact_posting_concept")
    ]


def seed_dicts(
    conn,
    skill_rows: list[dict],
    alias_rows: list[dict],
    cert_names: list[str],
    category_names: list[str],
) -> tuple[dict[str, int], dict[str, int]]:
    _insert_chunked(conn, Skill.__table__, skill_rows)
    skill_id = {
        row.canonical: row.id
        for row in conn.execute(select(Skill.id, Skill.canonical))
    }
    alias_params = [
        {
            "skill_id": skill_id[a["canonical"]],
            "alias": a["alias"],
            "is_korean": a["is_korean"],
        }
        for a in alias_rows
        if a["canonical"] in skill_id
    ]
    _insert_chunked(conn, SkillAlias.__table__, alias_params)

    _insert_chunked(conn, Cert.__table__, [{"name": n} for n in cert_names])
    cert_id = {row.name: row.id for row in conn.execute(select(Cert.id, Cert.name))}

    _insert_chunked(
        conn,
        JobCategory.__table__,
        [{"name": n, "is_tech": False} for n in category_names],
    )
    return skill_id, cert_id


def seed_concepts(conn, concept_rows: list[dict]) -> dict[str, int]:
    """concept 마스터를 적재하고 name -> id 맵을 반환."""
    _insert_chunked(conn, Concept.__table__, concept_rows)
    return {
        row.name: row.id for row in conn.execute(select(Concept.id, Concept.name))
    }


def load_postings(conn, mart, limit: int | None = None) -> dict[str, int]:
    """fact_posting을 posting으로 변환·적재, "source:uid" -> posting.id 맵 반환."""
    query = (
        "SELECT posting_id, source, company, title, post_date, close_date, "
        "career_min, career_max, region, industry, seniority FROM fact_posting"
    )
    if limit is not None:
        query += f" LIMIT {int(limit)}"

    rows: list[dict] = []
    for r in mart.execute(query):
        source, uid = split_posting_id(r["posting_id"])
        rows.append(
            {
                "source": source,
                "source_uid": uid,
                "pool": derive_pool(source),
                "company": r["company"],
                "title": r["title"] or "",
                "post_date": parse_date(r["post_date"]),
                "close_date": parse_date(r["close_date"]),
                "career_min": r["career_min"],
                "career_max": r["career_max"],
                "seniority_raw": r["seniority"],
                "region_country": region_country_for(source),
                "region_city": r["region"],
                "industry": r["industry"],
            }
        )
    _insert_chunked(conn, Posting.__table__, rows)

    return {
        f"{source}:{uid}": pid
        for pid, source, uid in conn.execute(
            select(Posting.id, Posting.source, Posting.source_uid)
        )
    }


def _load_link(
    conn,
    mart,
    table,
    id_col: str,
    query: str,
    posting_ids: dict[str, int],
    name_ids: dict[str, int],
) -> int:
    """(posting_id, name) 마트 행을 (posting.id, name.id) 링크 행으로 적재.

    posting_ids/name_ids에 없는 항목은 건너뛴다. (posting.id, name.id) 중복 제거.
    """
    seen: set[tuple[int, int]] = set()
    rows: list[dict] = []
    for pid_str, name in mart.execute(query):
        tid = posting_ids.get(pid_str)
        nid = name_ids.get(name)
        if tid is None or nid is None:
            continue
        key = (tid, nid)
        if key in seen:
            continue
        seen.add(key)
        rows.append({"posting_id": tid, id_col: nid})
    _insert_chunked(conn, table, rows)
    return len(rows)


def load_posting_tech(conn, mart, posting_ids, skill_ids) -> int:
    return _load_link(
        conn, mart, PostingTech.__table__, "skill_id",
        "SELECT posting_id, tech FROM fact_posting_tech", posting_ids, skill_ids,
    )


def load_posting_cert(conn, mart, posting_ids, cert_ids) -> int:
    return _load_link(
        conn, mart, PostingCert.__table__, "cert_id",
        "SELECT posting_id, cert FROM fact_posting_cert", posting_ids, cert_ids,
    )


def load_posting_concept(conn, mart, posting_ids, concept_ids) -> int:
    return _load_link(
        conn, mart, PostingConcept.__table__, "concept_id",
        "SELECT posting_id, concept FROM fact_posting_concept", posting_ids, concept_ids,
    )


def load_posting_category(conn, mart, posting_ids) -> int:
    """category는 마스터 FK가 아닌 문자열(String(64)). 64자로 절단·중복 제거."""
    seen: set[tuple[int, str]] = set()
    rows: list[dict] = []
    for pid_str, category in mart.execute(
        "SELECT posting_id, category FROM fact_posting_category"
    ):
        tid = posting_ids.get(pid_str)
        if tid is None or not category:
            continue
        cat = category[:64]
        key = (tid, cat)
        if key in seen:
            continue
        seen.add(key)
        rows.append({"posting_id": tid, "category": cat})
    _insert_chunked(conn, PostingCategory.__table__, rows)
    return len(rows)


def load_raw_postings(conn, mart, posting_ids) -> int:
    """raw_posting 원본 JSON을 적재(용량 큼, 기본 제외). payload는 JSON 문자열 파싱."""
    import json as _json

    rows: list[dict] = []
    for r in mart.execute(
        "SELECT posting_id, captured, json FROM raw_posting"
    ):
        tid = posting_ids.get(r["posting_id"])
        if tid is None:
            continue
        try:
            payload = _json.loads(r["json"]) if r["json"] else {}
        except (ValueError, TypeError):
            payload = {"_raw": r["json"]}
        rows.append(
            {
                "posting_id": tid,
                "payload": payload,
                "captured_at": parse_datetime(r["captured"]),
            }
        )
    _insert_chunked(conn, RawPosting.__table__, rows, size=2000)
    return len(rows)


def load_embeddings(engine, emb_path: str, posting_ids: dict[str, int]) -> int:
    """embeddings.db(float32 BLOB)를 스트리밍하며 pgvector 컬럼에 청크 적재.

    전량(56.5만)을 메모리에 올리지 않도록 배치마다 flush. vec은 little-endian float32.
    """
    src = sqlite3.connect(emb_path)
    total = 0
    batch: list[dict] = []

    def flush() -> None:
        nonlocal total, batch
        if not batch:
            return
        with engine.begin() as c:
            c.execute(PostingEmbedding.__table__.insert(), batch)
        total += len(batch)
        batch = []

    for pid_str, model, dim, vec in src.execute(
        "SELECT posting_id, model, dim, vec FROM posting_embedding"
    ):
        tid = posting_ids.get(pid_str)
        if tid is None:
            continue
        floats = list(struct.unpack(f"<{int(dim)}f", vec))
        batch.append({"id": tid, "embedding": floats, "model": model})
        if len(batch) >= 1000:
            flush()
    flush()
    src.close()
    return total


def main() -> None:
    import argparse
    import json
    import os

    from sqlalchemy import create_engine

    from app.core.config import settings

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    default_taxo = os.path.normpath(
        os.path.join(repo_root, "..", "data-collector-script")
    )

    p = argparse.ArgumentParser(description="mart.db + embeddings.db → Postgres 적재")
    p.add_argument("--mart", required=True, help="mart.db 경로")
    p.add_argument("--embeddings", default=None, help="embeddings.db 경로(생략 시 벡터 미적재)")
    p.add_argument("--taxonomy-dir", default=default_taxo, help="taxonomy JSON 디렉터리")
    p.add_argument("--limit", type=int, default=None, help="공고 수 제한(디버그)")
    p.add_argument("--with-raw", action="store_true", help="raw_posting 원본 JSON도 적재")
    p.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL", settings.database_url),
        help="대상 Postgres URL(기본: env DATABASE_URL 또는 settings)",
    )
    args = p.parse_args()

    taxo = json.load(open(os.path.join(args.taxonomy_dir, "taxonomy_v2.json")))
    certs = json.load(open(os.path.join(args.taxonomy_dir, "certs_taxonomy.json")))
    concepts_taxo = json.load(
        open(os.path.join(args.taxonomy_dir, "concepts_taxonomy.json"))
    )

    mart = open_mart(args.mart)
    engine = create_engine(args.database_url)
    ensure_schema(engine)

    skill_rows, alias_rows = build_skill_rows(taxo, distinct_techs(mart))
    cert_names = build_cert_names(certs, distinct_certs(mart))
    category_names = build_category_names(distinct_categories(mart))
    concept_rows = build_concept_rows(concepts_taxo, distinct_concepts(mart))

    print(
        f"[seed] skills={len(skill_rows)} aliases={len(alias_rows)} "
        f"certs={len(cert_names)} categories={len(category_names)} "
        f"concepts={len(concept_rows)}"
    )

    with engine.begin() as conn:
        wipe(conn)
        skill_id, cert_id = seed_dicts(
            conn, skill_rows, alias_rows, cert_names, category_names
        )
        concept_id = seed_concepts(conn, concept_rows)
        posting_ids = load_postings(conn, mart, args.limit)
        print(f"[postings] {len(posting_ids)}")
        n_tech = load_posting_tech(conn, mart, posting_ids, skill_id)
        n_cert = load_posting_cert(conn, mart, posting_ids, cert_id)
        n_cat = load_posting_category(conn, mart, posting_ids)
        n_concept = load_posting_concept(conn, mart, posting_ids, concept_id)
        print(
            f"[links] tech={n_tech} cert={n_cert} category={n_cat} concept={n_concept}"
        )
        if args.with_raw:
            n_raw = load_raw_postings(conn, mart, posting_ids)
            print(f"[raw] {n_raw}")

    if args.embeddings:
        n_emb = load_embeddings(engine, args.embeddings, posting_ids)
        print(f"[embeddings] {n_emb}")

    print("[done]")


if __name__ == "__main__":
    main()
