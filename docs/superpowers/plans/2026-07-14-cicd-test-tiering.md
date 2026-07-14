# CI/CD 2계층 테스트 + 배포 게이트 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 백엔드 테스트를 fast(SQLite)/slow(실 Postgres) 2계층으로 나누고, 빠른 유닛을 먼저 게이트하는 단계형 CI를 재사용 워크플로우로 구성하며, main 배포를 테스트 통과 뒤에만 나가게 한다.

**Architecture:** pytest 마커(`integration`)로 fast/slow를 구분하고 `conftest.py`가 `DATABASE_URL` 부재 시 통합 테스트를 자동 skip한다. `.github/workflows/test.yml`(`workflow_call`)이 `lint → unit → integration(pgvector 서비스) → docker-build` DAG를 담고, `ci.yml`(PR/브랜치)과 `deploy.yml`(main)이 이를 공유 호출한다. 통합 잡은 `pgvector/pgvector:pg17` 서비스에 대해 부트스트랩(확장 설치 + 스키마 생성) 후 실행한다.

**Tech Stack:** Python 3.12, pytest 8, SQLAlchemy 2, psycopg 3, pgvector, GitHub Actions.

## Global Constraints

- Python `3.12` (setup-python 고정).
- 테스트 잡에는 **더미/일회성 크리덴셜만** 주입한다: 테스트 Postgres = `postgres:postgres@localhost:5432/testdb`(에페메럴 → 민감정보 아님), `GEMINI_API_KEY` 미설정.
- CI 스텝에서 `env` / `printenv` / 비밀 포함 라인의 `set -x` **금지**. 진짜 시크릿(GCP WIF, `DISCORD_WEBHOOK_URL`, `SLACK_WEBHOOK_URL`)은 **deploy 잡에서만** 참조한다.
- pytest는 `-v --tb=short -ra`로 실행해 테스트별 결과를 Actions 로그에 노출한다.
- fast tier = 무마커(SQLite), slow tier = `@pytest.mark.integration`(실 Postgres). fast는 `pytest -m "not integration"`, slow는 `pytest -m integration`.
- 실 DB 파괴 금지: 통합 테스트는 TEMP TABLE 또는 에페메럴 CI DB에서만 쓰기한다.
- 관련 없는 워킹트리 변경(현재 `M app/crud/*`, `M app/routers/*`, `M tests/*`)은 커밋에 포함하지 않는다. 각 태스크는 **명시된 파일만** `git add` 한다.

---

## File Structure

- `pyproject.toml` (신규) — pytest 설정 + `integration` 마커 등록. **책임:** 테스트 러너 구성 단일 소스.
- `tests/conftest.py` (신규) — 공용 픽스처(`sqlite_engine`, `pg_conn`) + `DATABASE_URL` 부재 시 통합 자동 skip 훅. **책임:** 테스트 픽스처/게이팅 공용화.
- `tests/test_pg_integration.py` (신규) — pgvector 거리검색 / CITEXT 유니크 통합 테스트. **책임:** SQLite 사각지대 검증(slow tier 시드).
- `tests/test_fix_source_pool.py` (수정) — `skipif` → `@pytest.mark.integration`. **책임:** 기존 통합 테스트 표준화.
- `scripts/init_test_db.py` (신규) — CI 부트스트랩: 확장 설치 + 전체 스키마 생성. **책임:** 빈 CI Postgres 준비.
- `requirements-dev.txt` (수정) — `pytest-github-actions-annotate-failures` 추가. **책임:** 실패 인라인 어노테이션.
- `.github/workflows/test.yml` (신규) — `workflow_call` 재사용 워크플로우(lint/unit/integration/docker-build). **책임:** 테스트 파이프라인 단일 소스.
- `.github/workflows/ci.yml` (수정) — `test.yml` 호출로 축소. **책임:** PR/브랜치 트리거.
- `.github/workflows/deploy.yml` (수정) — `test` 게이트 추가. **책임:** main 배포 전 검증.

---

## Task 1: pytest 설정 + conftest(마커 게이팅 + 공용 픽스처)

**Files:**
- Create: `pyproject.toml`
- Create: `tests/conftest.py`

**Interfaces:**
- Produces: 마커 `integration`; 픽스처 `sqlite_engine() -> sqlalchemy.Engine`, `pg_conn() -> psycopg.Connection`; collection 훅으로 `DATABASE_URL` 부재 시 `integration` 테스트 자동 skip.
- Consumes: `app.core.db.Base`, `app.models`(모델 등록).

