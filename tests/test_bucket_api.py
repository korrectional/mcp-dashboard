import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import httpx
import main
from main import SESSION_COOKIE_NAME, AUTH_USERNAME

class TestBucketListFilesApi:
    @pytest.mark.parametrize("route", ["/api/bucket/files", "/mcpd/api/bucket/files"])
    def test_list_files_unauthenticated(self, client, route):
        response = client.get(route)
        assert response.status_code == 401

    @pytest.mark.parametrize("route", ["/api/bucket/files", "/mcpd/api/bucket/files"])
    def test_list_files_authenticated_success(self, auth_client, route):
        mock_files = [{"id": "f1", "name": "clip.mp4", "size": 1000}]
        with patch("main.fetch_bucket_files", new_callable=AsyncMock, return_value=mock_files):
            response = auth_client.get(route)
            assert response.status_code == 200
            assert response.json() == mock_files


class TestBucketDownloadEndpoints:
    @pytest.mark.parametrize("route", [
        "/api/bucket/files/file_99/download",
        "/mcpd/api/bucket/files/file_99/download",
        "/bucket/download/file_99",
        "/mcpd/bucket/download/file_99"
    ])
    def test_download_unauthenticated_api_or_redirect(self, client, route):
        response = client.get(route)
        # Note: /api/... returns 401, while /bucket/download/... returns 303 redirect
        if "/api/" in route:
            assert response.status_code == 401
        else:
            assert response.status_code == 303

    def test_download_streaming_success_with_filename(self, auth_client):
        async def fake_stream(chunk_size=65536):
            yield b"chunk_1_"
            yield b"chunk_2"

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {
            "content-type": "video/mp4",
            "content-length": "15",
            "accept-ranges": "bytes"
        }
        mock_resp.aiter_bytes = fake_stream
        mock_resp.aclose = AsyncMock()

        mock_client = MagicMock()
        mock_client.build_request.return_value = MagicMock()
        mock_client.send = AsyncMock(return_value=mock_resp)
        mock_client.aclose = AsyncMock()

        with patch("httpx.AsyncClient", return_value=mock_client), \
             patch("main.get_bucket_api_key", return_value="dummy_key"):
            response = auth_client.get("/api/bucket/files/f123/download?filename=my_video.mp4", headers={"range": "bytes=0-10"})
            assert response.status_code == 200
            assert response.content == b"chunk_1_chunk_2"
            assert response.headers.get("content-disposition") == 'attachment; filename="my_video.mp4"'
            assert response.headers.get("content-type") == "video/mp4"

    def test_download_streaming_preserves_upstream_attachment_disposition(self, auth_client):
        async def fake_stream(chunk_size=65536):
            yield b"data"

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {
            "content-type": "application/pdf",
            "content-disposition": 'attachment; filename="upstream_name.pdf"'
        }
        mock_resp.aiter_bytes = fake_stream
        mock_resp.aclose = AsyncMock()

        mock_client = MagicMock()
        mock_client.build_request.return_value = MagicMock()
        mock_client.send = AsyncMock(return_value=mock_resp)
        mock_client.aclose = AsyncMock()

        with patch("httpx.AsyncClient", return_value=mock_client):
            response = auth_client.get("/api/bucket/files/f456/download")
            assert response.status_code == 200
            assert response.headers.get("content-disposition") == 'attachment; filename="upstream_name.pdf"'

    def test_download_streaming_fallback_default_filename(self, auth_client):
        async def fake_stream(chunk_size=65536):
            yield b"bytes"

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"content-type": "application/octet-stream"}
        mock_resp.aiter_bytes = fake_stream
        mock_resp.aclose = AsyncMock()

        mock_client = MagicMock()
        mock_client.build_request.return_value = MagicMock()
        mock_client.send = AsyncMock(return_value=mock_resp)
        mock_client.aclose = AsyncMock()

        with patch("httpx.AsyncClient", return_value=mock_client):
            response = auth_client.get("/api/bucket/files/abc789/download")
            assert response.status_code == 200
            assert response.headers.get("content-disposition") == 'attachment; filename="bucket_file_abc789"'

    def test_download_upstream_error_code(self, auth_client):
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_resp.aread = AsyncMock(return_value=b"File not found in Google Drive")
        mock_resp.aclose = AsyncMock()

        mock_client = MagicMock()
        mock_client.build_request.return_value = MagicMock()
        mock_client.send = AsyncMock(return_value=mock_resp)
        mock_client.aclose = AsyncMock()

        with patch("httpx.AsyncClient", return_value=mock_client):
            response = auth_client.get("/api/bucket/files/missing/download")
            assert response.status_code == 404
            data = response.json()
            assert data["error"] == "Failed to stream file from bucket"
            assert "File not found" in data["detail"]

    def test_download_network_exception(self, auth_client):
        mock_client = MagicMock()
        mock_client.build_request.return_value = MagicMock()
        mock_client.send = AsyncMock(side_effect=httpx.ConnectError("Connection refused to :8090"))
        mock_client.aclose = AsyncMock()

        with patch("httpx.AsyncClient", return_value=mock_client):
            response = auth_client.get("/api/bucket/files/err/download")
            assert response.status_code == 500
            assert "Connection refused" in response.json()["error"]


