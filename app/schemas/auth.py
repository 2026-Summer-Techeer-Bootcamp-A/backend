from pydantic import BaseModel, ConfigDict, EmailStr, Field


class UserSignup(BaseModel):
    # EmailStr을 사용하여 이메일 형식 검증을 자동으로 수행.
    email: EmailStr
    # 패스워드의 최소 길이를 6자리로 강제하여 기초 보안 확보.
    password: str = Field(..., min_length=6)
    nickname: str | None = None


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    nickname: str | None = None


class Token(BaseModel):
    # JWT 클라이언트는 'bearer' 타입을 기대하므로 기본값으로 설정.
    access_token: str
    token_type: str = "bearer"
