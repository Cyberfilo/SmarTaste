"""Canonical genre taxonomy normalizer for cross-service unification.

Maps Spotify-style genres (lowercase-hyphenated: "hip-hop", "italian-hip-hop")
and Apple Music-style genres (Title Case with slashes: "Hip-Hop/Rap",
"Italian Hip-Hop/Rap") to a shared canonical representation.

The canonical form uses Apple Music's Title Case convention since it preserves
more information (regional prefixes, slash-separated parent/child).
"""

from __future__ import annotations


# ── Canonical Genre Mapping ──────────────────────────────────────────────────
#
# Keys: lowercase normalized forms (stripped of hyphens/slashes for matching).
# Values: canonical genre string in Apple Music Title Case format.
#
# Spotify genres are lowercase-hyphenated: "hip-hop", "r-n-b", "italian-hip-hop"
# Apple Music genres use Title Case + slashes: "Hip-Hop/Rap", "R&B/Soul"

CANONICAL_MAP: dict[str, str] = {
    # ── Hip-Hop / Rap ────────────────────────────────────────────────────
    "hip-hop": "Hip-Hop/Rap",
    "hip hop": "Hip-Hop/Rap",
    "hiphop": "Hip-Hop/Rap",
    "rap": "Hip-Hop/Rap",
    "hip-hop/rap": "Hip-Hop/Rap",
    "italian hip-hop": "Italian Hip-Hop/Rap",
    "italian-hip-hop": "Italian Hip-Hop/Rap",
    "italian hip-hop/rap": "Italian Hip-Hop/Rap",
    "uk hip-hop": "UK Hip-Hop/Rap",
    "uk-hip-hop": "UK Hip-Hop/Rap",
    "uk hip hop": "UK Hip-Hop/Rap",
    "french hip-hop": "French Hip-Hop/Rap",
    "french-hip-hop": "French Hip-Hop/Rap",
    "german hip-hop": "German Hip-Hop/Rap",
    "german-hip-hop": "German Hip-Hop/Rap",
    "spanish hip-hop": "Spanish Hip-Hop/Rap",
    "spanish-hip-hop": "Spanish Hip-Hop/Rap",
    "latin hip hop": "Latin Hip-Hop/Rap",
    "latin-hip-hop": "Latin Hip-Hop/Rap",
    "trap": "Trap",
    "trap italiano": "Italian Trap",
    "italian trap": "Italian Trap",
    "uk drill": "UK Drill",
    "drill": "Drill",
    "drill italiano": "Italian Drill",
    "italian drill": "Italian Drill",
    # ── Pop ───────────────────────────────────────────────────────────────
    "pop": "Pop",
    "italian pop": "Italian Pop",
    "italian-pop": "Italian Pop",
    "k-pop": "K-Pop",
    "k pop": "K-Pop",
    "j-pop": "J-Pop",
    "j pop": "J-Pop",
    "art pop": "Art Pop",
    "dream pop": "Dream Pop",
    "indie pop": "Indie Pop",
    "synth-pop": "Synth-Pop",
    "synthpop": "Synth-Pop",
    "electropop": "Electropop",
    "dance pop": "Dance Pop",
    "europop": "Europop",
    "latin pop": "Latin Pop",
    "french pop": "French Pop",
    "german pop": "German Pop",
    "spanish pop": "Spanish Pop",
    "brit pop": "Britpop",
    "britpop": "Britpop",
    # ── R&B / Soul ────────────────────────────────────────────────────────
    "r&b": "R&B/Soul",
    "rnb": "R&B/Soul",
    "r-n-b": "R&B/Soul",
    "r&b/soul": "R&B/Soul",
    "soul": "R&B/Soul",
    "neo-soul": "Neo-Soul",
    "neo soul": "Neo-Soul",
    # ── Rock ──────────────────────────────────────────────────────────────
    "rock": "Rock",
    "alternative": "Alternative",
    "alternative rock": "Alternative",
    "alt-rock": "Alternative",
    "indie rock": "Indie Rock",
    "indie": "Indie",
    "punk": "Punk",
    "punk rock": "Punk",
    "post-punk": "Post-Punk",
    "hard rock": "Hard Rock",
    "classic rock": "Classic Rock",
    "progressive rock": "Progressive Rock",
    "prog-rock": "Progressive Rock",
    "garage rock": "Garage Rock",
    "psychedelic rock": "Psychedelic Rock",
    "grunge": "Grunge",
    "metal": "Metal",
    "heavy metal": "Heavy Metal",
    "death metal": "Death Metal",
    "black metal": "Black Metal",
    "nu metal": "Nu Metal",
    # ── Electronic / Dance ────────────────────────────────────────────────
    "electronic": "Electronic",
    "electronica": "Electronic",
    "dance": "Dance",
    "edm": "Electronic/Dance",
    "house": "House",
    "deep house": "Deep House",
    "tech house": "Tech House",
    "techno": "Techno",
    "trance": "Trance",
    "drum and bass": "Drum & Bass",
    "drum-and-bass": "Drum & Bass",
    "dnb": "Drum & Bass",
    "dubstep": "Dubstep",
    "ambient": "Ambient",
    "idm": "IDM",
    "downtempo": "Downtempo",
    "trip-hop": "Trip-Hop",
    "trip hop": "Trip-Hop",
    # ── Latin ─────────────────────────────────────────────────────────────
    "latin": "Latin",
    "reggaeton": "Reggaeton",
    "latin trap": "Latin Trap",
    "latin urban": "Latin Urban",
    "salsa": "Salsa",
    "bachata": "Bachata",
    "cumbia": "Cumbia",
    "bossa nova": "Bossa Nova",
    "samba": "Samba",
    # ── Other ─────────────────────────────────────────────────────────────
    "jazz": "Jazz",
    "blues": "Blues",
    "country": "Country",
    "folk": "Folk",
    "classical": "Classical",
    "reggae": "Reggae",
    "ska": "Ska",
    "funk": "Funk",
    "disco": "Disco",
    "gospel": "Gospel",
    "world": "Worldwide",
    "worldwide": "Worldwide",
    "afrobeats": "Afrobeats",
    "afro-beats": "Afrobeats",
    "singer-songwriter": "Singer/Songwriter",
    "singer songwriter": "Singer/Songwriter",
    "singer/songwriter": "Singer/Songwriter",
    "soundtrack": "Soundtrack",
    "anime": "Anime",
    "lo-fi": "Lo-Fi",
    "lofi": "Lo-Fi",
}