class TestBucketRawDownloadEndpoints:
    @pytest.mark.parametrize("route", [
        "/api/bucket/raw/renders/clip.mp4/download",
        "/mcpd/api/bucket/raw/renders/clip.mp4/download",
        "/bucket/raw/renders/clip.mp4/download",
        "/mcpd/bucket/raw/renders/clip.mp4/download",
    ])
    def test_raw_download_unauthenticated(self, client, route):
        response = client.get(route)
        if "/api/" in route:
            assert response.status_code == 401
        else:
            assert response.status_code == 303

    def test_raw_download_success(self, auth_client):
        async def fake_stream(chunk_size=65536):
            yield b"raw_video_data"

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {
            "content-type": "video/mp4",
            "content-length": "14"
        }
        mock_resp.aiter_bytes = fake_stream
        mock_resp.aclose = AsyncMock()

        mock_client = MagicMock()
        mock_client.build_request.return_value = MagicMock()
        mock_client.send = AsyncMock(return_value=mock_resp)
        mock_client.aclose = AsyncMock()

        with patch("httpx.AsyncClient", return_value=mock_client):
            response = auth_client.get("/api/bucket/raw/renders/subfolder/teaser.mp4/download", headers={"range": "bytes=0-100"})
            assert response.status_code == 200
            assert response.content == b"raw_video_data"
            assert response.headers.get("content-disposition") == 'attachment; filename="teaser.mp4"'

    def test_raw_download_upstream_error(self, auth_client):
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_resp.aread = AsyncMock(return_value=b"Raw path not found")
        mock_resp.aclose = AsyncMock()

        mock_client = MagicMock()
        mock_client.build_request.return_value = MagicMock()
        mock_client.send = AsyncMock(return_value=mock_resp)
        mock_client.aclose = AsyncMock()

        with patch("httpx.AsyncClient", return_value=mock_client):
            response = auth_client.get("/api/bucket/raw/missing/path/file.txt/download")
            assert response.status_code == 404
            assert "Failed to retrieve file" in response.json()["error"]

    def test_raw_download_exception(self, auth_client):
        mock_client = MagicMock()
        mock_client.build_request.return_value = MagicMock()
        mock_client.send = AsyncMock(side_effect=Exception("Read timeout"))
        mock_client.aclose = AsyncMock()

        with patch("httpx.AsyncClient", return_value=mock_client):
            response = auth_client.get("/api/bucket/raw/error/path.txt/download")
            assert response.status_code == 500
            assert response.json() == {"error": "Read timeout"}


class TestBucketUploadAndSaveText:
    @pytest.mark.parametrize("route", ["/api/bucket/upload", "/mcpd/api/bucket/upload"])
    def test_upload_unauthenticated(self, client, route):
        response = client.post(route, files={"file": ("test.txt", b"content", "text/plain")})
        assert response.status_code == 401

    def test_upload_success(self, auth_client):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"id": "uploaded_123", "name": "test.txt", "size": 7}

        mock_client_instance = AsyncMock()
        mock_client_instance.post.return_value = mock_resp

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client_cls.return_value.__aenter__.return_value = mock_client_instance
            response = auth_client.post(
                "/api/bucket/upload",
                files={"file": ("test.txt", b"content", "text/plain")},
                data={"overwrite": "true"}
            )
            assert response.status_code == 200
            assert response.json()["id"] == "uploaded_123"

    def test_upload_upstream_error(self, auth_client):
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.text = "File already exists"

        mock_client_instance = AsyncMock()
        mock_client_instance.post.return_value = mock_resp

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client_cls.return_value.__aenter__.return_value = mock_client_instance
            response = auth_client.post(
                "/api/bucket/upload",
                files={"file": ("test.txt", b"content", "text/plain")},
                data={"overwrite": "false"}
            )
            assert response.status_code == 400
            assert response.json() == {"error": "File already exists"}

    def test_upload_network_exception(self, auth_client):
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client_cls.return_value.__aenter__.side_effect = Exception("Upload connection error")
            response = auth_client.post(
                "/api/bucket/upload",
                files={"file": ("test.txt", b"content", "text/plain")}
            )
            assert response.status_code == 500
            assert response.json() == {"error": "Upload connection error"}

    @pytest.mark.parametrize("route", ["/api/bucket/save_text", "/mcpd/api/bucket/save_text"])
    def test_save_text_unauthenticated(self, client, route):
        response = client.post(route, data={"filename": "doc.md", "content": "# Hello"})
        assert response.status_code == 401

    def test_save_text_success(self, auth_client):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"id": "text_doc_1", "status": "saved"}

        mock_client_instance = AsyncMock()
        mock_client_instance.post.return_value = mock_resp

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client_cls.return_value.__aenter__.return_value = mock_client_instance
            response = auth_client.post("/api/bucket/save_text", data={
                "filename": "summary.md",
                "content": "# Test content",
                "overwrite": "true"
            })
            assert response.status_code == 200
            assert response.json()["id"] == "text_doc_1"

    def test_save_text_upstream_error(self, auth_client):
        mock_resp = MagicMock()
        mock_resp.status_code = 502
        mock_resp.text = "Bad gateway from Google Drive"

        mock_client_instance = AsyncMock()
        mock_client_instance.post.return_value = mock_resp

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client_cls.return_value.__aenter__.return_value = mock_client_instance
            response = auth_client.post("/api/bucket/save_text", data={
                "filename": "error.md",
                "content": "content"
            })
            assert response.status_code == 502
            assert response.json() == {"error": "Bad gateway from Google Drive"}

    def test_save_text_exception(self, auth_client):
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client_cls.return_value.__aenter__.side_effect = RuntimeError("Save failure")
            response = auth_client.post("/api/bucket/save_text", data={
                "filename": "doc.txt",
                "content": "text"
            })
            assert response.status_code == 500
            assert response.json() == {"error": "Save failure"}


