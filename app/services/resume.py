from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from io import BytesIO

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.cert import Cert
from app.models.skill import Skill, SkillAlias
from app.schemas.resume import ParsedCert, ParsedSkill, ResumeParseResponse


POSITION_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("backend", ("backend", "back-end", "back end", "백엔드", "서버")),
    ("frontend", ("frontend", "front-end", "front end", "프론트엔드")),
    ("fullstack", ("fullstack", "full-stack", "full stack", "풀스택")),
    ("devops", ("devops", "dev ops", "sre", "인프라")),
    ("data", ("data engineer", "data scientist", "데이터")),
)

UNKNOWN_TECH_PATTERN = re.compile(r"\b(?:[A-Z]{2,}|[A-Z][A-Za-z0-9]*(?:Tool|DB|JS|API|Cloud))\b")


class TaxonomyEntry:
    def __init__(self, canonical: str, category: str, aliases: set[str]) -> None:
        self.canonical = canonical
        self.category = category
        self.aliases = aliases


def extract_pdf_text(pdf_bytes: bytes) -> str:
    text = extract_pdf_text_with_pdftotext(pdf_bytes)
    if text:
        return text

    return extract_pdf_text_with_pypdf(pdf_bytes)


def extract_pdf_text_with_pypdf(pdf_bytes: bytes) -> str:
    try:
        from pypdf import PdfReader

        reader = PdfReader(BytesIO(pdf_bytes))
        return "\n".join(page.extract_text() or "" for page in reader.pages).strip()
    except Exception:
        return ""


def extract_pdf_text_with_pdftotext(pdf_bytes: bytes) -> str:
    if not shutil.which("pdftotext"):
        return ""

    with tempfile.NamedTemporaryFile(suffix=".pdf") as pdf_file:
        pdf_file.write(pdf_bytes)
        pdf_file.flush()
        result = subprocess.run(
            ["pdftotext", "-layout", pdf_file.name, "-"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )

    return result.stdout.strip() if result.returncode == 0 else ""


def parse_resume_pdf(pdf_bytes: bytes, session: Session) -> ResumeParseResponse:
    text = extract_pdf_text(pdf_bytes)
    if not text:
        raise ValueError("no extractable text")
    return parse_resume_text(text, load_taxonomy(session), load_cert_names(session))


def parse_resume_text(
    text: str, taxonomy: list[TaxonomyEntry], cert_names: list[str] | None = None
) -> ResumeParseResponse:
    career_min, career_max = extract_career_range(text)
    return ResumeParseResponse(
        skills=extract_skills(text, taxonomy),
        certs=extract_certs(text, cert_names or []),
        position=extract_position(text),
        career_min=career_min,
        career_max=career_max,
        resume_text=text,
    )


def load_cert_names(session: Session) -> list[str]:
    certs = session.scalars(select(Cert).where(Cert.is_deleted.is_(False))).all()
    return [cert.name for cert in certs]


def extract_certs(text: str, cert_names: list[str]) -> list[ParsedCert]:
    normalized = text.lower()
    found: list[tuple[int, ParsedCert]] = []

    for name in cert_names:
        position = normalized.find(name.lower())
        if position != -1:
            found.append((position, ParsedCert(name=name, in_dict=True)))

    return [cert for _, cert in sorted(found, key=lambda item: item[0])]


def load_taxonomy(session: Session) -> list[TaxonomyEntry]:
    skills = session.scalars(select(Skill).where(Skill.deleted_at.is_(None))).all()
    aliases = session.execute(
        select(SkillAlias.skill_id, SkillAlias.alias).where(SkillAlias.deleted_at.is_(None))
    ).all()

    aliases_by_skill_id: dict[int, set[str]] = {}
    for skill_id, alias in aliases:
        aliases_by_skill_id.setdefault(skill_id, set()).add(alias)

    return [
        TaxonomyEntry(
            canonical=skill.canonical,
            category=skill.category,
            aliases={skill.canonical, *aliases_by_skill_id.get(skill.id, set())},
        )
        for skill in skills
    ]


def extract_skills(text: str, taxonomy: list[TaxonomyEntry]) -> list[ParsedSkill]:
    normalized = text.lower()
    found: list[tuple[int, ParsedSkill]] = []
    seen: set[str] = set()

    for item in taxonomy:
        aliases = tuple(alias.lower() for alias in item.aliases)
        position = _first_alias_position(normalized, aliases)
        if position is not None:
            _append_skill(found, seen, position, item.canonical, item.category, True)

    for match in UNKNOWN_TECH_PATTERN.finditer(text):
        label = match.group(0)
        if label.lower() not in seen:
            _append_skill(found, seen, match.start(), label, "unknown", False)

    return [skill for _, skill in sorted(found, key=lambda item: item[0])]


def extract_position(text: str) -> str | None:
    normalized = text.lower()
    for position, aliases in POSITION_ALIASES:
        if any(alias.lower() in normalized for alias in aliases):
            return position
    return None


def extract_career_range(text: str) -> tuple[int | None, int | None]:
    range_match = re.search(r"(\d{1,2})\s*(?:-|~|to)\s*(\d{1,2})\s*(?:years?|yrs?|년)", text, re.IGNORECASE)
    if range_match:
        return int(range_match.group(1)), int(range_match.group(2))

    minimum_match = re.search(r"(\d{1,2})\s*(?:\+|년\s*이상|years?\s*\+)", text, re.IGNORECASE)
    if minimum_match:
        return int(minimum_match.group(1)), None

    single_match = re.search(r"(\d{1,2})\s*(?:years?|yrs?|년)", text, re.IGNORECASE)
    if single_match:
        years = int(single_match.group(1))
        return years, years

    return None, None


def _first_alias_position(text: str, aliases: tuple[str, ...]) -> int | None:
    positions = [_alias_position(text, alias) for alias in aliases]
    found = [position for position in positions if position is not None]
    return min(found) if found else None


def _alias_position(text: str, alias: str) -> int | None:
    if re.fullmatch(r"[a-z0-9 .+-]+", alias):
        match = re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", text)
        return match.start() if match else None
    position = text.find(alias)
    return position if position >= 0 else None


def _append_skill(
    skills: list[tuple[int, ParsedSkill]],
    seen: set[str],
    position: int,
    canonical: str,
    category: str,
    in_dict: bool,
) -> None:
    seen.add(canonical.lower())
    skills.append((position, ParsedSkill(canonical=canonical, category=category, in_dict=in_dict)))
