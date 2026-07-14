"""CI 테스트 DB 부트스트랩: 확장 설치 후 전체 ORM 스키마 생성.

DATABASE_URL 환경변수로 대상 Postgres를 지정한다(app.core.db.engine이 이를 사용).
빈 에페메럴 CI DB를 가정하며, 실 운영/개발 DB를 가리키면 안 된다.
"""

from sqlalchemy import text

import app.models  # noqa: F401 - Base에 모든 모델을 등록
from app.core.db import Base, engine


def main() -> None:
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS citext"))
    Base.metadata.create_all(engine)
    print("test db schema ready")


if __name__ == "__main__":
    main()
