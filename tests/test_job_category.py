"""app.services.job_category.resolve_job_category 단위 테스트.

DB 없이 순수 파이썬 로직만 검증한다(fast tier). 이 모듈은 stats/skill-share의
position 필터 버그(mv_skill_share.position이 'Developer' 같은 글로벌 영어 직무
분류라 프론트가 보내는 'backend' 등과 절대 일치하지 않던 문제) 수정의 핵심이라
resolver 자체의 회귀를 여기서 잡는다.
"""

from app.services.job_category import resolve_job_category


def test_resolves_frontend_client_token() -> None:
    assert resolve_job_category("frontend") == "프론트"


def test_resolves_backend_client_token() -> None:
    assert resolve_job_category("backend") == "백엔드"


def test_resolves_fullstack_client_token() -> None:
    assert resolve_job_category("fullstack") == "풀스택"


def test_resolves_devops_client_token() -> None:
    assert resolve_job_category("devops") == "devops"


def test_resolves_data_client_token() -> None:
    assert resolve_job_category("data") == "데이터"


def test_resolves_korean_keyword() -> None:
    assert resolve_job_category("백엔드") == "백엔드"
    assert resolve_job_category("프론트엔드") == "프론트"


def test_is_case_insensitive() -> None:
    assert resolve_job_category("BACKEND") == "백엔드"
    assert resolve_job_category("DevOps") == "devops"


def test_ascii_token_only_matches_whole_word() -> None:
    # "data"라는 단어가 아니라 다른 단어에 우연히 포함된 경우까지 매칭되면 안 된다.
    assert resolve_job_category("metadata") is None
    assert resolve_job_category("qatar") is None


def test_unknown_position_returns_none() -> None:
    """알 수 없는 position은 None — 호출자가 이를 '필터 없음'으로 처리해야 한다."""
    assert resolve_job_category("없는직군") is None
    assert resolve_job_category("astronaut") is None


def test_empty_or_none_returns_none() -> None:
    assert resolve_job_category(None) is None
    assert resolve_job_category("") is None
