from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_port: int = 8000
    log_level: str = "info"

    # TODO: consumed by the future DB connection layer (SQLAlchemy engine / session).
    database_url: str = "postgresql+psycopg://appuser:change-me@db:5432/appdb"

    # TODO: consumed by the future Redis client setup.
    redis_url: str = "redis://redis:6379/0"

    resume_parse_max_bytes: int = 10 * 1024 * 1024
    resume_confirm_session_ttl_seconds: int = 3600
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-3.5-flash"
    gemini_timeout_seconds: float = 10.0

    # 임베딩 모델 = 로컬 BGE-M3(출력 1024차원). pgvector 컬럼 차원과 반드시 일치해야 함.
    # (구값 1536은 OpenAI ada-002 기준 잔재였음 — BGE-M3로 확정하며 1024로 정정.)
    embedding_model: str = "BAAI/bge-m3"
    embedding_dim: int = 1024

    # TODO: consumed by the future OTel trace exporter configuration.
    otel_exporter_otlp_endpoint: str = "http://tempo:4317"
    otel_service_name: str = "career-backend"


settings = Settings()
