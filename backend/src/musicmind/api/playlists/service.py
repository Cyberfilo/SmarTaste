"""Playlist service — fetch real playlists from connected music services."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import sqlalchemy as sa

from musicmind.api.playlists.fetch import (
    fetch_apple_music_playlist_tracks,
    fetch_apple_music_playlists,
    fetch_spotify_playlist_tracks,
    fetch_spotify_playlists,
)
from musicmind.api.services.service import (
    generate_apple_developer_token,
    get_user_connections,
    refresh_spotify_token,
    upsert_service_connection,
)
from musicmind.db.schema import (
    playlist_brief_songs,
    playlist_briefs,
    service_connections,
)
from musicmind.security.encryption import EncryptionService

import uuid as _uuid

_uuid7 = getattr(_uuid, "uuid7", _uuid.uuid4)

logger = logging.getLogger(__name__)


class PlaylistService:
    """Fetches real playlists from Spotify and Apple Music."""

    async def list_playlists(
        self,
        engine,
        encryption: EncryptionService,
        settings,
        *,
        user_id: str,
        service: str | None = None,
    ) -> list[dict[str, Any]]:
        """List the user's playlists from connected services.

        Fetches live from the service API (not cached).
        """
        connections = await get_user_connections(engine, user_id=user_id)
        if not connections:
            raise ValueError("No connected service found")

        all_playlists: list[dict[str, Any]] = []

        for conn_data in connections:
            svc = conn_data["service"]
            if service and svc != service:
                continue

            try:
                playlists = await self._fetch_service_playlists(
                    engine, encryption, settings,
                    user_id=user_id, service=svc,
                )
                all_playlists.extend(playlists)
            except Exception:
                logger.warning("Failed to fetch playlists from %s", svc)

        return all_playlists

    async def get_playlist_tracks(
        self,
        engine,
        encryption: EncryptionService,
        settings,
        *,
        user_id: str,
        service: str,
        playlist_id: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Get tracks from a specific service playlist."""
        access_token = await self._get_access_token(
            engine, encryption, settings,
            user_id=user_id, service=service,
        )

        if service == "spotify":
            return await fetch_spotify_playlist_tracks(
                access_token, playlist_id, limit=limit,
            )
        elif service == "apple_music":
            developer_token = generate_apple_developer_token(
                settings.apple_team_id,
                settings.apple_key_id,
                settings.apple_private_key_path,
                private_key_b64=settings.apple_private_key_b64,
            )
            return await fetch_apple_music_playlist_tracks(
                developer_token, access_token, playlist_id, limit=limit,
            )
        else:
            raise ValueError(f"Unsupported service: {service}")

    async def _fetch_service_playlists(
        self,
        engine,
        encryption: EncryptionService,
        settings,
        *,
        user_id: str,
        service: str,
    ) -> list[dict[str, Any]]:
        """Fetch playlists from a specific service."""
        access_token = await self._get_access_token(
            engine, encryption, settings,
            user_id=user_id, service=service,
        )

        if service == "spotify":
            return await fetch_spotify_playlists(access_token)
        elif service == "apple_music":
            developer_token = generate_apple_developer_token(
                settings.apple_team_id,
                settings.apple_key_id,
                settings.apple_private_key_path,
                private_key_b64=settings.apple_private_key_b64,
            )
            return await fetch_apple_music_playlists(
                developer_token, access_token,
            )
        else:
            raise ValueError(f"Unsupported service: {service}")

    async def _get_access_token(
        self,
        engine,
        encryption: EncryptionService,
        settings,
        *,
        user_id: str,
        service: str,
    ) -> str:
        """Get valid access token, refreshing Spotify if needed."""
        async with engine.begin() as conn:
            result = await conn.execute(
                sa.select(service_connections).where(
                    sa.and_(
                        service_connections.c.user_id == user_id,
                        service_connections.c.service == service,
                    )
                )
            )
            row = result.first()

        if row is None:
            raise ValueError(f"No {service} connection found")

        access_token = encryption.decrypt(row.access_token_encrypted)

        if service == "spotify":
            token_expires_at = row.token_expires_at
            now = datetime.now(UTC)
            if token_expires_at is not None:
                if token_expires_at.tzinfo is None:
                    token_expires_at = token_expires_at.replace(tzinfo=UTC)
                if token_expires_at < now + timedelta(seconds=60):
                    refresh_token_encrypted = row.refresh_token_encrypted
                    if refresh_token_encrypted:
                        refresh_token_value = encryption.decrypt(
                            refresh_token_encrypted
                        )
                        token_data = await refresh_spotify_token(
                            refresh_token_value,
                            settings.spotify_client_id,
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

    # ── Playlist brief (V 6.440 — chat-driven target data) ──────────

    async def create_brief(
        self,
        engine,
        settings,
        *,
        user_id: str,
        playlist_id: str,
        service: str,
        brief_text: str,
        mentioned_songs: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Persist a new playlist brief + synthesize its target_vector.

        V 6.441 (5b): after persisting the brief, call gpt-5.4 to turn
        `brief_text + mentioned songs' enrichment` into a structured audio-
        trait + mood target. The target vector is stored on the brief row
        and consumed by `get_playlist_recommendations(brief_id=...)`.

        Synthesis failures degrade gracefully — the brief is still
        persisted with `target_vector=NULL` and `synthesis_error=<msg>`.
        """
        from musicmind.api.playlists.brief_synthesis import (
            synthesize_target_vector,
        )

        brief_id = str(_uuid7())
        now = datetime.now(UTC)

        async with engine.begin() as conn:
            await conn.execute(
                playlist_briefs.insert().values(
                    id=brief_id,
                    user_id=user_id,
                    playlist_id=playlist_id,
                    service=service,
                    brief_text=brief_text,
                    target_vector=None,
                    synthesis_error=None,
                    created_at=now,
                    updated_at=now,
                )
            )
            for idx, song in enumerate(mentioned_songs):
                cid = song.get("catalog_id")
                if not cid:
                    continue
                role = song.get("role") or "referenced"
                if role not in ("referenced", "target_example", "anti_example"):
                    role = "referenced"
                await conn.execute(
                    playlist_brief_songs.insert().values(
                        brief_id=brief_id,
                        position=idx,
                        catalog_id=cid,
                        isrc=song.get("isrc"),
                        role=role,
                        reason_text=song.get("reason_text"),
                    )
                )

        target_vector: dict[str, Any] | None = None
        synthesis_error: str | None = None
        api_key = getattr(settings, "openai_api_key", None)
        if not api_key:
            synthesis_error = "MUSICMIND_OPENAI_API_KEY not configured"
        else:
            target_vector, synthesis_error = await synthesize_target_vector(
                engine,
                api_key=api_key,
                brief_text=brief_text,
                mentioned_songs=mentioned_songs,
            )

        # Persist whichever of (target_vector | synthesis_error) we ended up with.
        now2 = datetime.now(UTC)
        async with engine.begin() as conn:
            await conn.execute(
                playlist_briefs.update()
                .where(playlist_briefs.c.id == brief_id)
                .values(
                    target_vector=target_vector,
                    synthesis_error=synthesis_error,
                    updated_at=now2,
                )
            )

        return {
            "id": brief_id,
            "playlist_id": playlist_id,
            "service": service,
            "brief_text": brief_text,
            "mentioned_songs": mentioned_songs,
            "target_vector": target_vector,
            "synthesis_error": synthesis_error,
            "created_at": now.isoformat(),
            "updated_at": now2.isoformat(),
        }

    async def list_briefs(
        self,
        engine,
        *,
        user_id: str,
        playlist_id: str,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Return the user's briefs for this playlist, newest first."""
        async with engine.begin() as conn:
            brief_rows = (await conn.execute(
                sa.select(playlist_briefs).where(
                    sa.and_(
                        playlist_briefs.c.user_id == user_id,
                        playlist_briefs.c.playlist_id == playlist_id,
                    )
                ).order_by(playlist_briefs.c.created_at.desc()).limit(limit)
            )).fetchall()
            if not brief_rows:
                return []

            brief_ids = [r.id for r in brief_rows]
            song_rows = (await conn.execute(
                sa.select(playlist_brief_songs).where(
                    playlist_brief_songs.c.brief_id.in_(brief_ids)
                ).order_by(
                    playlist_brief_songs.c.brief_id,
                    playlist_brief_songs.c.position,
                )
            )).fetchall()

        songs_by_brief: dict[str, list[dict[str, Any]]] = {}
        for s in song_rows:
            songs_by_brief.setdefault(s.brief_id, []).append({
                "catalog_id": s.catalog_id,
                "isrc": s.isrc,
                "role": s.role,
                "reason_text": s.reason_text,
            })

        return [
            {
                "id": b.id,
                "playlist_id": b.playlist_id,
                "service": b.service,
                "brief_text": b.brief_text,
                "mentioned_songs": songs_by_brief.get(b.id, []),
                "target_vector": b.target_vector,
                "synthesis_error": b.synthesis_error,
                "created_at": b.created_at.isoformat() if b.created_at else "",
                "updated_at": b.updated_at.isoformat() if b.updated_at else "",
            }
            for b in brief_rows
        ]

    async def get_playlist_recommendations(
        self,
        engine,
        encryption: EncryptionService,
        settings,
        *,
        user_id: str,
        service: str,
        playlist_id: str,
        limit: int = 10,
        apple_only: bool = True,
        brief_id: str | None = None,
    ) -> dict:
        """V 6.430 — playlist-scoped recommendations with centroid blending.

        Scoring = **playlist centroid ×0.8 + user taste centroid ×0.2**. The
        playlist's own songs dominate the signal; the user's broader taste
        provides a small tiebreaker so suggestions feel "like this playlist,
        by someone who also likes what I like."

        Blended in 4 vector spaces:
          - genre_vector (tag distribution)
          - audio_centroid (tempo/energy/valence scalars)
          - clap_centroid (512-dim semantic audio-text)
          - mert_centroid (768-dim musical structure)

        Candidates come from `recommendation_candidates` (worker-populated),
        not a fresh discovery call — no ×22-second burn. Apple-only filter
        enforced by default per V 6.430 ("apple music only now" user spec).
        """
        import numpy as np

        from musicmind.api.recommendations.service import RecommendationService
        from musicmind.api.taste.service import TasteService
        from musicmind.db.schema import (
            audio_embeddings_global,
            audio_features_global,
        )
        from musicmind.engine.profile import expand_genres
        from musicmind.engine.scorer import rank_candidates

        from collections import Counter

        PLAYLIST_W = 0.8
        USER_W = 0.2

        # ── 1. Playlist tracks ────────────────────────────────────────
        tracks = await self.get_playlist_tracks(
            engine, encryption, settings,
            user_id=user_id, service=service, playlist_id=playlist_id,
        )
        if not tracks:
            return {"items": [], "total": 0, "blend": {"playlist": PLAYLIST_W, "user": USER_W}}

        track_ids = {t.get("catalog_id") for t in tracks if t.get("catalog_id")}
        isrcs = [t.get("isrc") for t in tracks if t.get("isrc")]

        # ── 2. Playlist genre vector + top artists ────────────────────
        genre_counter: Counter[str] = Counter()
        artist_counter: Counter[str] = Counter()
        for t in tracks:
            for g in t.get("genre_names", []) or []:
                genre_counter[g] += 1
            for eg in expand_genres(t.get("genre_names", []) or []):
                genre_counter[eg] += 0.3
            artist_name = t.get("artist_name", "")
            if artist_name:
                artist_counter[artist_name] += 1
        total_g = sum(genre_counter.values())
        playlist_genre_vector: dict[str, float] = (
            {g: c / total_g for g, c in genre_counter.items()}
            if total_g > 0 else {}
        )
        max_artist = max(artist_counter.values()) if artist_counter else 1
        playlist_top_artists = [
            {"name": name, "score": count / max_artist, "song_count": count}
            for name, count in artist_counter.most_common(20)
        ]

        # ── 3. Playlist CLAP/MERT/audio centroids via ISRC ────────────
        playlist_clap: list[float] | None = None
        playlist_mert: list[float] | None = None
        playlist_audio: dict[str, float] = {}

        if isrcs:
            async with engine.begin() as conn:
                emb_rows = (await conn.execute(
                    sa.select(
                        audio_embeddings_global.c.clap_embedding,
                        audio_embeddings_global.c.mert_embedding,
                    ).where(audio_embeddings_global.c.isrc.in_(isrcs))
                )).fetchall()
                afg_rows = (await conn.execute(
                    sa.select(
                        audio_features_global.c.tempo,
                        audio_features_global.c.energy,
                        audio_features_global.c.valence_proxy,
                        audio_features_global.c.danceability,
                        audio_features_global.c.acousticness,
                        audio_features_global.c.brightness,
                    ).where(audio_features_global.c.isrc.in_(isrcs))
                )).fetchall()

            def _mean_normed(embs: list[list[float]]) -> list[float] | None:
                if not embs:
                    return None
                arr = np.mean(np.array(embs, dtype=np.float32), axis=0)
                n = float(np.linalg.norm(arr))
                return (arr / n).tolist() if n > 0 else arr.tolist()

            clap_embs = [
                r.clap_embedding for r in emb_rows
                if r.clap_embedding
                and isinstance(r.clap_embedding, list)
                and len(r.clap_embedding) > 10
            ]
            mert_embs = [
                r.mert_embedding for r in emb_rows
                if r.mert_embedding
                and isinstance(r.mert_embedding, list)
                and len(r.mert_embedding) > 10
            ]
            playlist_clap = _mean_normed(clap_embs)
            playlist_mert = _mean_normed(mert_embs)

            feat_acc: dict[str, list[float]] = {
                k: [] for k in
                ("tempo", "energy", "valence_proxy", "danceability",
                 "acousticness", "brightness")
            }
            for r in afg_rows:
                for k in feat_acc:
                    v = getattr(r, k, None)
                    if v is not None:
                        feat_acc[k].append(float(v))
            playlist_audio = {
                k: float(np.mean(v)) for k, v in feat_acc.items() if v
            }

        # ── 4. User taste profile (for the 0.2 blend tail) ────────────
        ts = TasteService()
        try:
            user_profile = await ts.get_profile(
                engine, encryption, settings, user_id=user_id,
            )
        except Exception:
            user_profile = {}

        user_clap = user_profile.get("clap_centroid")
        user_mert = user_profile.get("mert_centroid")
        user_audio = user_profile.get("audio_centroid") or {}
        user_genre = user_profile.get("genre_vector") or {}

        # ── 5. Blend centroids (playlist 0.8 / user 0.2) ──────────────
        def blend_vec(
            a: list[float] | None, b: list[float] | None,
        ) -> list[float] | None:
            if a is None and b is None:
                return None
            if a is None:
                return b
            if b is None:
                return a
            arr_a = np.array(a, dtype=np.float32)
            arr_b = np.array(b, dtype=np.float32)
            if arr_a.shape != arr_b.shape:
                return a  # fall back to playlist-only when dims mismatch
            mixed = PLAYLIST_W * arr_a + USER_W * arr_b
            n = float(np.linalg.norm(mixed))
            return (mixed / n).tolist() if n > 0 else mixed.tolist()

        blended_clap = blend_vec(playlist_clap, user_clap)
        blended_mert = blend_vec(playlist_mert, user_mert)

        blended_genre: dict[str, float] = {}
        for g in set(playlist_genre_vector) | set(user_genre):
            blended_genre[g] = (
                PLAYLIST_W * playlist_genre_vector.get(g, 0.0)
                + USER_W * user_genre.get(g, 0.0)
            )

        blended_audio: dict[str, float] = {}
        for k in set(playlist_audio) | set(user_audio):
            a = playlist_audio.get(k)
            b = user_audio.get(k)
            if a is None:
                blended_audio[k] = float(b) if b is not None else 0.0
            elif b is None:
                blended_audio[k] = a
            else:
                blended_audio[k] = PLAYLIST_W * a + USER_W * float(b)

        # ── 5b. Optional brief-driven target-vector overrides ─────────
        target_vector: dict[str, Any] | None = None
        if brief_id:
            async with engine.begin() as conn:
                brow = (await conn.execute(
                    sa.select(
                        playlist_briefs.c.target_vector,
                    ).where(
                        sa.and_(
                            playlist_briefs.c.id == brief_id,
                            playlist_briefs.c.user_id == user_id,
                            playlist_briefs.c.playlist_id == playlist_id,
                        )
                    )
                )).first()
            if brow and brow.target_vector:
                target_vector = brow.target_vector
                # Audio scalar targets: override blended_audio where set.
                tv_map = {
                    "tempo": "tempo_target",
                    "energy": "energy_target",
                    "valence_proxy": "valence_target",
                    "danceability": "danceability_target",
                }
                for feat_key, tv_key in tv_map.items():
                    tval = target_vector.get(tv_key)
                    if tval is not None:
                        blended_audio[feat_key] = float(tval)
                # Emphasized genres get a floor weight in blended_genre so
                # the scorer rewards them even if the user's profile and
                # playlist don't already emphasize them.
                for g in target_vector.get("genre_emphasis", []) or []:
                    if isinstance(g, str) and g:
                        gl = g.lower()
                        blended_genre[gl] = max(blended_genre.get(gl, 0.0), 0.25)

        blended_profile = {
            "genre_vector": blended_genre,
            "audio_centroid": blended_audio,
            "top_artists": playlist_top_artists,
            "release_year_distribution": {},
            "familiarity_score": 0.5,
        }

        # ── 6. Candidate pool + filters ───────────────────────────────
        rec_svc = RecommendationService()
        candidates = await rec_svc._load_candidates_from_db(
            engine, user_id=user_id, strategy="all",
        )
        candidates = [c for c in candidates if c.get("catalog_id") not in track_ids]
        candidates = await rec_svc._filter_library_songs(
            engine, candidates, user_id=user_id,
        )
        if apple_only:
            candidates = [
                c for c in candidates
                if (c.get("service_source") or "").lower()
                in ("apple_music", "apple")
            ]
        if not candidates:
            return {"items": [], "total": 0, "blend": {"playlist": PLAYLIST_W, "user": USER_W}}

        # ── 7. Load candidate enrichment ──────────────────────────────
        clap_map, mert_map, embedding_map = await rec_svc._load_embeddings(
            engine, candidates, user_id=user_id,
        )
        audio_features_map = await rec_svc._load_audio_features(
            engine, candidates, user_id=user_id,
        )

        # ── 8. Score with blended centroids ───────────────────────────
        ranked = rank_candidates(
            candidates, blended_profile, count=limit,
            audio_features_map=audio_features_map,
            user_audio_centroid=blended_audio,
            embedding_map=embedding_map or None,
            clap_map=clap_map or None,
            user_clap_centroid=blended_clap,
            mert_map=mert_map or None,
            user_mert_centroid=blended_mert,
        )

        items = [
            {
                "catalog_id": r.get("catalog_id", ""),
                "name": r.get("name", ""),
                "artist_name": r.get("artist_name", ""),
                "album_name": r.get("album_name", ""),
                "artwork_url": r.get("artwork_url", ""),
                "preview_url": r.get("preview_url", ""),
                "score": r.get("_score", 0.0),
                "explanation": r.get("_explanation", ""),
                "genre_names": r.get("genre_names", []),
                "service_source": r.get("service_source", ""),
                "isrc": r.get("isrc"),
            }
            for r in ranked
        ]

        return {
            "items": items,
            "total": len(items),
            "blend": {"playlist": PLAYLIST_W, "user": USER_W},
            "brief": {
                "brief_id": brief_id,
                "applied": target_vector is not None,
                "target_vector": target_vector,
            } if brief_id else None,
            "playlist_signal": {
                "tracks_used": len(tracks),
                "clap_from_n": len(clap_embs) if isrcs else 0,
                "mert_from_n": len(mert_embs) if isrcs else 0,
                "audio_from_n": (
                    max(
                        (len(v) for v in (
                            feat_acc if isrcs else {"_": []}
                        ).values()),
                        default=0,
                    )
                ),
            },
        }
