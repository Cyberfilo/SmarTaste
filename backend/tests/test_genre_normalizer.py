"""Tests for cross-service genre taxonomy normalizer."""

from __future__ import annotations

from musicmind.engine.genres import (
    merge_genre_vectors,
    normalize_genre,
    normalize_genre_list,
)


# ── normalize_genre ──────────────────────────────────────────────────────────


class TestNormalizeGenre:
    """Test normalize_genre maps both service formats to canonical form."""

    def test_spotify_hiphop_to_canonical(self) -> None:
        assert normalize_genre("hip-hop") == "Hip-Hop/Rap"

    def test_spotify_rnb_to_canonical(self) -> None:
        assert normalize_genre("r-n-b") == "R&B/Soul"

    def test_spotify_italian_hiphop(self) -> None:
        assert normalize_genre("italian-hip-hop") == "Italian Hip-Hop/Rap"

    def test_apple_music_already_canonical(self) -> None:
        """Apple Music genres that are already canonical pass through."""
        assert normalize_genre("Hip-Hop/Rap") == "Hip-Hop/Rap"

    def test_apple_music_rnb_soul(self) -> None:
        assert normalize_genre("R&B/Soul") == "R&B/Soul"

    def test_case_insensitive_lookup(self) -> None:
        assert normalize_genre("HIP-HOP") == "Hip-Hop/Rap"
        assert normalize_genre("Pop") == "Pop"
        assert normalize_genre("POP") == "Pop"

    def test_unknown_genre_passthrough(self) -> None:
        """Unknown genres are returned unchanged."""
        assert normalize_genre("Neofolk Ambient Drone") == "Neofolk Ambient Drone"

    def test_empty_string_passthrough(self) -> None:
        assert normalize_genre("") == ""

    def test_whitespace_stripped(self) -> None:
        assert normalize_genre("  pop  ") == "Pop"

    def test_trap_italiano(self) -> None:
        assert normalize_genre("trap italiano") == "Italian Trap"

    def test_singer_songwriter_variants(self) -> None:
        assert normalize_genre("singer-songwriter") == "Singer/Songwriter"
        assert normalize_genre("singer songwriter") == "Singer/Songwriter"
        assert normalize_genre("Singer/Songwriter") == "Singer/Songwriter"

    def test_electronic_variants(self) -> None:
        assert normalize_genre("electronica") == "Electronic"
        assert normalize_genre("electronic") == "Electronic"

    def test_kpop_variants(self) -> None:
        assert normalize_genre("k-pop") == "K-Pop"
        assert normalize_genre("k pop") == "K-Pop"

    def test_drum_and_bass_variants(self) -> None:
        assert normalize_genre("drum and bass") == "Drum & Bass"
        assert normalize_genre("drum-and-bass") == "Drum & Bass"
        assert normalize_genre("dnb") == "Drum & Bass"


# ── normalize_genre_list ─────────────────────────────────────────────────────


class TestNormalizeGenreList:
    """Test normalize_genre_list with dedup and order preservation."""

    def test_deduplicates_after_normalization(self) -> None:
        genres = ["hip-hop", "Hip-Hop/Rap", "rap"]
        result = normalize_genre_list(genres)
        assert result == ["Hip-Hop/Rap"]

    def test_preserves_order_first_occurrence(self) -> None:
        genres = ["pop", "rock", "pop"]
        result = normalize_genre_list(genres)
        assert result == ["Pop", "Rock"]

    def test_mixed_services(self) -> None:
        """Spotify lowercase and Apple Music Title Case normalize together."""
        genres = ["italian-hip-hop", "Italian Hip-Hop/Rap", "pop", "Pop"]
        result = normalize_genre_list(genres)
        assert result == ["Italian Hip-Hop/Rap", "Pop"]

    def test_empty_list(self) -> None:
        assert normalize_genre_list([]) == []

    def test_unknown_genres_preserved(self) -> None:
        genres = ["Obscure Microgenre", "pop"]
        result = normalize_genre_list(genres)
        assert result == ["Obscure Microgenre", "Pop"]


# ── merge_genre_vectors ──────────────────────────────────────────────────────


class TestMergeGenreVectors:
    """Test merge_genre_vectors for cross-service genre vector merging."""

    def test_merge_identical_genres(self) -> None:
        vec_a = {"Pop": 0.6, "Rock": 0.4}
        vec_b = {"Pop": 0.5, "Rock": 0.5}
        result = merge_genre_vectors(vec_a, vec_b)
        assert "Pop" in result
        assert "Rock" in result
        total = sum(result.values())
        assert abs(total - 1.0) < 0.001

    def test_merge_normalizes_genres(self) -> None:
        """Spotify 'hip-hop' and Apple Music 'Hip-Hop/Rap' merge into one."""
        vec_a = {"hip-hop": 0.8, "pop": 0.2}  # Spotify-style
        vec_b = {"Hip-Hop/Rap": 0.7, "Pop": 0.3}  # Apple-style
        result = merge_genre_vectors(vec_a, vec_b)
        assert "Hip-Hop/Rap" in result
        assert "Pop" in result
        # hip-hop and Hip-Hop/Rap should be merged (not separate keys)
        assert "hip-hop" not in result
        total = sum(result.values())
        assert abs(total - 1.0) < 0.001

    def test_merge_disjoint_genres(self) -> None:
        vec_a = {"Pop": 1.0}
        vec_b = {"Rock": 1.0}
        result = merge_genre_vectors(vec_a, vec_b)
        assert "Pop" in result
        assert "Rock" in result
        assert abs(result["Pop"] - 0.5) < 0.001
        assert abs(result["Rock"] - 0.5) < 0.001

    def test_merge_with_weights(self) -> None:
        vec_a = {"Pop": 1.0}
        vec_b = {"Pop": 1.0}
        result = merge_genre_vectors(vec_a, vec_b, weight_a=2.0, weight_b=1.0)
        # Still sums to 1.0 after normalization
        total = sum(result.values())
        assert abs(total - 1.0) < 0.001

    def test_merge_empty_vectors(self) -> None:
        assert merge_genre_vectors({}, {}) == {}
        result = merge_genre_vectors({"Pop": 1.0}, {})
        assert "Pop" in result
        assert abs(result["Pop"] - 1.0) < 0.001

    def test_merge_sorted_by_weight_descending(self) -> None:
        vec_a = {"Pop": 0.1, "Rock": 0.9}
        vec_b = {}
        result = merge_genre_vectors(vec_a, vec_b)
        keys = list(result.keys())
        assert keys[0] == "Rock"
        assert keys[1] == "Pop"
