"""
현실적 더미 데이터 생성기.

'실 데이터에서 뽑아온 분포'를 최대한 반영한다:
  - 기술 사전(Skill/SkillAlias): collector/taxonomy_v2.json 정본
  - 자격증(Cert): collector/certs_taxonomy.json + marketData.certGap
  - 직군(JobCategory): jumpit 실 직군 분포(가중치)
  - 회사/지역/좌표: marketData.map.pins (실 공고에서 수집)
  - 기술 수요 가중치: marketData.skillShare(국내/국외) 실 점유율
  - 직군별 기술 풀: 실 공고 경향 반영(백엔드=Spring/Kafka, 프론트=React/Next…)
  - 관심 시그널(InterestSignal): out/hn_backfill/monthly_counts.csv 실 HN 데이터

규모: 회원 50 · 이력서 1,000 · 공고 2,000 (그 외 파생 테이블도 동일 스케일).

Requires: pip install faker bcrypt pgvector sqlalchemy
Run:      python backend/generate_dummy_data.py   (또는 backend/ 에서 python generate_dummy_data.py)
"""

import csv
import json
import os
import random
from datetime import date, datetime, timedelta, timezone

import bcrypt
from faker import Faker
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import Base, SessionLocal, engine
from app.models import (
    Cert, CollectorRun, InterestSignal, JobCategory, Posting, PostingCategory,
    PostingCert, PostingEmbedding, PostingTech, RawPosting, Resume, ResumeCert,
    ResumeSkill, Skill, SkillAlias, User,
)

fake = Faker("ko_KR")
fake_en = Faker("en_US")
random.seed(42)
Faker.seed(42)

# ── 규모 ──────────────────────────────────────────────────────────
N_USERS = 50
N_RESUMES = 1000
N_POSTINGS = 2000
N_EMBED = 300          # 벡터는 무거우니 일부만(테이블 시연용)
DOMESTIC_RATIO = 0.6   # 공고 국내 비율
RECENT_RATIO = 0.5     # 최근 30일 비율

# ── 경로 ──────────────────────────────────────────────────────────
BACKEND = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(BACKEND)
COLLECTOR = os.path.join(REPO, "data-collector-script")
FE_DATA = os.path.join(REPO, "frontend", "src", "data")


def _load_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return default if default is not None else {}


# ── 실데이터 로드 ─────────────────────────────────────────────────
TAXONOMY = _load_json(os.path.join(COLLECTOR, "taxonomy_v2.json"))
CERTS_TAX = _load_json(os.path.join(COLLECTOR, "certs_taxonomy.json"))
MARKET = _load_json(os.path.join(FE_DATA, "marketData.json"))

# 기술 사전: (canonical, category, is_ambiguous, [aliases])
def build_skill_dict():
    out = []
    seen = set()
    for cat, techs in TAXONOMY.items():
        if cat.startswith("_") and cat != "_ambiguous_llm_fallback":
            continue
        amb = cat == "_ambiguous_llm_fallback"
        cat_name = "ambiguous" if amb else cat
        if not isinstance(techs, dict):
            continue
        for canon, aliases in techs.items():
            if canon.startswith("_") or canon in seen:
                continue
            seen.add(canon)
            al = aliases if isinstance(aliases, list) else []
            out.append((canon, cat_name, amb, al))
    return out

SKILL_DICT = build_skill_dict()
SKILL_NAMES = {c for c, *_ in SKILL_DICT}

# 자격증: 정본 사전 + 시장 실측 명칭
def build_certs():
    names = []
    seen = set()
    for cat, items in CERTS_TAX.items():
        if cat.startswith("_") or not isinstance(items, dict):
            continue
        for canon in items:
            if canon not in seen:
                seen.add(canon)
                names.append(canon)
    for row in (MARKET.get("certGap", {}).get("required") or []):
        nm = row.get("name")
        if nm and nm not in seen:
            seen.add(nm)
            names.append(nm)
    return names

CERT_NAMES = build_certs()

# 기술 수요 가중치(실 점유율). 없으면 소량 기본값.
def build_weights():
    w = {}
    ss = MARKET.get("skillShare", {})
    for region in ("국내", "국외"):
        for it in (ss.get(region, {}).get("items") or []):
            w[it["tech"]] = max(w.get(it["tech"], 0), float(it.get("share", 1)))
    return w

