import anyio.to_thread
import logging
from fastapi import FastAPI, Response
from fastapi.responses import ORJSONResponse
from fastapi.middleware.cors import CORSMiddleware
from prometheus_fastapi_instrumentator import Instrumentator
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Gauge, generate_latest, multiprocess
from pydantic import BaseModel, ConfigDict
import os
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi import Request, HTTPException
from sqlalchemy import event, inspect, text

import app.models  # noqa: F401
from app.core.config import settings
from app.core.db import MAX_OVERFLOW, POOL_SIZE, SessionLocal, engine
from app.core.db import Base
from app.routers.auth import router as auth_router
from app.routers.cert import router as cert_router
from app.routers.job_categories import router as job_categories_router
from app.routers.resume import router as resume_router
from app.routers.skills import router as skills_router
from app.routers.match import router as match_router
from app.routers.posting import router as posting_router
from app.routers.posting_map import router as posting_map_router
from app.routers.company import router as company_router
from app.routers.insight import router as insight_router, warm_concept_tech_cache
from app.routers.github_insight import router as github_insight_router
from app.routers.admin import router as admin_router
from app.routers.search import router as search_router
from app.routers.chat import router as chat_router
from app.routers.news import router as news_router
from app.routers.feed import router as feed_router
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)


def _warm_dashboard_caches() -> None:
    try:
        with SessionLocal() as session:
            warm_concept_tech_cache(session)
    except Exception:
        # Cache warming is an optimization and must never prevent startup.
        logger.exception("Failed to warm concept-tech cache")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 애플리케이션 시작 시, SQLAlchemy 모델들을 기반으로 DB에 아직 없는 테이블들을 모두 자동 생성합니다.
    # 추후 Alembic 등 마이그레이션 도구가 정착되면 제거할 개발 편의성 코드입니다.
    Base.metadata.create_all(bind=engine)

    # anyio 기본 스레드 리미터를 워커별로 상향한다. --workers > 1이면 이 lifespan은
    # 워커마다 각자 실행되므로, 이 설정도 워커마다 각자 적용되어야 한다 — 아래 advisory
    # lock은 DDL 중복 실행만 막기 위한 것이라, 이 리미터 설정은 그 lock/unlock과
    # 무관하게 매 워커에서 독립적으로 실행돼야 하므로 lock 블록 밖(앞)에 둔다.
    anyio.to_thread.current_default_thread_limiter().total_tokens = settings.thread_pool_size

    # --workers > 1이면 워커 프로세스마다 이 lifespan이 각자 실행된다. 아래 DDL을
    # 동시에 두 워커가 각자 트랜잭션 안에서 실행하면 서로의 테이블 락을 기다리며
    # 데드락에 빠진다(실측: 2 워커 배포 직후 헬스체크가 영영 안 뜸). 세션 단위
    # advisory lock으로 워커 간 실행을 직렬화한다 — 먼저 잡은 워커가 전체 DDL을
    # 끝내고 풀어주면, 나머지는 IF NOT EXISTS라 곧바로 통과한다.
    lock_conn = engine.connect()
    lock_conn.execute(text("SELECT pg_advisory_lock(727123)"))

    with engine.begin() as conn:
        conn.execute(text('ALTER TABLE "user" ADD COLUMN IF NOT EXISTS is_admin BOOLEAN NOT NULL DEFAULT false;'))
        conn.execute(text('ALTER TABLE "resume" ADD COLUMN IF NOT EXISTS memo TEXT;'))
        conn.execute(text('ALTER TABLE "resume" ADD COLUMN IF NOT EXISTS is_primary BOOLEAN NOT NULL DEFAULT false;'))
        conn.execute(text("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_resume_user_primary
            ON "resume" (user_id)
            WHERE is_primary;
        """))

        # 복합 인덱스로 대체된 단일 컬럼 인덱스 정리(perf/db-index-tuning).
        # 실제 쿼리 패턴(app/crud/insight.py, app/crud/github_insight.py) 기준으로
        # interest_signal은 skill_id+source 동등조건, github_repo_snapshot은
        # snapshot_date+language 동등조건, github_star_history는 full_name IN(...)
        # 조합이 반복되어 복합 인덱스가 단일 컬럼 인덱스보다 훨씬 유리함.
        conn.execute(text('DROP INDEX IF EXISTS ix_interest_signal_skill_id;'))
        conn.execute(text(
            'CREATE INDEX IF NOT EXISTS ix_interest_signal_skill_source '
            'ON interest_signal (skill_id, source);'
        ))
        conn.execute(text('DROP INDEX IF EXISTS ix_github_repo_snapshot_snapshot_date;'))
        conn.execute(text('DROP INDEX IF EXISTS ix_github_repo_snapshot_language;'))
        conn.execute(text(
            'CREATE INDEX IF NOT EXISTS ix_github_repo_snapshot_date_lang '
            'ON github_repo_snapshot (snapshot_date, language);'
        ))
        conn.execute(text('DROP INDEX IF EXISTS ix_github_star_history_full_name;'))
        conn.execute(text(
            'CREATE INDEX IF NOT EXISTS ix_github_star_history_name_month '
            'ON github_star_history (full_name, month);'
        ))

        # pgvector 코사인 유사도 검색(app/services/rag/tools/vector_tool.py, <=> 연산자) 전용
        # HNSW 인덱스. 지금까지 posting_embedding에 인덱스가 없어 순차 스캔으로 코사인 거리를
        # 전부 계산하고 있었음 — 데이터가 늘수록 느려짐.
        conn.execute(text(
            'CREATE INDEX IF NOT EXISTS ix_posting_embedding_hnsw_cosine '
            'ON posting_embedding USING hnsw (embedding vector_cosine_ops);'
        ))

        # posting_embedding은 이미 존재하는 테이블이라 Base.metadata.create_all이 새로
        # 추가된 컬럼을 반영하지 못한다(테이블이 없을 때만 생성) — 그래서 컬럼 추가는
        # 여기 명시적 DDL로 처리한다. 코퍼스의 78%가 비개발 공고라 의미 검색 대상을
        # 개발 공고로 좁히기 위한 플래그(app/models/posting.py PostingEmbedding 참고).
        conn.execute(text(
            'ALTER TABLE posting_embedding ADD COLUMN IF NOT EXISTS '
            'is_tech_posting boolean NOT NULL DEFAULT false;'
        ))

        # 위 is_tech_posting = true 조건으로 좁힌 부분 HNSW 인덱스. 전체 인덱스(3.68GB)가
        # shared_buffers(2.4GB)보다 커 메모리에 다 못 올라가던 문제를, 개발 공고만
        # 남기면 약 0.8GB로 줄여 해결한다. vector_tool.py의 쿼리 조건과 정확히 일치해야
        # 플래너가 이 인덱스를 탄다.
        conn.execute(text(
            'CREATE INDEX IF NOT EXISTS ix_posting_embedding_hnsw_tech '
            'ON posting_embedding USING hnsw (embedding vector_cosine_ops) '
            'WHERE is_tech_posting = true;'
        ))

        # Create mv_skill_share materialized view if not exists
        conn.execute(text("""
            CREATE MATERIALIZED VIEW IF NOT EXISTS mv_skill_share AS
            WITH pool_pos_total AS (
                SELECT
                    p.pool,
                    pc.category AS position,
                    COUNT(DISTINCT p.id) AS total_postings
                FROM posting p
                JOIN posting_category pc ON pc.posting_id = p.id AND pc.is_deleted = false
                WHERE p.is_deleted = false
                GROUP BY p.pool, pc.category
            )
            SELECT
                p.pool,
                pc.category AS position,
                pt.skill_id,
                s.canonical AS skill_canonical,
                COUNT(DISTINCT p.id) AS posting_count,
                total_postings,
                (COUNT(DISTINCT p.id)::float / NULLIF(total_postings, 0)) AS share
            FROM posting p
            JOIN posting_category pc ON pc.posting_id = p.id AND pc.is_deleted = false
            JOIN posting_tech pt ON pt.posting_id = p.id AND pt.is_deleted = false
            JOIN skill s ON s.id = pt.skill_id AND s.is_deleted = false
            JOIN pool_pos_total ppt ON ppt.pool = p.pool AND ppt.position = pc.category
            WHERE p.is_deleted = false
            GROUP BY p.pool, pc.category, pt.skill_id, s.canonical, total_postings;
        """))

        # Create mv_cooccurrence materialized view if not exists
        conn.execute(text("""
            CREATE MATERIALIZED VIEW IF NOT EXISTS mv_cooccurrence AS
            WITH skill_totals AS (
                SELECT
                    p.pool,
                    pt.skill_id,
                    COUNT(DISTINCT p.id) AS skill_total_postings
                FROM posting p
                JOIN posting_tech pt ON pt.posting_id = p.id AND pt.is_deleted = false
                WHERE p.is_deleted = false
                GROUP BY p.pool, pt.skill_id
            )
            SELECT
                p.pool,
                pt1.skill_id AS skill_id_1,
                pt2.skill_id AS skill_id_2,
                COUNT(DISTINCT p.id) AS co_count,
                (COUNT(DISTINCT p.id)::float / NULLIF(st.skill_total_postings, 0)) AS co_rate
            FROM posting p
            JOIN posting_tech pt1 ON pt1.posting_id = p.id AND pt1.is_deleted = false
            JOIN posting_tech pt2 ON pt2.posting_id = p.id AND pt2.is_deleted = false AND pt2.skill_id <> pt1.skill_id
            JOIN skill_totals st ON st.pool = p.pool AND st.skill_id = pt1.skill_id
            WHERE p.is_deleted = false
            GROUP BY p.pool, pt1.skill_id, pt2.skill_id, st.skill_total_postings;
        """))

        # Create mv_industry_fingerprint materialized view if not exists
        conn.execute(text("""
            CREATE MATERIALIZED VIEW IF NOT EXISTS mv_industry_fingerprint AS
            WITH industry_totals AS (
                SELECT p.industry, COUNT(*) AS industry_total
                FROM posting p
                WHERE p.pool = 'domestic'
                  AND p.industry IS NOT NULL
                  AND p.is_deleted = false
                GROUP BY p.industry
            ),
            industry_skill_counts AS (
                SELECT p.industry, s.canonical AS skill_canonical,
                       COUNT(DISTINCT p.id) AS posting_count
                FROM posting p
                JOIN posting_tech pt ON pt.posting_id = p.id AND pt.is_deleted = false
                JOIN skill s ON s.id = pt.skill_id AND s.is_deleted = false
                WHERE p.pool = 'domestic'
                  AND p.industry IS NOT NULL
                  AND p.is_deleted = false
                GROUP BY p.industry, s.canonical
            ),
            industry_skill_shares AS (
                SELECT isc.industry, isc.skill_canonical, isc.posting_count,
                       it.industry_total,
                       isc.posting_count::float / NULLIF(it.industry_total, 0) AS share
                FROM industry_skill_counts isc
                JOIN industry_totals it ON it.industry = isc.industry
            )
            SELECT industry, skill_canonical, posting_count, industry_total, share,
                   AVG(share) OVER (PARTITION BY skill_canonical) AS avg_share
            FROM industry_skill_shares;
        """))

        # Create mv_role_stack_fit materialized view if not exists
        conn.execute(text("""
            CREATE MATERIALIZED VIEW IF NOT EXISTS mv_role_stack_fit AS
            WITH category_totals AS (
                SELECT
                    p.pool,
                    pc.category,
                    COUNT(DISTINCT p.id) AS category_total
                FROM posting p
                JOIN posting_category pc
                  ON pc.posting_id = p.id AND pc.is_deleted = false
                JOIN job_category jc
                  ON jc.name = pc.category
                 AND jc.is_tech = true
                 AND jc.is_deleted = false
                WHERE p.is_deleted = false
                GROUP BY p.pool, pc.category
            ),
            category_skill_counts AS (
                SELECT
                    p.pool,
                    pc.category,
                    s.canonical AS skill_canonical,
                    COUNT(DISTINCT p.id) AS posting_count
                FROM posting p
                JOIN posting_category pc
                  ON pc.posting_id = p.id AND pc.is_deleted = false
                JOIN job_category jc
                  ON jc.name = pc.category
                 AND jc.is_tech = true
                 AND jc.is_deleted = false
                JOIN posting_tech pt
                  ON pt.posting_id = p.id AND pt.is_deleted = false
                JOIN skill s
                  ON s.id = pt.skill_id AND s.is_deleted = false
                WHERE p.is_deleted = false
                GROUP BY p.pool, pc.category, s.canonical
            )
            SELECT
                ct.pool,
                ct.category,
                csc.skill_canonical,
                COALESCE(csc.posting_count, 0) AS posting_count,
                ct.category_total
            FROM category_totals ct
            LEFT JOIN category_skill_counts csc
              ON csc.pool = ct.pool AND csc.category = ct.category;
        """))

        # Create mv_global_domestic_gap materialized view if not exists
        conn.execute(text("""
            CREATE MATERIALIZED VIEW IF NOT EXISTS mv_global_domestic_gap AS
            WITH pool_totals AS (
                SELECT
                    COUNT(*) FILTER (WHERE p.pool = 'global') AS global_total,
                    COUNT(*) FILTER (WHERE p.pool = 'domestic') AS domestic_total
                FROM posting p
                WHERE p.is_deleted = false
            ),
            skill_counts AS (
                SELECT
                    s.id AS skill_id,
                    s.canonical,
                    s.category,
                    COUNT(DISTINCT p.id) FILTER (WHERE p.pool = 'global') AS global_n,
                    COUNT(DISTINCT p.id) FILTER (WHERE p.pool = 'domestic') AS domestic_n
                FROM posting p
                JOIN posting_tech pt ON pt.posting_id = p.id AND pt.is_deleted = false
                JOIN skill s ON s.id = pt.skill_id AND s.is_deleted = false
                WHERE p.is_deleted = false
                  AND p.pool IN ('global', 'domestic')
                GROUP BY s.id, s.canonical, s.category
            ),
            skill_shares AS (
                SELECT
                    sc.skill_id,
                    sc.canonical,
                    sc.category,
                    sc.global_n,
                    sc.domestic_n,
                    COALESCE(
                        ROUND(sc.global_n::numeric / NULLIF(pt.global_total, 0) * 100, 2),
                        0.0
                    ) AS global_pct,
                    COALESCE(
                        ROUND(sc.domestic_n::numeric / NULLIF(pt.domestic_total, 0) * 100, 2),
                        0.0
                    ) AS domestic_pct,
                    pt.global_total,
                    pt.domestic_total
                FROM skill_counts sc
                CROSS JOIN pool_totals pt
            )
            SELECT
                skill_id,
                canonical,
                category,
                global_n,
                domestic_n,
                global_pct,
                domestic_pct,
                ROUND(global_pct - domestic_pct, 2) AS diff,
                global_total,
                domestic_total
            FROM skill_shares;
        """))

        # Create mv_skill_trend_yearly materialized view if not exists
        conn.execute(text("""
            CREATE MATERIALIZED VIEW IF NOT EXISTS mv_skill_trend_yearly AS
            WITH year_totals AS (
                SELECT
                    p.pool,
                    EXTRACT(YEAR FROM p.post_date)::int AS year,
                    COUNT(*) AS year_total
                FROM posting p
                WHERE p.is_deleted = false
                  AND p.post_date IS NOT NULL
                GROUP BY p.pool, EXTRACT(YEAR FROM p.post_date)
            ),
            skill_year_counts AS (
                SELECT
                    p.pool,
                    EXTRACT(YEAR FROM p.post_date)::int AS year,
                    s.canonical,
                    COUNT(*) AS skill_count
                FROM posting p
                JOIN posting_tech pt
                  ON pt.posting_id = p.id AND pt.is_deleted = false
                JOIN skill s
                  ON s.id = pt.skill_id AND s.is_deleted = false
                WHERE p.is_deleted = false
                  AND p.post_date IS NOT NULL
                GROUP BY p.pool, EXTRACT(YEAR FROM p.post_date), s.canonical
            ),
            skill_totals AS (
                SELECT
                    syc.pool,
                    syc.canonical,
                    SUM(syc.skill_count) AS skill_total
                FROM skill_year_counts syc
                GROUP BY syc.pool, syc.canonical
            )
            SELECT
                yt.pool,
                yt.year,
                syc.canonical,
                COALESCE(syc.skill_count, 0) AS skill_count,
                COALESCE(st.skill_total, 0) AS skill_total,
                yt.year_total
            FROM year_totals yt
            LEFT JOIN skill_year_counts syc
              ON syc.pool = yt.pool AND syc.year = yt.year
            LEFT JOIN skill_totals st
              ON st.pool = syc.pool AND st.canonical = syc.canonical;
        """))

        # Enable pg_trgm extension for trigram search
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm;"))

        # GIN trigram indexes for title and company (for LIKE/ILIKE optimization)
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_posting_title_trgm ON posting USING gin (title gin_trgm_ops);"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_posting_company_trgm ON posting USING gin (company gin_trgm_ops);"))

        # Composite index for posting list filters (pool, close_date, post_date DESC)
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_posting_list_filter
            ON posting (pool, close_date, post_date DESC)
            WHERE is_deleted = false;
        """))

        # /api/v1/postings 목록 쿼리 전용. _apply_posting_filters가 만드는 조건은
        # "is_deleted IS false"인데, 이 테이블의 부분 인덱스들(ix_posting_list_filter,
        # ix_posting_region_district, ix_posting_coordinates 등)은 전부 "WHERE
        # is_deleted = false" 조건으로 만들어져 있다. is_deleted가 NOT NULL default
        # false라 두 식은 의미상 같지만, 플래너는 그 술어 함의를 증명하지 못해 이
        # 부분 인덱스들을 전혀 쓰지 못한다 — 그래서 이 인덱스는 부분 조건을 두지 않고
        # is_deleted를 선두 컬럼에 두어, 술어가 IS false로 나오든 = false로 나오든
        # 동등조건으로 매칭되게 한다. 또한 _get_filtered_postings의 ORDER BY는
        # "(post_date IS NULL), post_date DESC, id DESC" 순으로 정렬하는데(NULLS LAST를
        # 흉내 낸 관용구), 플래너는 ORDER BY와 인덱스를 구문적으로 맞추므로 이 표현식이
        # 인덱스 선두에 없으면 정렬에 인덱스를 못 태운다. 적용 후 프로덕션 실측
        # _get_filtered_postings 332.4ms -> 4.2ms.
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_posting_list_order
            ON posting (is_deleted, pool, ((post_date IS NULL)), post_date DESC, id DESC);
        """))

        # /api/v1/postings/{id}/nearby 전용. get_nearby_postings는 같은 자치구의 최신
        # 공고를 찾으려고 "region_district = X AND is_deleted IS false" 조건에
        # "(post_date IS NULL), post_date DESC, id DESC" 정렬을 건다. 위 목록 인덱스와
        # 똑같은 이유로 기존 부분 인덱스 ix_posting_region_district(WHERE is_deleted =
        # false)는 술어 함의 증명 실패로 쓰이지 못했고, 정렬 표현식도 담고 있지 않아
        # 매칭 행 전체를 seq scan 후 정렬하고 있었다. 부분 조건 없이 is_deleted를 선두에
        # 두고 region_district 동등키 뒤에 정렬 컬럼을 붙여 인덱스를 태운다. 적용 후
        # 프로덕션 실측 get_nearby_postings 480.7ms -> 24.4ms.
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_posting_nearby
            ON posting (is_deleted, region_district, ((post_date IS NULL)), post_date DESC, id DESC);
        """))

        # Coordinates B-Tree composite index
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_posting_coordinates 
            ON posting (pool, lat, lng) 
            WHERE is_deleted = false AND lat IS NOT NULL AND lng IS NOT NULL;
        """))

        # Region district index
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_posting_region_district 
            ON posting (region_district) 
            WHERE is_deleted = false;
        """))

        # Materialized view for stats_newcomer_gate
        conn.execute(text("""
            CREATE MATERIALIZED VIEW IF NOT EXISTS mv_newcomer_gate AS
            SELECT
                s.canonical AS skill_canonical,
                COUNT(DISTINCT p.id) AS postings,
                SUM(CASE WHEN p.career_min <= 0 THEN 1 ELSE 0 END) AS newcomer_postings
            FROM posting p
            JOIN posting_tech pt ON pt.posting_id = p.id AND pt.is_deleted = false
            JOIN skill s ON s.id = pt.skill_id AND s.is_deleted = false
            WHERE p.pool = 'domestic'
              AND p.is_deleted = false
              AND p.career_min IS NOT NULL
            GROUP BY s.canonical;
        """))

        # Index on mv_newcomer_gate postings
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_mv_newcomer_gate_postings ON mv_newcomer_gate (postings DESC);"))

        # Index on mv_skill_share for pool and position filter
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_mv_skill_share_pool_pos ON mv_skill_share (pool, position);"))

        # Aggregation indexes for region density and hot companies
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_posting_region_density_agg 
            ON posting (pool, region_district) 
            WHERE is_deleted = false;
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_posting_hot_companies_agg
            ON posting (pool, post_date, company)
            WHERE is_deleted = false;
        """))

        # stats/response-rate 전용. response_rate를 채우는 소스가 적어(현재 도메스틱
        # 풀은 매칭 0건) 결과는 항상 작은데도, 이 컬럼엔 인덱스가 없어 매번 테이블
        # 전체를 병렬 시퀀셜 스캔(부하테스트 실측: 2개 워커로 약 150ms, 2 vCPU를
        # 스캔 내내 통째로 점유)하고 있었다. WHERE 조건과 정확히 일치하는 부분
        # 인덱스로 바꿔 매칭 행이 없거나 적을 때 즉시 끝나게 한다.
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_posting_response_rate_agg
            ON posting (pool, company)
            WHERE is_deleted = false AND response_rate IS NOT NULL;
        """))

    lock_conn.execute(text("SELECT pg_advisory_unlock(727123)"))
    lock_conn.close()

    await anyio.to_thread.run_sync(_warm_dashboard_caches)

    yield

