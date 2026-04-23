"""Taste profile API endpoints."""

from __future__ import annotations

import logging

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Query,
    Request,
    status,
)

from musicmind.api.rate_limit import TASTE_LIMIT, limiter
from musicmind.api.taste.insights import (
    clean_genre_vector,
    compute_breadth_metrics,
    compute_sonic_neighbors,
    get_artist_artworks,
    get_library_distributions,
    get_recent_enrichments,
)
from musicmind.api.taste.schemas import (
    ArtistEntry,
    AudioTraitsResponse,
    BreadthMetrics,
    GenreEntry,
    LibraryDistributionsResponse,
    RecentEnrichment,
    RecentEnrichmentsResponse,
    SonicNeighbor,
    SonicNeighborsResponse,
    TasteProfileResponse,
    TopArtistsResponse,
    TopGenresResponse,
)
from musicmind.api.taste.service import TasteService
from musicmind.auth.dependencies import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/taste", tags=["taste"])
taste_service = TasteService()

TASTE_REBUILD_STEP = 99  # Dedicated step number for taste rebuild status


async def _get_rebuild_status(engine, user_id: str) -> str:
    """Read taste rebuild status from user_indexing_status (step=99)."""
    import sqlalchemy as sa

    from musicmind.db.schema import user_indexing_status

    async with engine.begin() as conn:
        row = (await conn.execute(
            sa.select(user_indexing_status.c.step_name).where(
                sa.and_(
                    user_indexing_status.c.user_id == user_id,
                    user_indexing_status.c.step == TASTE_REBUILD_STEP,
                )
            )
        )).first()
    if row is None:
        return "idle"
    return row.step_name or "idle"


async def _set_rebuild_status(engine, user_id: str, status_val: str) -> None:
    """Persist taste rebuild status to user_indexing_status (step=99)."""
    from datetime import UTC, datetime

    import sqlalchemy as sa

    from musicmind.db.schema import user_indexing_status

    now = datetime.now(UTC)
    try:
        async with engine.begin() as conn:
            existing = (await conn.execute(
                sa.select(user_indexing_status.c.user_id).where(
                    sa.and_(
                        user_indexing_status.c.user_id == user_id,
                        user_indexing_status.c.step == TASTE_REBUILD_STEP,
                    )
                )
            )).first()
            if existing:
                await conn.execute(
                    sa.update(user_indexing_status).where(
                        sa.and_(
                            user_indexing_status.c.user_id == user_id,
                            user_indexing_status.c.step == TASTE_REBUILD_STEP,
                        )
                    ).values(step_name=status_val, updated_at=now)
                )
            else:
                await conn.execute(
                    user_indexing_status.insert().values(
                        user_id=user_id, step=TASTE_REBUILD_STEP,
                        step_name=status_val,
                        progress_current=0, progress_total=0,
                        started_at=now, updated_at=now,
                    )
                )
    except Exception:
        logger.debug("Failed to persist rebuild status for %s", user_id[:8])


