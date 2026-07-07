from sqlalchemy.orm import Session
from app.models.user import User

def get_user_by_email(session: Session, email: str) -> User | None:
    # 이메일로 단건 조회를 수행하며, 결과가 없으면 None을 반환.
    return session.query(User).filter(User.email == email).first()

def create_user(session: Session, email: str, password_hash: str, nickname: str | None = None) -> User:
    # 새로 생성된 DB 세션 객체를 메모리에 만들고, 트랜잭션에 추가한 뒤 반영.
    db_user = User(
        email=email,
        password_hash=password_hash,
        nickname=nickname
    )
    session.add(db_user)
    session.commit()
    # DB에 삽입되며 생성된 id 등의 값을 객체에 새로고침(동기화).
    session.refresh(db_user)
    return db_user
