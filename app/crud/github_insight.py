"""GitHub 레포 단위 인사이트 쿼리(t,u,l). github_repo_snapshot/github_star_history 기반.

이 테이블들이 비어있으면(ETL 미실행) 모든 함수가 빈 결과 + sample_size=0을 반환한다 —
값을 지어내지 않는다(cite/05-data-sources.md "정직 표기" 원칙).
"""

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import GithubRepoSnapshot, GithubStarHistory, Posting, PostingTech, Skill, SkillAlias


def get_latest_snapshot_date(session: Session) -> date | None:
    return session.scalar(
        select(func.max(GithubRepoSnapshot.snapshot_date)).where(GithubRepoSnapshot.is_deleted.is_(False))
    )


def _build_skill_lookup(session: Session) -> dict[str, tuple[int, str, str]]:
    """소문자 canonical/alias -> (skill_id, canonical, category)."""
    lookup: dict[str, tuple[int, str, str]] = {}
    for skill_id, canonical, category in session.execute(
        select(Skill.id, Skill.canonical, Skill.category).where(Skill.is_deleted.is_(False))
    ).all():
        lookup[canonical.lower()] = (skill_id, canonical, category)

    for skill_id, canonical, category, alias in session.execute(
        select(Skill.id, Skill.canonical, Skill.category, SkillAlias.alias)
        .join(SkillAlias, SkillAlias.skill_id == Skill.id)
        .where(Skill.is_deleted.is_(False), SkillAlias.is_deleted.is_(False))
    ).all():
        lookup.setdefault(alias.lower(), (skill_id, canonical, category))

    return lookup


def _global_skill_share_pct(session: Session, skill_id: int) -> float:
    """global 풀 전체 공고 중 해당 skill을 요구하는 공고 비율(%). GitHub 데이터는 pool 무관 조연 신호라 global로 고정."""
    total = (
        session.scalar(
            select(func.count()).select_from(Posting).where(Posting.pool == "global", Posting.is_deleted.is_(False))
        )
        or 0
    )
    if total == 0:
        return 0.0

    # skill_id를 하나로 고정했으므로 posting_tech의 (posting_id, skill_id) 유니크
    # 제약상 posting_id는 중복될 수 없다. DISTINCT 불필요.
    matched = (
        session.scalar(
            select(func.count(PostingTech.posting_id))
            .select_from(PostingTech)
            .join(Posting, Posting.id == PostingTech.posting_id)
            .where(
                PostingTech.skill_id == skill_id,
                Posting.pool == "global",
                Posting.is_deleted.is_(False),
                PostingTech.is_deleted.is_(False),
            )
        )
        or 0
    )
    return matched / total * 100