@router.get("/profile")
@limiter.limit(TASTE_LIMIT)
async def get_profile(
    request: Request,
    background_tasks: BackgroundTasks,
    service: str | None = Query(default=None),
    refresh: bool = Query(default=False),
    current_user: dict = Depends(get_current_user),
) -> TasteProfileResponse:
    """Get the full taste profile for the authenticated user.

    Returns instantly from cache (even stale). If stale, triggers
    a background refresh so the next request gets fresh data.
    Never blocks the user waiting for API re-fetch.
    """
    # V 6.411 — per-step perf instrumentation.
    import time as _time
    _perf: dict[str, float] = {}
    _t_start = _time.perf_counter()
    _t_last = _t_start

    def _lap(step: str) -> None:
        nonlocal _t_last
        now = _time.perf_counter()
        _perf[step] = (now - _t_last) * 1000.0
        _t_last = now

    try:
        profile = await taste_service.get_profile(
            request.app.state.engine,
            request.app.state.encryption,
            request.app.state.settings,
            user_id=current_user["user_id"],
            service=service,
            force_refresh=refresh,
        )
        _lap("profile")

        # If profile is stale (>24h), trigger background refresh
        if not refresh:
            computed_at = profile.get("computed_at", "")
            if computed_at:
                from datetime import UTC, datetime, timedelta

                try:
                    ts = datetime.fromisoformat(computed_at)
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=UTC)
                    if datetime.now(UTC) - ts > timedelta(hours=24):
                        background_tasks.add_task(
                            _background_refresh_profile,
                            engine=request.app.state.engine,
                            encryption=request.app.state.encryption,
                            settings=request.app.state.settings,
                            user_id=current_user["user_id"],
                            service=service,
                        )
                except (ValueError, TypeError):
                    pass
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except Exception:
        logger.exception("Unexpected error building taste profile")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to build taste profile",
        )

    raw_artists = profile.get("top_artists", [])
    artist_names = [
        a["name"]
        for a in raw_artists
        if isinstance(a, dict) and a.get("name")
    ]
    # Generate an Apple developer token for catalog search fallback on artist
    # artwork (required when the user's library rows have empty artwork
    # templates — which is true for any library indexed before V 6.365).
    developer_token: str | None = None
    try:
        from musicmind.api.services.service import generate_apple_developer_token

        settings = request.app.state.settings
        developer_token = generate_apple_developer_token(
            settings.apple_team_id,
            settings.apple_key_id,
            settings.apple_private_key_path,
            private_key_b64=settings.apple_private_key_b64,
        )
    except Exception:
        logger.debug(
            "Apple developer token generation failed; "
            "artist artwork will fall back to library-only lookup"
        )

    _lap("token")
    try:
        artworks = await get_artist_artworks(
            request.app.state.engine,
            user_id=current_user["user_id"],
            artist_names=artist_names,
            developer_token=developer_token,
        )
    except Exception:
        logger.debug("Artist artwork lookup failed; returning profile without artwork")
        artworks = {}
    _lap("artworks")

    top_artists = [
        ArtistEntry(
            name=a["name"],
            score=a["score"],
            song_count=a["song_count"],
            sample_artwork_url=(artworks.get((a["name"] or "").lower()) or None),
        )
        if isinstance(a, dict)
        else a
        for a in raw_artists
    ]

    # Clean genre vector for display — strip "" / "Musica" noise + merge
    # redundant parent genres that Apple sometimes double-tags.
    cleaned_genres = clean_genre_vector(profile.get("genre_vector", {}) or {})

    breadth_raw = compute_breadth_metrics(
        genre_vector=cleaned_genres,
        top_artists=raw_artists if isinstance(raw_artists, list) else [],
    )

    total_ms = (_time.perf_counter() - _t_start) * 1000.0
    breakdown = " ".join(f"{k}={v:.0f}ms" for k, v in _perf.items())
    logger.info(
        "[PERF] taste_profile user=%s artists=%d total=%.0fms | %s",
        current_user["user_id"][:8], len(artist_names),
        total_ms, breakdown,
    )

    return TasteProfileResponse(
        service=profile.get("service", ""),
        computed_at=profile.get("computed_at", ""),
        total_songs_analyzed=profile.get("total_songs_analyzed", 0),
        listening_hours_estimated=profile.get("listening_hours_estimated", 0.0),
        familiarity_score=profile.get("familiarity_score", 0.0),
        genre_vector=cleaned_genres,
        top_artists=top_artists,
        audio_trait_preferences=profile.get("audio_trait_preferences", {}),
        audio_centroid=profile.get("audio_centroid", {}) or {},
        release_year_distribution=profile.get("release_year_distribution", {}),
        services_included=profile.get("services_included", []),
        breadth=BreadthMetrics(**breadth_raw),
    )


