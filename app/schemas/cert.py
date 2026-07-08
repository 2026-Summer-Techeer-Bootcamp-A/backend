from pydantic import BaseModel


class CertItem(BaseModel):
    name: str


class CertListResponse(BaseModel):
    certs: list[CertItem]


class CertRequirementItem(BaseModel):
    name: str
    share: float
    posting_count: int


class CertGapItem(BaseModel):
    name: str
    share: float


class CertGapResponse(BaseModel):
    required: list[CertRequirementItem]
    owned: list[CertItem]
    gap: list[CertGapItem]
    as_of: str
    sample_size: int
