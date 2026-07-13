from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_port: int = 8000
    log_level: str = "info"

    # 브라우저에서 API를 호출하는 프론트엔드 오리진 화이트리스트.
    # 로컬 개발 서버 + 프로덕션 Vercel 도메인을 기본값으로 둔다.
    cors_origins: list[str] = [
        "http://localhost:3000",
        "http://localhost:5173",
        "https://frontend-tan-chi-25.vercel.app",
    ]
    # Vercel 프리뷰 배포는 서브도메인이 매번 바뀌므로, 해당 프로젝트의
    # 프리뷰 URL만 정규식으로 허용한다. (와일드카드로 전체 vercel.app를 열지 않음)
    cors_origin_regex: str = r"https://frontend-tan-chi-25-[a-z0-9-]+\.vercel\.app"

    # TODO: consumed by the future DB connection layer (SQLAlchemy engine / session).
    database_url: str = "postgresql+psycopg://appuser:change-me@db:5432/appdb_load"

    # TODO: consumed by the future Redis client setup.
    redis_url: str = "redis://redis:6379/0"

    resume_parse_max_bytes: int = 10 * 1024 * 1024
    resume_confirm_session_ttl_seconds: int = 3600
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-2.5-flash"
    # 엄격한 면접관 시스템 프롬프트 + JSON 모드 조합은 실측 16~17초까지 걸려
    # 기존 10초 기본값으로는 항상 폴백으로 빠졌다. 여유를 두고 25초로 상향.
    gemini_timeout_seconds: float = 25.0

    # 임베딩 모델 = 로컬 BGE-M3(출력 1024차원). pgvector 컬럼 차원과 반드시 일치해야 함.
    # (구값 1536은 OpenAI ada-002 기준 잔재였음 — BGE-M3로 확정하며 1024로 정정.)
    embedding_model: str = "BAAI/bge-m3"
    embedding_dim: int = 1024

    # RAG vector_tool 활성화 플래그. 기본 off.
    # 쿼리 임베딩(BGE-M3 CPU 추론)은 RAM 2~3GB를 쓰므로, 메모리 여유가 있는 인스턴스에서만 켠다.
    # off면 vector_tool이 None을 반환해 라우터가 sql/graph로 폴백한다(정직성 유지).
    enable_vector_search: bool = False

    # TODO: consumed by the future OTel trace exporter configuration.
    otel_exporter_otlp_endpoint: str = "http://tempo:4317"
    otel_service_name: str = "career-backend"


settings = Settings()
