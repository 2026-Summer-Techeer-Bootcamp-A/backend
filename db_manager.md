# DB Manager TUI 실행 가이드

`db_manager.py`는 터미널 안에서 데이터베이스의 테이블과 데이터를 시각적으로 확인하고 초기화할 수 있는 인터랙티브 TUI(Text User Interface) 툴입니다.

## 1. 사전 준비 (의존성 설치)

이 툴은 터미널을 꾸미고 방향키 조작을 지원하기 위해 `rich`와 `questionary` 라이브러리를 사용합니다.
해당 패키지는 `requirements-dev.txt`에 포함되어 있습니다. 실행 환경(가상환경 또는 컨테이너)에서 설치를 진행해 주세요.

```bash
# 백엔드 루트 폴더(backend/)에서
pip install -r requirements-dev.txt
```

## 2. 스크립트 실행 방법

`db_manager.py` 파일이 백엔드 루트 폴더(`backend/`)에 위치해 있으므로, `.env` 파일의 설정을 자동으로 읽어옵니다. 추가 환경변수 없이 바로 실행할 수 있습니다.

### 🐳 Docker 컨테이너 내부에서 실행할 때 (권장)
이미 컨테이너 내부 쉘에 접속해 있다면 `.env`의 `db` 주소를 그대로 사용하여 완벽하게 동작합니다.

```bash
# 1. 백엔드 컨테이너 쉘 진입
docker compose exec app sh

# 2. 툴 실행
python db_manager.py
```

### 🎯 로컬 호스트(터미널)에서 직접 실행할 때
> **주의**: 로컬 환경에서 직접 실행하려면, `.env` 파일 안의 `DATABASE_URL` 주소가 `db`가 아닌 `localhost`로 변경되어 있어야 정상적으로 접속할 수 있습니다. (예: `DATABASE_URL=postgresql+psycopg://appuser:change-me@localhost:5432/appdb`)

```bash
python db_manager.py
```

## 3. 주요 기능 안내

스크립트를 실행하면 화면에 메뉴가 나타납니다. **키보드의 방향키(↑, ↓)로 메뉴를 이동하고 Enter(엔터) 키를 눌러 선택**하세요.

- 📊 **테이블 목록 보기**: 현재 존재하는 테이블 이름과 레코드 개수를 표 형식으로 보여줍니다.
- 🔍 **테이블 데이터 조회**: 테이블을 선택하면 가독성 높은 색상이 입혀진 표(Table)로 데이터를 조회할 수 있습니다. 긴 텍스트는 보기 좋게 잘려서(Truncated) 출력됩니다.
- 🧹 **DB 초기화 (TRUNCATE)**: 테이블 구조는 그대로 둔 채 모든 데이터만 비웁니다.
- 🗑️ **모든 테이블 삭제 (DROP)**: 스키마를 포함한 데이터베이스의 모든 테이블 구조를 파괴합니다.
- 🚪 **종료**: 툴을 종료하고 일반 쉘로 돌아갑니다.