def get_github_vitality(session: Session) -> tuple[list[dict], date | None, int]:
    snapshot_date = get_latest_snapshot_date(session)
    if snapshot_date is None:
        return [], None, 0

    rows = (
        session.execute(
            select(GithubRepoSnapshot).where(
                GithubRepoSnapshot.snapshot_date == snapshot_date,
                GithubRepoSnapshot.is_deleted.is_(False),
                GithubRepoSnapshot.language.isnot(None),
            )
        )
        .scalars()
        .all()
    )

    by_lang: dict[str, list[GithubRepoSnapshot]] = {}
    for row in rows:
        by_lang.setdefault(row.language, []).append(row)

    today = date.today()
    languages = []
    for lang, repos in by_lang.items():
        total_stars = sum(r.stargazers_count for r in repos) or 1
        total_forks = sum(r.forks_count for r in repos)
        total_issues = sum(r.open_issues_count for r in repos)
        push_days = sorted((today - r.pushed_at).days for r in repos if r.pushed_at is not None)
        median_days = push_days[len(push_days) // 2] if push_days else None

        skill = _build_skill_lookup(session).get(lang.lower())
        job_demand_pct = _global_skill_share_pct(session, skill[0]) if skill else None

        languages.append(
            {
                "lang": lang,
                "repo_n": len(repos),
                "fork_ratio": round(total_forks / total_stars * 100, 1),
                "issue_per_1k_star": round(total_issues / total_stars * 1000, 1),
                "median_days_since_push": median_days,
                "job_demand_pct": round(job_demand_pct, 3) if job_demand_pct is not None else None,
                "in_taxonomy": skill is not None,
            }
        )

    languages.sort(key=lambda x: x["repo_n"], reverse=True)
    return languages, snapshot_date, len(rows)


def get_github_topics(
    session: Session, *, owned_skill_ids: set[int] | None = None, limit: int = 30
) -> tuple[list[dict], date | None, int]:
    snapshot_date = get_latest_snapshot_date(session)
    if snapshot_date is None:
        return [], None, 0

    rows = session.execute(
        select(GithubRepoSnapshot.topics).where(
            GithubRepoSnapshot.snapshot_date == snapshot_date,
            GithubRepoSnapshot.is_deleted.is_(False),
        )
    ).all()

    total_repos = len(rows)
    topic_reach: dict[str, int] = {}
    for (topics,) in rows:
        for topic in set(topics or []):
            topic_reach[topic] = topic_reach.get(topic, 0) + 1

    skill_lookup = _build_skill_lookup(session)

    items = []
    for topic, reach in topic_reach.items():
        match = skill_lookup.get(topic.lower())
        if not match:
            continue
        skill_id, canonical, category = match
        job_demand_pct = _global_skill_share_pct(session, skill_id)
        items.append(
            {
                "canonical": canonical,
                "category": category,
                "repo_reach": reach,
                "reach_pct": round(reach / total_repos * 100, 1) if total_repos else 0.0,
                "job_demand_pct": round(job_demand_pct, 3),
                "owned": (skill_id in owned_skill_ids) if owned_skill_ids is not None else None,
            }
        )

    items.sort(key=lambda x: x["repo_reach"], reverse=True)
    return items[:limit], snapshot_date, total_repos


def get_github_chronicle(session: Session, *, limit_techs: int = 15) -> tuple[list[dict], list[int], date | None, int]:
    """기술별 대표 레포(현재 최다 스타) 1개씩 뽑아, 그 대표 레포 집합 안에서의 연도별 스타 순위 변천사."""
    snapshot_date = get_latest_snapshot_date(session)
    if snapshot_date is None:
        return [], [], None, 0

    latest_rows = session.execute(
        select(GithubRepoSnapshot.full_name, GithubRepoSnapshot.language, GithubRepoSnapshot.stargazers_count).where(
            GithubRepoSnapshot.snapshot_date == snapshot_date,
            GithubRepoSnapshot.language.isnot(None),
            GithubRepoSnapshot.is_deleted.is_(False),
        )
    ).all()

    skill_lookup = _build_skill_lookup(session)

    best_per_tech: dict[str, tuple[str, int]] = {}
    for full_name, language, stars in latest_rows:
        match = skill_lookup.get(language.lower())
        if not match:
            continue
        canonical = match[1]
        if canonical not in best_per_tech or stars > best_per_tech[canonical][1]:
            best_per_tech[canonical] = (full_name, stars)

    top_techs = sorted(best_per_tech.items(), key=lambda kv: kv[1][1], reverse=True)[:limit_techs]
    repo_to_tech = {repo: tech for tech, (repo, _stars) in top_techs}
    repo_names = list(repo_to_tech)
    if not repo_names:
        return [], [], snapshot_date, 0

    history_rows = session.execute(
        select(GithubStarHistory.full_name, GithubStarHistory.month, GithubStarHistory.stargazers_count).where(
            GithubStarHistory.full_name.in_(repo_names),
            GithubStarHistory.is_deleted.is_(False),
        )
    ).all()

    per_repo_year: dict[tuple[str, int], tuple[int, int]] = {}
    for full_name, month, stars in history_rows:
        key = (full_name, month.year)
        if key not in per_repo_year or month.month > per_repo_year[key][0]:
            per_repo_year[key] = (month.month, stars)

    years = sorted({year for (_repo, year) in per_repo_year})

    lines_by_tech: dict[str, list[dict]] = {}
    for year in years:
        year_stars = [
            (repo, per_repo_year[(repo, year)][1]) for repo in repo_names if (repo, year) in per_repo_year
        ]
        year_stars.sort(key=lambda rs: rs[1], reverse=True)
        for rank, (repo, stars) in enumerate(year_stars, start=1):
            lines_by_tech.setdefault(repo_to_tech[repo], []).append(
                {"year": year, "rank": rank, "stars": stars}
            )

    lines = [
        {"tech": tech, "repo": next(repo for repo, t in repo_to_tech.items() if t == tech), "points": points}
        for tech, points in lines_by_tech.items()
    ]
    lines.sort(key=lambda line: best_per_tech[line["tech"]][1], reverse=True)

    return lines, years, snapshot_date, len(repo_names)
