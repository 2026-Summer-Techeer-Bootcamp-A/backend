from pydantic import BaseModel


class CertItem(BaseModel):
    name: str


class CertListResponse(BaseModel):
    certs: list[CertItem]