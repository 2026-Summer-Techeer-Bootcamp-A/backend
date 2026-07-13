# 홈 피드 필터/정렬 수정 — Phase 1 (백엔드: 데이터 + API)

## 배경

홈 피드(국내/해외 탭 + 직무 칩 + 상세 필터)에서 두 가지가 보고됨:
1. 국내 탭인데도 영어 직무 칩(Content Creator, Customer Service 등)이 뜬다.
2. 상세 필터가 제대로 안 먹는 것 같다.

추가로 "최신순/매칭순 정렬 + 지역/기업종류/기술스택 등 필터를 DB가 허용하는 선에서 풍부하게" 요청.

## 조사 결과 (근본 원인)

- `job_category` 테이블은 pool 구분이 없는 단일 통제 어휘. `GET /job-categories`도 pool 파라미터가 없어 항상 전체 어휘를 반환. 프론트(`DesktopHome.tsx`)는 이걸 마운트 시 한 번만 불러와 pool과 무관하게 고정 노출 — 이게 버그 1의 원인.
- **직무 태깅(`posting_category`) 자체가 6개 소스 중 himalayas(56만 건 중 10.7만 건, 19%)에만 존재.** jumpit/wanted/jobkorea/wwr/rocketpunch는 0건. 국내 공고는 애초에 직무로 필터링할 데이터가 없었음.
- **jobkorea(39.6만 건, 실질적 국내 최대 소스)가 DB에 `pool='global'`로 잘못 적재돼 있음.** `scripts/load_mart.py`의 `DOMESTIC_SOURCES`엔 이미 jobkorea가 추가돼 있으나(작업 트리에 미커밋 상태) 실제 DB 재적재는 안 됨.
- "상세 필터"(지역/마감임박/최소매치율) 자체의 배선은 코드 추적 결과 정상 — state→API 파라미터 흐름에 끊긴 곳 없음. 다만 `GET /postings`로 가는 별개의 "기술스택" 필터(`/jobs` 검색 페이지)는 프론트가 `skills=` 파라미터를 보내는데 **백엔드 라우터에 해당 파라미터가 아예 선언돼 있지 않아 FastAPI가 조용히 무시**함 — 이게 "필터 안 먹힘"의 실체 중 하나.
- 홈 피드가 실제로 쓰는 `GET /feed/postings`엔 `sort` 파라미터 자체가 없음(항상 최신순 고정). `GET /postings`에만 최근(미커밋) `sort=match`가 추가됨.
- "기업 종류"에 대응하는 컬럼은 DB에 없음(`company`는 자유 텍스트). `industry`(업종) 필드는 있지만 커버리지 3.9%(56.5만 건 중 2.2만 건만 값 있음, 나머지는 "미분류") — 필터로 제공하되 데이터가 희소하다는 전제로 설계.
- `district`(지역) 필터는 이미 있고 커버리지 좋음(국내 93%). `skills`(기술스택) M:N 매핑도 이미 탄탄함(국내 공고당 평균 4.8개 태그).

## raw 데이터 감사(도메스틱 직무 카테고리화 가능성)

`data-collector-script/out/**/*.jsonl.gz` 원본을 직접 열어 확인:

- **jumpit**: `jobCategories: [{name: "서버/백엔드 개발자", ...}, ...]` — 21개 한국어 기술직군 필드가 그대로 있음. 추출만 하면 됨(신뢰도 높음).
- **jobkorea**: `categories: "화학·에너지·환경, 제품영업, 해외영업, ..., 잡코리아"` — 쉼표로 나열된 태그 문자열. 앞쪽이 대분류, 뒤쪽엔 "채용/구인/공고/입사 지원/잡코리아" 같은 사이트 노이즈 태그가 섞임. 노이즈 제거 + 앞부분 태그 추출로 규칙 기반 파싱 가능.
- **wanted**: `category_tags: [{"parent_id": 518, "id": 876}]` — 숫자 ID만 있고 이름이 없음. Wanted의 공개 ID→이름 매핑표가 이 저장소엔 없어 이번 스코프에선 **제외**(국내 공고의 5,819/47,065=12%뿐이라 영향 작음, 후속 과제로 남김).

## 설계

### 1. jobkorea pool 재분류 (데이터 수정, 신규 스크립트)

`scripts/load_mart.py`는 전체 TRUNCATE 후 mart.db에서 재적재하는 방식이라 blast radius가 너무 큼(mart.db 자체도 다른 저장소의 build_mart.py로 재빌드해야 함). 대신 이미 검증된 `scripts/enrich_postings.py` 패턴(원본 필드를 직접 DB에 반영하는 targeted UPDATE)을 재사용.

`scripts/fix_source_pool.py` 신규 작성:
```
UPDATE posting SET pool = 'domestic', region_country = 'KR'
WHERE source = 'jobkorea' AND (pool != 'domestic' OR region_country IS DISTINCT FROM 'KR');
```
`DATABASE_URL` 하나로 로컬/어디서든 실행 가능한 단발 스크립트(멱등적 — 여러 번 실행해도 안전).

