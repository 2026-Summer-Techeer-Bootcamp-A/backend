from fastapi import Depends, FastAPI
from prometheus_fastapi_instrumentator import Instrumentator
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session
import os
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi import Request, HTTPException
from sqlalchemy import inspect, text
from app.core.db import engine

from app.core.config import settings
from app.core.db import get_session
from app.models import Person

app = FastAPI(title=settings.otel_service_name)

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


class PersonOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str


@app.get("/")
def read_root(request: Request):
    if not templates:
        return {"error": "Templates not loaded"}
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}

@app.get("/test-ui")
def test_ui(request: Request):
    if not templates:
        return {"error": "Templates not loaded"}
    return templates.TemplateResponse("dashboard.html", {"request": request})

@app.get("/easy-dash")
def easy_dash(request: Request):
    if not templates:
        return {"error": "Templates not loaded"}
    return templates.TemplateResponse("easy_dash.html", {"request": request})

@app.get("/db-viewer")
def db_viewer(request: Request):
    if not templates:
        return {"error": "Templates not loaded"}
    return templates.TemplateResponse("db_viewer.html", {"request": request})

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
