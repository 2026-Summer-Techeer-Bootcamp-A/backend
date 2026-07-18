from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ParsedSkill(BaseModel):
    canonical: str
    category: str
    in_dict: bool


class ParsedCert(BaseModel):
    name: str
    in_dict: bool


class ResumeParseResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    skills: list[ParsedSkill]
    certs: list[ParsedCert] = Field(default_factory=list)
    position: str | None = None
    career_min: int | None = None
    career_max: int | None = None
    # 이력서 원문. 커리어 적합도 LLM 판정이 확인 세션 payload에 실어 쓰기 위한
    # 값으로, Postgres에는 저장하지 않는다(Global Constraints 참고).
    resume_text: str | None = None


class ResumeConfirmRequest(BaseModel):
    skills: list[ParsedSkill] = Field(min_length=1)
    certs: list[ParsedCert] = Field(default_factory=list)
    position: str | None = None
    career_min: int | None = Field(default=None, ge=0)
    career_max: int | None = Field(default=None, ge=0)
    pool: Literal["global", "domestic"]
    memo: str | None = Field(default=None, max_length=4000)
    # /resume/parse 응답의 resume_text를 그대로 되돌려주는 값. 확인 세션 payload에
    # 그대로 실려 Redis에만 TTL 범위로 저장되고 DB에는 저장되지 않는다.
    resume_text: str | None = Field(default=None, max_length=20000)

    @model_validator(mode="after")
    def validate_career_range(self) -> "ResumeConfirmRequest":
        if (
            self.career_min is not None
            and self.career_max is not None
            and self.career_min > self.career_max
        ):
            raise ValueError("career_min must be less than or equal to career_max")
        return self


class ResumeConfirmResponse(BaseModel):
    session_id: str
    ttl: int


class ResumeFeedbackRequest(BaseModel):
    session_id: str = Field(min_length=1)
    position: str = Field(min_length=1)


class ResumeFeedbackResponse(BaseModel):
    feedback: list[str]
    questions: list[str]
    model: str
    degraded: bool


class ResumeCreateRequest(BaseModel):
    title: str = Field(min_length=1)
    skills: list[ParsedSkill] = Field(min_length=1)
    certs: list[ParsedCert] = Field(default_factory=list)
    position: str = Field(min_length=1)
    career_min: int = Field(ge=0)
    career_max: int = Field(ge=0)
    pool: Literal["global", "domestic"]
    memo: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def validate_career_range(self) -> "ResumeCreateRequest":
        if self.career_min > self.career_max:
            raise ValueError("career_min must be less than or equal to career_max")
        return self


class ResumeCreateResponse(BaseModel):
    resume_id: int


class ResumeUpdateRequest(ResumeCreateRequest):
    pass


class ResumeUpdateResponse(BaseModel):
    resume_id: int


class ResumeListItem(BaseModel):
    resume_id: int
    title: str
    position: str | None
    is_primary: bool


class ResumeListResponse(BaseModel):
    items: list[ResumeListItem]


class ResumeDetailResponse(BaseModel):
    resume_id: int
    title: str
    skills: list[ParsedSkill]
    certs: list[ParsedCert]
    position: str
    career_min: int
    career_max: int
    pool: Literal["global", "domestic"]
    memo: str | None
    is_primary: bool