class TestBucketContentAndFileDeletion:
    @pytest.mark.parametrize("route", [
        "/api/bucket/files/f1/content",
        "/mcpd/api/bucket/files/f1/content"
    ])
    def test_file_content_unauthenticated(self, client, route):
        response = client.get(route)
        assert response.status_code == 401

    def test_file_content_utf8_text(self, auth_client):
        text_payload = "# Hello Antigravity\nProject Status: Nominal"
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = text_payload.encode("utf-8")

        mock_client_instance = AsyncMock()
        mock_client_instance.get.return_value = mock_resp

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client_cls.return_value.__aenter__.return_value = mock_client_instance
            response = auth_client.get("/api/bucket/files/text_file_1/content")
            assert response.status_code == 200
            data = response.json()
            assert data["type"] == "text"
            assert data["content"] == text_payload
            assert data["size"] == len(text_payload.encode("utf-8"))

    def test_file_content_binary_base64(self, auth_client):
        binary_payload = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\xff\xfe"
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = binary_payload
        mock_resp.headers = {"content-type": "image/png"}

        mock_client_instance = AsyncMock()
        mock_client_instance.get.return_value = mock_resp

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client_cls.return_value.__aenter__.return_value = mock_client_instance
            response = auth_client.get("/api/bucket/files/img_file_1/content")
            assert response.status_code == 200
            data = response.json()
            assert data["type"] == "binary"
            assert data["mime"] == "image/png"
            assert "base64" in data
            assert data["size"] == len(binary_payload)

    def test_file_content_upstream_error(self, auth_client):
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_resp.text = "File not found"

        mock_client_instance = AsyncMock()
        mock_client_instance.get.return_value = mock_resp

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client_cls.return_value.__aenter__.return_value = mock_client_instance
            response = auth_client.get("/api/bucket/files/missing/content")
            assert response.status_code == 404
            assert response.json() == {"error": "File not found"}

    def test_file_content_exception(self, auth_client):
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client_cls.return_value.__aenter__.side_effect = Exception("Read content error")
            response = auth_client.get("/api/bucket/files/err/content")
            assert response.status_code == 500
            assert response.json() == {"error": "Read content error"}

    @pytest.mark.parametrize("route", [
        "/api/bucket/files/f1",
        "/mcpd/api/bucket/files/f1"
    ])
    def test_delete_file_unauthenticated(self, client, route):
        response = client.delete(route)
        assert response.status_code == 401

    def test_delete_file_success(self, auth_client):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"success": True, "trashed": True}

        mock_client_instance = AsyncMock()
        mock_client_instance.delete.return_value = mock_resp

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client_cls.return_value.__aenter__.return_value = mock_client_instance
            response = auth_client.delete("/api/bucket/files/file_to_del")
            assert response.status_code == 200
            assert response.json()["trashed"] is True

    def test_delete_file_upstream_error(self, auth_client):
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_resp.text = "Permission denied to delete file"

        mock_client_instance = AsyncMock()
        mock_client_instance.delete.return_value = mock_resp

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client_cls.return_value.__aenter__.return_value = mock_client_instance
            response = auth_client.delete("/api/bucket/files/file_protected")
            assert response.status_code == 403
            assert response.json() == {"error": "Permission denied to delete file"}

    def test_delete_file_exception(self, auth_client):
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client_cls.return_value.__aenter__.side_effect = RuntimeError("Delete timeout")
            response = auth_client.delete("/api/bucket/files/err_del")
            assert response.status_code == 500
            assert response.json() == {"error": "Delete timeout"}
