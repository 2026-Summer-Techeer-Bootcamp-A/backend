# career-backend 인프라 뼈대 — 설계 스펙 (2026-07-06)

## 목적
커리어 포지셔닝 대시보드의 백엔드 레포에 **기능 코드 전에 인프라 뼈대**를 세운다.
FastAPI는 hello world 수준만 띄우고, Docker/모니터링/CI-CD/환경변수 관리 골격을 완성해
"첫 커밋부터 자동 배포되고 첫 에러부터 그래프에 찍히는" 상태를 만든다.

성공 기준: `docker compose up` 하면 app + 모니터링 스택이 뜨고, `/`는 hello world, `/metrics`는
Prometheus가 긁을 수 있으며, Grafana에서 Prometheus/Loki/Tempo 데이터소스가 연결돼 있다.

## 범위 (이번 작업)
- FastAPI 앱: `GET /` (hello), `GET /healthz`, `GET /metrics` 만. 나머지는 `# TODO`.
- Dockerfile + `.dockerignore`
- `docker-compose.yml` (VM 배포 스택: app + 모니터링)
- `docker-compose.dev.yml` (로컬 개발 의존성: Postgres[PostGIS+pgvector] + Redis)
- 관측 스택 설정 파일 (prometheus/loki/alloy/tempo/grafana)
- 중앙집중식 `.env.example` (모든 환경변수의 단일 소스)
- CI/CD 워크플로 (`ci.yml`, `deploy.yml`)

## 범위 밖 (TODO 시owable seam만 남김)
- 실제 DB/Redis 연결 로직, 라우터/서비스/스키마, alembic, taxonomy, collector
- 앱의 OTel 트레이스 전송 배선 (Tempo는 스택에 존재, 앱→Tempo 배선은 TODO)
- 구조화 JSON 로깅 (seam만)

## 아키텍처
- **프로덕션(app-vm)**: `docker-compose.yml` = app + prometheus + loki + alloy + tempo + grafana.
  Postgres/Redis는 **compose에 없음** — 앱이 env의 `DATABASE_URL`/`REDIS_URL`로 Cloud SQL·Memorystore(외부)에 붙는다.
- **로컬 개발**: `docker-compose.dev.yml` = db(PostGIS+pgvector) + redis. 앱은 로컬 uvicorn 또는 `docker-compose.yml`의 app으로 실행.
  전체 동시 실행: `docker compose -f docker-compose.yml -f docker-compose.dev.yml up`.
- **환경변수 단일 소스**: 루트 `.env` (커밋 금지). Compose가 자동 로드해 `${...}` 치환 + app 컨테이너에 `env_file`로 주입.

## 잠긴 계약 (Locked Contracts) — 서브에이전트는 이 값을 절대 변경 금지

### 앱 런타임
- 베이스 이미지: `python:3.12-slim`
- 컨테이너 내 리슨: `0.0.0.0:8000` (uvicorn), 실행: `uvicorn app.main:app --host 0.0.0.0 --port 8000`
- 엔드포인트:
  - `GET /` → `{"message": "Hello World"}`
  - `GET /healthz` → `{"status": "ok"}`
  - `GET /metrics` → Prometheus 텍스트 (prometheus-fastapi-instrumentator)
- 비루트 유저로 실행, HEALTHCHECK는 `/healthz` 대상

