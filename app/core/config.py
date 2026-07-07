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

    # TODO: consumed by the future OTel trace exporter configuration.
    otel_exporter_otlp_endpoint: str = "http://tempo:4317"
    otel_service_name: str = "career-backend"


settings = Settings()
