from fastapi import Depends, FastAPI
from prometheus_fastapi_instrumentator import Instrumentator
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_session
from app.models import Person

app = FastAPI(title=settings.otel_service_name)

Instrumentator().instrument(app).expose(app)

# TODO: wire up Redis client using settings.redis_url.
# TODO: wire up OTel trace export to settings.otel_exporter_otlp_endpoint.
# TODO: configure structured JSON logging.


class PersonOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str


@app.get("/", response_model=list[PersonOut])
def read_root(session: Session = Depends(get_session)) -> list[Person]:
    return list(session.execute(select(Person).order_by(Person.id)).scalars().all())


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}