- [ ] **Step 1: `pyproject.toml` 작성**

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-ra --tb=short"
markers = [
    "integration: 실 Postgres가 필요한 통합 테스트 (DATABASE_URL 설정 시에만 실행)",
]

[tool.ruff]
target-version = "py312"
```

- [ ] **Step 2: `tests/conftest.py` 작성**

```python
"""공용 테스트 픽스처 + fast/slow 게이팅.

DATABASE_URL이 없으면 @pytest.mark.integration 테스트를 전부 skip 한다
(로컬 개발자는 실 Postgres 없이 fast tier만 돌린다).
"""

import os
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 - Base에 모든 모델을 등록
from app.core.db import Base


def pytest_collection_modifyitems(config, items):
    """DATABASE_URL 부재 시 integration 마커 테스트를 일괄 skip."""
    if os.environ.get("DATABASE_URL"):
        return
    skip_integration = pytest.mark.skip(
        reason="requires a live Postgres (set DATABASE_URL)"
    )
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip_integration)


@pytest.fixture
def sqlite_engine() -> Iterator[Engine]:
    """인메모리 SQLite 엔진 + 전체 스키마 (fast tier 공용)."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def pg_conn() -> Iterator["object"]:
    """실 Postgres psycopg 커넥션 (통합 tier 공용). 확장 부트스트랩 포함.

    DATABASE_URL 부재 시 이 픽스처를 쓰는 테스트는 collection 훅에서 이미 skip 된다.
    """
    import psycopg

    url = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(url) as conn:
        with conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
            cur.execute("CREATE EXTENSION IF NOT EXISTS citext")
        conn.commit()
        yield conn
```

- [ ] **Step 3: fast tier 회귀 검증 (통합 자동 skip 확인)**

Run: `pytest -m "not integration" -q`
Expected: 기존 테스트 전부 PASS (동작 변화 없음).

Run: `pytest -q`
Expected: PASS. `DATABASE_URL`이 없으므로 `tests/test_fix_source_pool.py`는 이미 skip 상태 유지(현재도 skipif).

- [ ] **Step 4: 마커 등록 확인 (unknown-marker 경고 없음)**

Run: `pytest --markers | grep integration`
Expected: `@pytest.mark.integration: 실 Postgres가 필요한 통합 테스트 ...` 출력.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml tests/conftest.py
git commit -m "test: pytest 마커(integration) + conftest 공용 픽스처/게이팅

Claude-Session: https://claude.ai/code/session_01VKD79pbyWWL72X7VrmBdSS"
```

---

## Task 2: 통합 테스트 시드(pgvector·CITEXT) + 기존 통합 테스트 마커화

**Files:**
- Create: `tests/test_pg_integration.py`
- Modify: `tests/test_fix_source_pool.py:18-20`
- Modify: `requirements-dev.txt`

**Interfaces:**
- Consumes: `pg_conn` 픽스처(Task 1), 마커 `integration`(Task 1).
- Produces: 최소 3개의 `@pytest.mark.integration` 테스트가 실 Postgres에서 실제 실행/통과(전부 skip 아님).

- [ ] **Step 1: pgvector·CITEXT 통합 테스트 작성 (failing 예정 — DATABASE_URL 있을 때)**

Create `tests/test_pg_integration.py`:

```python
"""SQLite가 못 잡는 실 Postgres 기능 검증 (pgvector, CITEXT).

