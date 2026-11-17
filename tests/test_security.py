import time
import hmac
import hashlib
import pytest
from unittest.mock import MagicMock
import main
from main import (
    create_session_token,
    verify_session_token,
    SecurityRateLimiter,
    AUTH_USERNAME,
    SECRET_KEY,
    SESSION_MAX_AGE,
)

class TestSessionTokens:
    def test_create_session_token_format(self):
        token = create_session_token(AUTH_USERNAME)
        parts = token.split(":")
        assert len(parts) == 3
        username, ts_str, sig = parts
        assert username == AUTH_USERNAME
        assert ts_str.isdigit()
        assert len(sig) == 64  # SHA256 hex string length

    def test_verify_session_token_valid(self):
        token = create_session_token(AUTH_USERNAME)
        verified_user = verify_session_token(token)
        assert verified_user == AUTH_USERNAME

    def test_verify_session_token_empty_and_none(self):
        assert verify_session_token(None) is None
        assert verify_session_token("") is None
        assert verify_session_token("   ") is None

    def test_verify_session_token_malformed_structure(self):
        assert verify_session_token("singlepart") is None
        assert verify_session_token("two:parts") is None
        assert verify_session_token("four:parts:extra:item") is None

    def test_verify_session_token_invalid_timestamp(self):
        token = f"{AUTH_USERNAME}:not_a_number:somesignature"
        assert verify_session_token(token) is None

    def test_verify_session_token_expired(self):
        past_ts = int(time.time()) - (SESSION_MAX_AGE + 100)
        payload = f"{AUTH_USERNAME}:{past_ts}"
        sig = hmac.new(SECRET_KEY.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
        expired_token = f"{payload}:{sig}"
        assert verify_session_token(expired_token) is None

    def test_verify_session_token_tampered_signature(self):
        token = create_session_token(AUTH_USERNAME)
        parts = token.split(":")
        # Invert last character of signature
        tampered_sig = parts[2][:-1] + ("0" if parts[2][-1] != "0" else "1")
        tampered_token = f"{parts[0]}:{parts[1]}:{tampered_sig}"
        assert verify_session_token(tampered_token) is None

    def test_verify_session_token_tampered_username(self):
        ts = str(int(time.time()))
        # Sign for attacker
        attacker = "attacker"
        payload = f"{attacker}:{ts}"
        sig = hmac.new(SECRET_KEY.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
        attacker_token = f"{payload}:{sig}"
        # Even with valid signature for attacker, verify_session_token must reject
        assert verify_session_token(attacker_token) is None

    def test_verify_session_token_tampered_payload_with_original_sig(self):
        token = create_session_token(AUTH_USERNAME)
        parts = token.split(":")
        # Replace username in token but keep original signature
        tampered_token = f"otheruser:{parts[1]}:{parts[2]}"
        assert verify_session_token(tampered_token) is None


class TestSecurityRateLimiter:
    def test_get_client_ip_forwarded_for_single(self):
        limiter = SecurityRateLimiter()
        req = MagicMock()
        req.headers = {"x-forwarded-for": "203.0.113.195"}
        assert limiter.get_client_ip(req) == "203.0.113.195"

    def test_get_client_ip_forwarded_for_multiple(self):
        limiter = SecurityRateLimiter()
        req = MagicMock()
        req.headers = {"x-forwarded-for": "198.51.100.1, 10.0.0.1, 172.16.0.1"}
        assert limiter.get_client_ip(req) == "198.51.100.1"

    def test_get_client_ip_real_ip(self):
        limiter = SecurityRateLimiter()
        req = MagicMock()
        req.headers = {"x-real-ip": "198.51.100.42"}
        assert limiter.get_client_ip(req) == "198.51.100.42"

    def test_get_client_ip_client_host(self):
        limiter = SecurityRateLimiter()
        req = MagicMock()
        req.headers = {}
        req.client.host = "192.168.1.50"
        assert limiter.get_client_ip(req) == "192.168.1.50"

    def test_get_client_ip_fallback(self):
        limiter = SecurityRateLimiter()
        req = MagicMock()
        req.headers = {}
        req.client = None
        assert limiter.get_client_ip(req) == "127.0.0.1"

    @pytest.mark.asyncio
    async def test_register_failure_progression_and_lockout(self):
        limiter = SecurityRateLimiter()
        ip = "192.0.2.1"

        # 1st failure: delay = 2^0 = 1.0s, remaining = 4
        count, remaining, delay = await limiter.register_failure(ip)
        assert count == 1
        assert remaining == 4
        assert delay == 1.0
        locked, _ = limiter.check_lockout(ip)
        assert locked is False

        # 2nd failure: delay = 2^1 = 2.0s, remaining = 3
        count, remaining, delay = await limiter.register_failure(ip)
        assert count == 2
        assert remaining == 3
        assert delay == 2.0

        # 3rd failure: delay = 2^2 = 4.0s, remaining = 2
        count, remaining, delay = await limiter.register_failure(ip)
        assert count == 3
        assert remaining == 2
        assert delay == 4.0

        # 4th failure: delay = 2^3 = 8.0s, remaining = 1
        count, remaining, delay = await limiter.register_failure(ip)
        assert count == 4
        assert remaining == 1
        assert delay == 8.0

        # 5th failure: delay capped at 8.0s, remaining = 0, triggers lockout
        count, remaining, delay = await limiter.register_failure(ip)
        assert count == 5
        assert remaining == 0
        assert delay == 8.0

        # Verify lockout state
        locked, remaining_secs = limiter.check_lockout(ip)
        assert locked is True
        assert 800 < remaining_secs <= 900

    def test_register_success_clears_records(self):
        limiter = SecurityRateLimiter()
        ip = "192.0.2.2"
        limiter.records[ip] = {"attempts": 3, "lockout_until": 0, "last_attempt": time.time()}
        limiter.register_success(ip)
        assert ip not in limiter.records

    def test_lockout_expiration_clears_ip(self):
        limiter = SecurityRateLimiter()
        ip = "192.0.2.3"
        # Simulate lockout expired 10 seconds ago
        limiter.records[ip] = {
            "attempts": 5,
            "lockout_until": time.time() - 10,
            "last_attempt": time.time() - 910
        }
        locked, remaining = limiter.check_lockout(ip)
        assert locked is False
        assert remaining == 0
        assert ip not in limiter.records

    @pytest.mark.asyncio
    async def test_failure_window_reset_after_one_hour(self):
        limiter = SecurityRateLimiter()
        ip = "192.0.2.4"
        # Record an attempt from 3601 seconds ago
        limiter.records[ip] = {
            "attempts": 4,
            "lockout_until": 0,
            "last_attempt": time.time() - 3601
        }
        # Next attempt should reset counter to 1
        count, remaining, delay = await limiter.register_failure(ip)
        assert count == 1
        assert remaining == 4
        assert delay == 1.0
