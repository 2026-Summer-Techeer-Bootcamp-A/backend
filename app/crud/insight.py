"""Stats/Trend 확장 인사이트 쿼리 — 기존 posting/posting_tech/interest_signal 스키마만 사용.

프론트 `/widgets` 갤러리에만 있던 pearl 데이터(a,h,o,p,r,x)를 실제 DB 쿼리로 재현한다.
GitHub 레포 단위 데이터(l,t,u)는 별도 테이블이 필요해 여기 포함하지 않는다.
"""

import statistics
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import bindparam, case, distinct, func, select, text
from sqlalchemy.orm import Session

from app.models import (
    Concept,
    InterestSignal,
    Posting,
    PostingCategory,
    PostingConcept,
    PostingTech,
    Skill,
)
from app.services.match import build_posting_pool_query, get_skill_id_by_canonical

# 그룹내 상대 점유(§5 group-share) 대상 스킬 세트. 프론트/백엔드/DB 그룹은 서버 상수로 고정한다.
GROUP_SKILLS: dict[str, list[str]] = {
    "frontend_fw": ["React", "Vue", "Angular", "Next.js", "Svelte"],
    "backend_fw": ["Spring", "Node.js", "Django", "FastAPI", "Express", "NestJS", ".NET", "Flask", "Laravel"],
    "database": ["MySQL", "PostgreSQL", "MongoDB", "Redis", "MariaDB", "Oracle", "SQLite"],
}


def _quarter_of(d: date) -> str:
    q = (d.month - 1) // 3 + 1
    return f"{d.year}Q{q}"


def get_hype_vs_hire(session: Session, *, skill: str) -> dict:
    """관심(HN 월별 언급) vs 실수요(공고, himalayas 제외) 분기별 시계열."""
    skill_id, canonical = get_skill_id_by_canonical(session=session, canonical=skill)

    interest_rows = session.execute(
        select(InterestSignal.month, InterestSignal.value).where(
            InterestSignal.skill_id == skill_id,
            InterestSignal.source == "hn",
            InterestSignal.is_deleted.is_(False),
        )
    ).all()

    demand_rows = session.execute(
        select(Posting.post_date)
        .join(PostingTech, PostingTech.posting_id == Posting.id)
        .where(
            PostingTech.skill_id == skill_id,
            Posting.source != "himalayas",
            Posting.post_date.isnot(None),
            Posting.is_deleted.is_(False),
            PostingTech.is_deleted.is_(False),
        )
    ).all()

    interest_by_q: dict[str, float] = {}
    for month, value in interest_rows:
        q = _quarter_of(month)
        interest_by_q[q] = interest_by_q.get(q, 0.0) + float(value)

    demand_by_q: dict[str, int] = {}
    for (post_date,) in demand_rows:
        q = _quarter_of(post_date)
        demand_by_q[q] = demand_by_q.get(q, 0) + 1

    quarters = sorted(set(interest_by_q) | set(demand_by_q))
    series = [
        {
            "quarter": q,
            "interest_value": round(interest_by_q.get(q, 0.0), 2),
            "posting_count": demand_by_q.get(q, 0),
        }
        for q in quarters
    ]

    return {
        "skill": canonical,
        "quarters": series,
        "sample_size": len(demand_rows),
    }


def get_newcomer_gate(session: Session, *, limit: int = 15) -> tuple[list[dict], int]:
    """기술별 신입 진입장벽. career_min<=0을 '신입 가능' 근사치로 사용(jumpit의 newcomer 플래그는 미적재)."""
    if session.bind.dialect.name == "sqlite":
        is_newcomer = case((Posting.career_min <= 0, 1), else_=0)
        rows = session.execute(
            select(
                Skill.canonical.label("skill_canonical"),
                func.count(Posting.id).label("postings"),
                func.sum(is_newcomer).label("newcomer_postings")
            )
            .select_from(Posting)
            .join(PostingTech, PostingTech.posting_id == Posting.id)
            .join(Skill, Skill.id == PostingTech.skill_id)
            .where(
                Posting.pool == "domestic",
                Posting.is_deleted.is_(False),
                PostingTech.is_deleted.is_(False),
                Skill.is_deleted.is_(False),
                Posting.career_min.isnot(None),
            )
            .group_by(Skill.id, Skill.canonical)
            .order_by(func.count(Posting.id).desc())
            .limit(limit)
        ).all()
    else:
        rows = session.execute(
            text("""
                SELECT skill_canonical, postings, newcomer_postings
                FROM mv_newcomer_gate
                ORDER BY postings DESC
                LIMIT :limit
            """),
            {"limit": limit}
        ).all()

    items = [
        {
            "canonical": row.skill_canonical,
            "postings": row.postings,
            "newcomer_postings": int(row.newcomer_postings or 0),
            "open_rate": round((row.newcomer_postings or 0) / row.postings * 100, 1) if row.postings else 0.0,
        }
        for row in rows
    ]

    sample_size = (
        session.scalar(
            select(func.count())
            .select_from(Posting)
            .where(
                Posting.pool == "domestic",
                Posting.is_deleted.is_(False),
                Posting.career_min.isnot(None),
            )
        )
        or 0
    )

    return items, sample_size