# Build a lowercase lookup index from the map
_LOOKUP: dict[str, str] = {k.lower(): v for k, v in CANONICAL_MAP.items()}


def normalize_genre(genre: str) -> str:
    """Normalize a genre string to canonical form.

    Performs case-insensitive lookup against CANONICAL_MAP.
    If no match, returns the original genre unchanged (preserves
    Apple Music regional genre names that are already canonical).

    Args:
        genre: Genre string from either Spotify or Apple Music.

    Returns:
        Canonical genre string.
    """
    if not genre:
        return genre

    key = genre.strip().lower()

    # Direct lookup
    if key in _LOOKUP:
        return _LOOKUP[key]

    return genre


def normalize_genre_list(genres: list[str]) -> list[str]:
    """Normalize a list of genres, deduplicating after normalization.

    Preserves order (first occurrence wins when duplicates merge).

    Args:
        genres: List of genre strings from any service.

    Returns:
        Deduplicated list of canonical genre strings.
    """
    seen: set[str] = set()
    result: list[str] = []
    for g in genres:
        canonical = normalize_genre(g)
        if canonical not in seen:
            seen.add(canonical)
            result.append(canonical)
    return result


def merge_genre_vectors(
    vector_a: dict[str, float],
    vector_b: dict[str, float],
    *,
    weight_a: float = 1.0,
    weight_b: float = 1.0,
) -> dict[str, float]:
    """Merge two genre vectors with canonical normalization.

    Both vectors have genres normalized to canonical form before merging.
    Values are combined with optional weighting and re-normalized to sum=1.0.

    Args:
        vector_a: First genre vector (genre -> weight).
        vector_b: Second genre vector (genre -> weight).
        weight_a: Multiplier for vector_a values (default 1.0).
        weight_b: Multiplier for vector_b values (default 1.0).

    Returns:
        Merged genre vector normalized to sum=1.0.
    """
    merged: dict[str, float] = {}

    for genre, weight in vector_a.items():
        canonical = normalize_genre(genre)
        merged[canonical] = merged.get(canonical, 0.0) + weight * weight_a

    for genre, weight in vector_b.items():
        canonical = normalize_genre(genre)
        merged[canonical] = merged.get(canonical, 0.0) + weight * weight_b

    # Re-normalize to sum=1.0
    total = sum(merged.values())
    if total <= 0:
        return {}

    return {
        genre: count / total
        for genre, count in sorted(merged.items(), key=lambda x: x[1], reverse=True)
    }
