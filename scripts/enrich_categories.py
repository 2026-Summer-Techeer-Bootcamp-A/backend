"""raw jsonl.gz(수집 원본) -> posting_category / job_category 백필 (국내 소스: jumpit, jobkorea).

`posting_category`는 himalayas(해외) 소스에만 채워져 있고 국내 소스(jumpit/jobkorea/wanted)는
0건이었다. 원본에는 직무 카테고리 정보가 있었지만 mart 적재 단계에서 버려졌다. 이 스크립트는
`scripts/enrich_postings.py`와 동일한 emit/apply 2단계 구조로 원본에서 직무 카테고리를 뽑아
DB에 채운다.

- jumpit: `jobCategories: [{"name": "서버/백엔드 개발자", ...}, ...]` — 21개 한국어 기술직군
  통제 어휘가 그대로 있음. `name`만 추출하면 됨(신뢰도 높음).
- jobkorea: `categories: "화학·에너지·환경, 제품영업, ..., 잡코리아"` — 쉼표 구분 태그 문자열.
  사이트 UI 네비게이션에서 긁힌 노이즈 태그(공채/채용/구인/공고/입사 지원/잡코리아)가 섞여
  있어 이를 제거한 뒤 앞쪽 1~2개 태그를 대분류 후보로 취급한다(아래 `_JK_NOISE_TAGS` 및
  `jobkorea_categories` docstring의 한계 설명 참고).
- wanted: `category_tags`가 숫자 ID(`{"parent_id": 518, "id": 876}`)뿐이라 로컬에서 이름으로
  해석할 방법이 없음 — 스코프 밖(제외).

사용:
    # 1) 원본에서 채울 값 추출 -> ndjson.gz 로 저장 (원본이 있는 이 저장소 체크아웃에서만 가능)
    python -m scripts.enrich_categories emit out/categories.ndjson.gz

    # 2) 추출된 값을 DB에 반영 (DATABASE_URL 이 가리키는 곳에 적용 — 로컬/프로덕션 어디서든)
    python -m scripts.enrich_categories apply out/categories.ndjson.gz
"""

from __future__ import annotations

import gzip
import json
import sys

from scripts.enrich_postings import OUT, from_jobkorea, from_jumpit, iter_records

# jobkorea `categories` 필드 끝에 반복적으로 붙는 사이트 UI 네비게이션 노이즈 태그.
# 396,526개 표본 중 categories가 있는 383,614건을 스캔한 결과: 90.1%가 정확히 이 6개
# 토큰으로 끝났고, 나머지 9.9%는 애초에 태그 개수가 적어(3~8개) 이 토큰들이 전혀 없는
# 케이스였다(사이트 페이지 포맷 차이로 추정). 즉 이 토큰들은 어디에 있든 항상 순수
# 사이트 보일러플레이트이지, 실제 직무 카테고리로 쓰인 적은 없었다(표본 내에서는).
_JK_NOISE_TAGS = frozenset({"공채", "채용", "구인", "공고", "입사 지원", "잡코리아"})

# jobkorea 카테고리 문자열은 앞쪽일수록 넓은 분류(예: "소프트웨어·솔루션·ASP"), 그
# 다음이 좀 더 구체적인 직무(예: "웹프로그래머")인 패턴이 태그 개수 상관없이 대체로
# 유지됨(표본 확인). 노이즈 제거 후 앞에서부터 최대 이 개수만큼만 후보로 취급한다.
_JK_MAX_CANDIDATES = 2


def jumpit_categories(rec: dict) -> list[str]:
    """jumpit 레코드의 jobCategories[].name 을 순서 보존 dedup해서 뽑는다."""
    names: list[str] = []
    seen: set[str] = set()
    for item in rec.get("jobCategories") or []:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    return names


def jobkorea_categories(rec: dict) -> list[str]:
    """jobkorea 레코드의 categories(쉼표 구분 문자열)에서 노이즈를 걷어내고 앞쪽
    1~2개 태그를 대분류 후보로 뽑는다.

    한계: 이 태그 문자열은 잡코리아가 부여한 상세 직무 태그를 순서 없이(또는 사이트
    UI 순서로) 나열한 것으로 보이며, "앞쪽 = 대분류"는 표본 관찰에 기반한 휴리스틱일
    뿐 공식 스펙이 아니다. 특히 인력 알선 대행 공고는 첫 태그가 실제 직무 도메인이
    아니라 "서치펌·헤드헌팅"(대행사 자신의 업종)인 경우가 표본의 약 0.6%를 차지한다.
    완벽한 정확도를 주장하지 않는다 — 정성적으로 "쓸만한 근사치" 정도로 취급할 것.
    """
    raw = rec.get("categories")
    if not raw:
        return []
    tags = [t.strip() for t in raw.split(",") if t.strip()]
    tags = [t for t in tags if t not in _JK_NOISE_TAGS]

    names: list[str] = []
    seen: set[str] = set()
    for t in tags:
        if t not in seen:
            seen.add(t)
            names.append(t)
        if len(names) >= _JK_MAX_CANDIDATES:
            break
    return names


