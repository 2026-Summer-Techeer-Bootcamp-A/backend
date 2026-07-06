"""Seed the person table with demo data. Idempotent (re-runnable).

Run from the repo root against the dev-compose Postgres (port 5432 published):
    DATABASE_URL=postgresql+psycopg://appuser:change-me@localhost:5432/appdb \
        python -m scripts.seed
"""

from pathlib import Path

from sqlalchemy import create_engine, text

from app.core.config import settings

NAMES = ["김강문", "방준혁", "최혜민", "박성훈", "이동건"]
SCHEMA_FILE = Path(__file__).resolve().parent.parent / "app" / "schema" / "001_person.sql"


def main() -> None:
    engine = create_engine(settings.database_url, future=True)
    with engine.begin() as conn:
        conn.execute(text(SCHEMA_FILE.read_text(encoding="utf-8")))
        # RESTART IDENTITY so ids always start at 1 on every seed run.
        conn.execute(text("TRUNCATE person RESTART IDENTITY"))
        for name in NAMES:
            conn.execute(text("INSERT INTO person (name) VALUES (:name)"), {"name": name})
    print(f"Seeded {len(NAMES)} persons: {', '.join(NAMES)}")


if __name__ == "__main__":
    main()
