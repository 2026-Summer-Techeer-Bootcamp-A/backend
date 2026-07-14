# Global/Domestic Gap Materialized View Design

## 목표

`GET /api/v1/stats/global-domestic-gap`의 응답 계약과 계산 결과를 유지하면서, 요청마다 수행하던 전체 공고 집계를 PostgreSQL Materialized View 갱신 시점으로 옮긴다.

## 범위

- 전용 Materialized View `mv_global_domestic_gap`을 추가한다.
- 앱 시작 시 기존 MV 등록 방식과 동일하게 없으면 생성한다.
- collector 작업 완료 후 기존 MV들과 함께 갱신한다.
- `get_global_domestic_gap()`은 원본 `posting`, `posting_tech`, `skill` 테이블 대신 새 MV를 조회한다.
- API 경로, 쿼리 파라미터, 응답 필드명과 타입은 변경하지 않는다.
- `skill-trend-yearly`와 기존 MV 구현은 이번 변경 범위에 포함하지 않는다.

## 데이터 계약

MV는 기술별로 한 행을 저장하며 다음 컬럼을 제공한다.

- `skill_id`
- `canonical`
- `category`
- `global_n`
- `domestic_n`
- `global_pct`
- `domestic_pct`
- `diff`
- `global_total`
- `domestic_total`

기존 계산 의미를 그대로 유지한다.

- `global_total`, `domestic_total`은 각 pool의 삭제되지 않은 전체 공고 수다.
- 기술별 공고 수는 `COUNT(DISTINCT posting_id)`로 센다.
- 삭제된 `posting`, `posting_tech`, `skill`은 제외한다.
- 한 pool에만 있는 기술도 포함하고 반대쪽 값은 `0`, `0.0`으로 채운다.
- `global_pct`와 `domestic_pct`는 각 pool 전체 공고 수를 분모로 백분율을 계산해 소수점 둘째 자리로 반올림한다.
- `diff`는 기존 코드처럼 반올림된 `global_pct - domestic_pct`를 다시 소수점 둘째 자리로 반올림한다.

## 요청 흐름

앱 시작 시 `mv_global_domestic_gap`이 없으면 생성한다. collector 작업이 끝나면 `REFRESH MATERIALIZED VIEW mv_global_domestic_gap`을 실행하여 최신 원본 데이터를 반영한다.

API 요청 시 CRUD 함수는 MV에서 `diff DESC LIMIT :limit`으로 `global_favored`를, `diff ASC LIMIT :limit`으로 `domestic_favored`를 조회한다. `global_total`, `domestic_total`은 MV 행에서 읽어 기존 `sample_size` 응답을 구성한다. MV가 비어 있으면 두 목록과 두 모수를 모두 빈 값과 0으로 반환한다.

## 오류 및 호환성

- 기존 `limit` 검증 범위 `1..50`을 유지한다.
- 기존 `GlobalDomesticGapResponse`와 `PoolGapItem`은 변경하지 않는다.
- 기존 collector MV 갱신 오류 처리 방식을 그대로 따른다.
- 공용 파일에서는 기존 블록을 재배치하거나 수정하지 않고 담당 MV 블록만 끝에 추가한다.

## 테스트

- SQLite 테스트 DB에는 동일 컬럼의 `mv_global_domestic_gap` 일반 테이블을 생성해 CRUD 조회를 재현한다.
- 기존 테스트의 pool 분리와 공고 수 검증을 유지한다.
- 한쪽 pool에만 존재하는 기술이 반대쪽 0으로 반환되는지 검증한다.
- `limit`에 따라 `diff` 내림차순/오름차순 결과가 각각 제한되는지 검증한다.
- MV 기반 CRUD가 원본 데이터 변경 없이 시드된 MV 결과를 반환하는지 검증한다.
- 전체 자동화 테스트를 실행해 다른 팀원의 엔드포인트에 회귀가 없는지 확인한다.

## 제외 사항

- Redis 캐시 추가
- 기존 `mv_skill_share`, `mv_cooccurrence` 변경
- 응답 스키마 변경
- 기술직 필터 신규 적용
- `skill-trend-yearly` 구현
