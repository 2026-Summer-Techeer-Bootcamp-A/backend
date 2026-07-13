from scripts.enrich_postings import from_jobkorea


def test_jobkorea_description_is_split_into_standard_sections():
    record = {
        "_url": "https://www.jobkorea.co.kr/Recruit/GI_Read/12345",
        "title": "백엔드 개발자 채용",
        "description": """
            <h2>백엔드 개발자 채용</h2>
            <p>서비스를 함께 성장시킬 개발자를 찾습니다.</p>
            <h3>담당업무</h3>
            <ul><li>API 개발</li><li>서비스 운영</li></ul>
            <h3>자격요건</h3>
            <ul><li>Python 개발 경험</li></ul>
            <h3>우대사항</h3>
            <p>FastAPI 경험</p>
            <h3>기업정보</h3>
            <p>회사 주소와 매출 정보</p>
        """,
    }

    _, enrichment = from_jobkorea(record)

    assert enrichment["desc"] == [
        {"title": "소개", "text": "서비스를 함께 성장시킬 개발자를 찾습니다."},
        {"title": "주요 업무", "text": "• API 개발\n• 서비스 운영"},
        {"title": "자격 요건", "text": "• Python 개발 경험"},
        {"title": "우대 사항", "text": "FastAPI 경험"},
    ]


def test_jobkorea_unstructured_description_is_preserved():
    record = {
        "_url": "https://www.jobkorea.co.kr/Recruit/GI_Read/67890",
        "title": "개발자 모집",
        "description": "<p>정해진 제목이 없는 공고 본문입니다.</p><p>내용을 삭제하면 안 됩니다.</p>",
    }

    _, enrichment = from_jobkorea(record)

    assert enrichment["desc"] == [
        {
            "title": "채용 공고 원문",
            "text": "정해진 제목이 없는 공고 본문입니다.\n내용을 삭제하면 안 됩니다.",
        }
    ]


def test_jobkorea_inline_heading_is_split_and_company_footer_is_removed():
    record = {
        "_url": "https://www.jobkorea.co.kr/Recruit/GI_Read/54321",
        "description": """
            <p>담당업무: API 개발 및 운영</p>
            <p>자격요건: Python 개발 경험</p>
            <h3>기업정보</h3>
            <p>공고 상세가 아닌 회사 소개</p>
        """,
    }

    _, enrichment = from_jobkorea(record)

    assert enrichment["desc"] == [
        {"title": "주요 업무", "text": "API 개발 및 운영"},
        {"title": "자격 요건", "text": "Python 개발 경험"},
    ]
