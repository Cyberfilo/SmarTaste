"""Service connection API endpoints for Spotify and Apple Music OAuth flows."""

from __future__ import annotations

import logging
import secrets
from datetime import UTC, datetime

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status

from musicmind.api.services.schemas import (
    AppleMusicConnectRequest,
    AppleMusicDeveloperTokenResponse,
    DisconnectResponse,
    ServiceConnectionResponse,
    ServiceListResponse,
    SpotifyConnectResponse,
)
from musicmind.api.services.service import (
    build_spotify_authorize_url,
    delete_service_connection,
    exchange_spotify_code,
    fetch_spotify_user_profile,
    generate_apple_developer_token,
    generate_pkce_pair,
    get_user_connections,
    upsert_service_connection,
)
from musicmind.auth.dependencies import get_current_user

router = APIRouter(prefix="/api/services", tags=["services"])
logger = logging.getLogger(__name__)

VALID_SERVICES = ("spotify", "apple_music")


@router.get("")
async def list_connections(
    request: Request, current_user: dict = Depends(get_current_user)
) -> ServiceListResponse:
    """List all service connections for the authenticated user.

    Returns both spotify and apple_music entries with status derived from DB only.
    No external API calls are made (status endpoint must be fast, DB-only).
    """
    engine = request.app.state.engine
    user_id = current_user["user_id"]

    connections = await get_user_connections(engine, user_id=user_id)
    conn_by_service = {c["service"]: c for c in connections}

    services = []
    now = datetime.now(UTC)

    for svc in VALID_SERVICES:
        conn = conn_by_service.get(svc)
        if conn is None:
            services.append(
                ServiceConnectionResponse(
                    service=svc,
                    status="not_connected",
                    service_user_id=None,
                    connected_at=None,
                )
            )
        else:
            # For Spotify: check token_expires_at. For Apple Music: no expiry tracked.
            # SQLite returns timezone-naive datetimes; normalize before comparing.
            expires_at = conn.get("token_expires_at")
            if expires_at is not None:
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=UTC)
            if expires_at is not None and expires_at < now:
                svc_status = "expired"
            else:
                svc_status = "connected"

            connected_at_val = conn.get("connected_at")
            connected_at_str = (
                connected_at_val.isoformat() if connected_at_val else None
            )

            services.append(
                ServiceConnectionResponse(
                    service=svc,
                    status=svc_status,
                    service_user_id=conn.get("service_user_id"),
                    connected_at=connected_at_str,
                )
            )

    return ServiceListResponse(services=services)


@router.post("/spotify/connect")
async def spotify_connect(
    request: Request, current_user: dict = Depends(get_current_user)
) -> SpotifyConnectResponse:
    """Initiate Spotify OAuth PKCE flow.

    Generates PKCE pair and state, stores them in session, and returns
    the Spotify authorize URL for the frontend to redirect the user.
    """
    settings = request.app.state.settings

    if settings.spotify_client_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Spotify not configured",
        )

    code_verifier, code_challenge = generate_pkce_pair()
    state = secrets.token_urlsafe(16)

    # Store PKCE state in session for the callback endpoint.
    # user_id is stored because the callback cannot use get_current_user
    # (Spotify redirects the browser -- no access_token cookie on redirect).
    request.session["spotify_code_verifier"] = code_verifier
    request.session["spotify_state"] = state
    request.session["spotify_user_id"] = current_user["user_id"]

    url = build_spotify_authorize_url(
        settings.spotify_client_id,
        settings.spotify_redirect_uri,
        code_challenge,
        state,
    )

    return SpotifyConnectResponse(authorize_url=url)


