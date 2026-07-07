from fastapi import APIRouter

from app.core.deps import SessionDep
from app.crud.cert import search_certs
from app.schemas.cert import CertItem, CertListResponse


router = APIRouter()


@router.get("/certs", response_model=CertListResponse)
def get_certs(session: SessionDep, q: str | None = None) -> CertListResponse:
    certs = search_certs(session, q)
    return CertListResponse(certs=[CertItem(name=cert.name) for cert in certs])