WEIGHT = build_weights()
def wof(tech):
    return WEIGHT.get(tech, 0.6)

# 실 회사/지역/좌표 (marketData.map.pins) — 국내 공고 현실감
PINS = [p for p in (MARKET.get("map", {}).get("pins") or []) if p.get("company")]
DOM_COMPANIES = sorted({p["company"] for p in PINS}) or [
    "네이버", "카카오", "라인", "쿠팡", "배달의민족", "토스", "당근마켓", "야놀자", "무신사", "직방",
]
DISTRICTS = sorted({p.get("district") for p in PINS if p.get("district")}) or [
    "강남구", "서초구", "송파구", "성동구", "영등포구", "마포구",
]
PIN_BY_COMPANY = {}
for p in PINS:
    PIN_BY_COMPANY.setdefault(p["company"], p)

GLOBAL_COMPANIES = ["Google", "Meta", "Amazon", "Microsoft", "Netflix", "Stripe",
                    "Datadog", "Snowflake", "GitLab", "HashiCorp", "Cloudflare", "Airbnb"]

# 직군(실 jumpit 분포) + is_tech + 가중치
ROLE_ROWS = [
    ("SW/솔루션", True, 238), ("HW/임베디드", True, 190), ("devops/시스템 엔지니어", True, 176),
    ("서버/백엔드 개발자", True, 159), ("인공지능/머신러닝", True, 145), ("프론트엔드 개발자", True, 76),
    ("빅데이터 엔지니어", True, 70), ("정보보안 담당자", True, 50), ("웹 풀스택 개발자", True, 32),
    ("QA 엔지니어", True, 29), ("개발 PM", False, 22), ("DBA", True, 22),
    ("안드로이드 개발자", True, 13), ("iOS 개발자", True, 13), ("게임 클라이언트 개발자", True, 10),
    ("데이터 사이언티스트", True, 18), ("기획자", False, 15), ("프로덕트 디자이너", False, 12),
]
ROLE_NAMES = [r[0] for r in ROLE_ROWS]
ROLE_WEIGHTS = [r[2] for r in ROLE_ROWS]

# 직군별 현실적 기술 풀(taxonomy에 있는 것만 사용)
ROLE_TECHS_RAW = {
    "서버/백엔드 개발자": ["Java", "Spring", "Python", "Django", "FastAPI", "Node.js", "Express", "NestJS", "Go", "Kotlin", "MySQL", "PostgreSQL", "Redis", "MongoDB", "Kafka", "Docker", "Kubernetes", "AWS", "Git"],
    "웹 풀스택 개발자": ["JavaScript", "TypeScript", "React", "Next.js", "Node.js", "Express", "Python", "Django", "MySQL", "PostgreSQL", "AWS", "Docker", "Git"],
    "프론트엔드 개발자": ["JavaScript", "TypeScript", "React", "Next.js", "Vue", "Angular", "Svelte", "HTML", "CSS", "Git", "Node.js"],
    "devops/시스템 엔지니어": ["Docker", "Kubernetes", "Terraform", "Ansible", "AWS", "GCP", "Azure", "Jenkins", "ArgoCD", "Prometheus", "Grafana", "Linux", "Python", "Go", "Git"],
    "빅데이터 엔지니어": ["Python", "SQL", "Spark", "Airflow", "Kafka", "PostgreSQL", "BigQuery", "AWS", "Docker", "Git"],
    "데이터 사이언티스트": ["Python", "PyTorch", "TensorFlow", "SQL", "Pandas", "NumPy", "AWS", "Git"],
    "인공지능/머신러닝": ["Python", "PyTorch", "TensorFlow", "FastAPI", "Docker", "Kubernetes", "AWS", "SQL", "Git", "LangChain", "Hugging Face"],
    "안드로이드 개발자": ["Kotlin", "Java", "Android", "Jetpack Compose", "Git"],
    "iOS 개발자": ["Swift", "SwiftUI", "Objective-C", "Git"],
    "게임 클라이언트 개발자": ["C++", "C#", "Unreal Engine", "Git"],
    "HW/임베디드": ["C", "C++", "RTOS", "MCU", "FPGA", "Linux", "Python"],
    "정보보안 담당자": ["Python", "Linux", "OWASP", "Vault", "Snort", "OpenSSL", "Git"],
    "QA 엔지니어": ["Python", "Selenium", "Playwright", "Jest", "Cypress", "Git"],
    "DBA": ["SQL", "PostgreSQL", "MySQL", "Oracle DB", "Redis", "MongoDB", "Linux"],
    "SW/솔루션": ["Java", "C++", "C#", "Python", "Spring", "SQL", "Git"],
}
GENERIC_POOL = ["Python", "Java", "JavaScript", "TypeScript", "React", "AWS", "Docker", "Git", "SQL", "Kubernetes"]

