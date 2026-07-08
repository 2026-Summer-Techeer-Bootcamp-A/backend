from datetime import date

from fastapi import APIRouter

from app.core.deps import SessionDep
from app.crud.cert import count_matching_postings, get_owned_cert_names, get_required_cert_stats, search_certs
from app.schemas.cert import CertGapItem, CertGapResponse, CertItem, CertListResponse, CertRequirementItem


router = APIRouter()


@router.get("/certs", response_model=CertListResponse)
def get_certs(session: SessionDep, q: str | None = None) -> CertListResponse:
    certs = search_certs(session, q)
    return CertListResponse(certs=[CertItem(name=cert.name) for cert in certs])


@router.get("/cert/gap", response_model=CertGapResponse)
def get_cert_gap(
    session: SessionDep,
    resume_id: int,
    pool: str,
    position: str,
) -> CertGapResponse:
    sample_size = count_matching_postings(session, pool, position)
    owned_names = get_owned_cert_names(session, resume_id)
    owned_name_set = set(owned_names)
    required_stats = get_required_cert_stats(session, pool, position)

    required = [
        CertRequirementItem(
            name=name,
            share=posting_count / sample_size if sample_size else 0,
            posting_count=posting_count,
        )
        for name, posting_count in required_stats
    ]
    gap = [
        CertGapItem(name=item.name, share=item.share)
        for item in required
        if item.name not in owned_name_set
    ]

    return CertGapResponse(
        required=required,
        owned=[CertItem(name=name) for name in owned_names],
        gap=gap,
        as_of=date.today().isoformat(),
        sample_size=sample_size,
    )