@router.get("/spotify/callback")
async def spotify_callback(
    request: Request, code: str, state: str,
    background_tasks: BackgroundTasks,
):
    """Handle Spotify OAuth callback after user authorization.

    This endpoint does NOT use get_current_user. The browser is redirected here
    by Spotify -- there are no auth cookies on this request. The user_id is
    retrieved from the session (stored during connect).

    On success: redirect to /settings?service=spotify&status=connected
    On error: redirect to /settings?service=spotify&status=error&detail=...
    """
    from starlette.responses import RedirectResponse
    from urllib.parse import quote

    settings = request.app.state.settings
    engine = request.app.state.engine
    encryption = request.app.state.encryption
    frontend_url = settings.frontend_url.rstrip("/")

    def _error_redirect(detail: str) -> RedirectResponse:
        return RedirectResponse(
            url=f"{frontend_url}/settings?service=spotify&status=error&detail={quote(detail)}",
            status_code=302,
        )

    # Validate state parameter (CSRF protection for OAuth)
    stored_state = request.session.get("spotify_state")
    if not stored_state or stored_state != state:
        return _error_redirect("Invalid state parameter. Please try connecting again.")

    code_verifier = request.session.get("spotify_code_verifier")
    user_id = request.session.get("spotify_user_id")
    if not code_verifier or not user_id:
        return _error_redirect("Session expired. Please try connecting again.")

    # Clear session keys after retrieval
    request.session.pop("spotify_code_verifier", None)
    request.session.pop("spotify_state", None)
    request.session.pop("spotify_user_id", None)

    try:
        token_data = await exchange_spotify_code(
            code, code_verifier, settings.spotify_redirect_uri, settings.spotify_client_id
        )
        access_token = token_data["access_token"]
        refresh_token = token_data.get("refresh_token")
        expires_in = token_data.get("expires_in")

        profile = await fetch_spotify_user_profile(access_token)
        service_user_id = profile["id"]
    except httpx.HTTPStatusError as exc:
        logger.error("Spotify token exchange failed: %s", exc.response.text)
        return _error_redirect("Spotify authorization failed. Please try again.")
    except Exception as exc:
        logger.error("Spotify callback error: %s", exc)
        return _error_redirect("An unexpected error occurred. Please try again.")

    await upsert_service_connection(
        engine,
        encryption,
        user_id=user_id,
        service="spotify",
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=expires_in,
        service_user_id=service_user_id,
    )

    logger.info("Spotify connected for user: %s", user_id)

    # Trigger full library fetch + enrichment in background
    background_tasks.add_task(
        _background_initial_sync,
        engine=engine,
        encryption=encryption,
        settings=settings,
        user_id=user_id,
    )

    # Redirect back to the frontend settings page using configured frontend URL
    from starlette.responses import RedirectResponse

    frontend_url = settings.frontend_url.rstrip("/")
    return RedirectResponse(
        url=f"{frontend_url}/settings?service=spotify&status=connected",
        status_code=302,
    )


@router.get("/apple-music/developer-token")
async def apple_music_developer_token(
    request: Request, current_user: dict = Depends(get_current_user)
) -> AppleMusicDeveloperTokenResponse:
    """Return an Apple Developer Token for MusicKit JS authorization."""
    settings = request.app.state.settings

    has_key = settings.apple_private_key_path or settings.apple_private_key_b64
    if (
        not settings.apple_team_id
        or not settings.apple_key_id
        or not has_key
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Apple Music not configured",
        )

    token = generate_apple_developer_token(
        settings.apple_team_id,
        settings.apple_key_id,
        settings.apple_private_key_path,
        private_key_b64=settings.apple_private_key_b64,
    )
    return AppleMusicDeveloperTokenResponse(developer_token=token)