def role_pool(role):
    raw = ROLE_TECHS_RAW.get(role, GENERIC_POOL)
    pool = [t for t in raw if t in SKILL_NAMES]
    return pool or [t for t in GENERIC_POOL if t in SKILL_NAMES]

DOM_SOURCES = ["jumpit", "wanted"]
GLOBAL_SOURCES = ["himalayas", "hn", "wwr"]
INDUSTRIES = ["IT 서비스", "이커머스", "핀테크", "게임", "AI/딥테크", "SaaS", "물류", "헬스케어", "미디어/콘텐츠"]


def hash_pw(pw):
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()


def wipe(db: Session):
    """재실행 가능하도록 더미 팩트 테이블 정리(사전 테이블은 유지)."""
    for tbl in ("posting_embedding", "posting_tech", "posting_cert", "posting_category",
                "raw_posting", "posting", "resume_skill", "resume_cert", "resume",
                "interest_signal", "collector_run"):
        db.execute(text(f"DELETE FROM {tbl}"))
    db.execute(text("DELETE FROM \"user\" WHERE is_admin = false"))
    db.commit()


def get_or_create_dicts(db: Session):
    # Skill + SkillAlias
    existing = {s.canonical: s for s in db.query(Skill).all()}
    alias_seen = {a.alias for a in db.query(SkillAlias).all()}
    skills = []
    for canon, cat, amb, aliases in SKILL_DICT:
        s = existing.get(canon)
        if not s:
            s = Skill(canonical=canon, category=cat, is_ambiguous=amb)
            db.add(s)
            db.flush()
            for a in aliases:
                al = a.strip()
                if not al or al.lower() in {x.lower() for x in alias_seen}:
                    continue
                alias_seen.add(al)
                db.add(SkillAlias(skill_id=s.id, alias=al, is_korean=not al.isascii()))
        skills.append(s)
    # Cert
    existing_c = {c.name: c for c in db.query(Cert).all()}
    certs = []
    for nm in CERT_NAMES:
        c = existing_c.get(nm) or Cert(name=nm)
        if nm not in existing_c:
            db.add(c)
        certs.append(c)
    # JobCategory
    existing_j = {j.name: j for j in db.query(JobCategory).all()}
    for nm, is_tech, _w in ROLE_ROWS:
        if nm not in existing_j:
            db.add(JobCategory(name=nm, is_tech=is_tech))
    db.commit()
    return skills, certs


def pick_role():
    return random.choices(ROLE_NAMES, weights=ROLE_WEIGHTS, k=1)[0]


def sample_techs(role, k):
    pool = role_pool(role)
    weights = [wof(t) + 0.5 for t in pool]
    chosen = set()
    tries = 0
    while len(chosen) < min(k, len(pool)) and tries < 40:
        chosen.add(random.choices(pool, weights=weights, k=1)[0])
        tries += 1
    return list(chosen)


def gen_users(db: Session):
    # admin은 wipe()가 보존하므로 재실행 시 중복 생성하지 않는다(email 유니크).
    admin = db.query(User).filter(User.email == "admin@example.com").first()
    if admin is None:
        admin = User(email="admin@example.com", password_hash=hash_pw("password123!"),
                     nickname="System Admin", is_admin=True)
        db.add(admin)
    users = [admin]
    for _ in range(N_USERS - 1):
        users.append(User(email=fake.unique.email(), password_hash=hash_pw("password123!"),
                          nickname=fake.user_name(), is_admin=False))
        db.add(users[-1])
    db.commit()
    return users


