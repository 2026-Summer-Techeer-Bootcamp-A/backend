# 백엔드 레포
FastAPI + PostgreSQL + Redis + 모니터링(Grafana 등).

- `GET /healthz` : 헬스체크
- `GET /metrics` : Prometheus 지표

## 로컬

`docker compose` 하나에 백이랑 db랑 모니터링이랑 다 묶어둠
```bash         
docker compose up -d --build  
```

이제 브라우저로 확인:

- http://localhost:8000 : 앱 (person 목록 JSON)
- http://localhost:3000 : Grafana (admin / `.env`의 비밀번호)
- http://localhost:9090 : Prometheus

정리: `docker compose down`

- `.env.example`의 `COMPOSE_FILE` 덕분에 `-f` 없이 두 compose 파일이 자동으로 같이 뜬다.

## GCP

서버에서는 DB/Redis를 컨테이너로 띄우지 않고 Cloud SQL, Memorystore에 붙는다.
`main` 브랜치에 push하면 아래가 자동으로 돈다 (`.github/workflows/deploy.yml`):

1. 도커 이미지 빌드 → Artifact Registry에 push
2. VM에 접속 → `docker compose pull && docker compose up -d`

서버 최초 준비(한 번만):

```bash
# VM 안에서
curl -fsSL https://get.docker.com | sh          
mkdir -p ~/app && cd ~/app                       # docker-compose.yml + observability/ 복사
# .env 작성: COMPOSE_FILE 줄은 지우고, DATABASE_URL/REDIS_URL을 Cloud SQL/Memorystore 주소로,
# APP_IMAGE를 Artifact Registry 이미지 경로로 설정
python -m scripts.seed  # 테이블 + 데이터 최초 1회
```
