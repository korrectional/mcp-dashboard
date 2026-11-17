import pytest
from unittest.mock import MagicMock, patch, AsyncMock
import main

@pytest.fixture
def mock_agyc_server():
    server = MagicMock()
    with patch.dict("sys.modules", {"src.server": server}):
        yield server

class TestAutonomousCompletionApi:
    @pytest.mark.parametrize("route", ["/api/complete", "/mcpd/api/complete"])
    def test_complete_unauthenticated(self, client, route):
        response = client.post(route, data={"project_name": "p1", "goal": "build app"})
        assert response.status_code == 401

    def test_complete_start_success(self, auth_client, mock_agyc_server):
        mock_agyc_server.complete.return_value = {
            "session_id": "comp_12345",
            "project_name": "cli-snake",
            "goal": "build complete game",
            "duration_hours": 4.0,
            "status": "running",
        }
        response = auth_client.post("/api/complete", data={
            "project_name": "cli-snake",
            "goal": "build complete game",
            "duration_hours": "4.0",
            "model": "pro",
            "effort": "high"
        })
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["session_id"] == "comp_12345"
        mock_agyc_server.complete.assert_called_once_with(
            project_name="cli-snake",
            goal="build complete game",
            duration_hours=4.0,
            model="pro",
            effort="high"
        )

    def test_complete_start_error_response(self, auth_client, mock_agyc_server):
        mock_agyc_server.complete.return_value = {"error": "Project not found"}
        response = auth_client.post("/mcpd/api/complete", data={
            "project_name": "unknown-proj",
            "goal": "fail"
        })
        assert response.status_code == 200
        assert response.json() == {"success": False, "error": "Project not found"}

    def test_list_completions(self, auth_client, mock_agyc_server):
        mock_agyc_server.list_completions.return_value = [
            {"session_id": "comp_1", "status": "running"}
        ]
        response = auth_client.get("/api/complete?project_name=cli-snake")
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 1
        assert data["sessions"][0]["session_id"] == "comp_1"

    def test_get_completion_status_found(self, auth_client, mock_agyc_server):
        mock_agyc_server.get_completion_status.return_value = {
            "session_id": "comp_1",
            "status": "running",
            "tests_passing": True
        }
        response = auth_client.get("/mcpd/api/complete/comp_1")
        assert response.status_code == 200
        assert response.json()["tests_passing"] is True

    def test_get_completion_status_not_found(self, auth_client, mock_agyc_server):
        mock_agyc_server.get_completion_status.return_value = {"error": "Session not found"}
        response = auth_client.get("/api/complete/nonexistent")
        assert response.status_code == 404

    def test_cancel_completion(self, auth_client, mock_agyc_server):
        mock_agyc_server.cancel_completion.return_value = {"success": True, "message": "Cancelled"}
        response = auth_client.post("/api/complete/comp_1/cancel")
        assert response.status_code == 200
        assert response.json()["success"] is True
