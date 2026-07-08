from fastapi import FastAPI
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
from app.routers.resume import router as resume_router
from app.routers.skills import router as skills_router
from app.routers.match import router as match_router
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 애플리케이션 시작 시, SQLAlchemy 모델들을 기반으로 DB에 아직 없는 테이블들을 모두 자동 생성합니다.
    # 추후 Alembic 등 마이그레이션 도구가 정착되면 제거할 개발 편의성 코드입니다.
    Base.metadata.create_all(bind=engine)
    yield

app = FastAPI(title=settings.otel_service_name, lifespan=lifespan)


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
app.include_router(resume_router, prefix="/api/v1/resume", tags=["resume"])
app.include_router(skills_router, tags=["skills"])
app.include_router(match_router, prefix="/api/v1/match", tags=["match"])


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
