"""Tests for cross-service track deduplication."""

from __future__ import annotations

from musicmind.engine.dedup import (
    _fuzzy_dedup,
    _isrc_match,
    _merge_track_metadata,
    _normalize_text,
    deduplicate_tracks,
    fuzzy_title_artist_match,
)


# ── _normalize_text ──────────────────────────────────────────────────────────


class TestNormalizeText:
    """Test text normalization for fuzzy comparison."""

    def test_lowercase(self) -> None:
        assert _normalize_text("Hello World") == "hello world"

    def test_strips_parenthetical(self) -> None:
        assert _normalize_text("Song (feat. Artist)") == "song"

    def test_strips_brackets(self) -> None:
        assert _normalize_text("Song [Deluxe Edition]") == "song"

    def test_strips_featuring_suffix(self) -> None:
        assert _normalize_text("Song feat. Someone") == "song"
        assert _normalize_text("Song ft. Someone") == "song"

    def test_strips_accents(self) -> None:
        assert _normalize_text("cafe") == "cafe"

    def test_strips_punctuation(self) -> None:
        assert _normalize_text("don't stop") == "dont stop"

    def test_collapses_whitespace(self) -> None:
        assert _normalize_text("hello   world") == "hello world"

    def test_empty_string(self) -> None:
        assert _normalize_text("") == ""


# ── fuzzy_title_artist_match ─────────────────────────────────────────────────


class TestFuzzyMatch:
    """Test fuzzy title+artist matching."""

    def test_exact_match(self) -> None:
        assert fuzzy_title_artist_match("Song", "Artist", "Song", "Artist")

    def test_case_insensitive(self) -> None:
        assert fuzzy_title_artist_match("SONG", "ARTIST", "song", "artist")

    def test_featuring_difference(self) -> None:
        assert fuzzy_title_artist_match(
            "Song (feat. Other)", "Artist",
            "Song", "Artist",
        )

    def test_deluxe_suffix(self) -> None:
        assert fuzzy_title_artist_match(
            "Song [Deluxe]", "Artist",
            "Song", "Artist",
        )

    def test_different_songs_no_match(self) -> None:
        assert not fuzzy_title_artist_match(
            "Song A", "Artist", "Song B", "Artist"
        )

    def test_different_artists_no_match(self) -> None:
        assert not fuzzy_title_artist_match(
            "Song", "Artist A", "Song", "Artist B"
        )

    def test_empty_title_no_match(self) -> None:
        assert not fuzzy_title_artist_match("", "Artist", "Song", "Artist")

    def test_empty_artist_no_match(self) -> None:
        assert not fuzzy_title_artist_match("Song", "", "Song", "Artist")


# ── _merge_track_metadata ────────────────────────────────────────────────────


class TestMergeMetadata:
    """Test metadata merging from duplicate tracks."""

    def _make_track(self, **overrides) -> dict:
        base = {
            "catalog_id": "id1",
            "name": "Song",
            "artist_name": "Artist",
            "album_name": "Album",
            "genre_names": [],
            "duration_ms": 200000,
            "release_date": "2024-01-01",
            "isrc": None,
            "editorial_notes": "",
            "audio_traits": [],
            "artwork_url_template": "",
            "preview_url": "",
            "service_source": "spotify",
        }
        base.update(overrides)
        return base

    def test_prefers_longer_genre_list(self) -> None:
        a = self._make_track(genre_names=["Pop"], service_source="spotify")
        b = self._make_track(
            catalog_id="id2",
            genre_names=["Pop", "Italian Pop", "Dance Pop"],
            service_source="apple_music",
        )
        merged = _merge_track_metadata(a, b)
        assert merged["genre_names"] == ["Pop", "Italian Pop", "Dance Pop"]

    def test_prefers_nonempty_isrc(self) -> None:
        a = self._make_track(isrc=None, service_source="spotify")
        b = self._make_track(
            catalog_id="id2", isrc="USRC12345678", service_source="apple_music"
        )
        merged = _merge_track_metadata(a, b)
        assert merged["isrc"] == "USRC12345678"

    def test_preserves_service_ids(self) -> None:
        a = self._make_track(catalog_id="sp123", service_source="spotify")
        b = self._make_track(catalog_id="am456", service_source="apple_music")
        merged = _merge_track_metadata(a, b)
        assert merged["_service_ids"] == {"spotify": "sp123", "apple_music": "am456"}

    def test_marks_as_unified(self) -> None:
        a = self._make_track(service_source="spotify")
        b = self._make_track(catalog_id="id2", service_source="apple_music")
        merged = _merge_track_metadata(a, b)
        assert merged["service_source"] == "unified"

    def test_prefers_nonempty_editorial_notes(self) -> None:
        a = self._make_track(editorial_notes="", service_source="spotify")
        b = self._make_track(
            catalog_id="id2",
            editorial_notes="A great song about...",
            service_source="apple_music",
        )
        merged = _merge_track_metadata(a, b)
        assert merged["editorial_notes"] == "A great song about..."


