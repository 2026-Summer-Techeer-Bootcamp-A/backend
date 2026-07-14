# CI/CD 개선 — 2계층 테스트(fast/slow) + 배포 게이트 설계

- **Date:** 2026-07-14
- **Status:** Approved (구현 대기)
- **Scope:** backend 저장소 (`2026-Summer-Techeer-Bootcamp-A/backend`)

## 1. 배경 / 현재 상태

관측된 사실(코드 기준):

- **CI (`.github/workflows/ci.yml`)** — `pull_request` + `push`(`branches-ignore: [main]`)에서 단일 잡이 순차 실행:
  `pip install` → `ruff check .` → `pytest -q` → `docker build`. 캐싱 없음, 테스트 분리 없음, 커버리지 없음.
- **Deploy (`.github/workflows/deploy.yml`)** — `push`(`branches: [main]`)에서 이미지 빌드/푸시(Artifact Registry) → VM SSH 배포 → Discord/Slack 알림.
- **테스트** — `tests/` 아래 29개 파일. 대부분 **인메모리 SQLite(`StaticPool`)** 위에서 각 테스트가 직접 엔진·스키마·시드를 구성(`test_feed.py`의 `client` 픽스처, `tests/_mart_fixture.py`의 `make_target()`). `conftest.py` 없음, pytest 설정/마커 없음.
- **실 Postgres 통합의 씨앗** — `tests/test_fix_source_pool.py`가 이미 `@pytest.mark.skipif("DATABASE_URL" not in os.environ)` 패턴으로 실 Postgres에 접속하고, 세션 로컬 **TEMP TABLE**로 실데이터를 보호. (표준으로 승격할 기반)
- **SQLite/Postgres 분기** — `app/models/user.py`의 `email`은 `String(255).with_variant(CITEXT(), "postgresql")`, `app/models/posting.py`의 `embedding`은 `Text().with_variant(Vector(1024), "postgresql")`. 즉 SQLite에선 검증 불가한 pgvector/CITEXT 동작이 프로덕션에만 존재.
- **DB 확장** — `db/init/01-extensions.sql`: `postgis`, `citext`, `vector`. ORM 모델에 geometry 컬럼은 없음(postgis 미사용). 필요한 건 `vector` + `citext`.
- **커스텀 DB 이미지** — `db/Dockerfile` = `postgis/postgis:17-3.5` + `postgresql-17-pgvector`.

### 핵심 리스크

1. **main 배포가 테스트 게이트 없이 나감.** `ci.yml`은 main 제외, `deploy.yml`은 main 전용 → main에 머지/푸시되면 **테스트 없이 배포**. (최우선 해소 대상)
2. **실 통합 테스트 부재.** 전부 SQLite라 pgvector/CITEXT/raw SQL 동작을 못 잡음.
3. **캐싱 전무.** 매 실행마다 무거운 `fastembed` 포함 전체 설치 + docker build.

## 2. 목표 (합의됨: 4개 전부)

- **배포 안전성** — 배포 전 테스트 통과 강제.
- **실 통합 테스트 추가** — SQLite가 못 잡는 실 Postgres/pgvector 계층 신설.
- **속도/피드백** — 캐싱 + 단계형으로 빠른 유닛을 먼저 게이트, 느린 통합은 나중.
- **구조/유지보수** — 마커/conftest로 fast/slow 체계화.
- **로그 가시성 + 시크릿 안전** — 각 테스트 결과를 Actions 로그/UI에서 확인하되, 민감정보(시크릿 키 등)는 로그에 노출되지 않게.

## 3. 비목표 (YAGNI)

- 테스트 프레임워크 교체(pytest 유지).
- SQLite 스위트를 전부 Postgres로 이관(대부분은 fast tier로 SQLite 유지).
- 커버리지 임계값 강제 게이트(리포트만, P4 선택).
- 무관한 리팩터링.

## 4. 확정된 설계 결정

| 항목 | 결정 | 근거 |
|---|---|---|
| 통합 DB 인프라 | **GH Actions `services:` 컨테이너** | 프로덕션과 동일 엔진, 셋업 단순 |
| 서비스 이미지 | **`pgvector/pgvector:pg17`** (공개) | geometry 미사용 → postgis 불필요. 통합 픽스처가 `CREATE EXTENSION vector; citext;` 부트스트랩 |
| 배포 게이트 | **재사용 워크플로우(`workflow_call`)** | ci·deploy가 동일 테스트 스위트 공유(중복 제거) |
| fast/slow 구분 | pytest **마커 `integration`** + `DATABASE_URL` 게이팅 | 기존 `test_fix_source_pool.py` 관례 승격 |

## 5. 아키텍처

### 5.1 테스트 2계층

- **Fast tier (무마커, 기본)** — 인메모리 SQLite. 외부 인프라 불필요. 선택: `pytest -m "not integration"`.
- **Slow tier (`@pytest.mark.integration`)** — 실 Postgres 필요. `DATABASE_URL` 없으면 자동 skip(로컬 개발자 배려). 선택: `pytest -m integration`.

`tests/conftest.py`가 공용 픽스처를 제공하여 흩어진 셋업 중복을 흡수:

- `sqlite_engine` — 인메모리 SQLite 엔진(`StaticPool`) + `Base.metadata.create_all`. 기존 `test_feed.py`/`_mart_fixture.make_target()`의 중복을 대체.
- `pg_engine` — `DATABASE_URL` 기반 실 Postgres 엔진. 최초 사용 시 `CREATE EXTENSION IF NOT EXISTS vector; CREATE EXTENSION IF NOT EXISTS citext;` 실행 후 스키마 준비. 실데이터 보호는 기존 TEMP TABLE 관례 유지.

### 5.2 CI DAG — "빠른 것 먼저, 느린 것 나중"

```
lint (ruff) ─┐
             ├─→ unit (SQLite, pip 캐시) ─→ integration (pgvector 서비스) ─→ docker-build
             ┘         └ 실패 시 이후 단계 진입 안 함(느린 자원 절약)
```

`needs:`로 순서를 강제. `lint`/`unit`은 수 분 내 빠른 피드백, `unit` 통과 후에만 `integration`이 실 Postgres 서비스를 기동.

### 5.3 재사용 워크플로우 + 배포 게이트

- `.github/workflows/test.yml` — `on: workflow_call`. 위 DAG(lint→unit→integration→docker-build)를 담는 단일 소스.
- `.github/workflows/ci.yml` — `on: pull_request` + `push`(non-main). `test.yml`을 `uses:`로 호출.
- `.github/workflows/deploy.yml` — main push. **`test` 잡(= `test.yml` 호출)을 추가하고 `deploy: needs: test`.** 테스트 실패 시 배포 중단 → 리스크 #1 해소.

### 5.4 로그 가시성 & 시크릿 안전

**가시성**
- pytest 실행 옵션: `-v --tb=short -ra` (테스트별 결과 + 실패 요약 노출).
- `--junitxml`로 결과 파일 생성 → 잡 **step summary**에 요약 표기, 실패 라인 **인라인 어노테이션**(`pytest-github-actions-annotate-failures`, requirements-dev에 추가).
- 각 단계(lint/unit/integration)를 별도 잡/스텝으로 분리해 로그 탐색성 확보.

**시크릿 안전 (필수 규칙)**
- 테스트 잡에는 **더미/일회성 크리덴셜만** 주입: 테스트 Postgres는 `postgres:postgres`(에페메럴 CI 컨테이너 → 민감정보 아님), `GEMINI_API_KEY`는 미설정/빈값으로 두어 외부 호출 없이 폴백 경로로 검증.
- CI 스텝에서 `env`/`printenv`/비밀 포함 라인의 `set -x` **금지**. GitHub이 등록된 시크릿을 자동 마스킹하며, 동적으로 계산된 민감값이 있으면 `::add-mask::`로 명시 마스킹.
- 진짜 시크릿(GCP WIF, `DISCORD_WEBHOOK_URL`, `SLACK_WEBHOOK_URL`)은 **deploy 잡에서만** 참조(테스트 잡엔 노출 안 함).
- `.env`는 CI에서 사용하지 않음(테스트는 환경변수/기본값으로 동작).