def _pool_skill_shares(session: Session, pool: str) -> tuple[dict[int, dict], int]:
    total = (
        session.scalar(
            select(func.count()).select_from(Posting).where(Posting.pool == pool, Posting.is_deleted.is_(False))
        )
        or 0
    )
    if total == 0:
        return {}, 0

    rows = session.execute(
        select(
            Skill.id,
            Skill.canonical,
            Skill.category,
            func.count(distinct(PostingTech.posting_id)).label("n"),
        )
        .select_from(Posting)
        .join(PostingTech, PostingTech.posting_id == Posting.id)
        .join(Skill, Skill.id == PostingTech.skill_id)
        .where(
            Posting.pool == pool,
            Posting.is_deleted.is_(False),
            PostingTech.is_deleted.is_(False),
            Skill.is_deleted.is_(False),
        )
        .group_by(Skill.id, Skill.canonical, Skill.category)
    ).all()

    data = {
        row.id: {
            "canonical": row.canonical,
            "category": row.category,
            "n": row.n,
            "pct": round(row.n / total * 100, 2),
        }
        for row in rows
    }
    return data, total


def get_global_domestic_gap(session: Session, *, limit: int = 20) -> tuple[list[dict], list[dict], int, int]:
    """각 풀 내 점유율 비교. 절대 두 풀을 합산하지 않고, 풀별 share만 비교한다."""
    item_columns = """
        canonical,
        category,
        global_pct,
        domestic_pct,
        diff,
        global_n,
        domestic_n
    """
    global_favored = [
        dict(row)
        for row in session.execute(
            text(
                f"""
                SELECT {item_columns}
                FROM mv_global_domestic_gap
                WHERE skill_id IS NOT NULL
                ORDER BY diff DESC
                LIMIT :limit
                """
            ),
            {"limit": limit},
        )
        .mappings()
        .all()
    ]
    domestic_favored = [
        dict(row)
        for row in session.execute(
            text(
                f"""
                SELECT {item_columns}
                FROM mv_global_domestic_gap
                WHERE skill_id IS NOT NULL
                ORDER BY diff ASC
                LIMIT :limit
                """
            ),
            {"limit": limit},
        )
        .mappings()
        .all()
    ]
    totals = session.execute(
        text(
            """
            SELECT global_total, domestic_total
            FROM mv_global_domestic_gap
            LIMIT 1
            """
        )
    ).mappings().first()

    global_total = int(totals["global_total"]) if totals else 0
    domestic_total = int(totals["domestic_total"]) if totals else 0
    return global_favored, domestic_favored, global_total, domestic_total


def get_hiring_season(session: Session) -> tuple[list[dict], dict[str, int]]:
    """월별 채용 성수기 지수. himalayas(단일 스냅샷) 제외, 진행 중인 올해 제외."""
    current_year = date.today().year
    post_year = func.extract("year", Posting.post_date)
    post_month = func.extract("month", Posting.post_date)

    rows = session.execute(
        select(
            Posting.pool,
            post_month.label("month"),
            func.count(Posting.id).label("n"),
        )
        .where(
            Posting.source != "himalayas",
            Posting.post_date.isnot(None),
            post_year != current_year,
            Posting.pool.in_(("global", "domestic")),
            Posting.is_deleted.is_(False),
        )
        .group_by(Posting.pool, post_month)
    ).all()

    counts: dict[tuple[str, int], int] = {}
    pool_totals: dict[str, int] = {"global": 0, "domestic": 0}
    for pool, month, n in rows:
        month_number = int(month)
        count = int(n)
        counts[(pool, month_number)] = count
        pool_totals[pool] += count

    months = []
    for m in range(1, 13):
        g_n = counts.get(("global", m), 0)
        d_n = counts.get(("domestic", m), 0)
        g_avg = pool_totals["global"] / 12 if pool_totals["global"] else 0
        d_avg = pool_totals["domestic"] / 12 if pool_totals["domestic"] else 0
        months.append(
            {
                "month": m,
                "global_idx": round(g_n / g_avg, 2) if g_avg else 0.0,
                "domestic_idx": round(d_n / d_avg, 2) if d_avg else 0.0,
                "global_n": g_n,
                "domestic_n": d_n,
            }
        )

    return months, pool_totals