@router.post("/profile/refresh", status_code=status.HTTP_202_ACCEPTED)
@limiter.limit("5/minute")
async def refresh_profile(
    request: Request,
    background_tasks: BackgroundTasks,
    service: str | None = Query(default=None),
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Trigger a background taste profile rebuild.

    Returns 202 immediately. Poll GET /api/taste/profile/status for completion.
    """
    user_id = current_user["user_id"]
    engine = request.app.state.engine

    current_status = await _get_rebuild_status(engine, user_id)
    if current_status == "in_progress":
        return {"status": "in_progress", "message": "Profile rebuild already in progress"}

    async def _rebuild() -> None:
        await _set_rebuild_status(engine, user_id, "in_progress")
        try:
            await taste_service.get_profile(
                engine,
                request.app.state.encryption,
                request.app.state.settings,
                user_id=user_id,
                service=service,
                force_refresh=True,
            )
            await _set_rebuild_status(engine, user_id, "completed")
            logger.info("Background profile rebuild completed for user %s", user_id)
        except Exception:
            await _set_rebuild_status(engine, user_id, "failed")
            logger.exception("Background profile rebuild failed for user %s", user_id)

    background_tasks.add_task(_rebuild)
    await _set_rebuild_status(engine, user_id, "in_progress")
    return {"status": "accepted", "message": "Profile rebuild started"}


@router.get("/profile/status")
async def get_profile_status(
    request: Request,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Check status of a background profile rebuild."""
    user_id = current_user["user_id"]
    status_val = await _get_rebuild_status(request.app.state.engine, user_id)
    return {"status": status_val}


@router.get("/genres")
async def get_genres(
    request: Request,
    service: str | None = Query(default=None),
    refresh: bool = Query(default=False),
    current_user: dict = Depends(get_current_user),
) -> TopGenresResponse:
    """Get top genres from the user's taste profile."""
    try:
        profile = await taste_service.get_profile(
            request.app.state.engine,
            request.app.state.encryption,
            request.app.state.settings,
            user_id=current_user["user_id"],
            service=service,
            force_refresh=refresh,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except Exception:
        logger.exception("Unexpected error fetching genres")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch genres",
        )

    genre_vector = profile.get("genre_vector", {})
    genres = [
        GenreEntry(genre=k, weight=v)
        for k, v in sorted(genre_vector.items(), key=lambda x: x[1], reverse=True)
    ]

    return TopGenresResponse(
        service=profile.get("service", ""),
        genres=genres,
    )


@router.get("/artists")
async def get_artists(
    request: Request,
    service: str | None = Query(default=None),
    refresh: bool = Query(default=False),
    current_user: dict = Depends(get_current_user),
) -> TopArtistsResponse:
    """Get top artists from the user's taste profile, ranked by affinity score."""
    try:
        profile = await taste_service.get_profile(
            request.app.state.engine,
            request.app.state.encryption,
            request.app.state.settings,
            user_id=current_user["user_id"],
            service=service,
            force_refresh=refresh,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except Exception:
        logger.exception("Unexpected error fetching artists")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch artists",
        )

    raw_artists = profile.get("top_artists", [])
    artists = [
        ArtistEntry(
            name=a["name"],
            score=a["score"],
            song_count=a["song_count"],
        )
        if isinstance(a, dict)
        else a
        for a in raw_artists
    ]

    return TopArtistsResponse(
        service=profile.get("service", ""),
        artists=artists,
    )


@router.get("/audio-traits")
async def get_audio_traits(
    request: Request,
    service: str | None = Query(default=None),
    refresh: bool = Query(default=False),
    current_user: dict = Depends(get_current_user),
) -> AudioTraitsResponse:
    """Get audio trait preferences from the user's taste profile."""
    try:
        profile = await taste_service.get_profile(
            request.app.state.engine,
            request.app.state.encryption,
            request.app.state.settings,
            user_id=current_user["user_id"],
            service=service,
            force_refresh=refresh,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except Exception:
        logger.exception("Unexpected error fetching audio traits")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch audio traits",
        )

    traits = profile.get("audio_trait_preferences", {})
    resolved_service = profile.get("service", service or "")

    note = None
    if not traits and resolved_service == "spotify":
        note = "Audio traits not available for Spotify (Apple Music only)"

    return AudioTraitsResponse(
        service=resolved_service,
        traits=traits,
        note=note,
    )


@router.get("/enrichment-status")
@limiter.limit(TASTE_LIMIT)
async def enrichment_status(
    request: Request,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Get the current user's audio enrichment progress.

    Counts both library songs and artist discography tracks being enriched.
    Shows indexing=true while background enrichment is actively running.
    """
    import sqlalchemy as sa

    from musicmind.auth.router import is_indexing
    from musicmind.db.schema import audio_features_cache, song_metadata_cache

    engine = request.app.state.engine
    user_id = current_user["user_id"]

    async with engine.begin() as conn:
        # Total = library songs
        total_q = await conn.execute(
            sa.select(sa.func.count()).select_from(song_metadata_cache).where(
                song_metadata_cache.c.user_id == user_id
            )
        )
        total_library = total_q.scalar() or 0

        # Enriched = only tracks with REAL audio data (energy != null)
        # Excludes empty marker rows from failed enrichment attempts
        enriched_q = await conn.execute(
            sa.select(sa.func.count()).select_from(audio_features_cache).where(
                sa.and_(
                    audio_features_cache.c.user_id == user_id,
                    audio_features_cache.c.energy.isnot(None),
                )
            )
        )
        enriched = enriched_q.scalar() or 0

        # Library songs only (user's actual library, not worker-discovered)
        library_q = await conn.execute(
            sa.select(sa.func.count()).select_from(song_metadata_cache).where(
                sa.and_(
                    song_metadata_cache.c.user_id == user_id,
                    sa.or_(
                        song_metadata_cache.c.date_added_to_library.isnot(None),
                        song_metadata_cache.c.library_id.isnot(None),
                    ),
                )
            )
        )
        library_songs = library_q.scalar() or 0

    # Use library songs as denominator (worker songs are invisible to user)
    total = library_songs if library_songs > 0 else total_library
    user_indexing = is_indexing(user_id)

    return {
        "total_songs": total,
        "enriched_songs": min(enriched, total),
        "percentage": round(min(enriched, total) / total * 100, 1) if total > 0 else 0,
        "complete": enriched >= total and total > 0 and not user_indexing,
        "indexing": user_indexing,
    }


# ── Sonic Neighbors (CLAP-centroid discovery) ────────────────────────────


@router.get("/sonic-neighbors")
@limiter.limit(TASTE_LIMIT)
async def get_sonic_neighbors(
    request: Request,
    limit: int = Query(default=8, ge=1, le=24),
    service: str | None = Query(default=None),
    current_user: dict = Depends(get_current_user),
) -> SonicNeighborsResponse:
    """Artists whose CLAP embeddings most closely match the user's library centroid.

    Reads the user's `clap_centroid` from the latest taste snapshot, scores a
    sample of global CLAP-enriched catalog tracks against it, groups by artist
    (excluding artists already in the user's library), and returns the top N.

    If no CLAP centroid exists yet (new user, or GPU backfill still catching up),
    returns an empty list with a `note` explaining why.
    """
    engine = request.app.state.engine
    user_id = current_user["user_id"]

    try:
        profile = await taste_service.get_profile(
            engine,
            request.app.state.encryption,
            request.app.state.settings,
            user_id=user_id,
            service=service,
            force_refresh=False,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except Exception:
        logger.exception("Failed to fetch profile for sonic-neighbors")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch sonic neighbors",
        )

    centroid = profile.get("clap_centroid")
    resolved_service = profile.get("service", service or "")

    if not centroid:
        return SonicNeighborsResponse(
            service=resolved_service,
            neighbors=[],
            note=(
                "Sonic neighbors are not available yet — the GPU enrichment of "
                "your library is still in progress. Check back once the CLAP "
                "embeddings finish computing."
            ),
        )

    try:
        raw = await compute_sonic_neighbors(
            engine, user_id=user_id, clap_centroid=centroid, limit=limit,
        )
    except Exception:
        logger.exception("compute_sonic_neighbors failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to compute sonic neighbors",
        )

    neighbors = [SonicNeighbor(**n) for n in raw]
    return SonicNeighborsResponse(
        service=resolved_service,
        neighbors=neighbors,
        note=None if neighbors else "No matching discovery artists found yet.",
    )


@router.get("/distributions")
@limiter.limit(TASTE_LIMIT)
async def get_distributions_endpoint(
    request: Request,
    current_user: dict = Depends(get_current_user),
) -> LibraryDistributionsResponse:
    """Aggregated Essentia enrichment ready for interactive charts.

    Returns tempo histogram (7 BPM buckets), key × mode (12 keys × major/minor),
    acousticness + valence histograms (10 buckets each), and an energy ×
    danceability scatter sample (up to 200 points with track metadata for
    tooltip display). Computed on the fly from audio_features_cache — no cache
    layer, query is <50ms for libraries under 5000 songs.
    """
    engine = request.app.state.engine
    user_id = current_user["user_id"]

    try:
        data = await get_library_distributions(engine, user_id=user_id)
    except Exception:
        logger.exception("get_library_distributions failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to compute library distributions",
        )

    return LibraryDistributionsResponse(**data)


@router.get("/recent-enrichments")
@limiter.limit(TASTE_LIMIT)
async def get_recent_enrichments_endpoint(
    request: Request,
    limit: int = Query(default=12, ge=1, le=50),
    current_user: dict = Depends(get_current_user),
) -> RecentEnrichmentsResponse:
    """Last N songs the enrichment pipeline processed for this user.

    Joins audio_features_cache (ordered by enriched_at DESC with analyzed_at
    fallback) with song_metadata_cache for display names and artwork. Used
    by the dashboard to show "what we just learned about your taste."
    """
    engine = request.app.state.engine
    user_id = current_user["user_id"]

    try:
        items_raw = await get_recent_enrichments(
            engine, user_id=user_id, limit=limit,
        )
    except Exception:
        logger.exception("get_recent_enrichments failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch recent enrichments",
        )

    items = [RecentEnrichment(**i) for i in items_raw]
    return RecentEnrichmentsResponse(items=items, total=len(items))


# ── Background Profile Refresh ───────────────────────────────────────────


async def _background_refresh_profile(
    *,
    engine,
    encryption,
    settings,
    user_id: str,
    service: str | None,
) -> None:
    """Rebuild taste profile in background (triggered when stale cache served)."""
    try:
        await taste_service.get_profile(
            engine, encryption, settings,
            user_id=user_id, service=service, force_refresh=True,
        )
        logger.info("Background profile refresh complete for user %s", user_id)
    except Exception:
        logger.warning("Background profile refresh failed for user %s", user_id)
