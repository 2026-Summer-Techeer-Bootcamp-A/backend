from pydantic import BaseModel


class SkillResponse(BaseModel):
    canonical: str
    category: str
    aliases: list[str]


class SkillListResponse(BaseModel):
    skills: list[SkillResponse]

