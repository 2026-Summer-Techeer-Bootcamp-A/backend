# 폴더 구조

 - 개별 파일 제외, 아직 안 만든 건 `[예정]` 으로 

```
backend/
├── app/                          # FastAPI 백엔드 패키지
│   ├── main.py                   # FastAPI 인스턴스 생성, 라우터 include
│   ├── models/                     # SQLAlchemy ORM 모델 패키지 (ERD 15테이블 + person 플레이스홀더)
│   │   ├── mixins.py                #   TimestampMixin/SoftDeleteMixin — created_at·is_deleted·deleted_at 공통 규약
│   │   ├── person.py                #   인프라 뼈대 단계 플레이스홀더 (실제 도메인 테이블 아님, main.py/seed.py가 아직 씀)
│   │   ├── user.py, resume.py       #   User / Resume·ResumeSkill·ResumeCert
│   │   ├── posting.py               #   Posting·RawPosting·PostingTech·PostingCert·PostingCategory·PostingEmbedding
│   │   ├── skill.py, cert.py        #   Skill·SkillAlias / Cert
│   │   ├── job_category.py, interest_signal.py
│   │   └── __init__.py              #   전 모델 re-export (`from app.models import X`)
│   │
│   ├── core/                      # 앱 전역 인프라 설정
│   │   ├── config.py              # pydantic-settings 환경변수 (Settings)
│   │   ├── db.py                  # SQLAlchemy engine / session / Base
│   │   ├── security.py             # [예정] JWT 발급·검증, bcrypt 해시 
│   │   └── redis.py                 # [예정] Redis 클라이언트 — 미로그인 세션(TTL) + 로그아웃 JWT jti 블록리스트
│   │
│   ├── schema/                     # DB 마이그레이션용 원본 SQL (DDL)
│   │   └── NNN_*.sql                #  플레이스홀더
│   │
│   ├── routers/                    # [예정] 라우터 계층 — API 명세(34개 엔드포인트)를 도메인별로 분리
│   │   ├── auth.py                  #   /auth/signup, /auth/login, /auth/logout, /auth/me
│   │   ├── resume.py                #   /resume/parse, /resume/confirm, /resume (CRUD)
│   │   ├── match.py                 #   /match/coverage, /match/gap, /match/what-if  (F2~F4, 히어로)
│   │   ├── stats.py                 #   /stats/skills, /stats/cooccurrence, /stats/industry, /stats/trend
│   │   ├── cert.py                  #   /cert/gap
│   │   ├── company.py               #   /company/by-skill  (과거 vs 현재 + 응답률)
│   │   ├── postings.py              #   /postings, /postings/{id}, /postings/map, /postings/{id}/similar
│   │   ├── trend.py                 #   /trend/rising  (뜨는 기술 배지)
│   │   ├── meta.py                  #   /skills, /job-categories, /industries, /certs (자동완성류, 전부 미인증)
│   │   └── llm.py                   #   /resume/feedback, /news, /chat — 서브 기능, 실패해도 본체 무영향
│   │
│   ├── schemas/                     # [예정] pydantic 요청/응답 모델 — routers와 1:1 대응 파일 분리
│   │   └── (auth / resume / match / stats / cert / company / postings / trend / meta / llm).py
│   │
│   ├── crud/                        # [예정] DB 세션 받아서 쿼리만 수행
│   │   └── (user / resume / posting / skill / cert / job_category / interest_signal).py
│   │
│   ├── services/                    # [예정] 비즈니스 로직
│   │   ├── auth.py                   #   회원가입/로그인/JWT 발급
│   │   ├── resume.py                 #   PDF 파싱 폴백(pdftotext), taxonomy 정규화, 세션/영속 분기
│   │   ├── match.py                  #   매칭 엔진 — 커버리지·갭·what-if 산식 (결정적 SQL 집계)
│   │   ├── stats.py                  #   점유율·co-occurrence·산업별·연도 트렌드 집계
│   │   ├── company.py                #   180일 기준 과거/현재 기업 분할
│   │   └── llm.py                    #   이력서 피드백, RAG 챗봇 — 실패 시 `degraded:true` 폴백
│   │
│   ├── exceptions.py                 # [예정] 커스텀 예외 + 전역 예외 핸들러
│   └── deps.py                       # [예정] 공용 Depends — 인증(JWT), 분석 입력 해석(session_id/resume_id→스킬셋)
│
├── collector/                    # [예정] 채용공고·관심 시그널 수집기 — GCP VM에서 systemd 타이머로 앱과 독립 실행
│   ├── collect_<source>.py         #   소스별 수집기 (himalayas / hn / jumpit / wanted / wwr)
│   ├── extract.py                   #   title·description → 기술/자격증 추출 (사전 매칭, 매처 정본)
│   ├── taxonomy_v2.json              #   240 canonical 기술 사전 + 한글 별칭 (조인 관문)
│   ├── certs_taxonomy.json
│   └── enrich.py                     #   연차 매핑 등 후처리 (F12)
│
├── alembic/                      # [예정] DB 마이그레이션 버전 관리
│   ├── env.py
│   └── versions/
├── alembic.ini                   # [예정]
│
├── db/                          # 로컬 개발용 Postgres 이미지 빌드 컨텍스트 (PostGIS + pgvector)
│   ├── Dockerfile
│   └── init/                    # 컨테이너 최초 기동 시 1회 실행되는 init SQL
│
├── observability/               # Prometheus / Loki / Tempo / Grafana 설정 파일
│   └── grafana/provisioning/datasources/
│
├── scripts/                     # 운영/개발 보조 스크립트 (seed, notify, mart.db → Postgres 1회 이관 등)
│
├── tests/                       # pytest 테스트
│
├── docs/                        # 설계 스펙 문서
│
├── .github/workflows/           # CI/CD
│   ├── ci.yml
│   └── deploy.yml
│
├── docker-compose.yml           # 프로덕션 배포 스택 (app + 모니터링, DB/Redis는 외부 GCP 리소스)
├── docker-compose.dev.yml       # 로컬 개발 의존성 (db + redis 컨테이너)
├── Dockerfile
├── requirements.txt
├── requirements-dev.txt
├── .env.example
└── README.md
```