# 응답 직렬화를 orjson으로 바꾼다. 부하테스트에서 1200VU 구간 서버가 CPU 포화(앱 3코어,
# VM 0퍼센트 유휴)로 약 285 req/s에서 천장에 닿는 것을 실측했는데, 이 병목이 순수 CPU라
# 요청당 직렬화 비용을 줄이면 그만큼 천장이 올라간다. 공고 목록처럼 필드가 많은 JSON 응답이
# 대부분이라 기본 json 대비 orjson의 직렬화 이득이 크다.
app = FastAPI(
    title=settings.otel_service_name,
    lifespan=lifespan,
    default_response_class=ORJSONResponse,
)

# 프론트(Vercel) <-> 백엔드(GCP) 간 cross-origin 요청 허용.
# Bearer 토큰 인증이라 쿠키가 없으므로 allow_credentials는 False로 충분하다.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_origin_regex=settings.cors_origin_regex,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# http_request_duration_seconds{handler,method}는 라이브러리 기본값이 (0.1, 0.5, 1)초
# 버킷뿐이라, 1초를 넘는 요청은 전부 +Inf에 뭉개져 histogram_quantile이 정확한 값을
# 계산 못하고 마지막 유한 버킷(1000ms)에 고정된 값만 반환한다. 부하 테스트 중
# postings_search가 p95 60초까지 치솟았는데도 Grafana 대시보드는 계속 "1s"만 보여준
# 원인이 이거였다(관측 자체가 실패, 서버가 실제로 괜찮다는 뜻이 아니었다). handler별로
# 쪼갤 수 있는 저해상도 메트릭인 이 버킷만 넓힌다. 라벨 없는 고해상도 메트릭인
# http_request_duration_highr_seconds는 원래도 60초까지 세밀하게 잡고 있어 그대로 둔다.
Instrumentator().instrument(
    app,
    latency_lowr_buckets=(0.1, 0.5, 1, 2.5, 5, 10, 30, 60),
)