### Compose 서비스명 · 포트 · 이미지 (docker-compose.yml)
| 서비스 | 이미지 | 포트(host:container) | 설정 마운트 |
|---|---|---|---|
| `app` | `build: .` + `image: ${APP_IMAGE:-career-backend:local}` | `${APP_PORT:-8000}:8000` | `env_file: .env` |
| `prometheus` | `prom/prometheus:v3.2.0` | `9090:9090` | `./observability/prometheus.yml:/etc/prometheus/prometheus.yml` |
| `loki` | `grafana/loki:3.4.1` | `3100:3100` | `./observability/loki-config.yaml:/etc/loki/local-config.yaml`, command `-config.file=/etc/loki/local-config.yaml` |
| `alloy` | `grafana/alloy:v1.7.1` | (none) | `./observability/config.alloy:/etc/alloy/config.alloy`, `/var/log:/var/log:ro`, command `run /etc/alloy/config.alloy` |
| `tempo` | `grafana/tempo:2.7.1` | `3200:3200`, `4317:4317` | `./observability/tempo.yaml:/etc/tempo/tempo.yaml`, command `-config.file=/etc/tempo/tempo.yaml` |
| `grafana` | `grafana/grafana:11.5.2` | `3000:3000` | `./observability/grafana/provisioning/datasources/ds.yml:/etc/grafana/provisioning/datasources/ds.yml`, `depends_on: [prometheus, loki, tempo]` |

- 모든 서비스 `restart: unless-stopped`.
- grafana 환경변수 `GF_SECURITY_ADMIN_PASSWORD=${GF_SECURITY_ADMIN_PASSWORD}`.
- app에 `env_file: [.env]`. 공통 네트워크(기본 default) 사용 — 서비스명으로 서로 접근.

### Dev compose 서비스명 · 포트 (docker-compose.dev.yml)
| 서비스 | 이미지 | 포트 | 비고 |
|---|---|---|---|
| `db` | `build: ./db` (FROM `postgis/postgis:17-3.5` + pgvector) | `${POSTGRES_PORT:-5432}:5432` | env: `POSTGRES_DB/USER/PASSWORD`, volume `pgdata:/var/lib/postgresql/data`, init `./db/init:/docker-entrypoint-initdb.d` |
| `redis` | `redis:7-alpine` | `${REDIS_PORT:-6379}:6379` | volume `redisdata:/data`, `--appendonly no` |

- 네임드 볼륨 `pgdata`, `redisdata` 선언.

### 관측 설정 파일 계약
- `observability/prometheus.yml`: `scrape_interval: 15s`, job `app` → target `app:8000` (metrics_path 기본 `/metrics`). job `prometheus` → `localhost:9090` (선택).
- `observability/loki-config.yaml`: Loki 3.x 단일 바이너리 로컬 설정(파일시스템 스토리지). 표준 local-config 기반.
- `observability/config.alloy`: `/var/log` 파일 → Loki(`http://loki:3100/loki/api/v1/push`) 전달. Alloy River 문법.
- `observability/tempo.yaml`: OTLP 수신(gRPC `0.0.0.0:4317`), 로컬 파일시스템 스토리지, `http_listen_port: 3200`.
- `observability/grafana/provisioning/datasources/ds.yml`: `apiVersion: 1`, datasources 3개 — Prometheus(`http://prometheus:9090`), Loki(`http://loki:3100`), Tempo(`http://tempo:3200`).

### 환경변수 계약 (.env.example — 단일 소스, 모든 변수 여기서 관리)
```
# ── App ──
APP_IMAGE=career-backend:local
APP_PORT=8000
LOG_LEVEL=info

# ── Database (로컬=dev compose db / 프로덕션=Cloud SQL 사설 IP) ──
POSTGRES_DB=appdb
POSTGRES_USER=appuser
POSTGRES_PASSWORD=change-me
POSTGRES_PORT=5432
DATABASE_URL=postgresql+psycopg://appuser:change-me@db:5432/appdb

# ── Redis (로컬=dev compose redis / 프로덕션=Memorystore 사설 IP) ──
REDIS_PORT=6379
REDIS_URL=redis://redis:6379/0

# ── Observability ──
GF_SECURITY_ADMIN_PASSWORD=change-me
OTEL_EXPORTER_OTLP_ENDPOINT=http://tempo:4317
OTEL_SERVICE_NAME=career-backend

# ── 외부 API 키 (향후 추가) ──
# OPENAI_API_KEY=
# DISCORD_WEBHOOK_URL=
```
- 앱 `app/core/config.py`는 pydantic-settings로 위 변수를 읽는다(`database_url`, `redis_url`, `otel_*`, `log_level`). DB/Redis/OTel 사용부는 `# TODO`.

