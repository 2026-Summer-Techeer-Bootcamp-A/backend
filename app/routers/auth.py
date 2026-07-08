from fastapi import APIRouter, status

from app.core.deps import SessionDep, CurrentUser, TokenDep
from app.schemas.auth import UserSignup, UserLogin, UserResponse, Token
from app.services import auth as auth_service

router = APIRouter()

@router.post("/signup", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def signup(user_in: UserSignup, session: SessionDep):
    # 비즈니스 로직(service)을 호출하여 컨트롤러(router)를 가볍게 유지.
    return auth_service.signup(session, user_in)

@router.post("/login", response_model=Token, status_code=status.HTTP_200_OK)
def login(user_in: UserLogin, session: SessionDep):
    # 비밀번호 검증 후 JWT 토큰을 발급하여 반환.
    return auth_service.login(session, user_in)

@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(token: TokenDep, current_user: CurrentUser):
    # 클라이언트의 토큰을 서버 측(Redis)에서 무효화하여 즉시 로그아웃 처리.
    auth_service.logout(token)

@router.get("/me", response_model=UserResponse, status_code=status.HTTP_200_OK)
def read_users_me(current_user: CurrentUser):
    # 의존성 주입(Depends) 단계에서 이미 토큰 검증 및 DB 조회가 끝나므로, 여기서는 객체만 반환.
    return current_user
