from fastapi import APIRouter, HTTPException, UploadFile, status

from app.core.config import settings
from app.core.deps import SessionDep
from app.schemas.resume import ResumeParseResponse
from app.services.resume import parse_resume_pdf

router = APIRouter()


@router.post(
    "/parse",
    response_model=ResumeParseResponse,
    response_model_exclude_none=True,
    status_code=status.HTTP_200_OK,
)
async def parse_resume(file: UploadFile, session: SessionDep) -> ResumeParseResponse:
    if not _is_pdf_upload(file):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="unsupported media type",
        )

    contents = await file.read()
    if not contents.startswith(b"%PDF"):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="unsupported media type",
        )

    if len(contents) > settings.resume_parse_max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="file too large",
        )

    try:
        return parse_resume_pdf(contents, session)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="could not parse pdf",
        ) from exc


def _is_pdf_upload(file: UploadFile) -> bool:
    filename = file.filename or ""
    return file.content_type == "application/pdf" or filename.lower().endswith(".pdf")
