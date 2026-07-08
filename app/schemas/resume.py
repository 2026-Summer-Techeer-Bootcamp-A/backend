from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ParsedSkill(BaseModel):
    canonical: str
    category: str
    in_dict: bool


class ResumeParseResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    skills: list[ParsedSkill]
    position: str | None = None
    career_min: int | None = None
    career_max: int | None = None


class ResumeConfirmRequest(BaseModel):
    skills: list[ParsedSkill] = Field(min_length=1)
    position: str | None = None
    career_min: int | None = Field(default=None, ge=0)
    career_max: int | None = Field(default=None, ge=0)
    pool: Literal["global", "domestic"]

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
