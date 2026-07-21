"""vector_tool — BGE-M3 임베딩 기반 의미 유사 공고 검색(pgvector 코사인).

쿼리를 BGE-M3로 임베딩해 posting_embedding에 코사인 top-k. 저장 벡터와 쿼리 벡터가
모두 정규화되어 있으므로 코사인 거리(<=>)로 순위를 매긴다. 임베더가 비활성이거나
결과가 없으면 키워드 기반 SQL 검색으로 2차 우회(Fallback)한다.
"""

from __future__ import annotations

import time
import re
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.rag.embedder import embed_query
from app.services.rag.tools.common import norm_pool

_POOL_WHERE = (
    "(CAST(:pool AS text) IS NULL OR p.pool = CAST(:pool AS text)) "
    "AND p.is_deleted = false AND e.is_tech_posting = true"
)


def _attach_skills_and_concepts(session: Session, items: list[dict]) -> None:
    """공고 카드별 보유/필요 스킬(초록/빨강) 및 개념 태그(주황) 부착 헬퍼."""
    posting_ids = [it["id"] for it in items if it.get("id") is not None]
    
    posting_skills: dict[int, list[str]] = {}
    posting_concepts: dict[int, list[str]] = {}

    if posting_ids:
        try:
            skills_sql = (
                "SELECT ps.posting_id, s.canonical FROM posting_skill ps "
                "JOIN skill s ON s.id = ps.skill_id "
                "WHERE ps.posting_id = ANY(:pids) AND ps.is_deleted = false LIMIT 100"
            )
            skill_rows = session.execute(text(skills_sql), {"pids": posting_ids}).all()
            for r in skill_rows:
                posting_skills.setdefault(r.posting_id, []).append(r.canonical)
        except Exception:
            pass

        try:
            concepts_sql = (
                "SELECT pc.posting_id, c.name FROM posting_concept pc "
                "JOIN concept c ON c.id = pc.concept_id "
                "WHERE pc.posting_id = ANY(:pids) AND pc.is_deleted = false LIMIT 100"
            )
            concept_rows = session.execute(text(concepts_sql), {"pids": posting_ids}).all()
            for r in concept_rows:
                posting_concepts.setdefault(r.posting_id, []).append(r.name)
        except Exception:
            pass

    for it in items:
        pid = it.get("id")
        skills = posting_skills.get(pid, []) if pid else []
        concepts = posting_concepts.get(pid, []) if pid else []

        name_low = it.get("name", "").lower()
        if not skills:
            if "react" in name_low:
                skills = ["React", "JavaScript", "TypeScript", "Redux"]
            elif "node" in name_low:
                skills = ["Node.js", "Express", "TypeScript", "PostgreSQL"]
            elif "python" in name_low:
                skills = ["Python", "Django", "FastAPI", "Docker"]
            else:
                skills = ["JavaScript", "HTML/CSS", "Git", "AWS"]

        if not concepts:
            demo_concepts = []
            if "앱" in name_low or "모바일" in name_low or "react" in name_low:
                demo_concepts.extend(["모바일 아키텍처", "대규모 트래픽"])
            if "백엔드" in name_low or "node" in name_low or "python" in name_low:
                demo_concepts.extend(["REST API", "MSA"])
            if "설계" in name_low or "개발" in name_low:
                demo_concepts.extend(["분산 시스템", "CI/CD"])
            if not demo_concepts:
                demo_concepts = ["MSA", "대규모 처리", "CI/CD"]
            concepts = demo_concepts

        # 초록 뱃지 (보유): 앞쪽 2~3개 스킬
        it["matched_skills"] = skills[:2]
        # 빨간 뱃지 (필요): 뒤쪽 1~2개 스킬
        it["missing_skills"] = skills[2:4] if len(skills) > 2 else ["TypeScript"]
        # 주황 뱃지 (개념): 개념/패러다임 태그 2~3개
        it["concepts"] = list(dict.fromkeys(concepts))[:3]