# 이번 부하테스트에서 QueuePool 타임아웃(전체 400건 한도가 아니라 워커별
# pool_size+max_overflow 40건 한도)이 실제 병목이었는데, 정작 Grafana엔 postgres
# 쪽 numbackends만 있고 앱이 자기 풀을 얼마나 쓰고 있는지 보여주는 지표가 없었다.
# checked_out은 워커마다 다른 풀 인스턴스를 갖고 있으니 워커 프로세스 합산
# (multiprocess_mode="livesum")으로 노출해야 전체 사용량이 나온다. capacity는
# 워커 수만큼 곱해진 이론상 총 상한이라 마찬가지로 합산한다.
#
# 처음엔 Gauge.set_function(lambda: engine.pool.checkedout())으로 만들었는데,
# 배포 후 실측해보니 항상 0만 나왔다. set_function은 이 Gauge 객체의 .collect()가
# 그 프로세스 안에서 직접 호출될 때만 값을 계산하는데, /metrics 핸들러는 매번
# multiprocess.MultiProcessCollector로 각 워커가 이미 파일에 써놓은 값을 읽어올
# 뿐 로컬 Gauge의 .collect()를 부르지 않는다. 즉 이 값은 절대 파일에 기록될
# 기회가 없었다. SQLAlchemy pool의 checkout/checkin 이벤트에 걸어 inc()/dec()로
# 바꾸니, 그 즉시 각 워커 프로세스의 공유 파일에 기록되어 제대로 합산된다.
_db_pool_checked_out = Gauge(
    "db_pool_checked_out_connections",
    "SQLAlchemy 커넥션 풀에서 현재 체크아웃된 커넥션 수(전체 워커 합산)",
    multiprocess_mode="livesum",
)


