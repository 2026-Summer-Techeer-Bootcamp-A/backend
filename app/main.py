from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_fastapi_instrumentator import Instrumentator
from pydantic import BaseModel, ConfigDict
import os
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi import Request, HTTPException
from sqlalchemy import inspect, text

import app.models  # noqa: F401
from app.core.config import settings
from app.core.db import engine
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
from app.routers.insight import router as insight_router
from app.routers.github_insight import router as github_insight_router
from app.routers.admin import router as admin_router
from app.routers.search import router as search_router
from app.routers.chat import router as chat_router
from app.routers.news import router as news_router
from app.routers.feed import router as feed_router
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 애플리케이션 시작 시, SQLAlchemy 모델들을 기반으로 DB에 아직 없는 테이블들을 모두 자동 생성합니다.
    # 추후 Alembic 등 마이그레이션 도구가 정착되면 제거할 개발 편의성 코드입니다.
    Base.metadata.create_all(bind=engine)
    
    with engine.begin() as conn:
        conn.execute(text('ALTER TABLE "user" ADD COLUMN IF NOT EXISTS is_admin BOOLEAN NOT NULL DEFAULT false;'))
        conn.execute(text('ALTER TABLE "resume" ADD COLUMN IF NOT EXISTS memo TEXT;'))
        conn.execute(text('ALTER TABLE "resume" ADD COLUMN IF NOT EXISTS is_primary BOOLEAN NOT NULL DEFAULT false;'))
        conn.execute(text("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_resume_user_primary
            ON "resume" (user_id)
            WHERE is_primary;
        """))

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
    yield

app = FastAPI(title=settings.otel_service_name, lifespan=lifespan)

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


Instrumentator().instrument(app).expose(app)

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
app.include_router(skills_router, tags=["skills"])
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
def healthz() -> dict[str, str]:
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
