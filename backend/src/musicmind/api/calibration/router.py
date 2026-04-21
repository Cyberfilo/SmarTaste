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
from musicmind.api.rate_limit import CALIBRATION_LIMIT, limiter
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
@limiter.limit(CALIBRATION_LIMIT)
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

    # Profile rebuild + enrichment both run in background so response is instant
    background_tasks.add_task(
        _background_post_calibration,
        engine=request.app.state.engine,
        encryption=request.app.state.encryption,
        settings=request.app.state.settings,
        user_id=current_user["user_id"],
        top_artist_names=[
            item.item_name for item in body.items
            if item.calibration_type == "top_artist"
        ],
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


@router.get("/entries")
async def get_calibration_entries(
    request: Request,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Return the user's current calibration selections for display/editing."""
    try:
        entries = await calibration_service.get_calibration_entries(
            request.app.state.engine,
            user_id=current_user["user_id"],
        )
    except Exception:
        logger.exception("Failed to fetch calibration entries")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch calibration entries",
        )

    return {"items": entries}


# ── Background Enrichment ─────────────────────────────────────────────────


async def _background_post_calibration(
    *,
    engine,
    encryption,
    settings,
    user_id: str,
    top_artist_names: list[str],
) -> None:
    """Run profile rebuild + top artist enrichment after calibration save."""
    # 1. Rebuild taste profile with new calibration weights
    try:
        from musicmind.api.taste.service import TasteService

        taste_svc = TasteService()
        await taste_svc.get_profile(
            engine, encryption, settings,
            user_id=user_id, force_refresh=True,
        )
        logger.info("Profile rebuilt after calibration for user %s", user_id)
    except Exception:
        logger.warning("Profile rebuild after calibration failed for %s", user_id)

    # 2. Enrich top artists' discographies
    if top_artist_names:
        await _enrich_top_artists_discography(
            engine=engine, encryption=encryption, settings=settings,
            user_id=user_id, artist_names=top_artist_names,
        )


async def _enrich_top_artists_discography(
    *,
    engine,
    encryption,
    settings,
    user_id: str,
    artist_names: list[str],
) -> None:
    """Fetch and enrich full discographies for top 3 calibrated artists.

    For each artist:
    1. Check which tracks are already cached in song_metadata_cache
    2. Only fetch from the service API if tracks are missing
    3. Cache newly fetched tracks into song_metadata_cache
    4. Run audio enrichment on all tracks (skips already-enriched ones)
    """
    import json

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
    from musicmind.db.schema import (
        audio_features_cache,
        song_metadata_cache,
    )
    from musicmind.db.schema import (
        service_connections as sc_table,
    )
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
    fetched_new = 0
    cached_hit = 0

    for artist_name in artist_names[:3]:
        try:
            # Check what we already have cached for this artist
            async with engine.begin() as conn:
                existing = await conn.execute(
                    sa.select(
                        song_metadata_cache.c.catalog_id,
                    ).where(
                        sa.and_(
                            song_metadata_cache.c.user_id == user_id,
                            sa.func.lower(song_metadata_cache.c.artist_name)
                            == artist_name.lower(),
                        )
                    )
                )
                cached_ids = {r.catalog_id for r in existing}

                # Also check which of those already have audio features
                enriched = await conn.execute(
                    sa.select(audio_features_cache.c.catalog_id).where(
                        sa.and_(
                            audio_features_cache.c.user_id == user_id,
                            audio_features_cache.c.catalog_id.in_(cached_ids)
                            if cached_ids
                            else sa.literal(False),
                            audio_features_cache.c.energy.isnot(None),
                        )
                    )
                )
                enriched_ids = {r.catalog_id for r in enriched}

            # If we have cached songs that need enrichment, use those
            if cached_ids - enriched_ids:
                unenriched = cached_ids - enriched_ids
                logger.info(
                    "%s: %d cached, %d need enrichment",
                    artist_name, len(cached_ids), len(unenriched),
                )
                async with engine.begin() as conn:
                    result = await conn.execute(
                        sa.select(song_metadata_cache).where(
                            sa.and_(
                                song_metadata_cache.c.user_id == user_id,
                                song_metadata_cache.c.catalog_id.in_(unenriched),
                            )
                        )
                    )
                    for r in result:
                        all_tracks.append({
                            "catalog_id": r.catalog_id,
                            "name": r.name,
                            "artist_name": r.artist_name,
                            "isrc": r.isrc,
                            "service_source": r.service_source,
                        })
                        cached_hit += 1

            # Fetch from API to get more tracks we don't have yet
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

            # Cache new tracks into song_metadata_cache and collect for enrichment
            new_tracks = [t for t in tracks if t.get("catalog_id") not in cached_ids]
            if new_tracks:
                async with engine.begin() as conn:
                    for track in new_tracks:
                        cid = track.get("catalog_id", "")
                        if not cid:
                            continue
                        # Check if already exists (race condition safety)
                        exists = await conn.execute(
                            sa.select(song_metadata_cache.c.catalog_id).where(
                                sa.and_(
                                    song_metadata_cache.c.catalog_id == cid,
                                    song_metadata_cache.c.user_id == user_id,
                                )
                            )
                        )
                        if exists.first():
                            continue

                        await conn.execute(
                            song_metadata_cache.insert().values(
                                catalog_id=cid,
                                user_id=user_id,
                                name=track.get("name", ""),
                                artist_name=track.get("artist_name", ""),
                                album_name=track.get("album_name", ""),
                                genre_names=json.dumps(
                                    track.get("genre_names", [])
                                ),
                                duration_ms=track.get("duration_ms"),
                                release_date=track.get("release_date"),
                                isrc=track.get("isrc"),
                                preview_url=track.get("preview_url", ""),
                                service_source=service,
                            )
                        )
                        fetched_new += 1

                all_tracks.extend(new_tracks)

        except Exception:
            logger.warning("Failed to process discography for %s", artist_name)

    if all_tracks:
        logger.info(
            "Top artist enrichment for %s: %d tracks (%d from cache, %d newly fetched)",
            user_id, len(all_tracks), cached_hit, fetched_new,
        )
        await enrich_tracks(
            engine, all_tracks, user_id=user_id,
        )