### CI/CD 계약
- `.github/workflows/ci.yml`: 트리거 `pull_request` + `push`(main 제외 또는 전체). 스텝: checkout → setup-python 3.12 → `pip install -r requirements.txt -r requirements-dev.txt` → `ruff check .` → `pytest -q` → `docker build`(push 없음).
- `.github/workflows/deploy.yml`: 트리거 `push: branches:[main]`. `permissions: id-token: write, contents: read`.
  스텝: checkout → `google-github-actions/auth@v2`(WIF) → `setup-gcloud@v2` → Artifact Registry 도커 인증 → 이미지 build & push(`asia-northeast3-docker.pkg.dev/${PROJECT_ID}/app/career-backend:${GITHUB_SHA}` + `:latest`) → `gcloud compute ssh app-vm --zone asia-northeast3-a --command "cd ~/app && docker compose pull && docker compose up -d"`.
  자리표시자 명시: `PROJECT_ID=my-app-prod`, `PROJECT_NUM`, `REPO=your-org/your-repo`, `workload_identity_provider: projects/PROJECT_NUM/locations/global/workloadIdentityPools/github-pool/providers/github-provider`, `service_account: gh-deployer@my-app-prod.iam.gserviceaccount.com`.
  주석으로 필요한 IAM(artifactregistry.writer, compute.instanceAdmin.v1, iam.serviceAccountUser, OS Login) TODO 표기.

## 파일 트리 (산출물)
```
backend/
├── app/
│   ├── __init__.py
│   ├── main.py                  # / , /healthz, /metrics
│   └── core/
│       ├── __init__.py
│       └── config.py            # pydantic-settings, DB/Redis/OTel seam=TODO
├── requirements.txt             # fastapi, uvicorn[standard], prometheus-fastapi-instrumentator, pydantic-settings
├── requirements-dev.txt         # pytest, httpx, ruff
├── Dockerfile
├── .dockerignore
├── docker-compose.yml           # app + prometheus + loki + alloy + tempo + grafana
├── docker-compose.dev.yml       # db + redis
├── db/
│   ├── Dockerfile               # postgis + pgvector
│   └── init/01-extensions.sql   # CREATE EXTENSION postgis; vector;
├── observability/
│   ├── prometheus.yml
│   ├── loki-config.yaml
│   ├── config.alloy
│   ├── tempo.yaml
│   └── grafana/provisioning/datasources/ds.yml
├── tests/
│   ├── __init__.py
│   └── test_main.py             # / , /healthz, /metrics 스모크
├── .env.example
├── .gitignore                   # .env, __pycache__, *.pyc, .venv 등
└── .github/workflows/
    ├── ci.yml
    └── deploy.yml
```

## 테스트
- `tests/test_main.py`: httpx TestClient로 `GET /`(200, message), `GET /healthz`(200, status ok), `GET /metrics`(200, `text/plain`, `# HELP` 포함) 검증.

## 검증 (구현 후)
1. `pip install -r requirements.txt -r requirements-dev.txt && pytest -q` 통과.
2. `docker build -t career-backend:local .` 성공.
3. `cp .env.example .env && docker compose up -d` → `curl localhost:8000/` hello, `curl localhost:8000/metrics` 지표, `curl localhost:9090/-/healthy`, `curl localhost:3100/ready`, `curl localhost:3200/ready`.
4. `docker compose -f docker-compose.dev.yml up -d` → `psql`로 `\dx`에 postgis·vector 확장 존재.

## 병렬 구현 그룹 (파일 세트 무겹침)
- **A. FastAPI 앱**: `app/**`, `requirements.txt`, `requirements-dev.txt`, `tests/**`
- **B. Docker**: `Dockerfile`, `.dockerignore`, `docker-compose.yml`, `docker-compose.dev.yml`, `db/**`
- **C. 관측 설정**: `observability/**`
- **D. env + CI/CD**: `.env.example`, `.gitignore`, `.github/workflows/ci.yml`, `.github/workflows/deploy.yml`
