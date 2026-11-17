import pytest
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient
import main

@pytest.fixture(autouse=True)
def clean_security_limiter():
    """Ensure rate limiter records are empty before and after every test, and mock sleep."""
    main.security_limiter.records.clear()
    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        yield mock_sleep
    main.security_limiter.records.clear()

@pytest.fixture
def client():
    """Test client configured to not follow redirects by default."""
    return TestClient(main.app, follow_redirects=False)

@pytest.fixture
def auth_token():
    """Generates a valid signed session token for AUTH_USERNAME."""
    return main.create_session_token(main.AUTH_USERNAME)

@pytest.fixture
def auth_cookies(auth_token):
    """Cookie dictionary with valid agyc_session."""
    return {main.SESSION_COOKIE_NAME: auth_token}

@pytest.fixture
def auth_client(client, auth_cookies):
    """Test client with authenticated session cookie pre-configured."""
    client.cookies.set(main.SESSION_COOKIE_NAME, auth_cookies[main.SESSION_COOKIE_NAME])
    return client