def get_industry_fingerprint(
    session: Session, *, limit_industries: int = 8, limit_skills: int = 8
) -> tuple[list[dict], int]:
    """Read the pre-aggregated domestic industry fingerprint materialized view."""
    top_industries = session.execute(
        text(
            """
            SELECT industry, MAX(industry_total) AS industry_total
            FROM mv_industry_fingerprint
            GROUP BY industry
            ORDER BY industry_total DESC, industry ASC
            LIMIT :limit_industries
            """
        ),
        {"limit_industries": limit_industries},
    ).all()
    if not top_industries:
        return [], 0

    industry_names = [row.industry for row in top_industries]
    rows = session.execute(
        text(
            """
            SELECT industry, skill_canonical, posting_count, share, avg_share
            FROM mv_industry_fingerprint
            WHERE industry IN :industry_names
            ORDER BY industry ASC,
                     (share / NULLIF(avg_share, 0)) DESC,
                     skill_canonical ASC
            """
        ).bindparams(bindparam("industry_names", expanding=True)),
        {"industry_names": industry_names},
    ).all()

    signatures: dict[str, list[dict]] = {name: [] for name in industry_names}
    for row in rows:
        if row.avg_share and len(signatures[row.industry]) < limit_skills:
            signatures[row.industry].append(
                {
                    "canonical": row.skill_canonical,
                    "index": round(row.share / row.avg_share, 2),
                    "share_pct": round(row.share * 100, 1),
                    "n": row.posting_count,
                }
            )

    industries_out = [
        {"name": row.industry, "n": row.industry_total, "signature": signatures[row.industry]}
        for row in top_industries
    ]
    sample_size = session.execute(
        text(
            """
            SELECT COALESCE(SUM(industry_total), 0)
            FROM (
                SELECT industry, MAX(industry_total) AS industry_total
                FROM mv_industry_fingerprint
                GROUP BY industry
            ) totals
            """
        )
    ).scalar_one()
    return industries_out, sample_size


def get_role_stack_fit(
    session: Session, *, pool: str | None = None, top_n_categories: int = 6, top_k_skills: int = 20
) -> tuple[list[dict], list[list[float]], int]:
    """Read pre-aggregated role skill vectors and calculate Ruzicka similarity."""
    pool_filter = "WHERE pool = :pool" if pool else ""
    params = {"pool": pool} if pool else {}
    category_totals_rows = session.execute(
        text(
            f"""
            SELECT category, SUM(category_total) AS n
            FROM (
                SELECT pool, category, MAX(category_total) AS category_total
                FROM mv_role_stack_fit
                {pool_filter}
                GROUP BY pool, category
            ) category_totals
            GROUP BY category
            """
        ),
        params,
    ).all()
    category_totals = {row.category: row.n for row in category_totals_rows}
    top_categories = sorted(category_totals.items(), key=lambda kv: kv[1], reverse=True)[:top_n_categories]
    top_category_names = [c for c, _ in top_categories]
    if not top_category_names:
        return [], [], 0

    rows = session.execute(
        text(
            f"""
            SELECT category, skill_canonical AS canonical, SUM(posting_count) AS n
            FROM mv_role_stack_fit
            {pool_filter}
            GROUP BY category, skill_canonical
            """
        ),
        params,
    ).all()

    vectors: dict[str, dict[str, int]] = {c: {} for c in top_category_names}
    for row in rows:
        if row.category in vectors and row.canonical is not None:
            vectors[row.category][row.canonical] = row.n

    trimmed = {
        c: dict(sorted(v.items(), key=lambda kv: kv[1], reverse=True)[:top_k_skills]) for c, v in vectors.items()
    }

    def ruzicka(a: dict[str, int], b: dict[str, int]) -> float:
        keys = set(a) | set(b)
        if not keys:
            return 0.0
        num = sum(min(a.get(k, 0), b.get(k, 0)) for k in keys)
        den = sum(max(a.get(k, 0), b.get(k, 0)) for k in keys)
        return (num / den * 100) if den else 0.0

    matrix = [
        [100.0 if c1 == c2 else round(ruzicka(trimmed[c1], trimmed[c2]), 1) for c2 in top_category_names]
        for c1 in top_category_names
    ]

    categories_out = [{"name": c, "n": category_totals[c]} for c in top_category_names]

    return categories_out, matrix, sum(category_totals.values())


def get_skill_share(
    session: Session, *, pool: str, position: str | None = None, top_k: int = 20
) -> tuple[list[dict], int]:
    """mv_skill_share 마트 기반 기술 점유율. position 지정 시 그 직군만, 미지정 시 skill별 posting_count 합산."""
    if position:
        rows = session.execute(
            text(
                """
                SELECT s.canonical AS canonical, s.category AS category,
                       m.posting_count AS posting_count, m.share AS share, m.total_postings AS total_postings
                FROM mv_skill_share m
                JOIN skill s ON s.id = m.skill_id
                WHERE m.pool = :pool AND m.position = :position
                ORDER BY m.posting_count DESC
                LIMIT :top_k
                """
            ),
            {"pool": pool, "position": position, "top_k": top_k},
        ).all()

        items = [
            {
                "canonical": row.canonical,
                "category": row.category,
                "posting_count": int(row.posting_count),
                "share": round(float(row.share or 0.0), 4),
            }
            for row in rows
        ]
        sample_size = int(rows[0].total_postings) if rows else 0
        return items, sample_size

    sample_size = (
        session.scalar(
            select(func.count(distinct(Posting.id))).where(Posting.pool == pool, Posting.is_deleted.is_(False))
        )
        or 0
    )

    rows = session.execute(
        text(
            """
            SELECT s.canonical AS canonical, s.category AS category, SUM(m.posting_count) AS posting_count
            FROM mv_skill_share m
            JOIN skill s ON s.id = m.skill_id
            WHERE m.pool = :pool
            GROUP BY s.id, s.canonical, s.category
            ORDER BY posting_count DESC
            LIMIT :top_k
            """
        ),
        {"pool": pool, "top_k": top_k},
    ).all()

    items = [
        {
            "canonical": row.canonical,
            "category": row.category,
            "posting_count": int(row.posting_count),
            "share": round(int(row.posting_count) / sample_size, 4) if sample_size else 0.0,
        }
        for row in rows
    ]
    return items, sample_size