# ── _isrc_match ──────────────────────────────────────────────────────────────


class TestIsrcMatch:
    """Test ISRC-based dedup phase."""

    def _make_track(self, catalog_id: str, isrc: str | None, **kw) -> dict:
        return {
            "catalog_id": catalog_id,
            "name": "Song",
            "artist_name": "Artist",
            "genre_names": kw.get("genre_names", []),
            "isrc": isrc,
            "service_source": kw.get("service_source", "spotify"),
            "editorial_notes": "",
            "audio_traits": [],
            "artwork_url_template": "",
            "preview_url": "",
            "duration_ms": 200000,
        }

    def test_merges_same_isrc(self) -> None:
        tracks = [
            self._make_track("sp1", "USRC12345678", service_source="spotify"),
            self._make_track("am1", "USRC12345678", service_source="apple_music"),
        ]
        deduped, no_isrc = _isrc_match(tracks)
        assert len(deduped) == 1
        assert len(no_isrc) == 0
        assert deduped[0]["service_source"] == "unified"

    def test_separates_no_isrc(self) -> None:
        tracks = [
            self._make_track("sp1", "USRC12345678"),
            self._make_track("sp2", None),
        ]
        deduped, no_isrc = _isrc_match(tracks)
        assert len(deduped) == 1
        assert len(no_isrc) == 1

    def test_case_insensitive_isrc(self) -> None:
        tracks = [
            self._make_track("sp1", "usrc12345678", service_source="spotify"),
            self._make_track("am1", "USRC12345678", service_source="apple_music"),
        ]
        deduped, no_isrc = _isrc_match(tracks)
        assert len(deduped) == 1

    def test_different_isrcs_not_merged(self) -> None:
        tracks = [
            self._make_track("sp1", "USRC11111111"),
            self._make_track("sp2", "USRC22222222"),
        ]
        deduped, no_isrc = _isrc_match(tracks)
        assert len(deduped) == 2


# ── _fuzzy_dedup ─────────────────────────────────────────────────────────────


class TestFuzzyDedup:
    """Test fuzzy dedup phase."""

    def _make_track(self, name: str, artist: str, **kw) -> dict:
        return {
            "catalog_id": kw.get("catalog_id", "id1"),
            "name": name,
            "artist_name": artist,
            "genre_names": kw.get("genre_names", []),
            "isrc": None,
            "service_source": kw.get("service_source", "spotify"),
            "editorial_notes": "",
            "audio_traits": [],
            "artwork_url_template": "",
            "preview_url": "",
            "duration_ms": 200000,
        }

    def test_merges_fuzzy_match(self) -> None:
        tracks = [
            self._make_track("Song (feat. X)", "Artist", catalog_id="sp1",
                             service_source="spotify"),
            self._make_track("Song", "Artist", catalog_id="am1",
                             service_source="apple_music"),
        ]
        result = _fuzzy_dedup(tracks)
        assert len(result) == 1
        assert result[0]["service_source"] == "unified"

    def test_no_merge_different_songs(self) -> None:
        tracks = [
            self._make_track("Song A", "Artist", catalog_id="sp1"),
            self._make_track("Song B", "Artist", catalog_id="sp2"),
        ]
        result = _fuzzy_dedup(tracks)
        assert len(result) == 2

    def test_empty_list(self) -> None:
        assert _fuzzy_dedup([]) == []


