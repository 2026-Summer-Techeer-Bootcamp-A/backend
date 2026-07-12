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
    database_url: str = "postgresql+psycopg://appuser:change-me@db:5432/appdb"

    # TODO: consumed by the future Redis client setup.
    redis_url: str = "redis://redis:6379/0"

    resume_parse_max_bytes: int = 10 * 1024 * 1024
    resume_confirm_session_ttl_seconds: int = 3600
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-3.5-flash"
    gemini_timeout_seconds: float = 10.0

    # TODO: consumed by the future OTel trace exporter configuration.
    otel_exporter_otlp_endpoint: str = "http://tempo:4317"
    otel_service_name: str = "career-backend"


settings = Settings()
