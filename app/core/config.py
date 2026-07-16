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
    search_cache_ttl_seconds: int = 3 * 60 * 60
    search_cache_socket_timeout_seconds: float = 0.5

    # 참조성 데이터(스킬·직무 카테고리·자격증) 캐시 TTL.
    # 수집기가 돌 때만 바뀌므로 하루로 길게 잡는다.
    reference_cache_ttl_seconds: int = 24 * 60 * 60
    # 기술별 기업 목록은 posting과 더 밀접해 자주 바뀔 수 있어 6시간으로 짧게.
    company_by_skill_cache_ttl_seconds: int = 6 * 60 * 60
    # stats/*, 지도 등 posting 집계 통계도 수집 주기(스크래핑)에만 바뀌는 데이터라
    # company_by_skill과 같은 6시간으로 잡는다. 실시간성이 필요 없는데도 매 요청마다
    # DB를 다시 때리던 게 부하테스트에서 드러난 병목의 상당 부분이었다.
    stats_cache_ttl_seconds: int = 6 * 60 * 60
    # 참조 캐시도 성능 보조 기능이라 Redis 장애가 API를 오래 막지 않도록 짧은 타임아웃.
    reference_cache_socket_timeout_seconds: float = 0.5

    resume_parse_max_bytes: int = 10 * 1024 * 1024
    resume_confirm_session_ttl_seconds: int = 3600
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-3.5-flash"
    # 엄격한 면접관 시스템 프롬프트 + JSON 모드 조합은 실측 16~17초까지 걸려
    # 기존 10초 기본값으로는 항상 폴백으로 빠졌다. 여유를 두고 25초로 상향.
    gemini_timeout_seconds: float = 25.0
    # gemini-3.x는 thinking을 완전히 끌 수 없고 "minimal"이 최소값이다(2.5 계열의
    # thinkingBudget과는 다른 파라미터). RAG 파이프라인은 플래너+합성으로 LLM을
    # 순차 2회 호출하므로 thinking 토큰 절감이 지연 시간에 직접 반영된다.
    gemini_thinking_level: str = "minimal"
    gemini_max_output_tokens: int = 800

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

    # anyio 기본 스레드 리미터 토큰 수. 엔드포인트 대부분이 동기 def라 워커당 이
    # 리미터를 공유하는데, 기본값 40은 워커당 동시 처리 가능한 동기 요청 수의
    # 상한이다. Gemini 호출이 urllib 동기 호출로 최대 gemini_timeout_seconds(25초)까지
    # 스레드를 점유할 수 있어, 40으로는 부하 상황에서 스레드 차례를 기다리다
    # 다른 요청(예: /healthz)까지 줄줄이 막히는 head-of-line blocking이 실측됐다.
    thread_pool_size: int = 120

settings = Settings()