def gen_resumes(db: Session, users, skills, certs):
    skill_by = {s.canonical: s for s in skills}
    resumes = []
    for i in range(N_RESUMES):
        user = random.choice(users)
        role = pick_role()
        cmin = random.randint(0, 4)
        r = Resume(user_id=user.id, title=f"{role} 지원 이력서",
                   position=role, career_min=cmin, career_max=cmin + random.randint(1, 8),
                   pool=random.choices(["domestic", "global"], weights=[7, 3])[0])
        db.add(r)
        resumes.append(r)
        if (i + 1) % 300 == 0:
            db.flush()
    db.flush()
    # 스킬/자격증
    for r in resumes:
        for t in sample_techs(r.position, random.randint(3, 8)):
            s = skill_by.get(t)
            if s:
                db.add(ResumeSkill(resume_id=r.resume_id, skill_id=s.id, is_out_of_dict=False))
        if random.random() < 0.15:
            db.add(ResumeSkill(resume_id=r.resume_id, raw_label=fake.word(), is_out_of_dict=True))
        for c in random.sample(certs, k=random.randint(0, 2)):
            db.add(ResumeCert(resume_id=r.resume_id, cert_id=c.id, is_out_of_dict=False))
    db.commit()
    return resumes


def make_posting(idx, skill_by):
    domestic = random.random() < DOMESTIC_RATIO
    role = pick_role()
    if domestic:
        source = random.choice(DOM_SOURCES)
        company = random.choice(DOM_COMPANIES)
        pin = PIN_BY_COMPANY.get(company, {})
        region_country, region_city = "KR", "서울"
        region_dist = pin.get("district") or random.choice(DISTRICTS)
        lat = round(float(pin["lat"]), 6) if pin.get("lat") else None
        lng = round(float(pin["lng"]), 6) if pin.get("lng") else None
        title = f"[{company}] {role} 채용"
        career_min = random.randint(0, 6)
        career_max = career_min + random.randint(2, 7)
        seniority = None
        response = round(random.uniform(20, 95), 1) if source == "wanted" else None
    else:
        source = random.choice(GLOBAL_SOURCES)
        company = random.choice(GLOBAL_COMPANIES)
        region_country = fake_en.country_code()
        region_city = None if source == "hn" else fake_en.city()
        region_dist = lat = lng = None
        title = f"{company} is hiring a {role.split('/')[0]}"
        career_min = career_max = None
        seniority = random.choice(["Entry", "Mid", "Senior", "Staff"]) if source == "himalayas" else None
        response = None

    if random.random() < RECENT_RATIO:
        post_d = date.today() - timedelta(days=random.randint(0, 30))
    else:
        post_d = date(2022, 1, 1) + timedelta(days=random.randint(0, (date.today() - date(2022, 1, 1)).days))
    close_d = post_d + timedelta(days=random.randint(15, 60)) if random.random() < 0.4 else None

    p = Posting(
        source=source, source_uid=f"dummy-{source}-{idx}",
        pool="domestic" if domestic else "global", company=company, title=title,
        post_date=post_d, close_date=close_d, career_min=career_min, career_max=career_max,
        seniority_raw=seniority, region_country=region_country, region_city=region_city,
        region_district=region_dist, lat=lat, lng=lng,
        industry=random.choice(INDUSTRIES), response_rate=response,
    )
    p._role = role  # 임시: 기술 매핑용
    return p


def gen_postings(db: Session, skills, certs):
    skill_by = {s.canonical: s for s in skills}
    postings = [make_posting(i, skill_by) for i in range(N_POSTINGS)]
    for p in postings:
        db.add(p)
    db.flush()  # id 부여
    # 팩트: tech/cert/category/raw
    for i, p in enumerate(postings):
        cats = {p._role}
        if random.random() < 0.25:
            cats.add(pick_role())  # 보조 카테고리(주 카테고리와 겹치면 무시 — (posting_id, category) 유니크)
        for cat in cats:
            db.add(PostingCategory(posting_id=p.id, category=cat))
        techs = sample_techs(p._role, random.randint(2, 6))
        for t in techs:
            s = skill_by.get(t)
            if s:
                db.add(PostingTech(posting_id=p.id, skill_id=s.id))
        if random.random() < 0.12:
            c = random.choice(certs)
            db.add(PostingCert(posting_id=p.id, cert_id=c.id))
        db.add(RawPosting(posting_id=p.id,
                          payload={"raw_title": p.title, "raw_company": p.company,
                                   "source": p.source, "techs": techs, "generated": True},
                          captured_at=datetime.now(timezone.utc)))
        if (i + 1) % 300 == 0:
            db.commit()
    db.commit()
    # 임베딩(일부)
    for p in random.sample(postings, min(N_EMBED, len(postings))):
        vec = [round(random.gauss(0, 1), 4) for _ in range(settings.embedding_dim)]
        db.add(PostingEmbedding(id=p.id, embedding=vec, model=f"dummy-random-{settings.embedding_dim}"))
    db.commit()
    return postings