def get_cooccurrence(
    session: Session, *, pool: str, skill: str | None = None, top_k: int = 30
) -> tuple[list[dict], list[dict]]:
    """mv_cooccurrence 마트 기반 co-occurrence 네트워크.

    skill 지정 시 해당 기술을 중심으로 한 이웃 링크(co_count 내림차순)만 반환한다.
    미지정 시 pool 전체에서 co_count 상위 링크를 skill_id_1 < skill_id_2 조건으로 중복 없이 반환한다.
    """
    query_base = """
        SELECT m.skill_id_1 AS id1, s1.canonical AS canonical1, s1.category AS category1,
               m.skill_id_2 AS id2, s2.canonical AS canonical2, s2.category AS category2,
               m.co_count AS co_count, m.co_rate AS co_rate
        FROM mv_cooccurrence m
        JOIN skill s1 ON s1.id = m.skill_id_1
        JOIN skill s2 ON s2.id = m.skill_id_2
        WHERE m.pool = :pool AND {condition}
        ORDER BY m.co_count DESC
        LIMIT :top_k
    """

    if skill:
        skill_id, _canonical = get_skill_id_by_canonical(session=session, canonical=skill)
        rows = session.execute(
            text(query_base.format(condition="m.skill_id_1 = :skill_id")),
            {"pool": pool, "skill_id": skill_id, "top_k": top_k},
        ).all()
    else:
        rows = session.execute(
            text(query_base.format(condition="m.skill_id_1 < m.skill_id_2")),
            {"pool": pool, "top_k": top_k},
        ).all()

    nodes: dict[int, dict] = {}
    links: list[dict] = []
    for row in rows:
        node1 = nodes.setdefault(row.id1, {"canonical": row.canonical1, "category": row.category1, "freq": 0})
        node1["freq"] += int(row.co_count)
        node2 = nodes.setdefault(row.id2, {"canonical": row.canonical2, "category": row.category2, "freq": 0})
        node2["freq"] += int(row.co_count)
        links.append(
            {
                "source": row.canonical1,
                "target": row.canonical2,
                "co_count": int(row.co_count),
                "co_rate": round(float(row.co_rate or 0.0), 4),
            }
        )

    return list(nodes.values()), links


def get_posting_timeline(
    session: Session,
    *,
    pool: str,
    days: int,
    owned_skill_ids: set[int] | None,
    position: str | None = None,
) -> tuple[list[dict], str]:
    """풀/직무 내 최신 공고 기준 일별 타임라인과 선택적인 보유 기술 매칭 수를 집계한다."""
    posting_filters = [Posting.pool == pool, Posting.is_deleted.is_(False)]
    if position:
        posting_filters.append(
            select(PostingCategory.id)
            .where(
                PostingCategory.posting_id == Posting.id,
                PostingCategory.category == position,
                PostingCategory.is_deleted.is_(False),
            )
            .exists()
        )

    as_of_date = session.scalar(select(func.max(Posting.post_date)).where(*posting_filters))
    if as_of_date is None:
        return [], datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat()

    start_date = as_of_date - timedelta(days=days - 1)

    rows = session.execute(
        select(Posting.id, Posting.post_date).where(
            *posting_filters,
            Posting.post_date.isnot(None),
            Posting.post_date >= start_date,
            Posting.post_date <= as_of_date,
        )
    ).all()

    totals: dict[date, int] = {}
    posting_dates: dict[int, date] = {}
    for posting_id, post_date in rows:
        totals[post_date] = totals.get(post_date, 0) + 1
        posting_dates[posting_id] = post_date

    matched_by_date: dict[date, int] | None = None
    if owned_skill_ids is not None:
        matched_by_date = dict.fromkeys(totals, 0)
        if owned_skill_ids and posting_dates:
            skill_rows = session.execute(
                select(PostingTech.posting_id, PostingTech.skill_id).where(
                    PostingTech.posting_id.in_(posting_dates.keys()),
                    PostingTech.is_deleted.is_(False),
                )
            ).all()
            posting_skills: dict[int, set[int]] = {}
            for posting_id, skill_id in skill_rows:
                posting_skills.setdefault(posting_id, set()).add(skill_id)
            for posting_id, skills in posting_skills.items():
                if skills & owned_skill_ids:
                    d = posting_dates[posting_id]
                    matched_by_date[d] = matched_by_date.get(d, 0) + 1

    daily = []
    cursor = start_date
    while cursor <= as_of_date:
        entry: dict = {"date": cursor.isoformat(), "total": totals.get(cursor, 0)}
        if matched_by_date is not None:
            entry["matched"] = matched_by_date.get(cursor, 0)
        daily.append(entry)
        cursor += timedelta(days=1)

    return daily, as_of_date.isoformat()