이미 작업 트리에 있는 `scripts/load_mart.py`의 `DOMESTIC_SOURCES` 수정 + 관련 테스트(`test_load_mart_helpers.py`)는 그대로 커밋(다음번 mart 재적재 시에도 일관성 유지 목적).

### 2. 국내 직무 카테고리 백필 (신규 스크립트)

`scripts/enrich_categories.py` — `enrich_postings.py`와 동일한 emit/apply 2단계 구조.

- `emit`: jumpit(`jobCategories[].name`), jobkorea(`categories` 파싱 — 노이즈 토큰 제거 후 첫 1~2개 태그) 각각에서 `(source, source_uid, categories: list[str])` 추출 → ndjson.gz.
- `apply`: DATABASE_URL 대상으로
  1. 추출된 카테고리명 중 `job_category`에 없는 것들을 `INSERT ... ON CONFLICT (name) DO NOTHING`으로 새로 등록(jumpit 유래는 `is_tech=True`, jobkorea 유래는 `is_tech=False` — 업종 전반이라).
  2. `(source, source_uid)` → `posting.id` 매핑 후 `posting_category`에 `INSERT ... ON CONFLICT (posting_id, category) DO NOTHING`.

wanted는 제외(위 근본 원인 참고). 커버리지 부족은 정직하게 남겨둠 — 억지로 채우지 않음.

### 3. `GET /job-categories`에 pool 스코프 추가

- `list_job_categories(session, pool: str | None = None)`: `pool`이 주어지면 `JobCategory`를 `PostingCategory.category == JobCategory.name`으로 조인하고, `PostingCategory.posting_id`가 속한 `Posting.pool == pool`인 것만(DISTINCT) 반환. `pool` 없으면 기존 동작(전체 어휘) 유지 — 다른 소비처(이력서 직무 선택 등) 하위호환.
- 라우터에 `pool: Pool | None = None` 쿼리 파라미터 추가.

### 4. `GET /postings`에 `skills` 파라미터 추가

- `skills: str | None`(콤마 구분 canonical 이름, 프론트가 이미 이 포맷으로 보내고 있음).
- 시맨틱: **OR(하나라도 겹치면 포함)**. 다건 선택 시 결과가 급격히 줄어드는 AND보다, 후보를 넓게 보여주는 일반적인 채용 플랫폼 필터 UX에 더 맞음.
- `_apply_posting_filters` 공유 헬퍼에 추가 → `/postings`와 `/feed/postings` 양쪽에 자동 적용.

### 5. `GET /feed/postings`에 `sort`, `industry` 파라미터 추가

- `sort: Literal["latest", "match"] = "latest"`. `match`는 로그인 + 이력서 컨텍스트 있을 때만 유효 — 없으면 422 대신 latest로 안전 폴백(`/postings`의 `sort=match` 폴백 규칙과 동일하게).
- `industry: str | None` — `Posting.industry` 부분 일치(ilike). 커버리지 낮음을 알고 있는 채로 제공(프론트에서 "데이터 없는 값은 필터에서 숨김" 식으로 다뤄야 함 — Phase 2 몫).
- 구현은 `list_posting_cards`의 `sort == "match"` 처리(matched_count 기준 안정 정렬)와 동일한 패턴을 `list_feed_postings`에 이식.

## 테스트 계획

- `test_load_mart_helpers.py`: 기존 미커밋 diff 그대로(derive_pool 국내 소스에 jobkorea 포함 검증).
- `scripts/fix_source_pool.py`: 유닛 테스트로 UPDATE 대상 WHERE절 로직 검증(실 DB 연결 없이 SQL 텍스트/파라미터 검증) + 통합 테스트 1개(테스트 DB에 잘못된 pool row 심고 실행 후 재확인).
- `scripts/enrich_categories.py`: `from_jumpit`/`from_jobkorea` 파서 함수 단위 테스트(고정 샘플 딕셔너리 입력 → 기대 카테고리 리스트). apply 단계는 enrich_postings.py의 기존 테스트 패턴(있다면) 참고.
- `test_postings_filters.py`: 기존 미커밋 diff(sort=match) 유지 + `skills` 파라미터 필터링 테스트, `job-categories?pool=` 스코프 테스트, `feed/postings?sort=match`/`industry=` 테스트 추가.

## 스코프 밖 (Phase 2, 프론트엔드)

- `DesktopHome.tsx`가 pool 변경 시 `/job-categories?pool=`를 재조회하도록 수정.
- 홈 피드에 정렬 드롭다운(최신순/매칭순), 지역/업종/기술스택 필터 UI 추가 — 비주얼 설계 필요(Opus 서브에이전트).
- `/jobs` 검색 페이지가 보내는 `q`(자유 텍스트 검색) 파라미터도 백엔드 미지원 상태로 확인됨 — Phase 2에서 백엔드 검색 지원 여부 별도 논의.
- wanted 국내 소스 직무 카테고리화(공개 카테고리 ID 매핑표 확보 필요).
