import re


_JOBKOREA_SECTION_TITLES = {
    "모집요강": "모집 요강",
    "모집분야": "모집 분야",
    "상세요강": "상세 설명",
    "상세설명": "상세 설명",
    "담당업무": "주요 업무",
    "주요업무": "주요 업무",
    "업무내용": "주요 업무",
    "직무내용": "주요 업무",
    "지원자격": "자격 요건",
    "자격요건": "자격 요건",
    "지원조건": "자격 요건",
    "응시자격": "자격 요건",
    "우대사항": "우대 사항",
    "근무조건": "근무 조건",
    "근무환경": "근무 환경",
    "채용절차": "채용 절차",
    "전형절차": "채용 절차",
    "접수기간": "접수 기간",
    "접수기간및방법": "접수 기간 및 방법",
    "접수방법": "접수 방법",
    "복리후생": "혜택 및 복지",
    "혜택및복지": "혜택 및 복지",
}
_JOBKOREA_FOOTER_TITLES = {"기업정보", "지도보기"}
_JOBKOREA_HEADINGS = tuple(
    sorted(
        (*_JOBKOREA_SECTION_TITLES, *_JOBKOREA_FOOTER_TITLES),
        key=len,
        reverse=True,
    )
)
_JOBKOREA_HEADING_RE = re.compile(
    rf"(?<![0-9A-Za-z가-힣])({'|'.join(map(re.escape, _JOBKOREA_HEADINGS))})\s*[:：]?\s*"
)


def normalize_jobkorea_sections(
    sections: list[dict],
    *,
    posting_title: str | None = None,
) -> list[dict]:
    """기존 DB에 한 덩어리로 저장된 잡코리아 설명만 조회 시점에 보정한다.

    이미 구조화된 설명, 알 수 없는 형식, 다른 제목의 단일 섹션은 그대로 반환한다.
    """
    if len(sections) != 1 or not isinstance(sections[0], dict):
        return sections

    raw_section = sections[0]
    if raw_section.get("title") != "채용 공고 원문":
        return sections

    raw_text = raw_section.get("text")
    if not isinstance(raw_text, str) or not raw_text.strip():
        return sections

    text = re.sub(r"\s+", " ", raw_text).strip()
    matches = list(_JOBKOREA_HEADING_RE.finditer(text))
    content_matches = [
        match for match in matches
        if match.group(1) in _JOBKOREA_SECTION_TITLES
    ]
    if len(content_matches) < 2:
        return sections

    normalized: list[dict] = []
    first_match = matches[0]
    preamble = text[: first_match.start()].strip(" |:-")
    noisy_preamble_markers = ("잡코리아", "회원가입", "로그인", "JOB 찾기")
    if (
        preamble
        and preamble != (posting_title or "").strip()
        and not any(marker in preamble for marker in noisy_preamble_markers)
    ):
        normalized.append({"title": "소개", "text": preamble})

    for index, match in enumerate(matches):
        heading = match.group(1)
        if heading in _JOBKOREA_FOOTER_TITLES:
            break

        next_start = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        content = text[match.end() : next_start].strip(" |:-")
        if content:
            normalized.append(
                {"title": _JOBKOREA_SECTION_TITLES[heading], "text": content}
            )

    return normalized or sections
