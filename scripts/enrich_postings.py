"""raw jsonl.gz(수집 원본) -> posting.logo_url / description / region_district / lat / lng 백필.

재수집 없이, 이미 확보된 data-collector-script/out/**/*.jsonl.gz 원본에서 이미 있었지만
mart 적재 단계에서 버려졌던 필드(로고 URL, 상세 설명, 좌표)를 뽑아 채운다.

사용:
    # 1) 원본에서 채울 값 추출 -> ndjson.gz 로 저장 (원본이 있는 이 저장소 체크아웃에서만 가능)
    python -m scripts.enrich_postings emit out/enrichment.ndjson.gz

    # 2) 추출된 값을 DB에 반영 (DATABASE_URL 이 가리키는 곳에 적용 — 로컬/프로덕션 어디서든)
    python -m scripts.enrich_postings apply out/enrichment.ndjson.gz
"""

from __future__ import annotations

import glob
import gzip
import html
import json
import re
import sys
from html.parser import HTMLParser

ROOT = "/home/rivermoon/Documents/techeer-2026-summer-a"
OUT = f"{ROOT}/data-collector-script/out"

_JUMPIT_POS_RE = re.compile(r"/position/(\d+)")
_RP_ID_RE = re.compile(r"/jobs/(\d+)")
_JK_ID_RE = re.compile(r"/GI_Read/(\d+)")
_JK_NAV_MARKERS = (
    "이 기업이 선택한 키워드", "이 기업이 선택한", "직무별 검색", "키워드 정보",
    "메뉴 건너뛰기", "잡코리아 채용정보", "채용정보 - 좋은 일", "jobkorea.co.kr",
)
# jobkorea description 앞부분에 "{회사} 채용 - {제목} | 잡코리아 회원가입/로그인 ...
# 취업톡톡 ..." 형태의 사이트 헤더/내비게이션이 섞여 들어오는 경우가 있다(약 11k건).
# 실제 공고 본문은 항상 이 문구 다음부터 시작해서 신뢰할 수 있는 시작 마커로 쓴다.
_JK_BODY_START_MARKER = "채용정보에 잘못된 내용이 있을 경우 문의 해주세요."
# 본문 뒤에는 "로그인 하고 비슷한 조건의 AI추천공고를 확인해 보세요!"로 시작하는
# 추천공고 유도문구·마감일 안내·기업정보 푸터가 이어진다. 실제 공고 필드(모집분야/
# 지원자격/복리후생 등)는 항상 이 문구 이전에서 끝나므로 끝 마커로도 신뢰할 수 있다.
_JK_BODY_END_MARKER = "로그인 하고 비슷한 조건의"
_ADMIN_RE = re.compile(r"^\S*[시군구]$")


class _TagStrip(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data):
        self.parts.append(data)


def strip_html(text: str | None) -> str:
    if not text:
        return ""
    p = _TagStrip()
    try:
        p.feed(text)
    except Exception:
        return html.unescape(re.sub(r"<[^>]+>", " ", text))
    return html.unescape(" ".join(p.parts))


def extract_district(address: str | None) -> str | None:
    if not address:
        return None
    tokens = address.replace(",", " ").split()
    admin = [t for t in tokens[:4] if _ADMIN_RE.match(t)]
    return admin[-1] if admin else None


