# MusicMind MCP — Implementation Plan

## Decisions Log
- **Start clean** (not forking Cifero74/mcp-apple-music): the existing repo has 11 basic tools with a tightly-coupled client. MusicMind needs 30+ tools, a persistence layer, and a taste engine. Cleaner to build from scratch taking auth flow as inspiration.
- **SQLite + SQLAlchemy Core**: zero-config local storage, designed for Postgres migration later. Using Core (not ORM) for explicit control over queries.
- **Hybrid recommendation**: local algorithmic scoring on metadata (genres, artists, ratings, editorial notes, audio traits) + Claude reasons over the taste profile in natural language. The MCP tools provide Claude with rich structured data; Claude's own reasoning handles the NLP interpretation.
- **Storefront default "it"**: user is in Milan, Italy. Auto-detect endpoint available as fallback.
- **No native MusicKit**: REST API only for portability. Play counts unavailable — we approximate listening intensity from recently-played frequency and library presence.
- **numpy for vectors**: genre affinity is a sparse vector, cosine similarity for matching. Simple and fast.

## Current Phase
Phase 2: Apple Music API Client

## Progress Notes
- **Phase 1 complete**: Project scaffold with uv, config loading, ES256 JWT auth, setup wizard, FastMCP server with health tool, 16 tests passing, ruff clean.
