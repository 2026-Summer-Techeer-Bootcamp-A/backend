from pydantic import BaseModel, ConfigDict


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
