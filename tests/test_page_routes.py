import pytest
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient
import main
from main import SESSION_COOKIE_NAME, AUTH_USERNAME, create_session_token

class TestRequireAuthDependency:
    """Tests the require_auth dependency across varied auth contexts and accept headers."""

    def test_unauthenticated_browser_redirects(self, client):
        response = client.get("/")
        assert response.status_code == 303
        assert response.headers["location"].startswith("/mcpd/login?next=")

    def test_unauthenticated_api_returns_401_json(self, client):
        response = client.get("/api/status")
        assert response.status_code == 401
        data = response.json()
        assert data.get("detail") == "Authentication required"

    def test_unauthenticated_mcpd_api_returns_401_json(self, client):
        response = client.get("/mcpd/api/status")
        assert response.status_code == 401
        data = response.json()
        assert data.get("detail") == "Authentication required"

    def test_unauthenticated_accept_json_header_returns_401_json(self, client):
        response = client.get("/", headers={"accept": "application/json"})
        assert response.status_code == 401
        assert response.json().get("detail") == "Authentication required"

    def test_authenticated_via_cookie(self, client, auth_token):
        client.cookies.set(SESSION_COOKIE_NAME, auth_token)
        with patch("main.fetch_agyc_status", new_callable=AsyncMock) as mock_agyc, \
             patch("main.fetch_bucket_health", new_callable=AsyncMock) as mock_bucket:
            mock_agyc.return_value = {"projects": [], "managed_projects": [], "host_services": [], "tasks": [], "routes": [], "online": True}
            mock_bucket.return_value = {"status": "ok", "drive_authenticated": True}
            response = client.get("/")
            assert response.status_code == 200

    def test_authenticated_via_bearer_token(self, client, auth_token):
        headers = {"authorization": f"Bearer {auth_token}"}
        with patch("main.fetch_agyc_status", new_callable=AsyncMock) as mock_agyc, \
             patch("main.fetch_bucket_health", new_callable=AsyncMock) as mock_bucket:
            mock_agyc.return_value = {"projects": [], "managed_projects": [], "host_services": [], "tasks": [], "routes": [], "online": True}
            mock_bucket.return_value = {"status": "ok", "drive_authenticated": True}
            response = client.get("/api/status", headers=headers)
            assert response.status_code == 200

    def test_invalid_bearer_token_rejected(self, client):
        headers = {"authorization": "Bearer invalid_tampered_token_string"}
        response = client.get("/api/status", headers=headers)
        assert response.status_code == 401

    def test_query_params_preserved_in_redirect(self, client):
        response = client.get("/bucket?view=grid&sort=name")
        assert response.status_code == 303
        assert "view=grid" in response.headers["location"]
        assert "sort=name" in response.headers["location"]


class TestDashboardPageRoutes:
    """Tests the dashboard page across its root and /mcpd/ endpoints."""

    @pytest.mark.parametrize("path", ["/", "/mcpd", "/mcpd/"])
    def test_dashboard_unauthenticated_redirect(self, client, path):
        response = client.get(path)
        assert response.status_code == 303
        assert "/mcpd/login" in response.headers["location"]

    @pytest.mark.parametrize("path", ["/", "/mcpd", "/mcpd/"])
    def test_dashboard_authenticated_success(self, auth_client, path):
        mock_status = {
            "projects": [{"name": "proj1", "branch": "main", "commit": "abc - init", "dirty": False, "is_managed": True}],
            "managed_projects": [{"name": "proj1", "branch": "main", "commit": "abc - init", "dirty": False, "is_managed": True}],
            "host_services": [],
            "tasks": [],
            "routes": [],
            "online": True
        }
        mock_bucket = {"status": "ok", "drive_authenticated": True}

        with patch("main.fetch_agyc_status", new_callable=AsyncMock, return_value=mock_status), \
             patch("main.fetch_bucket_health", new_callable=AsyncMock, return_value=mock_bucket):
            response = auth_client.get(path)
            assert response.status_code == 200
            assert "text/html" in response.headers.get("content-type", "")
            assert "AGYC" in response.text or "DASHBOARD" in response.text
            assert AUTH_USERNAME in response.text

    @pytest.mark.parametrize("path", ["/", "/mcpd", "/mcpd/"])
    def test_dashboard_authenticated_with_active_tasks(self, auth_client, path):
        mock_status = {
            "projects": [{"name": "proj1", "branch": "main", "commit": "abc - init", "dirty": False, "is_managed": True}],
            "managed_projects": [{"name": "proj1", "branch": "main", "commit": "abc - init", "dirty": False, "is_managed": True}],
            "host_services": [],
            "tasks": [
                {"task_id": "task_active_99", "project_name": "proj1", "prompt": "build awesome feature", "status": "running", "started_at": "12:00:00"}
            ],
            "active_tasks": [
                {"task_id": "task_active_99", "project_name": "proj1", "prompt": "build awesome feature", "status": "running", "started_at": "12:00:00"}
            ],
            "active_count": 1,
            "routes": [],
            "online": True
        }
        mock_bucket = {"status": "ok", "drive_authenticated": True}

        with patch("main.fetch_agyc_status", new_callable=AsyncMock, return_value=mock_status), \
             patch("main.fetch_bucket_health", new_callable=AsyncMock, return_value=mock_bucket):
            response = auth_client.get(path)
            assert response.status_code == 200
            assert "1 RUNNING" in response.text
            assert 'id="val-active-tasks">1</div>' in response.text
            assert "1 ACTIVE" in response.text
            assert "task_active_99" in response.text
            assert "build awesome feature" in response.text


class TestBucketManagerPageRoutes:
    """Tests the interactive bucketloader file manager page."""

    @pytest.mark.parametrize("path", ["/bucket", "/bucket/", "/mcpd/bucket", "/mcpd/bucket/"])
    def test_bucket_manager_unauthenticated_redirect(self, client, path):
        response = client.get(path)
        assert response.status_code == 303
        assert "/mcpd/login" in response.headers["location"]

    @pytest.mark.parametrize("path", ["/bucket", "/bucket/", "/mcpd/bucket", "/mcpd/bucket/"])
    def test_bucket_manager_authenticated_success(self, auth_client, path):
        mock_bucket = {"status": "ok", "drive_authenticated": True}
        mock_files = [
            {"id": "file_123", "name": "sample.mp4", "mimeType": "video/mp4", "size": 1024, "modifiedTime": "2026-11-17T00:00:00Z"},
            {"id": "file_456", "name": "notes.md", "mimeType": "text/markdown", "size": 256, "modifiedTime": "2026-11-17T01:00:00Z"}
        ]

        with patch("main.fetch_bucket_health", new_callable=AsyncMock, return_value=mock_bucket), \
             patch("main.fetch_bucket_files", new_callable=AsyncMock, return_value=mock_files):
            response = auth_client.get(path)
            assert response.status_code == 200
            assert "text/html" in response.headers.get("content-type", "")
            assert "BUCKETLOADER" in response.text
            assert "sample.mp4" in response.text
            assert "notes.md" in response.text