def gen_interest_signals(db: Session, skills):
    """HN 실 월별 관심 시그널(matched keyword→skill)."""
    by_name = {s.canonical.lower(): s for s in skills}
    id_by = {s.id: s for s in skills}
    for a in db.query(SkillAlias).all():  # 별칭도 매칭(canonical 우선)
        s = id_by.get(a.skill_id)
        if s:
            by_name.setdefault(a.alias.lower(), s)
    alias_to_skill = by_name
    path = os.path.join(COLLECTOR, "out", "hn_backfill", "monthly_counts.csv")
    if not os.path.exists(path):
        return 0
    n = 0
    seen = set()
    with open(path, encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            kw = (row.get("keyword") or "").lower()
            s = alias_to_skill.get(kw)
            if not s:
                continue
            m = (row.get("month") or "")[:7]
            if m < "2024-06":
                continue
            try:
                mdate = datetime.strptime(m + "-01", "%Y-%m-%d").date()
            except ValueError:
                continue
            key = (s.id, mdate)
            if key in seen:
                continue
            seen.add(key)
            try:
                val = float(row.get("nb_hits") or 0)
            except ValueError:
                val = 0
            db.add(InterestSignal(skill_id=s.id, source="hn", month=mdate, value=val))
            n += 1
    db.commit()
    return n


def gen_collector_runs(db: Session):
    plan = {"jumpit": 898, "wanted": 2955, "himalayas": 77000, "hn": 10935,
            "github": 2056, "saramin": 4100, "programmers": 640}
    for src, cnt in plan.items():
        db.add(CollectorRun(source=src, job_id=f"seed-{src}",
                            last_run_at=datetime.now(timezone.utc) - timedelta(hours=random.randint(1, 72)),
                            ingested_count=cnt, status="success"))
    db.commit()


def generate(db: Session):
    print("0. 확장/테이블 준비…")
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS citext;"))
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector;"))
    Base.metadata.create_all(bind=engine)

    print("1. 기존 더미 정리(사전 유지)…")
    wipe(db)

    print(f"2. 사전 적재: Skill {len(SKILL_DICT)} · Cert {len(CERT_NAMES)} · JobCategory {len(ROLE_ROWS)}…")
    skills, certs = get_or_create_dicts(db)

    print(f"3. 회원 {N_USERS}명…")
    users = gen_users(db)

    print(f"4. 이력서 {N_RESUMES}건 + 스킬/자격증…")
    resumes = gen_resumes(db, users, skills, certs)

    print(f"5. 공고 {N_POSTINGS}건 + tech/cert/category/raw + 임베딩 {N_EMBED}…")
    postings = gen_postings(db, skills, certs)

    print("6. 관심 시그널(HN 실측)…")
    n_sig = gen_interest_signals(db, skills)

    print("7. 수집 실행 로그…")
    gen_collector_runs(db)

    pt = db.query(PostingTech).count()
    rs = db.query(ResumeSkill).count()
    print("\n완료:")
    print(f"  회원 {len(users)} · 이력서 {len(resumes)} · 공고 {len(postings)}")
    print(f"  posting_tech {pt} · resume_skill {rs} · interest_signal {n_sig}")
    print(f"  skill {db.query(Skill).count()} · skill_alias {db.query(SkillAlias).count()} · cert {db.query(Cert).count()}")


def main():
    try:
        import bcrypt as _b
        import faker as _f
        _ = (_b, _f)
    except ImportError:
        print("필요 패키지 없음: pip install faker bcrypt pgvector")
        return
    with SessionLocal() as db:
        generate(db)


if __name__ == "__main__":
    main()
