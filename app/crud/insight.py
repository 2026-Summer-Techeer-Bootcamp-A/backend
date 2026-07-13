"""Stats/Trend 확장 인사이트 쿼리 — 기존 posting/posting_tech/interest_signal 스키마만 사용.

프론트 `/widgets` 갤러리에만 있던 pearl 데이터(a,h,o,p,r,x)를 실제 DB 쿼리로 재현한다.
GitHub 레포 단위 데이터(l,t,u)는 별도 테이블이 필요해 여기 포함하지 않는다.
"""

import statistics
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import case, distinct, func, select, text
from sqlalchemy.orm import Session

from app.models import InterestSignal, JobCategory, Posting, PostingCategory, PostingTech, Skill
from app.services.match import build_posting_pool_query, get_skill_id_by_canonical


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
    is_newcomer = case((Posting.career_min <= 0, 1), else_=0)

    stmt = (
        select(
            Skill.canonical,
            func.count(distinct(Posting.id)).label("postings"),
            func.sum(is_newcomer).label("newcomer_postings"),
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
        .group_by(Skill.canonical)
        .order_by(func.count(distinct(Posting.id)).desc())
        .limit(limit)
    )

    rows = session.execute(stmt).all()
    items = [
        {
            "canonical": row.canonical,
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
    global_data, global_total = _pool_skill_shares(session, "global")
    domestic_data, domestic_total = _pool_skill_shares(session, "domestic")

    all_ids = set(global_data) | set(domestic_data)
    entries = []
    for sid in all_ids:
        g = global_data.get(sid)
        d = domestic_data.get(sid)
        base = g or d
        entries.append(
            {
                "canonical": base["canonical"],
                "category": base["category"],
                "global_pct": g["pct"] if g else 0.0,
                "domestic_pct": d["pct"] if d else 0.0,
                "diff": round((g["pct"] if g else 0.0) - (d["pct"] if d else 0.0), 2),
                "global_n": g["n"] if g else 0,
                "domestic_n": d["n"] if d else 0,
            }
        )

    global_favored = sorted(entries, key=lambda e: e["diff"], reverse=True)[:limit]
    domestic_favored = sorted(entries, key=lambda e: e["diff"])[:limit]

    return global_favored, domestic_favored, global_total, domestic_total


def get_hiring_season(session: Session) -> tuple[list[dict], dict[str, int]]:
    """월별 채용 성수기 지수. himalayas(단일 스냅샷) 제외, 진행 중인 올해 제외."""
    current_year = date.today().year

    rows = session.execute(
        select(Posting.pool, Posting.post_date).where(
            Posting.source != "himalayas",
            Posting.post_date.isnot(None),
            Posting.pool.in_(("global", "domestic")),
            Posting.is_deleted.is_(False),
        )
    ).all()

    counts: dict[tuple[str, int], int] = {}
    pool_totals: dict[str, int] = {"global": 0, "domestic": 0}
    for pool, post_date in rows:
        if post_date.year == current_year:
            continue
        counts[(pool, post_date.month)] = counts.get((pool, post_date.month), 0) + 1
        pool_totals[pool] += 1

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
    """산업별 기술 지문. index = 산업 내 비중 / 전 산업 평균 비중(그 기술이 등장하는 산업들 기준)."""
    base_filters = [
        Posting.pool == "domestic",
        Posting.industry.isnot(None),
        Posting.is_deleted.is_(False),
    ]

    industry_totals_rows = session.execute(
        select(Posting.industry, func.count().label("n")).where(*base_filters).group_by(Posting.industry)
    ).all()
    industry_totals = {row.industry: row.n for row in industry_totals_rows}
    if not industry_totals:
        return [], 0

    rows = session.execute(
        select(Posting.industry, Skill.canonical, func.count(distinct(Posting.id)).label("n"))
        .select_from(Posting)
        .join(PostingTech, PostingTech.posting_id == Posting.id)
        .join(Skill, Skill.id == PostingTech.skill_id)
        .where(*base_filters, PostingTech.is_deleted.is_(False), Skill.is_deleted.is_(False))
        .group_by(Posting.industry, Skill.canonical)
    ).all()

    shares: dict[str, dict[str, dict]] = {}
    skill_industry_shares: dict[str, list[float]] = {}
    for row in rows:
        share = row.n / industry_totals[row.industry]
        shares.setdefault(row.industry, {})[row.canonical] = {"n": row.n, "share": share}
        skill_industry_shares.setdefault(row.canonical, []).append(share)

    avg_share = {skill: sum(vals) / len(vals) for skill, vals in skill_industry_shares.items()}

    top_industries = sorted(industry_totals.items(), key=lambda kv: kv[1], reverse=True)[:limit_industries]

    industries_out = []
    for industry_name, n in top_industries:
        signature = []
        for skill_name, info in shares.get(industry_name, {}).items():
            avg = avg_share.get(skill_name, 0)
            if avg <= 0:
                continue
            signature.append(
                {
                    "canonical": skill_name,
                    "index": round(info["share"] / avg, 2),
                    "share_pct": round(info["share"] * 100, 1),
                    "n": info["n"],
                }
            )
        signature.sort(key=lambda s: s["index"], reverse=True)
        industries_out.append({"name": industry_name, "n": n, "signature": signature[:limit_skills]})

    return industries_out, sum(industry_totals.values())


def get_role_stack_fit(
    session: Session, *, pool: str | None = None, top_n_categories: int = 6, top_k_skills: int = 20
) -> tuple[list[dict], list[list[float]], int]:
    """직군간 상위 기술 벡터의 가중 자카드(Ruzicka) 유사도. 기술직(job_category.is_tech)만 대상."""
    base_filters = [
        Posting.is_deleted.is_(False),
        PostingCategory.is_deleted.is_(False),
        JobCategory.is_tech.is_(True),
        JobCategory.is_deleted.is_(False),
    ]
    if pool:
        base_filters.append(Posting.pool == pool)

    category_totals_rows = session.execute(
        select(PostingCategory.category, func.count(distinct(Posting.id)).label("n"))
        .select_from(Posting)
        .join(PostingCategory, PostingCategory.posting_id == Posting.id)
        .join(JobCategory, JobCategory.name == PostingCategory.category)
        .where(*base_filters)
        .group_by(PostingCategory.category)
    ).all()
    category_totals = {row.category: row.n for row in category_totals_rows}
    top_categories = sorted(category_totals.items(), key=lambda kv: kv[1], reverse=True)[:top_n_categories]
    top_category_names = [c for c, _ in top_categories]
    if not top_category_names:
        return [], [], 0

    rows = session.execute(
        select(PostingCategory.category, Skill.canonical, func.count(distinct(Posting.id)).label("n"))
        .select_from(Posting)
        .join(PostingCategory, PostingCategory.posting_id == Posting.id)
        .join(PostingTech, PostingTech.posting_id == Posting.id)
        .join(Skill, Skill.id == PostingTech.skill_id)
        .join(JobCategory, JobCategory.name == PostingCategory.category)
        .where(
            *base_filters,
            PostingCategory.category.in_(top_category_names),
            PostingTech.is_deleted.is_(False),
            Skill.is_deleted.is_(False),
        )
        .group_by(PostingCategory.category, Skill.canonical)
    ).all()

    vectors: dict[str, dict[str, int]] = {c: {} for c in top_category_names}
    for row in rows:
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


def get_skill_trend_yearly(session: Session, *, pool: str, top_k: int = 15, movers_limit: int = 5) -> dict:
    """연도별 기술 점유율(연도 내 posting_tech 빈도 / 그 연도 전체 공고 수) + 급상승/급하락 무버스."""
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
