# MusicMind MCP — Implementation Plan

## Decisions Log
- **Start clean** (not forking Cifero74/mcp-apple-music): the existing repo has 11 basic tools with a tightly-coupled client. MusicMind needs 30+ tools, a persistence layer, and a taste engine. Cleaner to build from scratch taking auth flow as inspiration.
- **SQLite + SQLAlchemy Core**: zero-config local storage, designed for Postgres migration later. Using Core (not ORM) for explicit control over queries.
- **Hybrid recommendation**: local algorithmic scoring on metadata (genres, artists, ratings, editorial notes, audio traits) + Claude reasons over the taste profile in natural language. The MCP tools provide Claude with rich structured data; Claude's own reasoning handles the NLP interpretation.
- **Storefront default "it"**: user is in Milan, Italy. Auto-detect endpoint available as fallback.
- **No native MusicKit**: REST API only for portability. Play counts unavailable — we approximate listening intensity from recently-played frequency and library presence.
- **numpy for vectors**: genre affinity is a sparse vector, cosine similarity for matching. Simple and fast.

## Current Phase
Phase 7: Polish & Ship

## Progress Notes
- **Phase 1 complete**: Project scaffold with uv, config loading, ES256 JWT auth, setup wizard, FastMCP server with health tool, 16 tests passing, ruff clean.
- **Phase 2 complete**: AppleMusicClient with 25+ endpoints, pagination, 429 retry, Pydantic models.
- **Phase 3 complete**: SQLite persistence with 5 tables, async queries, server lifespan DB init.
- **Phase 4 complete**: 21 MCP tools across library, catalog, playback, and management domains.
- **Phase 5 complete**: Taste engine with genre vectors, artist affinity, similarity scoring, 4 discovery strategies.
- **Phase 6 complete**: 7 new tools (taste_profile, taste_compare, listening_stats, discover, smart_playlist, refresh_cache, why_this_song). Total: 28 tools.
