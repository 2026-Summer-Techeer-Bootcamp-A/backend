from sqlalchemy.orm import Session
from fastapi import HTTPException, status
from app.crud import user as crud_user
from app.schemas.auth import UserSignup, UserLogin, UserResponse, Token
from app.core.security import get_password_hash, verify_password, create_access_token
from app.core.redis import add_token_to_blocklist
from app.core.config import settings

def signup(session: Session, user_in: UserSignup) -> UserResponse:
    # 이메일 중복 시 409 Conflict 에러를 반환하여 클라이언트가 알맞게 대처할 수 있도록 처리.
    user = crud_user.get_user_by_email(session, email=user_in.email)
    if user:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )
    # 평문 비밀번호가 DB에 저장되지 않도록 단방향 해싱 후 저장.
    hashed_password = get_password_hash(user_in.password)
    new_user = crud_user.create_user(
        session=session,
        email=user_in.email,
        password_hash=hashed_password,
        nickname=user_in.nickname
    )
    # ORM 객체를 Pydantic 모델로 변환하여 응답 포맷(비밀번호 제외)을 강제.
    return UserResponse.model_validate(new_user)

def login(session: Session, user_in: UserLogin) -> Token:
    # 이메일 존재 여부와 비밀번호 일치 여부를 한 번에 검증.
    # 보안상 어느 것이 틀렸는지 명확히 알려주지 않는 것이 권장됨.
    user = crud_user.get_user_by_email(session, email=user_in.email)
    if not user or not verify_password(user_in.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # 식별자(user.id)를 기반으로 JWT 생성 후 반환.
    access_token = create_access_token(subject=user.id)
    return Token(access_token=access_token, token_type="bearer")

def logout(token: str) -> None:
    # JWT의 상태 비저장(Stateless) 한계를 극복하기 위해, 만료 시간(7일)만큼만 Redis에 기록.
    add_token_to_blocklist(token, expires_in_seconds=60 * 24 * 7 * 60)
