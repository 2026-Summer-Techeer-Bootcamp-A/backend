"""
Generate realistic dummy data for the backend.
Requires:
    pip install faker bcrypt
Run from repo root:
    python generate_dummy_data.py
"""

import random
from datetime import date, datetime, timedelta, timezone
import bcrypt
from faker import Faker
from sqlalchemy.orm import Session

from app.core.db import SessionLocal, engine, Base
from app.models import (
    User, Resume, ResumeSkill, ResumeCert, Cert, Skill, JobCategory,
    Posting, PostingTech, PostingCert, PostingCategory, RawPosting
)

fake = Faker('ko_KR')
fake_en = Faker('en_US')

TECH_STACKS = [
    ("Python", "language", False), ("AWS", "cloud", False), ("JavaScript", "language", False),
    ("Git", "tool", False), ("TypeScript", "language", False), ("SQL", "database", False),
    ("Kubernetes", "devops", False), ("PostgreSQL", "database", False), ("Azure", "cloud", False),
    ("Salesforce", "crm", False), ("GCP", "cloud", False), ("Docker", "devops", False),
    ("Java", "language", False), ("Spring", "framework", False), ("React", "frontend", False),
    ("C++", "language", False), ("Go", "language", True), ("Rust", "language", True)
]

CATEGORIES = [
    "Developer", "Data Science", "Product", "Design", "Sales",
    "Customer Service", "Marketing", "Operations", "HR", "Finance"
]

CERTS = [
    "AWS Solutions Architect", "PMP", "CISSP", "CISM",
    "AWS Certified", "CISA", "CEH", "AWS Developer", "정보처리기사", "CKA"
]

COMPANIES_DOMESTIC = [
    "네이버", "카카오", "라인", "쿠팡", "배달의민족", "토스", "당근마켓", "직방", "야놀자", "무신사"
]