@router.post("/apple-music/connect")
async def apple_music_connect(
    request: Request,
    body: AppleMusicConnectRequest,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Store an Apple Music User Token obtained via MusicKit JS.

    Triggers background library fetch + audio enrichment after connection.
    """
    engine = request.app.state.engine
    encryption = request.app.state.encryption
    settings = request.app.state.settings

    await upsert_service_connection(
        engine,
        encryption,
        user_id=current_user["user_id"],
        service="apple_music",
        access_token=body.music_user_token,
        refresh_token=None,
        expires_in=None,
        service_user_id=None,
    )

    logger.info("Apple Music connected for user: %s", current_user["user_id"])

    # Trigger full library fetch + enrichment in background
    background_tasks.add_task(
        _background_initial_sync,
        engine=engine,
        encryption=encryption,
        settings=settings,
        user_id=current_user["user_id"],
    )

    return {"message": "Apple Music connected"}


@router.delete("/{service}")
async def disconnect_service(
    request: Request,
    service: str,
    current_user: dict = Depends(get_current_user),
) -> DisconnectResponse:
    """Disconnect a music service by removing stored tokens.

    Note: Spotify has no revocation API -- deletion only removes local tokens.
    """
    if service not in VALID_SERVICES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid service: {service}. Must be one of {VALID_SERVICES}",
        )

    engine = request.app.state.engine
    deleted = await delete_service_connection(
        engine, user_id=current_user["user_id"], service=service
    )

    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Service not connected",
        )

    logger.info("Service %s disconnected for user: %s", service, current_user["user_id"])
    return DisconnectResponse(message=f"{service} disconnected")


# ── Background Sync ──────────────────────────────────────────────────────────


async def _background_initial_sync(
    *,
    engine,
    encryption,
    settings,
    user_id: str,
) -> None:
    """Full library fetch + audio enrichment after service connection.

    Runs in the background after Spotify OAuth callback or Apple Music connect.
    Fetches the user's library, builds taste profile, and enriches all tracks
    with audio features via Deezer + ReccoBeats.
    """
    try:
        from musicmind.api.taste.service import TasteService

        taste_svc = TasteService()
        profile = await taste_svc.get_profile(
            engine, encryption, settings,
            user_id=user_id, force_refresh=True,
        )
        logger.info("Background initial sync complete for user %s", user_id)

        # Phase 2: Pre-cache top artists' discographies with audio features
        try:
            await _cache_artist_discographies(
                engine, encryption, settings,
                user_id=user_id, profile=profile,
            )
        except Exception:
            logger.warning("Artist discography caching failed for %s", user_id)

    except Exception:
        logger.warning("Background initial sync failed for user %s", user_id)


async def _cache_artist_discographies(
    *,
    engine,
    encryption,
    settings,
    user_id: str,
    profile: dict,
) -> None:
    """Fetch top artists' discographies and enrich their songs.

    For each of the user's top 10 artists, fetches their top songs
    from the connected service and enriches them with audio features.
    This pre-populates the feature cache for better recommendations.
    """
    from musicmind.api.recommendations.fetch import _fetch_artist_top_tracks
    from musicmind.api.services.service import get_user_connections
    from musicmind.engine.enrichment.orchestrator import enrich_tracks

    top_artists = profile.get("top_artists", [])[:10]
    if not top_artists:
        return

    connections = await get_user_connections(engine, user_id=user_id)
    if not connections:
        return

    conn_data = connections[0]
    service = conn_data["service"]

    # Get credentials
    import sqlalchemy as sa
    from musicmind.db.schema import service_connections as sc_table

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

    # Resolve artist IDs and fetch their top songs
    import httpx
    from musicmind.api.recommendations.fetch import _search_artist_id

    all_tracks: list[dict] = []
    for artist_entry in top_artists:
        artist_name = artist_entry.get("name", "")
        if not artist_name:
            continue

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
                    limit=10,
                )
                all_tracks.extend(tracks)
        except Exception:
            logger.debug("Failed to fetch discography for %s", artist_name)
            continue

    if all_tracks:
        logger.info(
            "Pre-caching %d tracks from %d artists for user %s",
            len(all_tracks), len(top_artists), user_id,
        )
        await enrich_tracks(
            engine, all_tracks, user_id=user_id,
            soundstat_api_key=settings.soundstat_api_key,
        )