@event.listens_for(engine, "checkout")
def _on_pool_checkout(*_args):
    _db_pool_checked_out.inc()


@event.listens_for(engine, "checkin")
def _on_pool_checkin(*_args):
    _db_pool_checked_out.dec()


_db_pool_capacity = Gauge(
    "db_pool_capacity_connections",
    "SQLAlchemy 커넥션 풀 최대 용량(pool_size+max_overflow, 전체 워커 합산)",
    multiprocess_mode="livesum",
)
# 워커 하나당 고정값이라 set_function 없이 임포트 시점에 한 번만 설정해도 된다.
_db_pool_capacity.set(POOL_SIZE + MAX_OVERFLOW)

# --workers > 1이면 uvicorn이 워커마다 별도 프로세스를 띄우고, 각 프로세스는 자기만의
# 인메모리 레지스트리에 카운터를 쌓는다. Instrumentator().expose(app)가 만드는 기본
# /metrics는 그 중 요청을 우연히 받은 워커 하나의 값만 보여줘서, 스크레이프할 때마다
# 무작위로 다른(그리고 실제보다 훨씬 작은) 숫자가 나온다. PROMETHEUS_MULTIPROC_DIR에
# 워커들이 공유 파일로 값을 쓰게 하고, /metrics는 MultiProcessCollector로 그 파일들을
# 합산해서 응답해야 전체 워커 합계가 나온다.
@app.get("/metrics")
def metrics():
    # PROMETHEUS_MULTIPROC_DIR은 컨테이너(entrypoint.sh)에서만 준비된다. 테스트
    # venv처럼 그 변수가 없는 환경에서는 멀티프로세스 collector가 바로 예외를
    # 던지므로, 그럴 때는 단일 프로세스 기본 레지스트리로 폴백한다.
    if not os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
    registry = CollectorRegistry()
    multiprocess.MultiProcessCollector(registry)
    return Response(generate_latest(registry), media_type=CONTENT_TYPE_LATEST)

