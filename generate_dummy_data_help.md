# 더미 데이터 생성 스크립트 실행 가이드

`generate_dummy_data.py`는 로컬 개발 환경의 데이터베이스에 시계열 공고 데이터, 회원, 이력서, 기술 및 자격증 매핑 데이터 등 방대한 양의 정교한 더미 데이터를 적재하기 위한 스크립트입니다.

## 1. 사전 준비 (의존성 설치)

스크립트는 랜덤 데이터 생성을 위해 `Faker`와 암호 해싱을 위해 `bcrypt` 패키지를 사용합니다. 실행 환경(가상환경 또는 컨테이너 내부)에 해당 패키지들이 설치되어 있는지 확인하세요.

```bash
pip install faker bcrypt
```

## 2. 스크립트 실행 방법

스크립트가 백엔드 루트 폴더(`backend/`)에 위치해 있으므로, `python-dotenv`(pydantic-settings)가 루트에 있는 `.env` 파일을 자동으로 읽어옵니다! 추가적인 환경변수 입력 없이 바로 스크립트를 실행하시면 됩니다.

### 로컬 호스트(터미널)에서 직접 실행할 때
(포트 5432가 호스트 머신에 열려 있어야 합니다.)

> **주의**: 로컬에서 직접 실행할 경우 `.env` 파일 안의 `DATABASE_URL` 주소가 `db`가 아닌 `localhost`를 가리켜야 정상적으로 DB에 접속할 수 있습니다. (예: `DATABASE_URL=postgresql+psycopg://appuser:change-me@localhost:5432/appdb`)

```bash
# 백엔드 루트 폴더(backend/)에서
python generate_dummy_data.py
```

### Docker 컨테이너 내부에서 실행할 때
이미 컨테이너 내부 쉘에 접속해 있다면, 기본적으로 필요한 환경변수와 네트워크 셋업이 완료되어 있으므로 바로 실행할 수 있습니다. `.env` 파일의 `db` 주소도 완벽하게 동작합니다.

```bash
# 백엔드 컨테이너 쉘 진입
docker compose exec app sh

# 패키지 설치 확인 (안되어 있다면 설치)
pip install -r requirements-dev.txt

# 스크립트 실행
python generate_dummy_data.py
```

## 3. 실행 확인

실행이 완료되면 아래와 같이 진행 상황이 출력됩니다:
```text
1. Inserting basic dictionaries (Skill, JobCategory, Cert)...
2. Generating Users and Resumes...
3. Adding Skills and Certs to Resumes...
4. Generating Postings (Current & Historical)...
5. Mapping Tech, Certs, Categories, and Raw to Postings...
Successfully generated 20 users, 35 resumes, and 1800 postings.
```

정상적으로 완료된 후, 데이터베이스에 정상적으로 더미 데이터들이 들어갔는지 확인하시면 됩니다.
