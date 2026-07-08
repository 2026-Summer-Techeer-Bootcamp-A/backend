from datetime import date
from typing import Annotated
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, status

from app.core.deps import SessionDep
from app.core.redis import resume_confirm_session_exists
from app.crud.cert import (
    count_matching_postings,
    get_owned_cert_names,
    get_required_cert_stats,
    resume_exists,
    search_certs,
)
from app.schemas.cert import CertGapItem, CertGapResponse, CertItem, CertListResponse, CertRequirementItem


router = APIRouter()


@router.get("/certs", response_model=CertListResponse)
def get_certs(session: SessionDep, q: str | None = None) -> CertListResponse:
    certs = search_certs(session, q)
    return CertListResponse(certs=[CertItem(name=cert.name) for cert in certs])


@router.get("/cert/gap", response_model=CertGapResponse)
def get_cert_gap(
    session: SessionDep,
    pool: Literal["domestic", "global"],
    resume_id: Annotated[int | None, Query()] = None,
    session_id: Annotated[str | None, Query()] = None,
    position: Annotated[str | None, Query()] = None,
) -> CertGapResponse:
    if resume_id is None and session_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="resume_id or session_id is required",
        )

    if resume_id is not None:
        if not resume_exists(session, resume_id):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="resume not found")
        owned_names = get_owned_cert_names(session, resume_id)
    else:
        if session_id is None or not resume_confirm_session_exists(session_id):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="session not found")
        owned_names = []

    sample_size = count_matching_postings(session, pool, position)
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
