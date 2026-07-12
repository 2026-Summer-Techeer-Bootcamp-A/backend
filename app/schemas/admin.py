from datetime import datetime
from pydantic import BaseModel, ConfigDict


class CollectorSourceStatus(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    source: str
    last_run_at: datetime | None = None
    ingested_count: int = 0
    error: str | None = None


class CollectorStatusResponse(BaseModel):
    sources: list[CollectorSourceStatus]


class CollectorRunRequest(BaseModel):
    source: str | None = None
    limit: int | None = None
    from_year: int | None = None
    to_year: int | None = None


class CollectorRunResponse(BaseModel):
    job_id: str
    sources: list[str]
