"""GitHub 레포 원본 스냅샷/스타 히스토리를 backend DB로 적재한다.

gh-hn-data-collector가 이미 수집해둔 원본(jsonl.gz)을 있는 그대로 적재할 뿐,
값을 새로 계산하거나 보간하지 않는다 — cite/05-data-sources.md "정직 표기" 원칙.

Usage:
    python -m scripts.ingest_github_snapshots \
        --snapshot-dir ../gh-hn-data-collector/collector/out/github \
        --star-history ../gh-hn-data-collector/collector/out/github_backfill/star_history.jsonl.gz
"""

import argparse
import gzip
import json
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

from app.core.db import SessionLocal
from app.models.github import GithubRepoSnapshot, GithubStarHistory


def _iter_jsonl_gz(path: Path) -> Iterator[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def ingest_snapshots(snapshot_dir: Path) -> int:
    count = 0
    with SessionLocal() as session:
        for path in sorted(snapshot_dir.glob("*.jsonl.gz")):
            snapshot_date = datetime.strptime(path.name.removesuffix(".jsonl.gz"), "%Y-%m-%d").date()

            existing = {
                row.full_name
                for row in session.query(GithubRepoSnapshot.full_name).filter_by(snapshot_date=snapshot_date)
            }

            for row in _iter_jsonl_gz(path):
                full_name = row["full_name"]
                if full_name in existing:
                    continue

                pushed_at = None
                if row.get("pushed_at"):
                    pushed_at = datetime.fromisoformat(row["pushed_at"].replace("Z", "+00:00")).date()

                session.add(
                    GithubRepoSnapshot(
                        full_name=full_name,
                        snapshot_date=snapshot_date,
                        language=row.get("language"),
                        stargazers_count=row.get("stargazers_count", 0),
                        forks_count=row.get("forks_count", 0),
                        open_issues_count=row.get("open_issues_count", 0),
                        subscribers_count=row.get("subscribers_count"),
                        topics=row.get("topics") or [],
                        pushed_at=pushed_at,
                    )
                )
                count += 1
            session.commit()
    return count


def ingest_star_history(star_history_path: Path) -> int:
    count = 0
    with SessionLocal() as session:
        existing = {
            (row.full_name, row.month) for row in session.query(GithubStarHistory.full_name, GithubStarHistory.month)
        }

        buffer: list[GithubStarHistory] = []
        for row in _iter_jsonl_gz(star_history_path):
            month = datetime.strptime(row["date"], "%Y-%m-%d").date()
            key = (row["repo"], month)
            if key in existing:
                continue

            buffer.append(GithubStarHistory(full_name=row["repo"], month=month, stargazers_count=row["stargazers"]))
            existing.add(key)

            if len(buffer) >= 1000:
                session.add_all(buffer)
                session.commit()
                count += len(buffer)
                buffer = []

        if buffer:
            session.add_all(buffer)
            session.commit()
            count += len(buffer)

    return count


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--snapshot-dir", type=Path, required=True, help="collector/out/github 디렉토리")
    parser.add_argument("--star-history", type=Path, required=True, help="github_backfill/star_history.jsonl.gz")
    args = parser.parse_args()

    snapshot_count = ingest_snapshots(args.snapshot_dir)
    print(f"ingested {snapshot_count} github_repo_snapshot rows")

    star_count = ingest_star_history(args.star_history)
    print(f"ingested {star_count} github_star_history rows")


if __name__ == "__main__":
    main()
