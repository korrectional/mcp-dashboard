import os
import tempfile
import pytest
from unittest.mock import patch, MagicMock
import main
from main import (
    normalize_mcpd_url,
    get_or_create_secret_key,
    get_bucket_api_key,
    _get_git_info,
)

class TestNormalizeMcpdUrl:
    def test_default_bucket_redirection(self):
        assert normalize_mcpd_url(None) == "/mcpd/bucket"
        assert normalize_mcpd_url("") == "/mcpd/bucket"
        assert normalize_mcpd_url("  ") == "/mcpd/bucket"
        assert normalize_mcpd_url("/") == "/mcpd/bucket"
        assert normalize_mcpd_url("/mcpd") == "/mcpd/bucket"
        assert normalize_mcpd_url("/mcpd/") == "/mcpd/bucket"

    def test_bucket_subpaths(self):
        assert normalize_mcpd_url("/bucket") == "/mcpd/bucket"
        assert normalize_mcpd_url("/bucket/download/42") == "/mcpd/bucket/download/42"

    def test_already_prefixed_mcpd(self):
        assert normalize_mcpd_url("/mcpd/login") == "/mcpd/login"
        assert normalize_mcpd_url("/mcpd/api/status") == "/mcpd/api/status"

    def test_root_relative_paths(self):
        assert normalize_mcpd_url("/login") == "/mcpd/login"
        assert normalize_mcpd_url("/tasks/123") == "/mcpd/tasks/123"

    def test_bare_relative_paths(self):
        assert normalize_mcpd_url("login") == "/mcpd/login"
        assert normalize_mcpd_url("bucket") == "/mcpd/bucket"


class TestSecretKeyAndApiKey:
    def test_get_or_create_secret_key_from_env(self, monkeypatch):
        test_key = "custom_env_secret_key_1234567890"
        monkeypatch.setenv("DASHBOARD_SECRET_KEY", test_key)
        assert get_or_create_secret_key() == test_key

    def test_get_or_create_secret_key_from_file(self, monkeypatch, tmp_path):
        monkeypatch.delenv("DASHBOARD_SECRET_KEY", raising=False)
        dummy_file = tmp_path / ".secret_key"
        dummy_file.write_text("file_stored_secret_key_abcdef")
        with patch("main.SECRET_KEY_PATH", str(dummy_file)):
            assert get_or_create_secret_key() == "file_stored_secret_key_abcdef"

    def test_get_bucket_api_key_from_env(self, monkeypatch):
        monkeypatch.setenv("BUCKET_API_KEY", "secret_bucket_api_key_xyz")
        assert get_bucket_api_key() == "secret_bucket_api_key_xyz"

    def test_get_bucket_api_key_empty_fallback(self, monkeypatch):
        monkeypatch.delenv("BUCKET_API_KEY", raising=False)
        with patch("os.path.exists", return_value=False):
            assert get_bucket_api_key() == ""


class TestGetGitInfo:
    def test_get_git_info_non_existent(self):
        info = _get_git_info("/non/existent/path/for/sure")
        assert info["has_git"] is False
        assert info["branch"] == "none"
        assert info["commit"] == "No commits"
        assert info["dirty"] is False
        assert info["dirty_count"] == 0

    def test_get_git_info_current_repo(self):
        # Current workspace is a git repo
        current_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        info = _get_git_info(current_dir)
        assert info["has_git"] is True
        assert info["branch"] != "none"
        assert isinstance(info["dirty"], bool)
        assert isinstance(info["dirty_count"], int)

    def test_get_git_info_mocked_git_commands(self, tmp_path):
        repo_dir = str(tmp_path)
        os.makedirs(os.path.join(repo_dir, ".git"))

        def fake_subprocess_run(cmd, cwd=None, capture_output=None, text=None, timeout=None):
            mock_res = MagicMock()
            mock_res.returncode = 0
            if "rev-parse" in cmd:
                mock_res.stdout = "feature-test\n"
            elif "log" in cmd:
                mock_res.stdout = "a1b2c3d - Implement test cases (2 hours ago)\n"
            elif "status" in cmd:
                mock_res.stdout = " M file1.py\n?? file2.py\n"
            return mock_res

        with patch("subprocess.run", side_effect=fake_subprocess_run):
            info = _get_git_info(repo_dir)
            assert info["has_git"] is True
            assert info["branch"] == "feature-test"
            assert "a1b2c3d" in info["commit"]
            assert info["dirty"] is True
            assert info["dirty_count"] == 2
