from typing import Literal

from pydantic import BaseModel, Field, field_validator

Level = Literal["intern", "junior", "mid", "senior", "lead", "director"]
JobSearchStatus = Literal["active", "casual", "none"]
CompanyStage = Literal["hide", "show", "boost"]

_COMPANY_STAGE_KEYS = ("대기업", "중견", "중소")


class LocationPrefs(BaseModel):
    remote: bool = False
    onsite: bool = False
    regions: list[str] = Field(default_factory=list)


class ResumePreferences(BaseModel):
    """이력서 선호도. companyStagePrefs 키는 대기업/중견/중소로 고정한다."""

    level: Level | None = None
    jobSearchStatus: JobSearchStatus | None = None
    companyStagePrefs: dict[str, CompanyStage] = Field(default_factory=dict)
    sectorInterests: list[str] = Field(default_factory=list)
    location: LocationPrefs = Field(default_factory=LocationPrefs)

    @field_validator("companyStagePrefs")
    @classmethod
    def validate_company_stage_prefs(cls, value: dict[str, str]) -> dict[str, str]:
        invalid_keys = set(value) - set(_COMPANY_STAGE_KEYS)
        if invalid_keys:
            raise ValueError(f"invalid companyStagePrefs keys: {sorted(invalid_keys)}")
        return value
