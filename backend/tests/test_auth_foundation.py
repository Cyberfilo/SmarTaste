"""Tests for auth foundation: Settings JWT fields and refresh_tokens schema."""

from __future__ import annotations

import os

import sqlalchemy as sa

from musicmind.db.schema import metadata, refresh_tokens


class TestSettingsJwtFields:
    """Verify Settings has JWT configuration fields."""

    def test_jwt_secret_key_required(self) -> None:
        """Settings requires jwt_secret_key with no default value."""
        from musicmind.config import Settings

        # Must be loadable when env var is set
        env = {
            "MUSICMIND_FERNET_KEY": "test-fernet-key-value-32-bytes==",
            "MUSICMIND_JWT_SECRET_KEY": "test-jwt-secret-for-unit-tests",
        }
        for k, v in env.items():
            os.environ[k] = v
        try:
            settings = Settings()
            assert settings.jwt_secret_key == "test-jwt-secret-for-unit-tests"
        finally:
            for k in env:
                os.environ.pop(k, None)

    def test_jwt_secret_key_loads_from_env(self) -> None:
        """Settings loads jwt_secret_key from MUSICMIND_JWT_SECRET_KEY env var."""
        from musicmind.config import Settings

        env = {
            "MUSICMIND_FERNET_KEY": "test-fernet-key-value-32-bytes==",
            "MUSICMIND_JWT_SECRET_KEY": "my-secret-123",
        }
        for k, v in env.items():
            os.environ[k] = v
        try:
            settings = Settings()
            assert settings.jwt_secret_key == "my-secret-123"
        finally:
            for k in env:
                os.environ.pop(k, None)

    def test_jwt_algorithm_defaults_to_hs256(self) -> None:
        """Settings has jwt_algorithm field defaulting to HS256."""
        from musicmind.config import Settings

        env = {
            "MUSICMIND_FERNET_KEY": "test-fernet-key-value-32-bytes==",
            "MUSICMIND_JWT_SECRET_KEY": "test-jwt-secret-for-unit-tests",
        }
        for k, v in env.items():
            os.environ[k] = v
        try:
            settings = Settings()
            assert settings.jwt_algorithm == "HS256"
        finally:
            for k in env:
                os.environ.pop(k, None)


class TestRefreshTokensTable:
    """Verify refresh_tokens table exists with correct schema."""

    def test_refresh_tokens_table_exists(self) -> None:
        """refresh_tokens table exists in metadata."""
        assert "refresh_tokens" in metadata.tables

    def test_refresh_tokens_has_id_pk(self) -> None:
        """refresh_tokens has id column as Text primary key."""
        col = refresh_tokens.c.id
        assert col.primary_key
        assert isinstance(col.type, sa.Text)

    def test_refresh_tokens_has_user_id_fk(self) -> None:
        """refresh_tokens has user_id as FK to users.id with CASCADE."""
        col = refresh_tokens.c.user_id
        assert not col.nullable
        fks = list(col.foreign_keys)
        assert len(fks) == 1
        fk = fks[0]
        assert fk.target_fullname == "users.id"
        assert fk.ondelete == "CASCADE"

    def test_refresh_tokens_user_id_indexed(self) -> None:
        """refresh_tokens.user_id has index=True."""
        col = refresh_tokens.c.user_id
        assert col.index is True

    def test_refresh_tokens_has_expires_at(self) -> None:
        """refresh_tokens has expires_at DateTime(timezone=True) NOT NULL."""
        col = refresh_tokens.c.expires_at
        assert not col.nullable
        assert isinstance(col.type, sa.DateTime)
        assert col.type.timezone is True

    def test_refresh_tokens_has_revoked(self) -> None:
        """refresh_tokens has revoked Boolean with server_default false."""
        col = refresh_tokens.c.revoked
        assert not col.nullable
        assert isinstance(col.type, sa.Boolean)
        assert col.server_default is not None
        default_text = str(col.server_default.arg)
        assert "false" in default_text.lower()

    def test_refresh_tokens_has_created_at(self) -> None:
        """refresh_tokens has created_at DateTime(tz) with server_default now."""
        col = refresh_tokens.c.created_at
        assert not col.nullable
        assert isinstance(col.type, sa.DateTime)
        assert col.type.timezone is True
        assert col.server_default is not None
