"""Genius lyrics fetcher + semantic embedding pipeline.

Pipeline:
1. Search Genius by artist+title (free API, no OAuth needed)
2. Scrape lyrics from the song page
3. Embed with sentence-transformers/all-MiniLM-L6-v2 (384-dim, local, no API)

Embeddings stored in audio_embeddings table with model_version="lyrics-minilm-v2".
"""

from __future__ import annotations

import logging
import re

import httpx

logger = logging.getLogger(__name__)

GENIUS_API = "https://api.genius.com"
GENIUS_SEARCH = f"{GENIUS_API}/search"

# Lazy-loaded model
_model = None


def _get_model():
    """Lazy-load the sentence-transformer model."""
    global _model
    if _model is None:
        try:
            from sentence_transformers import SentenceTransformer

            _model = SentenceTransformer("all-MiniLM-L6-v2")
            logger.info("Loaded sentence-transformer model (all-MiniLM-L6-v2)")
        except ImportError:
            logger.warning(
                "sentence-transformers not installed — lyrics embeddings disabled. "
                "Install with: pip install sentence-transformers"
            )
            return None
    return _model


async def search_genius(
    artist: str,
    title: str,
    *,
    api_key: str | None = None,
) -> str | None:
    """Search Genius for a song and return the lyrics page URL.

    Uses Genius search API (free, no OAuth). If no API key provided,
    falls back to scraping the search page directly.
    """
    if not artist or not title:
        return None

    query = f"{artist} {title}"

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            if api_key:
                resp = await client.get(
                    GENIUS_SEARCH,
                    headers={"Authorization": f"Bearer {api_key}"},
                    params={"q": query},
                )
                resp.raise_for_status()
                hits = resp.json().get("response", {}).get("hits", [])
                if hits:
                    return hits[0].get("result", {}).get("url")
            else:
                # Scrape-based fallback: construct URL from artist+title
                slug_artist = re.sub(r"[^a-z0-9]", "-", artist.lower()).strip("-")
                slug_title = re.sub(r"[^a-z0-9]", "-", title.lower()).strip("-")
                url = f"https://genius.com/{slug_artist}-{slug_title}-lyrics"
                resp = await client.head(url)
                if resp.status_code == 200:
                    return url
    except (httpx.HTTPStatusError, httpx.HTTPError):
        logger.debug("Genius search failed for %s - %s", artist, title)

    return None


async def scrape_lyrics(url: str) -> str | None:
    """Scrape lyrics text from a Genius song page.

    Extracts text from data-lyrics-container divs.
    Returns cleaned lyrics text or None.
    """
    if not url:
        return None

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, follow_redirects=True)
            resp.raise_for_status()
            html = resp.text

        # Extract lyrics from data-lyrics-container
        # Genius uses: <div data-lyrics-container="true">...</div>
        pattern = r'data-lyrics-container="true"[^>]*>(.*?)</div>'
        matches = re.findall(pattern, html, re.DOTALL)

        if not matches:
            # Fallback: look for Lyrics__Container
            pattern = r'class="Lyrics__Container[^"]*"[^>]*>(.*?)</div>'
            matches = re.findall(pattern, html, re.DOTALL)

        if not matches:
            return None

        lyrics = "\n".join(matches)

        # Clean HTML tags
        lyrics = re.sub(r"<br\s*/?>", "\n", lyrics)
        lyrics = re.sub(r"<[^>]+>", "", lyrics)
        lyrics = re.sub(r"&amp;", "&", lyrics)
        lyrics = re.sub(r"&[a-z]+;", " ", lyrics)
        lyrics = re.sub(r"\n{3,}", "\n\n", lyrics)
        lyrics = lyrics.strip()

        # Skip very short lyrics (likely instrumental or failed scrape)
        if len(lyrics) < 50:
            return None

        return lyrics

    except (httpx.HTTPStatusError, httpx.HTTPError):
        logger.debug("Failed to scrape lyrics from %s", url)
        return None


def embed_lyrics(text: str) -> list[float] | None:
    """Embed lyrics text into a 384-dim vector using sentence-transformers.

    Returns None if the model isn't available.
    """
    model = _get_model()
    if model is None:
        return None

    if not text or len(text) < 50:
        return None

    # Truncate to ~500 words to stay within model's context
    words = text.split()
    if len(words) > 500:
        text = " ".join(words[:500])

    try:
        embedding = model.encode(text, show_progress_bar=False)
        return embedding.tolist()
    except Exception:
        logger.debug("Failed to embed lyrics")
        return None


async def fetch_and_embed_lyrics(
    artist: str,
    title: str,
    *,
    api_key: str | None = None,
) -> tuple[str | None, list[float] | None]:
    """Full pipeline: search → scrape → embed.

    Returns (lyrics_text, embedding_vector) or (None, None).
    """
    url = await search_genius(artist, title, api_key=api_key)
    if not url:
        return None, None

    lyrics = await scrape_lyrics(url)
    if not lyrics:
        return None, None

    embedding = embed_lyrics(lyrics)
    return lyrics, embedding