def get_response_rate(session: Session, *, pool: str, company_limit: int = 20) -> dict:
    """응답률 분포(20포인트 폭 5버킷) + 회사별 평균 응답률. wanted 소스만 response_rate를 적재하므로 표본이 얇다."""
    rows = session.execute(
        select(Posting.company, Posting.response_rate).where(
            Posting.pool == pool,
            Posting.is_deleted.is_(False),
            Posting.response_rate.isnot(None),
        )
    ).all()

    rates = [float(r.response_rate) for r in rows]
    sample_size = len(rates)
    if sample_size == 0:
        return {"median_rate": 0.0, "levels": [], "companies": [], "sample_size": 0}

    median_rate = round(statistics.median(rates), 1)

    bucket_width = 20
    level_labels = [f"{i}-{i + bucket_width}" for i in range(0, 100, bucket_width)]
    level_counts: dict[str, int] = dict.fromkeys(level_labels, 0)
    for rate in rates:
        idx = min(int(rate // bucket_width), len(level_labels) - 1)
        level_counts[level_labels[idx]] += 1
    levels = [{"level": label, "n": level_counts[label]} for label in level_labels]

    company_rates: dict[str, list[float]] = {}
    for company, rate in rows:
        if company is None:
            continue
        company_rates.setdefault(company, []).append(float(rate))

    companies = [
        {"company": company, "rate": round(sum(vals) / len(vals), 1), "n": len(vals)}
        for company, vals in company_rates.items()
    ]
    companies.sort(key=lambda c: c["rate"], reverse=True)

    return {
        "median_rate": median_rate,
        "levels": levels,
        "companies": companies[:company_limit],
        "sample_size": sample_size,
    }


def _skill_yearly_counts(session: Session, *, pool: str) -> tuple[dict[str, dict[int, int]], dict[str, int], dict[int, int]]:
    """기술별 연도별 등장 횟수(skill_year_count), 기술별 총 등장 수(skill_total), 연도별 전체 공고 수(year_denominator)."""
    rows = session.execute(
        select(Posting.post_date, Skill.canonical)
        .select_from(Posting)
        .join(PostingTech, PostingTech.posting_id == Posting.id)
        .join(Skill, Skill.id == PostingTech.skill_id)
        .where(
            Posting.pool == pool,
            Posting.is_deleted.is_(False),
            Posting.post_date.isnot(None),
            PostingTech.is_deleted.is_(False),
            Skill.is_deleted.is_(False),
        )
    ).all()

    skill_year_count: dict[str, dict[int, int]] = {}
    skill_total: dict[str, int] = {}
    for post_date, canonical in rows:
        year = post_date.year
        year_counts = skill_year_count.setdefault(canonical, {})
        year_counts[year] = year_counts.get(year, 0) + 1
        skill_total[canonical] = skill_total.get(canonical, 0) + 1

    year_posting_rows = session.execute(
        select(Posting.post_date).where(
            Posting.pool == pool, Posting.is_deleted.is_(False), Posting.post_date.isnot(None)
        )
    ).all()
    year_denominator: dict[int, int] = {}
    for (post_date,) in year_posting_rows:
        year_denominator[post_date.year] = year_denominator.get(post_date.year, 0) + 1

    return skill_year_count, skill_total, year_denominator


def _skill_yearly_counts_from_mv(
    session: Session, *, pool: str
) -> tuple[dict[str, dict[int, int]], dict[str, int], dict[int, int]]:
    """skill-trend-yearly 전용 MV에서 기존 연도별 집계 자료구조를 복원한다."""
    rows = session.execute(
        text(
            """
            SELECT year, canonical, skill_count, skill_total, year_total
            FROM mv_skill_trend_yearly
            WHERE pool = :pool
            ORDER BY skill_total DESC, canonical ASC, year ASC
            """
        ),
        {"pool": pool},
    ).mappings().all()

    skill_year_count: dict[str, dict[int, int]] = {}
    skill_total: dict[str, int] = {}
    year_denominator: dict[int, int] = {}
    for row in rows:
        year = int(row["year"])
        year_denominator[year] = int(row["year_total"])

        canonical = row["canonical"]
        if canonical is None:
            continue

        year_counts = skill_year_count.setdefault(canonical, {})
        year_counts[year] = int(row["skill_count"])
        skill_total[canonical] = int(row["skill_total"])

    return skill_year_count, skill_total, year_denominator


def get_skill_trend_yearly(session: Session, *, pool: str, top_k: int = 15, movers_limit: int = 5) -> dict:
    """연도별 기술 점유율(연도 내 posting_tech 빈도 / 그 연도 전체 공고 수) + 급상승/급하락 무버스."""
    skill_year_count, skill_total, year_denominator = _skill_yearly_counts_from_mv(session, pool=pool)

    years = sorted(year_denominator.keys())

    top_skills = sorted(skill_total.items(), key=lambda kv: kv[1], reverse=True)[:top_k]

    series = []
    for canonical, _total in top_skills:
        year_counts = skill_year_count.get(canonical, {})
        shares = []
        for year in years:
            denom = year_denominator.get(year, 0)
            n = year_counts.get(year, 0)
            shares.append(round(n / denom * 100, 1) if denom else 0.0)
        delta = round(shares[-1] - shares[0], 1) if shares else 0.0
        series.append({"canonical": canonical, "shares": shares, "delta": delta})

    rising = sorted((s for s in series if s["delta"] > 0), key=lambda s: s["delta"], reverse=True)[:movers_limit]
    falling = sorted((s for s in series if s["delta"] < 0), key=lambda s: s["delta"])[:movers_limit]

    return {
        "years": years,
        "series": series,
        "movers": {
            "rising": [{"canonical": s["canonical"], "delta": s["delta"]} for s in rising],
            "falling": [{"canonical": s["canonical"], "delta": s["delta"]} for s in falling],
        },
        "sample_size": sum(year_denominator.values()),
    }


def get_hot_companies(session: Session, *, pool: str, days: int = 30, limit: int = 20) -> tuple[list[dict], str]:
    """최근 days일간(as_of=풀 내 최신 post_date 기준) 신규 공고가 많은 활발 기업."""
    as_of_date = session.scalar(
        select(func.max(Posting.post_date)).where(Posting.pool == pool, Posting.is_deleted.is_(False))
    )
    if as_of_date is None:
        return [], date.today().isoformat()

    start_date = as_of_date - timedelta(days=days - 1)

    rows = session.execute(
        select(Posting.company, func.count().label("n"))
        .where(
            Posting.pool == pool,
            Posting.is_deleted.is_(False),
            Posting.company.isnot(None),
            Posting.post_date.isnot(None),
            Posting.post_date >= start_date,
            Posting.post_date <= as_of_date,
        )
        .group_by(Posting.company)
        .order_by(func.count().desc())
        .limit(limit)
    ).all()

    items = [{"company": row.company, "posting_count": row.n} for row in rows]
    return items, as_of_date.isoformat()


def get_region_density(session: Session, *, pool: str = "domestic", limit: int = 20) -> tuple[list[dict], str]:
    """지역(구/동)별 공고 밀도. region_district는 domestic 공고에만 적재됨."""
    as_of_date = session.scalar(
        select(func.max(Posting.post_date)).where(Posting.pool == pool, Posting.is_deleted.is_(False))
    )

    rows = session.execute(
        select(Posting.region_district, func.count().label("n"))
        .where(
            Posting.pool == pool,
            Posting.is_deleted.is_(False),
            Posting.region_district.isnot(None),
        )
        .group_by(Posting.region_district)
        .order_by(func.count().desc())
        .limit(limit)
    ).all()

    items = [{"region_district": row.region_district, "posting_count": row.n} for row in rows]
    return items, as_of_date.isoformat() if as_of_date is not None else date.today().isoformat()


def get_skill_unlock(
    session: Session,
    *,
    pool: str,
    owned_skill_ids: set[int],
    position: str | None = None,
    candidate_limit: int = 15,
) -> dict:
    """한계 해금 — 기술 하나를 더 배우면 지원 가능(apply)해지는 공고가 얼마나 늘어나는지.

    missing = 공고 요구기술 - 보유기술. missing_count로 apply(0)/near1(1)/near2_3(2~3)/far(4+) 4단계 퍼널을 만들고,
    near1 공고의 유일한 미보유 기술을 marginal_apply로, 전체 미보유 상태에서 요구되는 횟수를 req_count로 집계한다.
    """
    posting_pool_query = build_posting_pool_query(pool=pool, position=position).subquery()

    rows = session.execute(
        select(PostingTech.posting_id, PostingTech.skill_id, Skill.canonical)
        .join(posting_pool_query, posting_pool_query.c.id == PostingTech.posting_id)
        .join(Skill, Skill.id == PostingTech.skill_id)
        .where(PostingTech.is_deleted.is_(False), Skill.is_deleted.is_(False))
    ).all()

    posting_skills: dict[int, set[int]] = {}
    skill_canonical: dict[int, str] = {}
    for posting_id, skill_id, canonical in rows:
        posting_skills.setdefault(posting_id, set()).add(skill_id)
        skill_canonical[skill_id] = canonical

    funnel = {"apply": 0, "near1": 0, "near2_3": 0, "far": 0}
    req_count: dict[int, int] = {}
    marginal_apply: dict[int, int] = {}

    for skills in posting_skills.values():
        missing = skills - owned_skill_ids
        missing_count = len(missing)
        if missing_count == 0:
            funnel["apply"] += 1
            continue
        if missing_count == 1:
            funnel["near1"] += 1
            (only_id,) = tuple(missing)
            marginal_apply[only_id] = marginal_apply.get(only_id, 0) + 1
        elif missing_count <= 3:
            funnel["near2_3"] += 1
        else:
            funnel["far"] += 1

        for skill_id in missing:
            req_count[skill_id] = req_count.get(skill_id, 0) + 1

    candidates = [
        {
            "canonical": skill_canonical[skill_id],
            "req_count": count,
            "marginal_apply": marginal_apply.get(skill_id, 0),
        }
        for skill_id, count in req_count.items()
    ]
    candidates.sort(key=lambda c: (c["marginal_apply"], c["req_count"]), reverse=True)

    return {
        "funnel": funnel,
        "candidates": candidates[:candidate_limit],
        "sample_size": len(posting_skills),
    }


def get_group_share(session: Session, *, group: str, pool: str) -> dict:
    """프레임워크/DB 그룹 내 상대 점유율. share=count/union_count(그룹 스킬 중 하나라도 걸린 공고 수) 기준.

    절대 전체 공고 대비 비율이 아니다 — 그룹끼리 비교 목적(예: 프론트 프레임워크 판도).
    """
    skills = GROUP_SKILLS[group]

    base_filters = [
        PostingTech.is_deleted.is_(False),
        Skill.is_deleted.is_(False),
        Skill.canonical.in_(skills),
        Posting.pool == pool,
        Posting.is_deleted.is_(False),
    ]

    union_count = (
        session.scalar(
            select(func.count(distinct(PostingTech.posting_id)))
            .select_from(PostingTech)
            .join(Skill, Skill.id == PostingTech.skill_id)
            .join(Posting, Posting.id == PostingTech.posting_id)
            .where(*base_filters)
        )
        or 0
    )

    if union_count == 0:
        return {"union_count": 0, "items": []}

    rows = session.execute(
        select(Skill.canonical, func.count(distinct(PostingTech.posting_id)).label("n"))
        .select_from(PostingTech)
        .join(Skill, Skill.id == PostingTech.skill_id)
        .join(Posting, Posting.id == PostingTech.posting_id)
        .where(*base_filters)
        .group_by(Skill.canonical)
        .order_by(func.count(distinct(PostingTech.posting_id)).desc())
    ).all()

    items = [
        {"canonical": row.canonical, "count": row.n, "share": round(100 * row.n / union_count, 1)} for row in rows
    ]

    return {"union_count": union_count, "items": items}


def get_concept_tech(session: Session, *, pool: str, top_concepts: int = 6, top_techs: int = 5) -> dict:
    """개념→기술 Sankey. posting_concept×posting_tech 공동출현 상위 개념 N × 개념당 상위 기술 M."""
    concept_rows = session.execute(
        select(Concept.id, Concept.name, func.count(distinct(PostingConcept.posting_id)).label("n"))
        .select_from(PostingConcept)
        .join(Concept, Concept.id == PostingConcept.concept_id)
        .join(Posting, Posting.id == PostingConcept.posting_id)
        .where(
            PostingConcept.is_deleted.is_(False),
            Concept.is_deleted.is_(False),
            Posting.pool == pool,
            Posting.is_deleted.is_(False),
        )
        .group_by(Concept.id, Concept.name)
        .order_by(func.count(distinct(PostingConcept.posting_id)).desc())
        .limit(top_concepts)
    ).all()

    if not concept_rows:
        return {"nodes": [], "links": []}

    concept_ids = [row.id for row in concept_rows]
    concept_names = {row.id: row.name for row in concept_rows}

    tech_rows = session.execute(
        select(
            PostingConcept.concept_id,
            Skill.canonical,
            func.count(distinct(PostingTech.posting_id)).label("n"),
        )
        .select_from(PostingConcept)
        .join(PostingTech, PostingTech.posting_id == PostingConcept.posting_id)
        .join(Skill, Skill.id == PostingTech.skill_id)
        .join(Posting, Posting.id == PostingConcept.posting_id)
        .where(
            PostingConcept.is_deleted.is_(False),
            PostingTech.is_deleted.is_(False),
            Skill.is_deleted.is_(False),
            Posting.pool == pool,
            Posting.is_deleted.is_(False),
            PostingConcept.concept_id.in_(concept_ids),
        )
        .group_by(PostingConcept.concept_id, Skill.canonical)
    ).all()

    by_concept: dict[int, list[tuple[str, int]]] = {}
    for concept_id, canonical, n in tech_rows:
        by_concept.setdefault(concept_id, []).append((canonical, n))

    nodes: dict[str, dict] = {}
    links: list[dict] = []
    for concept_id in concept_ids:
        cname = concept_names[concept_id]
        nodes[cname] = {"name": cname, "type": "concept"}
        top = sorted(by_concept.get(concept_id, []), key=lambda t: t[1], reverse=True)[:top_techs]
        for tech_name, n in top:
            nodes.setdefault(tech_name, {"name": tech_name, "type": "tech"})
            links.append({"source": cname, "target": tech_name, "value": n})

    return {"nodes": list(nodes.values()), "links": links}


def get_skill_count_dist(session: Session, *, pool: str) -> dict:
    """공고당 요구 스킬 개수 분포(히스토그램) + 평균/중앙값. posting_tech가 하나도 없는 공고는 제외한다."""
    rows = session.execute(
        text(
            """
            SELECT cnt, COUNT(*) AS n
            FROM (
                SELECT p.id, COUNT(pt.skill_id) AS cnt
                FROM posting p
                JOIN posting_tech pt ON pt.posting_id = p.id AND pt.is_deleted = false
                WHERE p.pool = :pool AND p.is_deleted = false
                GROUP BY p.id
            ) t
            GROUP BY cnt
            ORDER BY cnt
            """
        ),
        {"pool": pool},
    ).all()

    histogram = [{"k": int(row.cnt), "count": int(row.n)} for row in rows]
    total_postings = sum(h["count"] for h in histogram)
    if total_postings == 0:
        return {"histogram": [], "avg": 0.0, "median": 0.0}

    total_skills = sum(h["k"] * h["count"] for h in histogram)
    avg = round(total_skills / total_postings, 1)

    expanded = [h["k"] for h in histogram for _ in range(h["count"])]
    median = float(statistics.median(expanded))

    return {"histogram": histogram, "avg": avg, "median": median}


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 2:
        return None
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True))
    den_x = sum((x - mean_x) ** 2 for x in xs) ** 0.5
    den_y = sum((y - mean_y) ** 2 for y in ys) ** 0.5
    if den_x == 0 or den_y == 0:
        return None
    return num / (den_x * den_y)