각 테스트는 세션 로컬 TEMP TABLE에서만 쓰므로 실 테이블을 건드리지 않는다.
"""

import pytest

pytestmark = pytest.mark.integration


def test_pgvector_orders_by_l2_distance(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute("CREATE TEMP TABLE emb (id int, v vector(3))")
        cur.execute("INSERT INTO emb VALUES (1, '[1,0,0]'), (2, '[0,1,0]'), (3, '[0.9,0.1,0]')")
        pg_conn.commit()
        cur.execute("SELECT id FROM emb ORDER BY v <-> '[1,0,0]' LIMIT 2")
        nearest = [row[0] for row in cur.fetchall()]
    assert nearest == [1, 3]


def test_citext_unique_is_case_insensitive(pg_conn):
    import psycopg

    with pg_conn.cursor() as cur:
        cur.execute("CREATE TEMP TABLE u (email citext UNIQUE)")
        cur.execute("INSERT INTO u VALUES ('User@Example.com')")
        pg_conn.commit()
        with pytest.raises(psycopg.errors.UniqueViolation):
            cur.execute("INSERT INTO u VALUES ('user@example.com')")
        pg_conn.commit()
    pg_conn.rollback()
```

- [ ] **Step 2: DATABASE_URL 없이 skip 확인**

Run: `pytest tests/test_pg_integration.py -v`
Expected: 2개 테스트가 SKIPPED (`requires a live Postgres`).

- [ ] **Step 3: `test_fix_source_pool.py`를 마커로 표준화**

`tests/test_fix_source_pool.py`의 18-20행을 아래로 교체:

```python
pytestmark = pytest.mark.integration
```

(상단 `import os` 는 더 이상 필요 없으면 제거. `import pytest`는 유지.) 게이팅은 Task 1의 conftest 훅이 담당한다.

- [ ] **Step 4: requirements-dev.txt에 어노테이션 플러그인 추가**

`requirements-dev.txt` 끝에 한 줄 추가:

```
pytest-github-actions-annotate-failures
```

- [ ] **Step 5: 실 Postgres에 대해 통합 테스트 통과 확인 (로컬 도커)**

```bash
docker run -d --rm --name pgtest -e POSTGRES_USER=postgres -e POSTGRES_PASSWORD=postgres \
  -e POSTGRES_DB=testdb -p 5433:5432 pgvector/pgvector:pg17
# 준비 대기
until docker exec pgtest pg_isready -U postgres; do sleep 1; done
DATABASE_URL="postgresql+psycopg://postgres:postgres@localhost:5433/testdb" \
  pytest -m integration tests/test_pg_integration.py -v --tb=short
docker stop pgtest
```

Expected: `test_pgvector_orders_by_l2_distance PASSED`, `test_citext_unique_is_case_insensitive PASSED`.

- [ ] **Step 6: Commit**

```bash
git add tests/test_pg_integration.py tests/test_fix_source_pool.py requirements-dev.txt
git commit -m "test: pgvector/CITEXT 통합 테스트 추가 + fix_source_pool 마커 표준화

Claude-Session: https://claude.ai/code/session_01VKD79pbyWWL72X7VrmBdSS"
```

---

## Task 3: CI 부트스트랩 스크립트

**Files:**
- Create: `scripts/init_test_db.py`

**Interfaces:**
- Consumes: 환경변수 `DATABASE_URL`(pydantic-settings가 `settings.database_url`로 매핑 → `app.core.db.engine`이 사용).
- Produces: 실행 시 CI Postgres에 `vector`/`citext` 확장 + 전체 ORM 스키마 생성. `test_fix_source_pool.py`의 `LIKE posting`이 참조할 `posting` 테이블을 준비.

- [ ] **Step 1: 스크립트 작성**

```python
"""CI 테스트 DB 부트스트랩: 확장 설치 후 전체 ORM 스키마 생성.

