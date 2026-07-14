from datetime import datetime, timezone
import glob
import gzip
import os
import subprocess
import sys
import uuid

from fastapi import APIRouter, BackgroundTasks, HTTPException, status
from sqlalchemy import select, text

from app.core.db import SessionLocal
from app.core.deps import CurrentAdmin, SessionDep
from app.models.collector_run import CollectorRun
from app.schemas.admin import (
    CollectorRunRequest,
    CollectorRunResponse,
    CollectorSourceStatus,
    CollectorStatusResponse,
)

router = APIRouter()


def get_collector_cwd() -> str:
    if os.path.exists("/opt/collector"):
        return "/opt/collector"
    return os.path.abspath(
        os.path.join(os.path.dirname(__file__), "../../../gh-hn-data-collector/collector")
    )


def get_ingested_count(cwd: str, source: str) -> int:
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    file_path = None
    if source == "himalayas":
        file_path = os.path.join(cwd, "out", "himalayas", f"{today_str}.jsonl.gz")
    elif source == "jumpit":
        file_path = os.path.join(cwd, "out", "jumpit", f"{today_str}.jsonl.gz")
    elif source == "wanted":
        file_path = os.path.join(cwd, "out", "wanted", f"{today_str}.jsonl.gz")
    elif source == "wwr":
        file_path = os.path.join(cwd, "out", "wayback", "weworkremotely.jsonl.gz")
    elif source == "hn":
        hn_dir = os.path.join(cwd, "out", "hn", today_str)
        if os.path.exists(hn_dir):
            files = glob.glob(os.path.join(hn_dir, "*.jsonl.gz"))
            if files:
                files.sort(key=os.path.getmtime)
                file_path = files[-1]

    if file_path and os.path.exists(file_path):
        try:
            with gzip.open(file_path, "rt", encoding="utf-8") as f:
                return sum(1 for _ in f)
        except Exception:
            pass
    return 0


def run_collector_job(
    job_id: str,
    sources: list[str],
    limit: int | None = None,
    from_year: int | None = None,
    to_year: int | None = None,
):
    db = SessionLocal()
    try:
        cwd = get_collector_cwd()

        # Load environment variables from collector's .env file
        env = os.environ.copy()
        env_path = os.path.join(cwd, ".env")
        if os.path.exists(env_path):
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        key, val = line.split("=", 1)
                        env[key.strip()] = val.strip()

        for source in sources:
            # Create a run log entry
            run_record = CollectorRun(
                source=source,
                job_id=job_id,
                status="running",
                last_run_at=datetime.now(timezone.utc),
                ingested_count=0,
                error=None,
            )
            db.add(run_record)
            db.commit()

            # Map source to script arguments
            script_args = []
            if source == "himalayas":
                script_args = ["collect_himalayas.py"]
            elif source == "hn":
                script_args = ["collect_hn.py"]
            elif source == "wanted":
                script_args = ["collect_wanted.py"]
                if limit is not None:
                    script_args += ["--limit", str(limit)]
            elif source == "jumpit":
                script_args = ["collect_jumpit.py"]
                if limit is not None:
                    script_args += ["--limit", str(limit)]
            elif source == "wwr":
                script_args = ["backfill_wayback.py", "--sites", "weworkremotely", "--yes"]
                if limit is not None:
                    script_args += ["--limit", str(limit)]
                else:
                    script_args += ["--limit", "50"]
                if from_year is not None:
                    script_args += ["--from", str(from_year)]
                if to_year is not None:
                    script_args += ["--to", str(to_year)]
            else:
                continue

            try:
                # Execute collector script as subprocess inside container python environment
                cmd = [sys.executable] + script_args
                process = subprocess.Popen(
                    cmd,
                    cwd=cwd,
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                )

                output_lines = []
                if process.stdout:
                    for line in process.stdout:
                        sys.stdout.write(line)
                        sys.stdout.flush()
                        output_lines.append(line)

                process.wait()
                full_output = "".join(output_lines)

                if process.returncode == 0:
                    run_record.status = "success"
                    run_record.ingested_count = get_ingested_count(cwd, source)
                else:
                    run_record.status = "failed"
                    run_record.error = full_output or f"Return code {process.returncode}"
            except Exception as e:
                run_record.status = "failed"
                run_record.error = str(e)

            db.commit()

        # Automatically refresh materialized views
        try:
            db.execute(text("REFRESH MATERIALIZED VIEW mv_skill_share;"))
            db.execute(text("REFRESH MATERIALIZED VIEW mv_cooccurrence;"))
            db.execute(text("REFRESH MATERIALIZED VIEW mv_industry_fingerprint;"))
            db.execute(text("REFRESH MATERIALIZED VIEW mv_role_stack_fit;"))
            db.execute(text("REFRESH MATERIALIZED VIEW mv_global_domestic_gap;"))
            db.commit()
        except Exception as e:
            print(f"Error refreshing materialized views: {e}", flush=True)

    finally:
        db.close()


@router.get("/admin/collector/status", response_model=CollectorStatusResponse)
def get_collector_status(
    session: SessionDep,
    current_admin: CurrentAdmin,
) -> CollectorStatusResponse:
    sources = ["himalayas", "hn", "wwr", "wanted", "jumpit"]
    results = []
    for source in sources:
        stmt = (
            select(CollectorRun)
            .where(CollectorRun.source == source)
            .order_by(CollectorRun.created_at.desc())
            .limit(1)
        )
        run = session.execute(stmt).scalar_one_or_none()
        if run:
            results.append(
                CollectorSourceStatus(
                    source=source,
                    last_run_at=run.last_run_at,
                    ingested_count=run.ingested_count,
                    error=run.error,
                )
            )
        else:
            results.append(
                CollectorSourceStatus(
                    source=source,
                    last_run_at=None,
                    ingested_count=0,
                    error=None,
                )
            )
    return CollectorStatusResponse(sources=results)


@router.post(
    "/admin/collector/run",
    response_model=CollectorRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def run_collector(
    background_tasks: BackgroundTasks,
    current_admin: CurrentAdmin,
    body: CollectorRunRequest | None = None,
) -> CollectorRunResponse:
    valid_sources = ["himalayas", "hn", "wwr", "wanted", "jumpit"]
    target_sources = []
    limit = None
    from_year = None
    to_year = None

    if body:
        if body.source:
            if body.source not in valid_sources:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Invalid source: {body.source}. Valid sources are: {valid_sources}",
                )
            target_sources = [body.source]
        else:
            target_sources = valid_sources

        limit = body.limit
        from_year = body.from_year
        to_year = body.to_year
    else:
        target_sources = valid_sources

    job_id = uuid.uuid4().hex
    background_tasks.add_task(run_collector_job, job_id, target_sources, limit, from_year, to_year)

    return CollectorRunResponse(job_id=job_id, sources=target_sources)
