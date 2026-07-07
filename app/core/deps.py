from typing import Annotated
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
import jwt
from jwt.exceptions import InvalidTokenError

from app.core.db import get_session
from app.core.security import ALGORITHM, SECRET_KEY
from app.core.redis import is_token_blocklisted
from app.models.user import User

# Swagger UI 및 자동 문서화에서 토큰을 입력받을 수 있도록 엔드포인트를 명시.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")

SessionDep = Annotated[Session, Depends(get_session)]
TokenDep = Annotated[str, Depends(oauth2_scheme)]

def get_current_user(session: SessionDep, token: TokenDep) -> User:
    # 401 에러 포맷을 미리 정의하여 검증 실패 시 즉각적으로 동일한 에러를 반환하도록 설계.
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    # 가장 먼저 Redis를 조회하여 이미 로그아웃된(블록된) 토큰인지 확인.
    if is_token_blocklisted(token):
        raise credentials_exception

    try:
        # 서명 검증을 통해 변조되지 않은 토큰인지 확인.
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")
        if user_id is None:
            raise credentials_exception
    except InvalidTokenError:
        raise credentials_exception
        
    # 토큰의 user_id가 실제 DB에 존재하는 유효한 회원인지 최종 확인.
    user = session.get(User, int(user_id))
    if user is None:
        raise credentials_exception
        
    return user

CurrentUser = Annotated[User, Depends(get_current_user)]