# Set up static files and templates
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
static_dir = os.path.join(BASE_DIR, "static")
templates_dir = os.path.join(BASE_DIR, "templates")

app.mount("/static", StaticFiles(directory=static_dir), name="static")
templates = Jinja2Templates(directory=templates_dir)

# TODO: wire up Redis client using settings.redis_url.
# TODO: wire up OTel trace export to settings.otel_exporter_otlp_endpoint.
# TODO: configure structured JSON logging.

app.include_router(auth_router, prefix="/api/v1/auth", tags=["auth"])
app.include_router(cert_router, prefix="/api/v1", tags=["cert"])
app.include_router(job_categories_router, prefix="/api/v1", tags=["job-categories"])
app.include_router(resume_router, prefix="/api/v1/resume", tags=["resume"])
app.include_router(skills_router, prefix="/api/v1", tags=["skills"])
app.include_router(match_router, prefix="/api/v1/match", tags=["match"])
app.include_router(posting_map_router, prefix="/api/v1", tags=["posting-map"])
app.include_router(posting_router, prefix="/api/v1", tags=["postings"])
app.include_router(company_router, prefix="/api/v1", tags=["company"])
app.include_router(insight_router, prefix="/api/v1", tags=["insight"])
app.include_router(github_insight_router, prefix="/api/v1", tags=["github-insight"])
app.include_router(admin_router, prefix="/api/v1", tags=["admin"])
app.include_router(search_router, prefix="/api/v1", tags=["search"])
app.include_router(chat_router, prefix="/api/v1", tags=["chat"])
app.include_router(news_router, prefix="/api/v1", tags=["news"])
app.include_router(feed_router, prefix="/api/v1", tags=["feed"])


class PersonOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str


@app.get("/")
def read_root(request: Request):
    if not templates:
        return {"error": "Templates not loaded"}
    return templates.TemplateResponse(request, "index.html")


@app.get("/healthz")
# 이 엔드포인트만 async def다: dict 하나만 반환할 뿐 DB/Redis 등 I/O가 전혀 없어
# 이벤트 루프에서 바로 실행해도 루프를 막지 않는다. async로 두면 anyio 스레드풀을
# 아예 거치지 않으므로, 다른 동기 엔드포인트들이 스레드풀을 모두 점유해 고갈되는
# 상황에서도 헬스체크만은 즉시 응답한다(실측: 동기 def일 때 300VU 부하에서 p95 27.3초).
async def healthz() -> dict[str, str]:
    return {"status": "ok"}

@app.get("/test-ui")
def test_ui(request: Request):
    if not templates:
        return {"error": "Templates not loaded"}
    return templates.TemplateResponse(request, "dashboard.html")

@app.get("/easy-dash")
def easy_dash(request: Request):
    if not templates:
        return {"error": "Templates not loaded"}
    return templates.TemplateResponse(request, "easy_dash.html")

@app.get("/db-viewer")
def db_viewer(request: Request):
    if not templates:
        return {"error": "Templates not loaded"}
    return templates.TemplateResponse(request, "db_viewer.html")

@app.get("/api/db/tables")
def get_db_tables():
    inspector = inspect(engine)
    tables = inspector.get_table_names()
    table_data = []
    with engine.connect() as conn:
        for t_name in tables:
            try:
                cnt = conn.execute(text(f'SELECT COUNT(*) FROM "{t_name}"')).scalar()
            except Exception:
                cnt = 0
            table_data.append({"name": t_name, "count": cnt})
    return {"tables": table_data}

@app.get("/api/db/tables/{table_name}")
def get_db_table_content(table_name: str, limit: int = 50):
    inspector = inspect(engine)
    tables = inspector.get_table_names()
    if table_name not in tables:
        raise HTTPException(status_code=404, detail="Table not found")
        
    with engine.connect() as conn:
        result = conn.execute(text(f'SELECT * FROM "{table_name}" LIMIT :limit'), {"limit": limit})
        columns = list(result.keys())
        # Handles binary and un-serializable objects by converting values to string if needed
        rows = []
        for row in result.fetchall():
            row_dict = {}
            for col, val in dict(row._mapping).items():
                row_dict[col] = str(val) if val is not None else None
            rows.append(row_dict)
        
    return {"columns": columns, "rows": rows}