def iter_records(*patterns: str):
    for pattern in patterns:
        for path in sorted(glob.glob(pattern)):
            with gzip.open(path, "rt", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError:
                        continue


def sections(pairs: list[tuple[str, str | None]]) -> list[dict]:
    out = []
    for title, text in pairs:
        text = (text or "").strip()
        if text:
            out.append({"title": title, "text": text})
    return out


def from_jumpit(rec: dict) -> tuple[str, dict] | None:
    m = _JUMPIT_POS_RE.search(rec.get("_url") or rec.get("url") or "")
    sid = (m.group(1) if m else None) or rec.get("id") or rec.get("serialNumber") or rec.get("_url")
    if not sid:
        return None
    places = rec.get("workingPlaces") or []
    addr = rec.get("location") or (places[0].get("address") if places and isinstance(places[0], dict) else None)
    return str(sid), {
        "logo_url": rec.get("logo") or None,
        "region_district": extract_district(addr),
        "lat": None,
        "lng": None,
        "desc": sections([
            ("주요 업무", rec.get("responsibility")),
            ("자격 요건", rec.get("qualifications")),
            ("우대 사항", rec.get("preferredRequirements")),
            ("채용 절차", rec.get("recruitProcess")),
        ]),
    }


def from_wanted(rec: dict) -> tuple[str, dict] | None:
    uid = rec.get("url") or rec.get("_url")
    if not uid:
        return None
    if rec.get("_source") == "wanted_live" or isinstance(rec.get("detail"), dict):
        det = rec.get("detail") or {}
        addr = rec.get("address") or {}
        full_loc = addr.get("full_location") if isinstance(addr, dict) else None
        geo = (addr.get("geo_location") or {}) if isinstance(addr, dict) else {}
        loc = geo.get("location") or {}
        lat, lng = loc.get("lat"), loc.get("lng")
        logo = rec.get("logo_img") or {}
        return str(uid), {
            "logo_url": logo.get("origin") or logo.get("thumb") if isinstance(logo, dict) else None,
            "region_district": extract_district(full_loc),
            "lat": lat,
            "lng": lng,
            "desc": sections([
                ("소개", det.get("intro")),
                ("주요 업무", det.get("main_tasks")),
                ("자격 요건", det.get("requirements")),
                ("우대 사항", det.get("preferred_points")),
                ("혜택 및 복지", det.get("benefits")),
            ]),
        }
    org = rec.get("hiringOrganization") or {}
    return str(uid), {
        "logo_url": org.get("logo") if isinstance(org, dict) else None,
        "region_district": None,
        "lat": None,
        "lng": None,
        "desc": sections([("상세 설명", strip_html(rec.get("description")))]),
    }


def from_himalayas(rec: dict) -> tuple[str, dict] | None:
    uid = rec.get("guid") or rec.get("id") or rec.get("_url") or rec.get("url")
    if not uid:
        return None
    return str(uid), {
        "logo_url": rec.get("companyLogo") or None,
        "region_district": None,
        "lat": None,
        "lng": None,
        "desc": sections([("상세 설명", strip_html(rec.get("description")))]),
    }


def from_rocketpunch(rec: dict) -> tuple[str, dict] | None:
    src = rec.get("url") or rec.get("_url") or ""
    m = _RP_ID_RE.search(src)
    uid = m.group(1) if m else (rec.get("_url") or rec.get("url"))
    if not uid:
        return None
    org = rec.get("hiringOrganization") or {}
    return str(uid), {
        "logo_url": org.get("logo") if isinstance(org, dict) else None,
        "region_district": None,
        "lat": None,
        "lng": None,
        "desc": sections([("상세 설명", strip_html(rec.get("description")))]),
    }


def from_jobkorea(rec: dict) -> tuple[str, dict] | None:
    src = rec.get("_url") or rec.get("url") or ""
    m = _JK_ID_RE.search(src)
    uid = m.group(1) if m else src
    if not uid:
        return None
    desc = rec.get("description") or ""

    start = desc.find(_JK_BODY_START_MARKER)
    if start != -1:
        desc = desc[start + len(_JK_BODY_START_MARKER) :]

    end = desc.find(_JK_BODY_END_MARKER)
    if end != -1:
        desc = desc[:end]

    cuts = [p for p in (desc.find(mk) for mk in _JK_NAV_MARKERS) if p != -1]
    if cuts:
        desc = desc[: min(cuts)]

    return str(uid), {
        "logo_url": None,
        "region_district": None,
        "lat": None,
        "lng": None,
        "desc": sections([("채용 공고 원문", strip_html(desc))]),
    }


SOURCES = {
    "jumpit": (from_jumpit, [f"{OUT}/jumpit/*.jsonl.gz", f"{OUT}/wayback/jumpit_co.jsonl.gz", f"{OUT}/wayback/jumpit_saramin.jsonl.gz"]),
    "wanted": (from_wanted, [f"{OUT}/wanted/*.jsonl.gz", f"{OUT}/wayback/wanted.jsonl.gz", f"{OUT}/wayback/wanted_raw.jsonl.gz"]),
    "himalayas": (from_himalayas, [f"{OUT}/himalayas/*.jsonl.gz"]),
    "rocketpunch": (from_rocketpunch, [f"{OUT}/wayback/rocketpunch.jsonl.gz"]),
    "jobkorea": (from_jobkorea, [f"{OUT}/wayback/jobkorea.jsonl.gz"]),
}


def cmd_emit(out_path: str) -> None:
    seen: dict[str, dict] = {}
    counts: dict[str, int] = {}
    for source, (parser, patterns) in SOURCES.items():
        n = 0
        for rec in iter_records(*patterns):
            result = parser(rec)
            if not result:
                continue
            uid, enrichment = result
            if not (enrichment.get("logo_url") or enrichment.get("desc") or enrichment.get("region_district") or enrichment.get("lat")):
                continue
            key = f"{source}:{uid}"
            seen[key] = {"source": source, "source_uid": uid, **enrichment}
            n += 1
        counts[source] = n
        print(f"{source}: {n} records scanned with usable enrichment", file=sys.stderr)

    with gzip.open(out_path, "wt", encoding="utf-8") as f:
        for row in seen.values():
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"wrote {len(seen)} unique (source, source_uid) rows -> {out_path}", file=sys.stderr)


def cmd_apply(in_path: str) -> None:
    import os

    import psycopg

    database_url = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://")
    updated = 0
    skipped_no_match = 0
    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            with gzip.open(in_path, "rt", encoding="utf-8") as f:
                batch = []
                for line in f:
                    row = json.loads(line)
                    desc_json = json.dumps(row["desc"], ensure_ascii=False) if row.get("desc") else None
                    batch.append((
                        row.get("logo_url"),
                        desc_json,
                        row.get("region_district"),
                        row.get("lat"),
                        row.get("lng"),
                        row["source"],
                        row["source_uid"],
                    ))
                    if len(batch) >= 2000:
                        cur.executemany(
                            """
                            UPDATE posting SET
                              logo_url = COALESCE(%s, logo_url),
                              description = COALESCE(%s, description),
                              region_district = COALESCE(%s, region_district),
                              lat = COALESCE(%s, lat),
                              lng = COALESCE(%s, lng)
                            WHERE source = %s AND source_uid = %s
                            """,
                            batch,
                        )
                        updated += len(batch)
                        conn.commit()
                        print(f"...{updated} rows sent", file=sys.stderr)
                        batch = []
                if batch:
                    cur.executemany(
                        """
                        UPDATE posting SET
                          logo_url = COALESCE(%s, logo_url),
                          description = COALESCE(%s, description),
                          region_district = COALESCE(%s, region_district),
                          lat = COALESCE(%s, lat),
                          lng = COALESCE(%s, lng)
                        WHERE source = %s AND source_uid = %s
                        """,
                        batch,
                    )
                    updated += len(batch)
                    conn.commit()
    print(f"done, {updated} enrichment rows applied (some may not have matched any posting row)", file=sys.stderr)


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] not in ("emit", "apply"):
        print(__doc__)
        sys.exit(1)
    if sys.argv[1] == "emit":
        cmd_emit(sys.argv[2])
    else:
        cmd_apply(sys.argv[2])