## 6. 파일별 변경

1. **`pyproject.toml` (신규)** — `[tool.pytest.ini_options]`: `markers = ["integration: requires live Postgres (DATABASE_URL)"]`, `addopts` 기본값 정리. 기존 `ruff` 설정도 여기로 수렴(선택).
2. **`tests/conftest.py` (신규)** — `sqlite_engine`, `pg_engine`, 공용 시드 헬퍼. 기존 테스트의 중복 셋업을 점진 흡수(동작 보존).
3. **`.github/workflows/test.yml` (신규)** — `workflow_call` 재사용 워크플로우. lint/unit/integration/docker-build 잡.
4. **`.github/workflows/ci.yml` (수정)** — 본문을 `uses: ./.github/workflows/test.yml` 호출로 축소.
5. **`.github/workflows/deploy.yml` (수정)** — `test` 호출 잡 추가 + `deploy: needs: test`.
6. **`tests/test_fix_source_pool.py` (수정)** — `skipif` → `@pytest.mark.integration`(+ `DATABASE_URL` 게이팅 유지)로 표준화.
7. **통합 테스트 1개 추가** — `load_mart`를 실 Postgres에 적용하는 `@pytest.mark.integration` 테스트(slow tier 채우기, TEMP TABLE/롤백으로 실데이터 보호).
8. **`requirements-dev.txt` (수정)** — `pytest-github-actions-annotate-failures` 추가.

## 7. 단계적 진행 (Phase) 및 완료 조건

- **P1 — 뼈대(fast만).** `pyproject.toml` 마커 + `conftest.py` + `test.yml`(lint/unit)와 `ci.yml` 축소.
  - 완료 조건: PR CI가 `-m "not integration"`으로 초록, 기존 테스트 전부 통과.
- **P2 — 배포 게이트.** `deploy.yml`에 `test` 잡 + `needs: test`.
  - 완료 조건: main 배포가 테스트 통과 후에만 진행됨을 워크플로우 구조로 확인.
- **P3 — 통합 계층.** `integration` 잡 + `pgvector/pgvector:pg17` 서비스 + `DATABASE_URL` 주입 + 첫 통합 테스트(≥1개) + `test_fix_source_pool` 마커 승격.
  - 완료 조건: `integration` 잡이 실 Postgres에서 통합 테스트를 실제로 실행하고 통과(전부 skip이 아님).
- **P4 — 튜닝(선택).** pip/buildx 캐시 최적화, `concurrency`로 중복 실행 취소, 커버리지 리포트.

## 8. 리스크 / 열린 판단

- **서비스 이미지 parity** — `pgvector/pgvector:pg17`는 postgis 미포함. 현재 ORM에 geometry 없어 문제없으나, 향후 postgis 의존 스키마가 생기면 프로덕션 `db/` 이미지를 GHCR로 빌드·푸시해 서비스로 쓰는 방식으로 전환. (P3에서 최종 확인)
- **slow tier 공동화 방지** — 인프라만 짓고 통합 테스트가 비면 무의미. P3 완료 조건에 "실제 실행/통과하는 통합 테스트 ≥1개"를 명시.
- **`fastembed` 설치 비용** — 무거움. 유닛 잡 설치 시간에 영향. P4에서 캐시로 완화(또는 유닛 잡이 임포트하지 않으면 분리 설치 검토).
- **conftest 중복 흡수 범위** — 기존 28개 파일을 한 번에 리팩터링하지 않음. 동작 보존을 최우선으로, 신규 픽스처는 opt-in으로 도입 후 점진 이행.

## 9. CI 자체 검증 방법

- P1: `pytest -m "not integration" -v`가 로컬/CI에서 통과.
- P3: `DATABASE_URL` 세팅 후 `pytest -m integration -v`가 실 Postgres에서 통과. Actions 로그에 테스트별 결과가 보이고, 로그 스캔 시 시크릿/비밀번호(더미 제외) 미노출.
- 배포 게이트: 의도적으로 실패하는 테스트를 PR/브랜치에서 넣어 deploy가 차단되는지 1회 검증(머지 전 되돌림).