COMPANIES_GLOBAL = [
    "Google", "Apple", "Meta", "Amazon", "Microsoft", "Netflix", "Tesla", "Uber", "Airbnb", "Stripe"
]

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def generate_data(db: Session):
    print("0. Creating database extensions and tables if they do not exist...")
    from sqlalchemy import text
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS citext;"))
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector;"))
    Base.metadata.create_all(bind=engine)

    print("1. Inserting basic dictionaries (Skill, JobCategory, Cert)...")
    skill_objs = []
    for name, cat, ambiguous in TECH_STACKS:
        skill = db.query(Skill).filter_by(canonical=name).first()
        if not skill:
            skill = Skill(canonical=name, category=cat, is_ambiguous=ambiguous)
            db.add(skill)
        skill_objs.append(skill)

    category_objs = []
    for name in CATEGORIES:
        cat = db.query(JobCategory).filter_by(name=name).first()
        if not cat:
            cat = JobCategory(name=name)
            db.add(cat)
        category_objs.append(cat)

    cert_objs = []
    for name in CERTS:
        cert = db.query(Cert).filter_by(name=name).first()
        if not cert:
            cert = Cert(name=name)
            db.add(cert)
        cert_objs.append(cert)
        
    db.commit()

    print("2. Generating Users and Resumes...")
    users = []
    hashed_pw = hash_password("password123!")
    for _ in range(20):
        email = fake.unique.email()
        user = User(
            email=email,
            password_hash=hashed_pw,
            nickname=fake.user_name(),
        )
        db.add(user)
        users.append(user)
    db.commit()

    resumes = []
    for user in users:
        for _ in range(random.randint(1, 2)):
            resume = Resume(
                user_id=user.id,
                title=f"{fake.job()} 지원서",
                position=random.choice(category_objs).name,
                career_min=random.randint(0, 3),
                career_max=random.randint(3, 10),
                pool=random.choice(['domestic', 'global'])
            )
            db.add(resume)
            resumes.append(resume)
    db.commit()

    print("3. Adding Skills and Certs to Resumes...")
    for resume in resumes:
        num_skills = random.randint(3, 7)
        chosen_skills = random.sample(skill_objs, min(num_skills, len(skill_objs)))
        for s in chosen_skills:
            rs = ResumeSkill(resume_id=resume.resume_id, skill_id=s.id, is_out_of_dict=False)
            db.add(rs)
        if random.random() < 0.2:
            db.add(ResumeSkill(resume_id=resume.resume_id, raw_label=fake.word(), is_out_of_dict=True))
            
        num_certs = random.randint(0, 2)
        chosen_certs = random.sample(cert_objs, min(num_certs, len(cert_objs)))
        for c in chosen_certs:
            rc = ResumeCert(resume_id=resume.resume_id, cert_id=c.id, is_out_of_dict=False)
            db.add(rc)
            
    db.commit()

    print("4. Generating Postings (Current & Historical)...")
    postings = []
    
    def create_posting(is_current: bool, is_domestic: bool):
        if is_domestic:
            source = random.choice(['jumpit', 'wanted'])
            company = random.choice(COMPANIES_DOMESTIC) + " " + fake.company_suffix()
            region_c = 'KR'
            region_city = '서울'
            region_dist = random.choice(['역삼동', '서초동', '삼성동', '성수동', '판교동', '여의도동'])
            lat = float(fake.latitude()) if source == 'wanted' else None
            lng = float(fake.longitude()) if source == 'wanted' else None
            pool = 'domestic'
            title = f"[{company}] {random.choice(category_objs).name} 채용"
        else:
            source = random.choice(['himalayas', 'wwr', 'hn'])
            company = random.choice(COMPANIES_GLOBAL) + " " + fake_en.company_suffix()
            region_c = fake_en.country_code()
            region_city = fake_en.city() if source != 'hn' else None
            region_dist = None
            lat = None
            lng = None
            pool = 'global'
            title = f"{company} is hiring a {random.choice(category_objs).name}"

        if is_current:
            post_d = date.today() - timedelta(days=random.randint(0, 30))
        else:
            start_date = date(2022, 1, 1)
            end_date = date(2026, 7, 7)
            delta = end_date - start_date
            post_d = start_date + timedelta(days=random.randint(0, delta.days))
            
        close_d = None
        if source in ['jumpit', 'wwr'] or (source == 'wanted' and random.random() < 0.2):
            close_d = post_d + timedelta(days=random.randint(15, 60))

        career_min, career_max = None, None
        if source in ['jumpit', 'wanted']:
            career_min = random.randint(0, 5)
            career_max = career_min + random.randint(2, 7)
            
        seniority_raw = random.choice(["Entry", "Mid", "Senior", "Manager"]) if source == 'himalayas' else None

        posting = Posting(
            source=source,
            source_uid=fake.unique.uuid4()[:32],
            pool=pool,
            company=company,
            title=title,
            post_date=post_d,
            close_date=close_d,
            career_min=career_min,
            career_max=career_max,
            seniority_raw=seniority_raw,
            region_country=region_c,
            region_city=region_city,
            region_district=region_dist,
            lat=lat,
            lng=lng,
            industry=random.choice(["IT", "Finance", "Healthcare", "E-commerce"]),
            response_rate=random.uniform(10.0, 99.0) if source == 'wanted' else None
        )
        return posting

    configs = [
        (True, True, 100),     # current domestic
        (True, False, 200),    # current global
        (False, True, 500),    # past domestic
        (False, False, 1000),  # past global
    ]

    count = 0
    for is_current, is_domestic, num in configs:
        for _ in range(num):
            p = create_posting(is_current, is_domestic)
            db.add(p)
            postings.append(p)
            count += 1
            if count % 200 == 0:
                db.commit()
    db.commit()

    print("5. Mapping Tech, Certs, Categories, and Raw to Postings...")
    count = 0
    for p in postings:
        db.add(PostingCategory(posting_id=p.id, category=random.choice(category_objs).name))
        
        num_skills = random.randint(2, 6)
        chosen_skills = random.sample(skill_objs, min(num_skills, len(skill_objs)))
        for s in chosen_skills:
            db.add(PostingTech(posting_id=p.id, skill_id=s.id))
            
        if random.random() < 0.1:
            c = random.choice(cert_objs)
            db.add(PostingCert(posting_id=p.id, cert_id=c.id))
            
        raw = RawPosting(
            posting_id=p.id,
            payload={"raw_title": p.title, "raw_company": p.company, "generated": True},
            captured_at=datetime.now(timezone.utc)
        )
        db.add(raw)
        
        count += 1
        if count % 200 == 0:
            db.commit()
            
    db.commit()
    print(f"Successfully generated {len(users)} users, {len(resumes)} resumes, and {len(postings)} postings.")

def main():
    try:
        import bcrypt
        import faker
        _ = (bcrypt, faker)
    except ImportError:
        print("Required packages missing. Run: pip install faker bcrypt passlib")
        return

    with SessionLocal() as db:
        generate_data(db)

if __name__ == "__main__":
    main()