# (parser, category-extractor, glob patterns, is_tech)
# jumpit 패턴은 enrich_postings.py의 SOURCES와 동일하게 wayback 백업분까지 포함한다.
# out/jumpit/*.jsonl.gz(최근 일별 수집분)만 쓰면 43,315건 중 극히 일부(2,183건)만
# 잡혀 커버리지가 크게 부족해진다.
SOURCES = {
    "jumpit": (
        from_jumpit,
        jumpit_categories,
        [f"{OUT}/jumpit/*.jsonl.gz", f"{OUT}/wayback/jumpit_co.jsonl.gz", f"{OUT}/wayback/jumpit_saramin.jsonl.gz"],
        True,
    ),
    "jobkorea": (from_jobkorea, jobkorea_categories, [f"{OUT}/wayback/jobkorea.jsonl.gz"], False),
}


def cmd_emit(out_path: str) -> None:
    seen: dict[str, dict] = {}
    counts: dict[str, int] = {}
    for source, (uid_parser, cat_extractor, patterns, _is_tech) in SOURCES.items():
        n = 0
        for rec in iter_records(*patterns):
            result = uid_parser(rec)
            if not result:
                continue
            uid, _enrichment = result
            categories = cat_extractor(rec)
            if not categories:
                continue
            key = f"{source}:{uid}"
            seen[key] = {"source": source, "source_uid": uid, "categories": categories}
            n += 1
        counts[source] = n
        print(f"{source}: {n} records scanned with usable categories", file=sys.stderr)

    with gzip.open(out_path, "wt", encoding="utf-8") as f:
        for row in seen.values():
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"wrote {len(seen)} unique (source, source_uid) rows -> {out_path}", file=sys.stderr)


_IN_CLAUSE_CHUNK_SIZE = 5000


def _chunked(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def cmd_apply(in_path: str) -> None:
    import os

    import psycopg

    database_url = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://")
    is_tech_by_source = {source: is_tech for source, (_, _, _, is_tech) in SOURCES.items()}

    rows: list[dict] = []
    with gzip.open(in_path, "rt", encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            if row.get("categories"):
                rows.append(row)
    print(f"loaded {len(rows)} enrichment rows from {in_path}", file=sys.stderr)

    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            # 1) 소스별로 등장한 카테고리명을 job_category에 upsert.
            names_by_source: dict[str, set[str]] = {}
            for row in rows:
                names_by_source.setdefault(row["source"], set()).update(row["categories"])

            for source, names in names_by_source.items():
                is_tech = is_tech_by_source.get(source, False)
                sorted_names = sorted(names)
                cur.executemany(
                    "INSERT INTO job_category (name, is_tech) VALUES (%s, %s) "
                    "ON CONFLICT (name) DO NOTHING",
                    [(name, is_tech) for name in sorted_names],
                )
                print(
                    f"job_category: upserted {len(sorted_names)} distinct {source} names "
                    f"(is_tech={is_tech})",
                    file=sys.stderr,
                )
            conn.commit()

            # 2) (source, source_uid) -> posting.id 매핑을 소스별/청크별로 조회.
            uids_by_source: dict[str, list[str]] = {}
            for row in rows:
                uids_by_source.setdefault(row["source"], []).append(row["source_uid"])

            posting_id_map: dict[tuple[str, str], int] = {}
            for source, uids in uids_by_source.items():
                unique_uids = list(dict.fromkeys(uids))
                for batch in _chunked(unique_uids, _IN_CLAUSE_CHUNK_SIZE):
                    cur.execute(
                        "SELECT source_uid, id FROM posting WHERE source = %s AND source_uid = ANY(%s)",
                        (source, batch),
                    )
                    for source_uid, posting_id in cur.fetchall():
                        posting_id_map[(source, source_uid)] = posting_id
            total_uids = sum(len(v) for v in uids_by_source.values())
            print(
                f"resolved {len(posting_id_map)} / {total_uids} distinct (source, source_uid) "
                "pairs to posting rows",
                file=sys.stderr,
            )

            # 3) posting_category insert, 배치 커밋.
            inserted = 0
            skipped_no_match = 0
            batch: list[tuple[int, str]] = []
            for row in rows:
                posting_id = posting_id_map.get((row["source"], row["source_uid"]))
                if posting_id is None:
                    skipped_no_match += 1
                    continue
                for category in row["categories"]:
                    batch.append((posting_id, category))
                if len(batch) >= 5000:
                    cur.executemany(
                        "INSERT INTO posting_category (posting_id, category) VALUES (%s, %s) "
                        "ON CONFLICT (posting_id, category) DO NOTHING",
                        batch,
                    )
                    inserted += len(batch)
                    conn.commit()
                    print(f"...{inserted} posting_category rows sent", file=sys.stderr)
                    batch = []
            if batch:
                cur.executemany(
                    "INSERT INTO posting_category (posting_id, category) VALUES (%s, %s) "
                    "ON CONFLICT (posting_id, category) DO NOTHING",
                    batch,
                )
                inserted += len(batch)
                conn.commit()

    print(
        f"done, {inserted} posting_category rows sent "
        f"({skipped_no_match} enrichment rows skipped - no matching posting)",
        file=sys.stderr,
    )


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] not in ("emit", "apply"):
        print(__doc__)
        sys.exit(1)
    if sys.argv[1] == "emit":
        cmd_emit(sys.argv[2])
    else:
        cmd_apply(sys.argv[2])