DATABASE_URL 환경변수로 대상 Postgres를 지정한다(app.core.db.engine이 이를 사용).
빈 에페메럴 CI DB를 가정하며, 실 운영/개발 DB를 가리키면 안 된다.
"""

from sqlalchemy import text

import app.models  # noqa: F401 - Base에 모든 모델을 등록
from app.core.db import Base, engine


def main() -> None:
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS citext"))
    Base.metadata.create_all(engine)
    print("test db schema ready")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 로컬 도커로 부트스트랩 + 통합 전체 실행 확인**

```bash
docker run -d --rm --name pgtest -e POSTGRES_USER=postgres -e POSTGRES_PASSWORD=postgres \
  -e POSTGRES_DB=testdb -p 5433:5432 pgvector/pgvector:pg17
until docker exec pgtest pg_isready -U postgres; do sleep 1; done
export DATABASE_URL="postgresql+psycopg://postgres:postgres@localhost:5433/testdb"
python scripts/init_test_db.py
pytest -m integration -v --tb=short
docker stop pgtest; unset DATABASE_URL
```

Expected: `test db schema ready` 출력 후 `tests/test_pg_integration.py`(2개) + `tests/test_fix_source_pool.py`(3개) 전부 PASSED (skip 아님).

- [ ] **Step 3: Commit**

```bash
git add scripts/init_test_db.py
git commit -m "test: CI 테스트 DB 부트스트랩 스크립트(확장+스키마)

Claude-Session: https://claude.ai/code/session_01VKD79pbyWWL72X7VrmBdSS"
```

---

## Task 4: 재사용 테스트 워크플로우 (`test.yml`)

**Files:**
- Create: `.github/workflows/test.yml`

**Interfaces:**
- Produces: `on: workflow_call` 워크플로우. 잡: `lint`, `unit`(needs lint), `integration`(needs unit, pgvector 서비스), `docker-build`(needs unit).
- Consumes: Task 1~3 산출물(`pytest -m`, `scripts/init_test_db.py`).

- [ ] **Step 1: 워크플로우 작성**

```yaml
name: test

on:
  workflow_call:

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip
      - run: pip install -r requirements-dev.txt
      - run: ruff check .

  unit:
    needs: lint
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip
      - run: pip install -r requirements.txt -r requirements-dev.txt
      - name: Run fast tests (SQLite)
        run: pytest -m "not integration" -v --tb=short -ra --junitxml=junit-unit.xml

  integration:
    needs: unit
    runs-on: ubuntu-latest
    services:
      postgres:
        image: pgvector/pgvector:pg17
        env:
          POSTGRES_USER: postgres
          POSTGRES_PASSWORD: postgres
          POSTGRES_DB: testdb
        ports:
          - 5432:5432
        options: >-
          --health-cmd "pg_isready -U postgres"
          --health-interval 5s
          --health-timeout 3s
          --health-retries 10
    env:
      DATABASE_URL: postgresql+psycopg://postgres:postgres@localhost:5432/testdb
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip
      - run: pip install -r requirements.txt -r requirements-dev.txt
      - name: Bootstrap test DB (extensions + schema)
        run: python scripts/init_test_db.py
      - name: Run slow tests (real Postgres)
        run: pytest -m integration -v --tb=short -ra --junitxml=junit-integration.xml

  docker-build:
    needs: unit
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: docker build -t career-backend:ci .
```

주: 테스트 Postgres 크리덴셜(`postgres:postgres`, `DATABASE_URL`)은 에페메럴 컨테이너용 더미라 로그 노출이 허용된다. 실 시크릿은 이 워크플로우에서 참조하지 않는다.

- [ ] **Step 2: YAML 유효성 검증**

Run: `python -c "import yaml; yaml.safe_load(open('.github/workflows/test.yml')); print('ok')"`
Expected: `ok`

(선택) `actionlint` 설치돼 있으면: `actionlint .github/workflows/test.yml` → 경고 0.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/test.yml
git commit -m "ci: 재사용 테스트 워크플로우(lint/unit/integration/docker-build)

Claude-Session: https://claude.ai/code/session_01VKD79pbyWWL72X7VrmBdSS"
```

---

## Task 5: `ci.yml`을 재사용 워크플로우 호출로 축소

**Files:**
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: `.github/workflows/test.yml`(Task 4).

- [ ] **Step 1: `ci.yml` 전체 교체**

```yaml
name: ci

on:
  pull_request:
  push:
    branches-ignore: [main]

jobs:
  test:
    uses: ./.github/workflows/test.yml
```

- [ ] **Step 2: 브랜치에 푸시해 Actions 실행 관찰**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: ci.yml을 재사용 test 워크플로우 호출로 축소

Claude-Session: https://claude.ai/code/session_01VKD79pbyWWL72X7VrmBdSS"
git push
```

Expected: GitHub Actions에서 `ci / test / lint`, `unit`, `integration`, `docker-build` 잡이 순서대로(needs 반영) 실행. `unit`/`integration` 로그에 테스트별 `PASSED/FAILED` 라인이 보인다. `integration`이 실제로 5개 통합 테스트를 실행(skip 아님).

---

## Task 6: 배포 게이트 — `deploy.yml`에 test 통과 강제

**Files:**
- Modify: `.github/workflows/deploy.yml:17-19`

**Interfaces:**
- Consumes: `.github/workflows/test.yml`(Task 4).
- Produces: `deploy` 잡이 `test` 통과에 의존 → main 무검증 배포 차단.

- [ ] **Step 1: `deploy.yml`에 test 잡 추가 + deploy에 needs 부여**

`jobs:` 블록 최상단에 아래 `test` 잡을 추가:

```yaml
jobs:
  test:
    uses: ./.github/workflows/test.yml

  deploy:
    needs: test
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      # ... (기존 deploy 스텝 전부 그대로 유지) ...
```

즉 기존 `deploy:` 잡 정의에 `needs: test` 한 줄만 추가하고, 그 위에 `test:` 재사용 호출 잡을 신설한다. 기존 deploy 스텝(auth/gcloud/build/push/scp/ssh/notify)과 시크릿 참조는 **그대로 유지**한다.

- [ ] **Step 2: YAML 유효성 검증**

Run: `python -c "import yaml; yaml.safe_load(open('.github/workflows/deploy.yml')); print('ok')"`
Expected: `ok`

- [ ] **Step 3: 게이트 동작 검증 (일회성, 되돌림)**

브랜치에서 의도적으로 실패하는 테스트를 추가해 PR을 열고, `ci / test`가 빨강이 되는지 확인한다. (main 병합 전 되돌린다. deploy 잡은 main에서만 도므로, 게이트 로직은 `needs: test` 구조로 보장됨을 리뷰로 확인.)

Expected: 실패 테스트가 있으면 test 잡 빨강 → main 병합 시 deploy가 실행되지 않음(needs 미충족).

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/deploy.yml
git commit -m "ci: main 배포 전 test 통과 게이트(needs) 추가

Claude-Session: https://claude.ai/code/session_01VKD79pbyWWL72X7VrmBdSS"
```

---

## Task 7 (선택, P4): 캐시·취소·커버리지 튜닝

**Files:**
- Modify: `.github/workflows/test.yml`, `.github/workflows/ci.yml`

**Interfaces:** 기능 변화 없음(성능/가시성 개선).

- [ ] **Step 1: 중복 실행 취소(`concurrency`)를 `ci.yml`에 추가**

`ci.yml`의 `on:` 아래에:

```yaml
concurrency:
  group: ci-${{ github.ref }}
  cancel-in-progress: true
```

- [ ] **Step 2: docker buildx 레이어 캐시로 `docker-build` 가속**

`test.yml`의 `docker-build` 잡을 buildx + gha 캐시로 교체:

```yaml
  docker-build:
    needs: unit
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: docker/setup-buildx-action@v3
      - uses: docker/build-push-action@v6
        with:
          context: .
          push: false
          tags: career-backend:ci
          cache-from: type=gha
          cache-to: type=gha,mode=max
```

- [ ] **Step 3: (선택) JUnit 결과를 잡 요약에 게시**

`unit`/`integration` 잡 끝에 `if: always()`로 `mikepenz/action-junit-report@v5`를 추가해 `junit-*.xml`을 step summary로 노출. (외부 액션 도입이 부담되면 생략 — `-v` 로그로 이미 테스트별 결과 확인 가능.)

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/test.yml .github/workflows/ci.yml
git commit -m "ci: concurrency 취소 + buildx 캐시(+선택 junit 요약)

Claude-Session: https://claude.ai/code/session_01VKD79pbyWWL72X7VrmBdSS"
```

---

## Self-Review 결과

**Spec coverage:**
- 배포 안전성 → Task 6. 실 통합 테스트 → Task 2·3. 속도/피드백(캐시·단계형) → Task 4·7. 구조(마커/conftest) → Task 1. 로그 가시성 → Task 4(`-v`) + Task 2(annotate 플러그인). 시크릿 안전 → Global Constraints + Task 4 주석. 모든 스펙 요구가 태스크에 매핑됨.
- 스펙 §6-7의 "load_mart 실 DB 통합"은 실 DB `wipe` 위험 때문에 **pgvector/CITEXT 격리 테스트(Task 2)로 대체** — SQLite 사각지대를 더 정확·안전하게 검증. (설계 개선, 스펙 목표와 일치)

**Placeholder scan:** TBD/TODO/"적절히 처리" 없음. YAML·파이썬 전부 실행 가능한 완성 코드.

**Type consistency:** `pg_conn` 픽스처(Task 1) ↔ `test_pg_integration.py`/`test_fix_source_pool.py` 사용 일치. `DATABASE_URL` 환경변수 ↔ `scripts/init_test_db.py`의 `app.core.db.engine`(settings 매핑) 일치. `pytest -m integration` 선택자 ↔ `@pytest.mark.integration` 마커 일치. 워크플로우 잡 `needs:` 그래프 일관.
