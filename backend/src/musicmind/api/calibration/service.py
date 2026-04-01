"""Calibration service — fetch albums, save selections, check status."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa

from musicmind.api.calibration.fetch import (
    fetch_apple_music_albums,
    fetch_spotify_albums,
)
from musicmind.api.services.service import (
    generate_apple_developer_token,
    get_user_connections,
    refresh_spotify_token,
    upsert_service_connection,
)
from musicmind.db.schema import service_connections, user_calibration
from musicmind.security.encryption import EncryptionService

logger = logging.getLogger(__name__)


class CalibrationService:
    """Orchestrates calibration data fetching and persistence."""

    async def get_albums(
        self,
        engine,
        encryption: EncryptionService,
        settings,
        *,
        user_id: str,
        service: str | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch library albums from connected service(s)."""
        connections = await get_user_connections(engine, user_id=user_id)
        if not connections:
            raise ValueError("No connected service found")

        all_albums: list[dict[str, Any]] = []

        for conn in connections:
            svc = conn["service"]
            if service and svc != service:
                continue

            async with engine.begin() as db_conn:
                result = await db_conn.execute(
                    sa.select(service_connections).where(
                        sa.and_(
                            service_connections.c.user_id == user_id,
                            service_connections.c.service == svc,
                        )
                    )
                )
                row = result.first()

            if not row:
                continue

            access_token = encryption.decrypt(row.access_token_encrypted)

            if svc == "spotify":
                access_token = await self._ensure_spotify_token(
                    engine, encryption, settings,
                    user_id=user_id, row=row, access_token=access_token,
                )
                albums = await fetch_spotify_albums(access_token)
                all_albums.extend(albums)

            elif svc == "apple_music":
                developer_token = generate_apple_developer_token(
                    settings.apple_team_id,
                    settings.apple_key_id,
                    settings.apple_private_key_path,
                    private_key_b64=settings.apple_private_key_b64,
                )
                albums = await fetch_apple_music_albums(developer_token, access_token)
                all_albums.extend(albums)

        return all_albums

    async def get_artists(
        self,
        engine,
        encryption: EncryptionService,
        settings,
        *,
        user_id: str,
    ) -> tuple[list[dict[str, Any]], str]:
        """Get top artists from the taste profile for confirm/reject step."""
        from musicmind.api.taste.service import TasteService

        taste_svc = TasteService()
        profile = await taste_svc.get_profile(
            engine, encryption, settings,
            user_id=user_id,
        )
        artists = profile.get("top_artists", [])
        service = profile.get("service", "")
        return artists, service

    async def save_calibration(
        self,
        engine,
        *,
        user_id: str,
        items: list[dict[str, Any]],
    ) -> int:
        """Save calibration selections, replacing any existing ones."""
        async with engine.begin() as conn:
            # Clear existing calibration for this user
            await conn.execute(
                user_calibration.delete().where(
                    user_calibration.c.user_id == user_id
                )
            )

            # Insert new selections, dedup by (calibration_type, item_id)
            seen: set[tuple[str, str]] = set()
            inserted = 0
            for item in items:
                key = (item["calibration_type"], item["item_id"])
                if key in seen:
                    continue
                seen.add(key)
                await conn.execute(
                    user_calibration.insert().values(
                        user_id=user_id,
                        calibration_type=item["calibration_type"],
                        item_id=item["item_id"],
                        item_name=item.get("item_name", ""),
                        weight=item["weight"],
                    )
                )
                inserted += 1

        return inserted

    async def get_calibration_status(
        self,
        engine,
        *,
        user_id: str,
    ) -> dict[str, Any]:
        """Check if the user has completed calibration."""
        async with engine.begin() as conn:
            result = await conn.execute(
                sa.select(sa.func.count()).select_from(user_calibration).where(
                    user_calibration.c.user_id == user_id
                )
            )
            count = result.scalar() or 0

        return {
            "completed": count > 0,
            "item_count": count,
        }

    async def get_calibration_entries(
        self,
        engine,
        *,
        user_id: str,
    ) -> list[dict[str, Any]]:
        """Load all calibration entries for a user (used by profile builder)."""
        async with engine.begin() as conn:
            result = await conn.execute(
                sa.select(user_calibration).where(
                    user_calibration.c.user_id == user_id
                )
            )
            rows = result.fetchall()

        return [
            {
                "calibration_type": row.calibration_type,
                "item_id": row.item_id,
                "item_name": row.item_name,
                "weight": row.weight,
            }
            for row in rows
        ]

    async def _ensure_spotify_token(
        self, engine, encryption, settings, *, user_id, row, access_token,
    ) -> str:
        """Refresh Spotify token if expired."""
        from datetime import timedelta

        token_expires_at = row.token_expires_at
        now = datetime.now(UTC)
        if token_expires_at is not None:
            if token_expires_at.tzinfo is None:
                token_expires_at = token_expires_at.replace(tzinfo=UTC)
            if token_expires_at < now + timedelta(seconds=60):
                refresh_token_encrypted = row.refresh_token_encrypted
                if refresh_token_encrypted:
                    refresh_token_value = encryption.decrypt(refresh_token_encrypted)
                    token_data = await refresh_spotify_token(
                        refresh_token_value, settings.spotify_client_id
                    )
                    if token_data:
                        access_token = token_data["access_token"]
                        await upsert_service_connection(
                            engine, encryption,
                            user_id=user_id,
                            service="spotify",
                            access_token=access_token,
                            refresh_token=token_data.get(
                                "refresh_token", refresh_token_value
                            ),
                            expires_in=token_data.get("expires_in"),
                            service_user_id=row.service_user_id,
                        )
        return access_token