def _best_lag(
    global_shares: list[float], domestic_shares: list[float], *, max_lag: int, min_overlap: int = 4
) -> int | None:
    """global이 domestic을 lag년만큼 선행한다고 가정하고 0~max_lag 중 상관 최대인 lag를 근사 추정한다.

    두 시계열은 이미 동일한 연도축으로 정렬돼 있어야 한다(같은 길이, 인덱스=연도 순서).
    표본이 min_overlap년 미만으로 줄어드는 lag는 후보에서 제외한다.
    """
    n = len(global_shares)
    best_lag = None
    best_corr = None
    for lag in range(0, max_lag + 1):
        overlap = n - lag
        if overlap < min_overlap:
            break
        corr = _pearson(global_shares[:overlap], domestic_shares[lag : lag + overlap])
        if corr is None:
            continue
        if best_corr is None or corr > best_corr:
            best_corr = corr
            best_lag = lag
    return best_lag


def get_global_domestic_lag(
    session: Session,
    *,
    limit: int = 10,
    min_postings: int = 200,
    min_years: int = 5,
    max_lag: int = 3,
) -> dict:
    """기술별 글로벌 연도 점유율 추이가 국내를 선행하는 근사 시차(교차상관, lag 0~3년).

    양 풀 모두 표본(min_postings)·연도 폭(min_years)이 충분한 기술만 대상으로 하며,
    스크래핑 배치 노이즈·표본 편향으로 lag는 정밀한 값이 아닌 방향성 참고용 근사치다.
    """
    g_year_count, g_total, g_year_denom = _skill_yearly_counts(session, pool="global")
    d_year_count, d_total, d_year_denom = _skill_yearly_counts(session, pool="domestic")

    g_years = sorted(g_year_denom.keys())
    d_years = sorted(d_year_denom.keys())
    common_years = sorted(set(g_years) & set(d_years))
    if len(common_years) < min_years:
        return {"items": []}

    candidates = [
        canonical
        for canonical in set(g_total) & set(d_total)
        if g_total[canonical] >= min_postings and d_total[canonical] >= min_postings
    ]

    def _series(year_counts: dict[int, int], years: list[int], denom: dict[int, int]) -> list[float]:
        return [round(year_counts.get(y, 0) / denom[y] * 100, 2) if denom.get(y) else 0.0 for y in years]

    items = []
    for canonical in candidates:
        g_shares_full = _series(g_year_count.get(canonical, {}), g_years, g_year_denom)
        d_shares_full = _series(d_year_count.get(canonical, {}), d_years, d_year_denom)

        g_common = [g_shares_full[g_years.index(y)] for y in common_years]
        d_common = [d_shares_full[d_years.index(y)] for y in common_years]

        lag = _best_lag(g_common, d_common, max_lag=max_lag)
        if lag is None:
            continue

        items.append(
            {
                "canonical": canonical,
                "lag_years": lag,
                "global_series": [{"year": y, "share": s} for y, s in zip(g_years, g_shares_full, strict=True)],
                "domestic_series": [{"year": y, "share": s} for y, s in zip(d_years, d_shares_full, strict=True)],
                "_sample": g_total[canonical] + d_total[canonical],
            }
        )

    items.sort(key=lambda i: i["_sample"], reverse=True)
    for item in items:
        del item["_sample"]

    return {"items": items[:limit]}