# ── deduplicate_tracks (integration) ─────────────────────────────────────────


class TestDeduplicateTracks:
    """Test the full deduplication pipeline."""

    def _make_track(
        self,
        catalog_id: str,
        name: str,
        artist: str,
        *,
        isrc: str | None = None,
        service_source: str = "spotify",
        genre_names: list[str] | None = None,
    ) -> dict:
        return {
            "catalog_id": catalog_id,
            "name": name,
            "artist_name": artist,
            "album_name": "Album",
            "genre_names": genre_names or [],
            "duration_ms": 200000,
            "release_date": "2024-01-01",
            "isrc": isrc,
            "editorial_notes": "",
            "audio_traits": [],
            "artwork_url_template": "",
            "preview_url": "",
            "service_source": service_source,
        }

    def test_isrc_dedup_takes_priority(self) -> None:
        """ISRC match merges tracks even if titles differ slightly."""
        tracks = [
            self._make_track(
                "sp1", "Song (Radio Edit)", "Artist",
                isrc="USRC12345678", service_source="spotify",
            ),
            self._make_track(
                "am1", "Song", "Artist",
                isrc="USRC12345678", service_source="apple_music",
            ),
        ]
        result = deduplicate_tracks(tracks)
        assert len(result) == 1
        assert result[0]["service_source"] == "unified"

    def test_fuzzy_fallback_for_no_isrc(self) -> None:
        """Tracks without ISRC still dedup via fuzzy match."""
        tracks = [
            self._make_track(
                "sp1", "Song (feat. X)", "Artist",
                service_source="spotify",
            ),
            self._make_track(
                "am1", "Song", "Artist",
                service_source="apple_music",
            ),
        ]
        result = deduplicate_tracks(tracks)
        assert len(result) == 1

    def test_preserves_unique_tracks(self) -> None:
        tracks = [
            self._make_track(
                "sp1", "Song A", "Artist A",
                isrc="USRC11111111", service_source="spotify",
            ),
            self._make_track(
                "am1", "Song B", "Artist B",
                isrc="USRC22222222", service_source="apple_music",
            ),
        ]
        result = deduplicate_tracks(tracks)
        assert len(result) == 2

    def test_mixed_isrc_and_no_isrc(self) -> None:
        """ISRC dupes merge, non-ISRC fuzzy matches merge, unique tracks preserved."""
        tracks = [
            self._make_track(
                "sp1", "Song A", "Artist A",
                isrc="USRC12345678", service_source="spotify",
            ),
            self._make_track(
                "am1", "Song A", "Artist A",
                isrc="USRC12345678", service_source="apple_music",
            ),
            self._make_track(
                "sp2", "Song B (feat. X)", "Artist B",
                service_source="spotify",
            ),
            self._make_track(
                "am2", "Song B", "Artist B",
                service_source="apple_music",
            ),
            self._make_track(
                "sp3", "Unique Song", "Solo Artist",
                isrc="USRC99999999", service_source="spotify",
            ),
        ]
        result = deduplicate_tracks(tracks)
        # Song A merged by ISRC, Song B merged by fuzzy, Unique stays
        assert len(result) == 3

    def test_empty_list(self) -> None:
        assert deduplicate_tracks([]) == []

    def test_genre_merge_prefers_richer(self) -> None:
        tracks = [
            self._make_track(
                "sp1", "Song", "Artist",
                isrc="USRC12345678", service_source="spotify",
                genre_names=["pop"],
            ),
            self._make_track(
                "am1", "Song", "Artist",
                isrc="USRC12345678", service_source="apple_music",
                genre_names=["Pop", "Italian Pop", "Dance Pop"],
            ),
        ]
        result = deduplicate_tracks(tracks)
        assert len(result) == 1
        assert len(result[0]["genre_names"]) == 3
