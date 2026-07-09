"""GET /company/by-skill 엔드포인트 테스트 (F7+F11)."""

from datetime import date, timedelta
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

ENDPOINT = "/api/v1/company/by-skill"


# ──────────────────────────── Validation tests ────────────────────────────


class TestValidation:
    """요청 파라미터 검증 (422 케이스)."""

    def test_skill_missing_returns_422(self):
        """skill 파라미터 누락 시 422."""
        resp = client.get(ENDPOINT)
        assert resp.status_code == 422

    def test_skill_empty_returns_422(self):
        """skill이 빈 문자열이면 422."""
        resp = client.get(ENDPOINT, params={"skill": ""})
        assert resp.status_code == 422

    def test_invalid_pool_returns_422(self):
        """pool이 global/domestic 밖이면 422."""
        resp = client.get(ENDPOINT, params={"skill": "Python", "pool": "invalid"})
        assert resp.status_code == 422


# ──────────────────────────── Functional tests ────────────────────────────


class TestCompanyBySkill:
    """CRUD 레이어를 모킹한 기능 테스트."""

    @patch("app.routers.company.find_skill_id", return_value=None)
    def test_unknown_skill_returns_empty(self, mock_find):
        """사전에 없는 기술 → 200 + 빈 리스트."""
        resp = client.get(ENDPOINT, params={"skill": "UnknownLang"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["skill"] == "UnknownLang"
        assert data["present"] == []
        assert data["past"] == []

    @patch("app.routers.company.get_companies_by_skill")
    @patch("app.routers.company.find_skill_id", return_value=42)
    def test_normal_response_structure(self, mock_find, mock_get):
        """정상 조회 시 present/past 분리 + response_rate 확인."""
        as_of = date(2026, 7, 7)
        split = as_of - timedelta(days=180)
        mock_get.return_value = (
            split,
            as_of,
            [{"company": "토스", "posting_count": 12, "response_rate": 0.82}],
            [{"company": "배달의민족", "posting_count": 7, "response_rate": None}],
        )

        resp = client.get(ENDPOINT, params={"skill": "Kotlin", "pool": "domestic"})
        assert resp.status_code == 200
        data = resp.json()

        assert data["skill"] == "Kotlin"
        assert data["split_date"] == split.isoformat()
        assert data["as_of"] == as_of.isoformat()
        assert len(data["present"]) == 1
        assert data["present"][0]["company"] == "토스"
        assert data["present"][0]["response_rate"] == 0.82
        assert len(data["past"]) == 1
        assert data["past"][0]["company"] == "배달의민족"
        assert data["past"][0]["response_rate"] is None

    @patch("app.routers.company.get_companies_by_skill")
    @patch("app.routers.company.find_skill_id", return_value=42)
    def test_domestic_note_present_for_domestic(self, mock_find, mock_get):
        """pool=domestic이면 domestic_note가 존재."""
        mock_get.return_value = (date.today(), date.today(), [], [])

        resp = client.get(ENDPOINT, params={"skill": "Kotlin", "pool": "domestic"})
        data = resp.json()
        assert data["domestic_note"] is not None
        assert "원티드" in data["domestic_note"]

    @patch("app.routers.company.get_companies_by_skill")
    @patch("app.routers.company.find_skill_id", return_value=42)
    def test_no_domestic_note_for_global(self, mock_find, mock_get):
        """pool=global이면 domestic_note가 None."""
        mock_get.return_value = (date.today(), date.today(), [], [])

        resp = client.get(ENDPOINT, params={"skill": "Python", "pool": "global"})
        data = resp.json()
        assert data["domestic_note"] is None

    @patch("app.routers.company.get_companies_by_skill")
    @patch("app.routers.company.find_skill_id", return_value=42)
    def test_pool_omitted_no_domestic_note(self, mock_find, mock_get):
        """pool 생략 시 domestic_note가 None."""
        mock_get.return_value = (date.today(), date.today(), [], [])

        resp = client.get(ENDPOINT, params={"skill": "Python"})
        data = resp.json()
        assert data["domestic_note"] is None