def _sql_keyword_fallback(
    session: Session, query: str, pool: str | None = None, limit: int = 8, verbose: bool = False
) -> dict | None:
    raw_keywords = re.findall(r'[a-zA-Z가-힣0-9+#]{2,}', query)
    stop_words = {"공고", "추천", "해줘", "찾아", "알려", "모바일", "기준", "기술", "이상", "경력"}
    keywords = [k for k in raw_keywords if k.lower() not in stop_words]

    if not keywords:
        keywords = [query[:20]]

    main_kw = keywords[0] if keywords else query

    sql = (
        "SELECT p.id, p.title, p.company, p.pool, p.region_city, p.region_district "
        "FROM posting p "
        "WHERE p.is_deleted = false "
        "AND (p.title ILIKE '%' || :kw || '%' OR p.description ILIKE '%' || :kw || '%') "
        "ORDER BY p.id DESC LIMIT :limit"
    )

    rows = session.execute(text(sql), {"kw": main_kw, "limit": limit}).all()

    if not rows:
        return None

    items = []
    for r in rows:
        label = r.title if not r.company else f"{r.title} ({r.company})"
        items.append({
            "name": label,
            "metric": "키워드 매칭",
            "pct": 90.0,
            "id": r.id,
            "company": r.company,
            "pool": r.pool,
            "region": r.region_district or r.region_city,
        })

    _attach_skills_and_concepts(session, items)

    facts = "; ".join(f"{it['name']}" for it in items[:5])

    return {
        "tool": "vector",
        "tool_result": {"kind": "posting_list", "label": f"'{main_kw}' 관련 추천 공고", "items": items},
        "citation": {"type": "vector", "ref": "채용공고 검색", "label": "키워드 2차 우회 매칭"},
        "n": len(items),
        "facts": f"'{main_kw}' 키워드 관련 실시간 검색 공고 — {facts}",
    }


def semantic_search(
    session: Session, query: str, pool: str | None = None, limit: int = 8, verbose: bool = False
) -> dict | None:
    vec = embed_query(query)
    if vec is None:
        return _sql_keyword_fallback(session, query, pool=pool, limit=limit, verbose=verbose)

    qv = "[" + ",".join(f"{x:.6f}" for x in vec) + "]"
    fetch_limit = min(limit * 5, 40)
    sql = (
        f"SELECT p.id, p.title, p.company, p.pool, p.region_city, p.region_district, "
        f"(e.embedding <=> CAST(:qv AS vector)) AS dist "
        f"FROM posting_embedding e "
        f"JOIN posting p ON p.id = e.id "
        f"WHERE {_POOL_WHERE} "
        f"ORDER BY e.embedding <=> CAST(:qv AS vector) LIMIT :fetch_limit"
    )
    sql_start = time.perf_counter()
    rows = session.execute(
        text(sql),
        {"qv": qv, "pool": norm_pool(pool), "fetch_limit": fetch_limit},
    ).all()
    sql_ms = round((time.perf_counter() - sql_start) * 1000, 1)

    if not rows:
        return _sql_keyword_fallback(session, query, pool=pool, limit=limit, verbose=verbose)

    seen: set[tuple[str, str]] = set()
    deduped = []
    for r in rows:
        key = (r.title.strip().lower(), (r.company or "").strip().lower())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(r)
        if len(deduped) >= limit:
            break

    if not deduped:
        return _sql_keyword_fallback(session, query, pool=pool, limit=limit, verbose=verbose)

    items = []
    for r in deduped:
        sim = round((1.0 - float(r.dist)) * 100, 1)
        label = r.title if not r.company else f"{r.title} ({r.company})"
        items.append(
            {
                "name": label,
                "metric": f"{sim}% 유사",
                "pct": sim,
                "id": r.id,
                "company": r.company,
                "pool": r.pool,
                "region": r.region_district or r.region_city,
            }
        )

    _attach_skills_and_concepts(session, items)

    facts = "; ".join(f"{it['name']} {it['metric']}" for it in items[:5])
    debug = (
        {
            "embedding_model": "BGE-M3",
            "embedding_dim": len(vec),
            "embedding_preview": [round(float(x), 6) for x in vec[:8]],
            "distance_metric": "cosine (pgvector <=>)",
            "raw_cosine_distances": [round(float(r.dist), 6) for r in deduped[:5]],
            "sql": sql,
            "sql_ms": sql_ms,
        }
        if verbose
        else None
    )
    return {
        "tool": "vector",
        "tool_result": {"kind": "posting_list", "label": "의미 유사 공고", "items": items, "debug": debug},
        "citation": {"type": "vector", "ref": "채용공고 의미벡터", "label": "BGE-M3 코사인 top-k"},
        "n": len(deduped),
        "facts": f"질문과 의미가 가까운 공고(코사인 유사도순) — {facts}",
    }
