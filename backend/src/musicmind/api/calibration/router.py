"""Calibration API endpoints for onboarding taste wizard."""

from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, status

from musicmind.api.calibration.schemas import (
    AlbumOut,
    AlbumsResponse,
    ArtistCalibrationEntry,
    ArtistsResponse,
    CalibrationStatusResponse,
    SaveCalibrationRequest,
)
from musicmind.api.calibration.service import CalibrationService
from musicmind.auth.dependencies import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/calibration", tags=["calibration"])
calibration_service = CalibrationService()


@router.get("/albums")
async def get_albums(
    request: Request,
    service: str | None = Query(default=None),
    current_user: dict = Depends(get_current_user),
) -> AlbumsResponse:
    """Fetch the user's library albums for calibration step 1.

    Returns albums with artwork for the selection grid.
    """
    try:
        albums = await calibration_service.get_albums(
            request.app.state.engine,
            request.app.state.encryption,
            request.app.state.settings,
            user_id=current_user["user_id"],
            service=service,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except Exception:
        logger.exception("Failed to fetch albums for calibration")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch albums",
        )

    items = [AlbumOut(**a) for a in albums]
    return AlbumsResponse(albums=items, total=len(items))


@router.get("/artists")
async def get_artists(
    request: Request,
    current_user: dict = Depends(get_current_user),
) -> ArtistsResponse:
    """Get top artists for calibration step 2 (confirm/reject).

    Returns artists sorted by affinity score from the taste profile.
    """
    try:
        artists, service = await calibration_service.get_artists(
            request.app.state.engine,
            request.app.state.encryption,
            request.app.state.settings,
            user_id=current_user["user_id"],
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except Exception:
        logger.exception("Failed to fetch artists for calibration")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch artists",
        )

    items = [
        ArtistCalibrationEntry(
            name=a["name"] if isinstance(a, dict) else a.name,
            score=a["score"] if isinstance(a, dict) else a.score,
            song_count=a["song_count"] if isinstance(a, dict) else a.song_count,
        )
        for a in artists[:20]
    ]
    return ArtistsResponse(artists=items, service=service)


@router.post("/save")
async def save_calibration(
    request: Request,
    body: SaveCalibrationRequest,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Save all calibration selections from the onboarding wizard.

    Replaces any existing calibration. After saving, triggers:
    1. Profile rebuild so weights take effect immediately
    2. Background discography enrichment for top 3 artists
    """
    try:
        count = await calibration_service.save_calibration(
            request.app.state.engine,
            user_id=current_user["user_id"],
            items=[item.model_dump() for item in body.items],
        )
    except Exception:
        logger.exception("Failed to save calibration")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to save calibration",
        )

    # Trigger profile rebuild so calibration weights take effect
    from musicmind.api.taste.service import TasteService

    taste_svc = TasteService()
    try:
        await taste_svc.get_profile(
            request.app.state.engine,
            request.app.state.encryption,
            request.app.state.settings,
            user_id=current_user["user_id"],
            force_refresh=True,
        )
    except Exception:
        logger.warning("Profile rebuild after calibration failed, will rebuild on next access")

    # Enrich top 3 artists' full discographies in background
    top_artist_names = [
        item.item_name for item in body.items
        if item.calibration_type == "top_artist"
    ]
    if top_artist_names:
        background_tasks.add_task(
            _enrich_top_artists_discography,
            engine=request.app.state.engine,
            encryption=request.app.state.encryption,
            settings=request.app.state.settings,
            user_id=current_user["user_id"],
            artist_names=top_artist_names,
        )

    return {"message": "Calibration saved", "items_saved": count}


@router.get("/status")
async def get_calibration_status(
    request: Request,
    current_user: dict = Depends(get_current_user),
) -> CalibrationStatusResponse:
    """Check if the user has completed the onboarding calibration."""
    try:
        result = await calibration_service.get_calibration_status(
            request.app.state.engine,
            user_id=current_user["user_id"],
        )
    except Exception:
        logger.exception("Failed to check calibration status")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to check calibration status",
        )

    return CalibrationStatusResponse(**result)


# ── Background Enrichment ─────────────────────────────────────────────────


async def _enrich_top_artists_discography(
    *,
    engine,
    encryption,
    settings,
    user_id: str,
    artist_names: list[str],
) -> None:
    """Fetch and enrich full discographies for top 3 calibrated artists.

    Searches each artist by name, fetches their top songs, and runs audio
    enrichment so the recommendation engine has rich features for these
    high-priority artists.
    """
    import httpx
    import sqlalchemy as sa

    from musicmind.api.recommendations.fetch import (
        _fetch_artist_top_tracks,
        _search_artist_id,
    )
    from musicmind.api.services.service import (
        detect_apple_music_storefront,
        generate_apple_developer_token,
        get_user_connections,
    )
    from musicmind.db.schema import service_connections as sc_table
    from musicmind.engine.enrichment.orchestrator import enrich_tracks

    connections = await get_user_connections(engine, user_id=user_id)
    if not connections:
        return

    conn_data = connections[0]
    service = conn_data["service"]

    async with engine.begin() as conn:
        result = await conn.execute(
            sa.select(sc_table).where(
                sa.and_(sc_table.c.user_id == user_id, sc_table.c.service == service)
            )
        )
        row = result.first()

    if not row:
        return

    access_token = encryption.decrypt(row.access_token_encrypted)
    developer_token = None
    storefront = "us"

    if service == "apple_music":
        developer_token = generate_apple_developer_token(
            settings.apple_team_id, settings.apple_key_id,
            settings.apple_private_key_path,
            private_key_b64=settings.apple_private_key_b64,
        )
        storefront = await detect_apple_music_storefront(
            access_token, developer_token,
        )

    all_tracks: list[dict] = []
    for artist_name in artist_names[:3]:
        try:
            artist_id = await _search_artist_id(
                service, access_token, artist_name,
                developer_token=developer_token,
                storefront=storefront,
            )
            if not artist_id:
                continue

            async with httpx.AsyncClient(timeout=15.0) as client:
                tracks = await _fetch_artist_top_tracks(
                    client, service, access_token, artist_id,
                    developer_token=developer_token,
                    storefront=storefront,
                    limit=25,
                )
                all_tracks.extend(tracks)
        except Exception:
            logger.debug("Failed to fetch discography for %s", artist_name)

    if all_tracks:
        logger.info(
            "Enriching %d tracks from top %d calibrated artists for user %s",
            len(all_tracks), len(artist_names[:3]), user_id,
        )
        await enrich_tracks(
            engine, all_tracks, user_id=user_id,
            soundstat_api_key=settings.soundstat_api_key,
        )
