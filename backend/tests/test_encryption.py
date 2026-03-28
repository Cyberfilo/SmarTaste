"""Tests for EncryptionService and Settings configuration."""

from __future__ import annotations

import pytest

from cryptography.fernet import InvalidToken

from musicmind.security.encryption import EncryptionService


def test_encrypt_decrypt_roundtrip(encryption_service: EncryptionService) -> None:
    """Encrypting then decrypting returns the original plaintext."""
    original = "sk-ant-api03-test-key-1234567890abcdef"
    encrypted = encryption_service.encrypt(original)
    decrypted = encryption_service.decrypt(encrypted)
    assert decrypted == original


def test_encrypted_not_plaintext(encryption_service: EncryptionService) -> None:
    """Encrypted output must differ from the plaintext input."""
    plaintext = "my-secret-api-key"
    encrypted = encryption_service.encrypt(plaintext)
    assert encrypted != plaintext


def test_generate_key_produces_valid_key() -> None:
    """A generated key can be used to instantiate EncryptionService and round-trip data."""
    key = EncryptionService.generate_key()
    svc = EncryptionService(key)
    assert svc.decrypt(svc.encrypt("test")) == "test"


def test_decrypt_wrong_key_raises() -> None:
    """Decrypting with a different key raises InvalidToken."""
    key_a = EncryptionService.generate_key()
    key_b = EncryptionService.generate_key()
    svc_a = EncryptionService(key_a)
    svc_b = EncryptionService(key_b)
    encrypted = svc_a.encrypt("secret-data")
    with pytest.raises(InvalidToken):
        svc_b.decrypt(encrypted)


def test_encrypt_empty_string(encryption_service: EncryptionService) -> None:
    """Encrypting an empty string round-trips back to empty string."""
    encrypted = encryption_service.encrypt("")
    assert encryption_service.decrypt(encrypted) == ""


def test_settings_loads_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Settings loads database_url and fernet_key from MUSICMIND_ prefixed env vars."""
    monkeypatch.setenv("MUSICMIND_DATABASE_URL", "postgresql+asyncpg://test:test@db:5432/testdb")
    monkeypatch.setenv("MUSICMIND_FERNET_KEY", EncryptionService.generate_key())
    monkeypatch.setenv("MUSICMIND_JWT_SECRET_KEY", "test-jwt-secret-for-settings-test")
    monkeypatch.delenv("MUSICMIND_DEBUG", raising=False)
    monkeypatch.delenv("MUSICMIND_LOG_LEVEL", raising=False)
    monkeypatch.delenv("MUSICMIND_SANDBOX", raising=False)

    from musicmind.config import Settings

    # _env_file=None prevents loading .env which would override test env vars
    settings = Settings(_env_file=None)
    assert settings.database_url == "postgresql+asyncpg://test:test@db:5432/testdb"
    assert settings.fernet_key  # non-empty
    assert settings.debug is False  # default
    assert settings.log_level == "INFO"  # default
