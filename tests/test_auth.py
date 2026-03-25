"""Tests for auth module — ES256 JWT developer token generation."""

from __future__ import annotations

import time
from unittest.mock import patch

import jwt
import pytest

from musicmind.auth import AuthManager, TOKEN_EXPIRY_SECONDS
from musicmind.config import MusicMindConfig

# Test ES256 key pair — P-256 / PKCS8 format (DO NOT use in production)
TEST_PRIVATE_KEY = """-----BEGIN PRIVATE KEY-----
MIGHAgEAMBMGByqGSM49AgEGCCqGSM49AwEHBG0wawIBAQQgYPl/uVchiF+q/y39
eKZGVJUt4U2E7zwR0q/TFVJoTgGhRANCAAR11w5DMXuEIm+ByrJ/nE6l7qo7Ln0w
Ie22tgn21jFF7iqS1Zvh5Sk2Ku8p6jnCuraYmGNcpmeJGsKo8LmdgWTh
-----END PRIVATE KEY-----"""


@pytest.fixture
def config(tmp_path) -> MusicMindConfig:
    """Create a test config with a temporary key file."""
    key_file = tmp_path / "AuthKey_TEST123.p8"
    key_file.write_text(TEST_PRIVATE_KEY)
    return MusicMindConfig(
        team_id="TEAM123456",
        key_id="KEY1234567",
        private_key_path=str(key_file),
        music_user_token="test-user-token-abc",
        storefront="it",
    )


@pytest.fixture
def config_no_user_token(tmp_path) -> MusicMindConfig:
    key_file = tmp_path / "AuthKey_TEST123.p8"
    key_file.write_text(TEST_PRIVATE_KEY)
    return MusicMindConfig(
        team_id="TEAM123456",
        key_id="KEY1234567",
        private_key_path=str(key_file),
        storefront="it",
    )


class TestAuthManager:
    def test_developer_token_is_valid_jwt(self, config: MusicMindConfig) -> None:
        auth = AuthManager(config)
        token = auth.developer_token

        # Decode without verification to inspect claims
        decoded = jwt.decode(token, options={"verify_signature": False})
        assert decoded["iss"] == "TEAM123456"
        assert "iat" in decoded
        assert "exp" in decoded
        assert decoded["exp"] - decoded["iat"] == TOKEN_EXPIRY_SECONDS

    def test_developer_token_has_correct_headers(self, config: MusicMindConfig) -> None:
        auth = AuthManager(config)
        token = auth.developer_token

        header = jwt.get_unverified_header(token)
        assert header["alg"] == "ES256"
        assert header["kid"] == "KEY1234567"

    def test_developer_token_is_cached(self, config: MusicMindConfig) -> None:
        auth = AuthManager(config)
        token1 = auth.developer_token
        token2 = auth.developer_token
        assert token1 == token2

    def test_developer_token_regenerates_when_expired(self, config: MusicMindConfig) -> None:
        auth = AuthManager(config)
        token1 = auth.developer_token

        # Simulate expiry
        auth._developer_token_expiry = time.time() - 1
        token2 = auth.developer_token
        assert token1 != token2

    def test_music_user_token(self, config: MusicMindConfig) -> None:
        auth = AuthManager(config)
        assert auth.music_user_token == "test-user-token-abc"

    def test_music_user_token_raises_when_missing(
        self, config_no_user_token: MusicMindConfig
    ) -> None:
        auth = AuthManager(config_no_user_token)
        with pytest.raises(ValueError, match="Music User Token not configured"):
            _ = auth.music_user_token

    def test_auth_headers(self, config: MusicMindConfig) -> None:
        auth = AuthManager(config)
        headers = auth.auth_headers()

        assert "Authorization" in headers
        assert headers["Authorization"].startswith("Bearer ")
        assert headers["Music-User-Token"] == "test-user-token-abc"

    def test_auth_headers_without_user_token(
        self, config_no_user_token: MusicMindConfig
    ) -> None:
        auth = AuthManager(config_no_user_token)
        headers = auth.auth_headers()

        assert "Authorization" in headers
        assert "Music-User-Token" not in headers
