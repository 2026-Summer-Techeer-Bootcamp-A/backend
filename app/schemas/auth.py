from pydantic import BaseModel, ConfigDict, EmailStr, Field


class UserSignup(BaseModel):
    email: EmailStr
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
    access_token: str
    token_type: str = "bearer"
