"""GitHub 레포 단위 인사이트 라우터(t,u,l). ETL 미실행 시 빈 결과 + sample_size=0을 반환한다."""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Header, Query

from app.core.config import settings
from app.core.deps import SessionDep
from app.crud.github_insight import get_github_chronicle, get_github_topics, get_github_vitality
from app.routers.match import resolve_optional_owned_skill_ids
from app.schemas.github_insight import (
    GithubChronicleResponse,
    GithubTopicsResponse,
    GithubVitalityResponse,
)
from app.services.reference_cache import get_cached, make_reference_cache_key, set_cached

router = APIRouter()


@router.get("/trend/github-vitality", response_model=GithubVitalityResponse)
def trend_github_vitality(session: SessionDep) -> GithubVitalityResponse:
    """언어별 GitHub 활력도(fork율, issue율, 최근 푸시일) + 채용수요 비교."""
    cache_key = make_reference_cache_key("trend_github_vitality", {})
    cached = get_cached(cache_key, GithubVitalityResponse)
    if cached is not None:
        return cached

    languages, snapshot_date, sample_size = get_github_vitality(session=session)
    response = GithubVitalityResponse(
        languages=languages,
        as_of=(snapshot_date.isoformat() if snapshot_date else date.today().isoformat()),
        sample_size=sample_size,
        note=(
            "GitHub 일별 스냅샷 기준. job_demand_pct는 global 풀 공고 대비 비율"
            if snapshot_date
            else "github_repo_snapshot 테이블이 비어있음 — scripts/ingest_github_snapshots.py 실행 필요"
        ),
    )
    set_cached(cache_key, response, settings.stats_cache_ttl_seconds)
    return response


@router.get("/trend/github-topics", response_model=GithubTopicsResponse)
def trend_github_topics(
    session: SessionDep,
    resume_id: Annotated[int | None, Query(description="저장 이력서 ID(선택)")] = None,
    session_id: Annotated[str | None, Query(description="비로그인 분석 세션 ID(선택)")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> GithubTopicsResponse:
    """GitHub topics 태그 기반 관심(reach) vs 채용수요(job_demand_pct) 비교."""
    owned_skill_ids = resolve_optional_owned_skill_ids(session, resume_id, session_id, authorization)

    # 보유기술이 섞이면 사용자마다 응답이 달라지므로 익명 요청(owned_skill_ids=None)만 캐시한다.
    cache_key = None
    if owned_skill_ids is None:
        cache_key = make_reference_cache_key("trend_github_topics", {})
        cached = get_cached(cache_key, GithubTopicsResponse)
        if cached is not None:
            return cached

    items, snapshot_date, sample_size = get_github_topics(session=session, owned_skill_ids=owned_skill_ids)
    response = GithubTopicsResponse(
        items=items,
        as_of=(snapshot_date.isoformat() if snapshot_date else date.today().isoformat()),
        sample_size=sample_size,
        note=(
            "GitHub topics taxonomy 자동 매칭(수동 매핑 없음)"
            if snapshot_date
            else "github_repo_snapshot 테이블이 비어있음 — scripts/ingest_github_snapshots.py 실행 필요"
        ),
    )
    if cache_key is not None:
        set_cached(cache_key, response, settings.stats_cache_ttl_seconds)
    return response


@router.get("/trend/github-chronicle", response_model=GithubChronicleResponse)
def trend_github_chronicle(
    session: SessionDep,
    limit_techs: Annotated[int, Query(ge=1, le=30)] = 15,
) -> GithubChronicleResponse:
    """기술별 대표 레포의 연도별 스타 순위 변천사."""
    cache_key = make_reference_cache_key("trend_github_chronicle", {"limit_techs": limit_techs})
    cached = get_cached(cache_key, GithubChronicleResponse)
    if cached is not None:
        return cached

    lines, years, snapshot_date, sample_size = get_github_chronicle(session=session, limit_techs=limit_techs)
    response = GithubChronicleResponse(
        years=years,
        lines=lines,
        as_of=(snapshot_date.isoformat() if snapshot_date else date.today().isoformat()),
        sample_size=sample_size,
        note=(
            "language 필드로 매칭된 대표 레포 1개씩의 스타 히스토리 기준(원본 수동 큐레이션 목록은 미보존)"
            if snapshot_date
            else "github_repo_snapshot/github_star_history 테이블이 비어있음 — scripts/ingest_github_snapshots.py 실행 필요"
        ),
    )
    set_cached(cache_key, response, settings.stats_cache_ttl_seconds)
    return response
