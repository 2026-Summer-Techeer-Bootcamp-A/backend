from datetime import datetime, timedelta, timezone
import bcrypt
import jwt

# 개발용 임시 시크릿 키. 실제 운영 환경에서는 환경 변수(settings)에서 가져와야 함.
SECRET_KEY = "dummy-secret-key-for-dev"
ALGORITHM = "HS256"
# 토큰 탈취 위험을 최소화하기 위해 만료 시간을 7일로 제한.
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7

def verify_password(plain_password: str, hashed_password: str) -> bool:
    # 입력된 평문 비밀번호가 해시된 비밀번호와 일치하는지 단방향 검증.
    # passlib가 최신 bcrypt와 호환성 문제가 있어 bcrypt 모듈을 직접 사용합니다.
    return bcrypt.checkpw(plain_password.encode('utf-8'), hashed_password.encode('utf-8'))

def get_password_hash(password: str) -> str:
    # DB에 비밀번호를 평문으로 저장하지 않기 위해 bcrypt로 단방향 해시 생성.
    # 비밀번호 길이는 bcrypt 제한(72바이트)을 안전하게 처리하기 위해 필요시 자를 수 있습니다.
    password_bytes = password.encode('utf-8')
    if len(password_bytes) > 72:
        password_bytes = password_bytes[:72]
    return bcrypt.hashpw(password_bytes, bcrypt.gensalt()).decode('utf-8')

def create_access_token(subject: str | int, expires_delta: timedelta | None = None) -> str:
    # 토큰의 유효 기간(exp)을 명시적으로 설정하여 만료된 토큰이 사용되는 것을 방지.
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    
    # sub(Subject) 클레임에 고유 식별자인 user_id를 문자열 형태로 담아 발행.
    to_encode = {"exp": expire, "sub": str(subject)}
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt
