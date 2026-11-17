import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import httpx
import main
from main import fetch_bucket_health, fetch_bucket_files, fetch_agyc_status

class TestFetchBucketHealth:
    @pytest.mark.asyncio
    async def test_health_success(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"status": "ok", "drive_authenticated": True, "quota_used": "12%"}

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            res = await fetch_bucket_health()
            assert res["status"] == "ok"
            assert res["drive_authenticated"] is True

    @pytest.mark.asyncio
    async def test_health_non_200(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 503

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            res = await fetch_bucket_health()
            assert res["status"] == "error"
            assert res["drive_authenticated"] is False
            assert res["http_status"] == 503

    @pytest.mark.asyncio
    async def test_health_exception(self):
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client_cls.return_value.__aenter__.side_effect = httpx.ConnectError("Connection refused")
            res = await fetch_bucket_health()
            assert res["status"] == "error"
            assert res["drive_authenticated"] is False
            assert "Connection refused" in res["error"]


class TestFetchBucketFiles:
    @pytest.mark.asyncio
    async def test_fetch_files_flat(self):
        files_data = [
            {"id": "1", "name": "clip.mp4", "mimeType": "video/mp4"},
            {"id": "2", "name": "readme.txt", "mimeType": "text/plain"}
        ]
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = files_data

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp

        with patch("httpx.AsyncClient") as mock_client_cls, \
             patch("main.get_bucket_api_key", return_value="test_api_key"):
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            res = await fetch_bucket_files()
            assert len(res) == 2
            assert res[0]["name"] == "clip.mp4"
            mock_client.get.assert_called_once_with(
                f"{main.BUCKET_URL}/files",
                headers={"X-API-Key": "test_api_key"}
            )

    @pytest.mark.asyncio
    async def test_fetch_files_with_subfolder_discovery(self):
        root_data = [
            {"id": "f_root", "name": "root_file.txt", "mimeType": "text/plain"},
            {"id": "folder_1", "name": "renders", "mimeType": "application/vnd.google-apps.folder"}
        ]
        subfolder_data = [
            {"id": "sub_1", "name": "render_001.mp4", "mimeType": "video/mp4"}
        ]

        mock_client = AsyncMock()

        async def fake_get(url, headers=None):
            resp = MagicMock()
            if "subfolder=renders" in url:
                resp.status_code = 200
                resp.json.return_value = subfolder_data
            else:
                resp.status_code = 200
                resp.json.return_value = root_data
            return resp

        mock_client.get = AsyncMock(side_effect=fake_get)

        with patch("httpx.AsyncClient") as mock_client_cls, \
             patch("main.get_bucket_api_key", return_value=""):
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            res = await fetch_bucket_files()
            # Root file + folder + subfolder file = 3 items
            assert len(res) == 3
            sub_item = next(item for item in res if item["id"] == "sub_1")
            assert sub_item["subfolder"] == "renders"
            assert sub_item["display_name"] == "renders/render_001.mp4"

    @pytest.mark.asyncio
    async def test_fetch_files_subfolder_exception_handled_gracefully(self):
        root_data = [
            {"id": "f1", "name": "docs", "mimeType": "application/vnd.google-apps.folder"}
        ]
        mock_client = AsyncMock()

        async def fake_get(url, headers=None):
            if "subfolder=docs" in url:
                raise httpx.RequestError("Subfolder read failure")
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = root_data
            return resp

        mock_client.get = AsyncMock(side_effect=fake_get)

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            res = await fetch_bucket_files()
            assert len(res) == 1
            assert res[0]["name"] == "docs"

    @pytest.mark.asyncio
    async def test_fetch_files_root_non_200(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 500

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            res = await fetch_bucket_files()
            assert res == []

    @pytest.mark.asyncio
    async def test_fetch_files_root_exception(self):
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client_cls.return_value.__aenter__.side_effect = Exception("General network failure")
            res = await fetch_bucket_files()
            assert res == []


class TestFetchAgycStatus:
    @pytest.mark.asyncio
    async def test_fetch_agyc_status_direct_import_success(self):
        mock_server = MagicMock()
        mock_server.list_projects.return_value = [
            {"name": "mcp-dashboard", "path": "/home/azureadmin/mcp/projects/mcp-dashboard", "auto_discovered": True},
            {"name": "host-nginx", "path": "/etc/nginx", "auto_discovered": True},
            {"name": "custom-app", "path": "/home/azureadmin/mcp/projects/custom-app", "auto_discovered": False},
        ]
        mock_server.list_tasks.return_value = [{"id": "task_1", "state": "done"}]
        mock_server.list_routes.return_value = [{"path": "/mcpd/", "target": ":8096"}]

        with patch.dict("sys.modules", {"src.server": mock_server}), \
             patch("main._get_git_info", return_value={"branch": "main", "commit": "123", "dirty": False, "dirty_count": 0, "has_git": True}):
            res = await fetch_agyc_status()
            assert res["online"] is True
            assert len(res["tasks"]) == 1
            assert len(res["routes"]) == 1

            # Check project partitioning
            # mcp-dashboard is in MCP_PROJECTS_ROOT and name list -> managed
            # custom-app has auto_discovered=False and in MCP_PROJECTS_ROOT -> managed
            # host-nginx has auto_discovered=True and path /etc/nginx -> host_services
            managed_names = [p["name"] for p in res["managed_projects"]]
            host_names = [p["name"] for p in res["host_services"]]
            assert "mcp-dashboard" in managed_names
            assert "custom-app" in managed_names
            assert "host-nginx" in host_names

    @pytest.mark.asyncio
    async def test_fetch_agyc_status_fallback_to_http_success(self):
        # Force import to fail
        with patch.dict("sys.modules", {"src.server": None}):
            mock_resp = MagicMock()
            mock_resp.status_code = 200

            mock_client = AsyncMock()
            mock_client.get.return_value = mock_resp

            with patch("httpx.AsyncClient") as mock_client_cls:
                mock_client_cls.return_value.__aenter__.return_value = mock_client
                res = await fetch_agyc_status()
                assert res["online"] is True
                assert res["projects"] == []

    @pytest.mark.asyncio
    async def test_fetch_agyc_status_total_failure(self):
        with patch.dict("sys.modules", {"src.server": None}):
            with patch("httpx.AsyncClient") as mock_client_cls:
                mock_client_cls.return_value.__aenter__.side_effect = Exception("All connections failed")
                res = await fetch_agyc_status()
                assert res["online"] is False
                assert res["projects"] == []
                assert res["tasks"] == []
