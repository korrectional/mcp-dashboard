import pytest
from fastapi.testclient import TestClient
import main
from main import AUTH_USERNAME, AUTH_PASSWORD, SESSION_COOKIE_NAME

class TestLoginGetRoutes:
    @pytest.mark.parametrize("route", ["/login", "/mcpd/login"])
    def test_login_page_unauthenticated(self, client, route):
        response = client.get(route)
        assert response.status_code == 200
        assert "text/html" in response.headers.get("content-type", "")
        assert "username" in response.text
        assert "password" in response.text

    @pytest.mark.parametrize("route", ["/login", "/mcpd/login"])
    def test_login_page_already_authenticated_redirects(self, client, auth_token, route):
        client.cookies.set(SESSION_COOKIE_NAME, auth_token)
        response = client.get(route)
        assert response.status_code == 303
        assert response.headers["location"] == "/mcpd/bucket"

    def test_login_page_with_next_param_when_authenticated(self, client, auth_token):
        client.cookies.set(SESSION_COOKIE_NAME, auth_token)
        response = client.get("/login?next=/mcpd/dashboard")
        assert response.status_code == 303
        assert response.headers["location"] == "/mcpd/dashboard"

    def test_login_page_displays_lockout_warning_when_ip_locked(self, client):
        client_ip = "127.0.0.1"
        main.security_limiter.records[client_ip] = {
            "attempts": 5,
            "lockout_until": 9999999999.0,
            "last_attempt": 1000.0,
        }
        response = client.get("/login")
        assert response.status_code == 200
        assert "SECURITY LOCKOUT" in response.text


class TestLoginPostRoutes:
    @pytest.mark.parametrize("route", ["/login", "/mcpd/login"])
    def test_login_success_sets_cookie_and_redirects(self, client, route):
        data = {
            "username": AUTH_USERNAME,
            "password": AUTH_PASSWORD,
            "next": "/mcpd/bucket"
        }
        response = client.post(route, data=data)
        assert response.status_code == 303
        assert response.headers["location"] == "/mcpd/bucket"

        # Verify session cookie was set
        assert SESSION_COOKIE_NAME in response.cookies
        cookie_val = response.cookies[SESSION_COOKIE_NAME]
        assert main.verify_session_token(cookie_val) == AUTH_USERNAME

    def test_login_success_with_custom_next_redirect(self, client):
        data = {
            "username": AUTH_USERNAME,
            "password": AUTH_PASSWORD,
            "next": "/mcpd/custom-view"
        }
        response = client.post("/mcpd/login", data=data)
        assert response.status_code == 303
        assert response.headers["location"] == "/mcpd/custom-view"

    def test_login_failure_invalid_password(self, client):
        data = {
            "username": AUTH_USERNAME,
            "password": "wrong_password_123"
        }
        response = client.post("/login", data=data)
        assert response.status_code == 401
        assert "ACCESS DENIED" in response.text
        assert "attempt(s) remaining" in response.text
        assert SESSION_COOKIE_NAME not in response.cookies

    def test_login_failure_invalid_username(self, client):
        data = {
            "username": "unknown_operator",
            "password": AUTH_PASSWORD
        }
        response = client.post("/mcpd/login", data=data)
        assert response.status_code == 401
        assert "ACCESS DENIED" in response.text

    def test_login_lockout_on_fifth_failure(self, client):
        bad_data = {
            "username": AUTH_USERNAME,
            "password": "incorrect_password"
        }
        # First 4 attempts return 401
        for i in range(4):
            resp = client.post("/login", data=bad_data)
            assert resp.status_code == 401, f"Attempt {i+1} failed"

        # 5th attempt triggers lockout and returns 429
        resp5 = client.post("/login", data=bad_data)
        assert resp5.status_code == 429
        assert "Maximum login attempts exceeded" in resp5.text or "SECURITY LOCKOUT" in resp5.text

        # 6th attempt is blocked immediately by check_lockout
        resp6 = client.post("/login", data={"username": AUTH_USERNAME, "password": AUTH_PASSWORD})
        assert resp6.status_code == 429
        assert "temporarily locked" in resp6.text


class TestLogoutRoutes:
    @pytest.mark.parametrize("route", ["/logout", "/mcpd/logout"])
    def test_logout_redirects_and_deletes_cookie(self, client, auth_token, route):
        client.cookies.set(SESSION_COOKIE_NAME, auth_token)
        response = client.get(route)
        assert response.status_code == 303
        assert response.headers["location"] == "/mcpd/login"

        # Check Set-Cookie deletes agyc_session (max-age=0 or expires in past)
        set_cookie_header = response.headers.get("set-cookie", "")
        assert SESSION_COOKIE_NAME in set_cookie_header
        assert 'max-age=0' in set_cookie_header.lower() or 'expires=' in set_cookie_header.lower()
