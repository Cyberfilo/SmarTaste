"""Fernet symmetric encryption for sensitive values at rest.

Uses cryptography.fernet.Fernet which provides AES-128-CBC + HMAC-SHA256.
Encrypted values are base64-encoded strings safe for database TEXT columns.
"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

__all__ = ["EncryptionService", "InvalidToken"]


class EncryptionService:
    """Encrypts and decrypts sensitive values using Fernet symmetric encryption."""

    def __init__(self, key: str) -> None:
        """Initialize with a Fernet key (base64-encoded 32-byte key).

        Args:
            key: Fernet key string. Generate with EncryptionService.generate_key().
        """
        self._fernet = Fernet(key.encode() if isinstance(key, str) else key)

    def encrypt(self, plaintext: str) -> str:
        """Encrypt a plaintext string. Returns base64-encoded ciphertext."""
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, ciphertext: str) -> str:
        """Decrypt a ciphertext string. Returns original plaintext.

        Raises:
            cryptography.fernet.InvalidToken: If the key is wrong or data is corrupted.
        """
        try:
            return self._fernet.decrypt(ciphertext.encode()).decode()
        except InvalidToken:
            raise ValueError(
                "Token decryption failed — Fernet key may have changed or data is corrupted. "
                "The user should reconnect their service."
            ) from None

    def decrypt_or_none(self, ciphertext: str | None) -> str | None:
        """Decrypt a ciphertext string, returning None on failure instead of raising.

        Use this in non-critical paths (logging, background tasks) where a
        corrupted token should not crash the operation.
        """
        if not ciphertext:
            return None
        try:
            return self._fernet.decrypt(ciphertext.encode()).decode()
        except (InvalidToken, Exception):
            return None

    @staticmethod
    def generate_key() -> str:
        """Generate a new Fernet key. Store this securely in environment variables."""
        return Fernet.generate_key().decode()
