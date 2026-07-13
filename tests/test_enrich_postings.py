from scripts.enrich_postings import from_jobkorea


def test_from_jobkorea_strips_header_and_footer_boilerplate() -> None:
    rec = {
        "_url": "https://www.jobkorea.co.kr/Recruit/GI_Read/49556095?Oem_Code=C1",
        "description": (
            "㈜파이브 채용 - 주식회사파이브에서 카페24·스마트스토어 마케팅/운영 담당을 찾고 있어요 | "
            "잡코리아 회원가입/로그인 기업 서비스 JOB 찾기 합격축하금 공채정보 신입·인턴 기업·연봉 콘텐츠 취업톡톡 "
            "㈜파이브 주식회사파이브에서 카페24·스마트스토어 마케팅/운영 담당을 찾고 있어요 "
            "상세요강 접수기간∙방법 기업정보 추천공고 디자인 직무채용관 "
            "채용정보에 잘못된 내용이 있을 경우 문의 해주세요. "
            "모집요강 모집분야 주식회사파이브에서 카페24스마트스토어 마케팅 모집인원 1 명 "
            "고용형태 정규직 (수습 3개월) 직급/직책 급여 월급 240만원 이상 "
            "근무시간 주5일(월~금) 10:00 ~ 18:00 "
            "근무지주소 경남 창원시 마산합포구 해안대로 1 (월남동5가, 삼우빌딩) 601호 "
            "지도보기 지원자격 경력 경력무관 학력 학력무관 "
            "로그인 하고 비슷한 조건의 AI추천공고를 확인해 보세요! TOP 궁금해요 "
            "접수기간 · 방법 마감일은 기업의 사정으로 인해 조기 마감 또는 변경될 수 있습니다 "
            "남은기간 시작일 2026.07.10(금) 마감일 2026.08.09(일) "
            "기업 정보 기업정보 더보기 사원수 - 기업구분 중소기업 (-) 산업(업종) - 지도보기 "
            "위치 경남 창원시 마산합포구 해안대로 1 (월남동5가, 삼우빌딩) 601호"
        ),
    }

    uid, result = from_jobkorea(rec)

    assert uid == "49556095"
    text = result["desc"][0]["text"]
    assert "잡코리아" not in text
    assert "로그인" not in text
    assert text.startswith("모집요강 모집분야")
    assert text.endswith("학력 학력무관")


def test_from_jobkorea_leaves_short_description_untouched() -> None:
    rec = {
        "_url": "https://www.jobkorea.co.kr/Recruit/GI_Read/28179150",
        "description": "카페24 카페24 마케팅센터 온라인광고 운영 담당자 모집 :",
    }

    uid, result = from_jobkorea(rec)

    assert uid == "28179150"
    assert result["desc"][0]["text"] == "카페24 카페24 마케팅센터 온라인광고 운영 담당자 모집 :"


def test_from_jobkorea_returns_none_without_uid() -> None:
    assert from_jobkorea({"description": "no url here"}) is None
