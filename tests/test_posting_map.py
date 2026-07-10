"""GET /postings/map 엔드포인트 테스트 (F16)."""

from datetime import date
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

ENDPOINT = "/api/v1/postings/map"


# ──────────────────────────── Validation tests ────────────────────────────


class TestValidation:
    """요청 파라미터 검증."""

    def test_pool_global_returns_422(self):
        """pool=global이면 422 (국내 전용)."""
        resp = client.get(ENDPOINT, params={"pool": "global"})
        assert resp.status_code == 422
        assert "국내" in resp.json()["detail"]

    def test_invalid_bbox_format_returns_422(self):
        """bbox 형식이 잘못되면 422."""
        resp = client.get(ENDPOINT, params={"bbox": "1,2,3"})
        assert resp.status_code == 422

    def test_bbox_non_numeric_returns_422(self):
        """bbox에 숫자가 아닌 값이 있으면 422."""
        resp = client.get(ENDPOINT, params={"bbox": "a,b,c,d"})
        assert resp.status_code == 422


# ──────────────────────────── Functional tests ────────────────────────────


class TestPostingsMap:
    """CRUD 레이어를 모킹한 기능 테스트."""

    @patch("app.routers.posting_map.get_clusters", return_value=[])
    @patch("app.routers.posting_map.get_heatmap", return_value=[])
    @patch(
        "app.routers.posting_map.get_map_pins",
        return_value=(
            [{"id": 1, "lat": 37.49, "lng": 127.02, "title": "백엔드", "company": "토스"}],
            date(2026, 7, 7),
        ),
    )
    def test_normal_response_with_pins(self, mock_pins, mock_heatmap, mock_clusters):
        """정상 조회 시 pins 구조 확인."""
        resp = client.get(ENDPOINT)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["pins"]) == 1
        assert data["pins"][0]["company"] == "토스"
        assert data["pins"][0]["lat"] == 37.49
        assert data["as_of"] == "2026-07-07"

    @patch("app.routers.posting_map.get_clusters", return_value=[])
    @patch(
        "app.routers.posting_map.get_heatmap",
        return_value=[{"region_district": "강남구", "posting_count": 214}],
    )
    @patch(
        "app.routers.posting_map.get_map_pins",
        return_value=([], date(2026, 7, 7)),
    )
    def test_heatmap_structure(self, mock_pins, mock_heatmap, mock_clusters):
        """히트맵 구조 확인."""
        resp = client.get(ENDPOINT)
        data = resp.json()
        assert len(data["heatmap"]) == 1
        assert data["heatmap"][0]["region_district"] == "강남구"
        assert data["heatmap"][0]["posting_count"] == 214

    @patch("app.routers.posting_map.get_clusters", return_value=[])
    @patch("app.routers.posting_map.get_heatmap", return_value=[])
    @patch(
        "app.routers.posting_map.get_map_pins",
        return_value=([], date(2026, 7, 7)),
    )
    def test_region_param_passed(self, mock_pins, mock_heatmap, mock_clusters):
        """region 파라미터가 CRUD에 전달되는지 확인."""
        resp = client.get(ENDPOINT, params={"region": "서울"})
        assert resp.status_code == 200
        mock_pins.assert_called_once()
        assert mock_pins.call_args.kwargs["region"] == "서울"

    @patch("app.routers.posting_map.get_clusters", return_value=[])
    @patch("app.routers.posting_map.get_heatmap", return_value=[])
    @patch(
        "app.routers.posting_map.get_map_pins",
        return_value=([], date(2026, 7, 7)),
    )
    def test_bbox_parsed_correctly(self, mock_pins, mock_heatmap, mock_clusters):
        """bbox 문자열이 tuple로 파싱되어 CRUD에 전달되는지 확인."""
        resp = client.get(ENDPOINT, params={"bbox": "126.9,37.4,127.1,37.6"})
        assert resp.status_code == 200
        mock_pins.assert_called_once()
        assert mock_pins.call_args.kwargs["bbox"] == (126.9, 37.4, 127.1, 37.6)

    @patch("app.routers.posting_map.get_clusters", return_value=[])
    @patch("app.routers.posting_map.get_heatmap", return_value=[])
    @patch(
        "app.routers.posting_map.get_map_pins",
        return_value=([], date(2026, 7, 7)),
    )
    def test_pool_domestic_accepted(self, mock_pins, mock_heatmap, mock_clusters):
        """pool=domestic은 정상 처리."""
        resp = client.get(ENDPOINT, params={"pool": "domestic"})
        assert resp.status_code == 200