## 계층 역할

요청이 들어오면 `routers → services → crud → models` 순으로 내려가고, 응답은 `schemas`로 나가는 흐름이 기본이에요.

- **routers**: HTTP 엔드포인트 정의만. 요청 파싱하고 services 호출해서 결과 반환.
- **schemas**: pydantic 요청/응답 모델. `models.py`(DB)와는 분리.
- **services**: 실제 비즈니스 로직 — 특히 `match.py`가 이 제품의 핵심(커버리지/갭/what-if 산식). 여러 crud 조합, 검증 규칙, LLM degraded 폴백 등도 여기.
- **crud**: DB 세션 받아서 단순 쿼리만 수행.
- **models/**: SQLAlchemy ORM. `posting`, `skill`, `resume`, `user` 등 15테이블 + `person`(플레이스홀더). CITEXT/JSONB/pgvector 타입은 `.with_variant(..., "postgresql")`로 감싸서 테스트용 SQLite에서도 `create_all`이 깨지지 않게 해둠.
- **app/schema/** (SQL): `schemas/`(pydantic)와 이름이 비슷하지만 다른 용도 — DB DDL 원본. 지금 든 `001_person.sql`은 인프라 뼈대 단계 플레이스홀더고, 실제 스키마는 `erd-f2.sql` 기준(한글 테이블명 → 논리 영문명 매핑은 04-erd.md 참고)으로 다시 짜야 함.
- **collector**: 채용공고(himalayas/hn/jumpit/wanted/wwr)와 관심 시그널(GitHub/HN)을 모으는 배치 스크립트 묶음. **FastAPI 요청 경로와 무관** — GCP VM에서 systemd 타이머로 따로 돔. 기존 `gh-hn-data-collector` 레포의 동일 이름 폴더가 실제 구현 레퍼런스.
- **alembic/**: DB 마이그레이션 이력 관리. 지금은 `app/schema/*.sql` + `scripts/seed.py`로 수동 관리 중인데, 15테이블 스키마가 들어오면 alembic으로 전환 예정.
- **exceptions.py**: 도메인 예외 정의 + FastAPI 전역 예외 핸들러 등록 (`sample_warning`, `pool` 불일치 422 등 공통 처리에 유용).

도메인이 늘어나면 위 각 계층 아래에 도메인 이름으로 파일을 하나씩 추가하는 방식으로 확장.

## 확실해진 부분 (지난 버전 대비)

- **Redis 용도 확정**: (1) 미로그인 사용자의 확정 스킬셋 세션(TTL) — `POST /resume/confirm`이 발급, (2) 로그아웃 시 JWT `jti` 블록리스트, (3) `/news` 3시간 캐시. → `app/core/redis.py`에서 세 용도 다 처리 가능.
- **pgvector 용도 확정**: `posting_embedding.embedding VECTOR(1536)`, HNSW cosine — `GET /postings/{id}/similar`(유사 공고) 전용. 매칭 점수 계산(F2~F4)에는 안 씀. 우선순위 P3(서브 B)라 급하지 않음.
- **collector는 app 패키지 밖**: 처음엔 `app/collector/`로 잡았었는데, 실제로는 systemd 타이머로 앱과 완전히 분리 실행되는 배치라 최상위 `collector/`가 맞음 (`db/`, `observability/`와 같은 급).

## 아직 확실치 않은 부분

- **taxonomy_v2.json을 앱이 어떻게 읽나**: 이력서 정규화(F1)도 같은 사전을 써야 하는데, `collector/taxonomy_v2.json`을 앱이 직접 import해서 읽을지, 아니면 이 JSON을 최초 1회 `skill`/`skill_alias` 테이블에 적재해두고 앱은 DB만 보는 방식일지 아직 결정 안 됨. (DB만 보는 쪽이 API 서버 입장에선 더 깔끔해 보임 — 다음에 확정 필요.)
- **mart.db(SQLite) → PostgreSQL 1회 이관 스크립트**의 정확한 위치/이름 (`scripts/migrate_mart.py` 정도로 예상, 아직 안 정함).
- **실제 DB 마이그레이션(alembic/`app/schema/*.sql`)은 아직 안 만듦**: `app/models/`는 채워졌지만, 이 모델대로 실제 Postgres 테이블을 만드는 마이그레이션은 다음 단계.

