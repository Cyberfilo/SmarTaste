# MusicMind Web

## What This Is

A hybrid dashboard + AI chat webapp for music discovery, built on top of an existing MCP-based recommendation engine. Users connect their Spotify and/or Apple Music accounts, bring their own Claude API key, and get a unified taste profile with personalized recommendations — plus a Claude chat interface for deeper musical exploration. Designed for a small group of friends, not a public product.

## Core Value

Users get genuinely good music recommendations powered by real audio analysis and their actual listening data across services — not just "people who liked X also liked Y."

## Requirements

### Validated

<!-- Shipped and confirmed valuable — existing MusicMind MCP capabilities. -->

- ✓ Apple Music API client with 25+ endpoints — existing (`src/musicmind/client.py`)
- ✓ ES256 JWT developer token generation — existing (`src/musicmind/auth.py`)
- ✓ Apple Music User Token OAuth flow — existing (`src/musicmind/setup.py`)
- ✓ SQLite persistence for songs, history, profiles, feedback, audio features — existing (`src/musicmind/db/`)
- ✓ Taste profile building with genre vectors, artist affinity, release year distribution — existing (`src/musicmind/engine/profile.py`)
- ✓ 7-dimension adaptive scoring with weight optimization from feedback — existing (`src/musicmind/engine/scorer.py`, `weights.py`)
- ✓ 4 discovery strategies: similar artist crawl, genre adjacent, editorial mining, chart filter — existing (`src/musicmind/engine/discovery.py`)
- ✓ Regional genre prioritization for non-English music markets — existing
- ✓ Mood filtering for contextual recommendations — existing (`src/musicmind/engine/mood.py`)
- ✓ Audio feature extraction via librosa (Tier 2) — existing (`src/musicmind/engine/audio.py`)
- ✓ SoundAnalysis classification via macOS (Tier 3) — existing (`src/musicmind/engine/classifier.py`)
- ✓ MCP server with 30+ tools organized by domain — existing (`src/musicmind/server.py`, `tools/`)
- ✓ Multi-user PostgreSQL database with user-scoped data isolation — Phase 1
- ✓ Database migrations via Alembic — Phase 1
- ✓ API key and OAuth token encryption at rest (Fernet) — Phase 1
- ✓ Local-first deployment via docker-compose — Phase 1
- ✓ User accounts with signup, login, persistent JWT sessions, logout — Phase 2
- ✓ Session security with httpOnly cookies and CSRF protection — Phase 2

### Active

<!-- Current scope. Building toward these. -->

- [ ] Web application with dashboard and Claude chat interface
- [ ] Spotify OAuth integration for user account connection
- [ ] Apple Music OAuth integration adapted for webapp context
- [ ] Spotify Web API client (library, recently played, saved tracks, playlists)
- [ ] Spotify audio features API integration as primary audio data source
- [ ] Unified multi-service data model (normalize Apple Music + Spotify data)
- [ ] BYOK Claude API integration with tool_use for the recommendation engine
- [ ] Dashboard: taste profile visualization (genres, moods, how taste changes over time)
- [ ] Dashboard: recommendation feed with explanations
- [ ] Claude chat interface for conversational music exploration
- [ ] Multi-user database (upgrade from single-user SQLite)

### Out of Scope

- Public SaaS / billing / payment — small friend group only
- Mobile native app — web-only for now
- Social features between users — individual experience
- Real-time playback control from the webapp — use native apps for playback
- Streaming audio within the webapp — webapp discovers, native apps play
- User analytics / admin panel — not needed at this scale

## Context

**Existing codebase:** MusicMind MCP v2.20 — a fully functional Python MCP server connecting Claude Desktop to Apple Music. The engine, scorer, discovery strategies, and database layer are production-tested and sophisticated. The challenge is not building recommendations from scratch but adapting the existing engine for multi-service, multi-user, web-delivered use.

**Spotify advantage:** Spotify's Web API provides audio features (danceability, energy, valence, tempo, acousticness, instrumentalness, speechiness, liveness, loudness, key, mode) per track for free via API. This replaces the current librosa-based Tier 2 extraction (which requires ffmpeg + downloading 30s previews) with a simple API call. Librosa remains as fallback enrichment for Apple Music-only tracks.

**Apple Music limitations:** Apple Music API lacks audio features entirely. Library songs lack play counts (only available via native MusicKit on iOS/macOS). Recently played is capped at 50 with no timestamps. These constraints are already handled in the existing engine.

**Claude integration model:** Each user provides their own Anthropic API key. The backend uses the Anthropic API with tool_use, defining MusicMind engine functions as Claude tools. This replicates the MCP experience but through a web UI, where Claude can call the same recommendation/discovery/taste tools.

**Deployment:** Local-first. Run on the developer's machine initially, deploy to cloud later if needed.

## Constraints

- **Auth complexity**: Three OAuth flows needed (Spotify, Apple Music, user accounts) — each with different requirements
- **Apple Music**: Requires Apple Developer account with MusicKit key (.p8 file) — already have this
- **Spotify**: Requires Spotify Developer app registration for OAuth client credentials
- **Claude BYOK**: Users need their own Anthropic API keys — no free tier for AI features
- **Data normalization**: Apple Music and Spotify use completely different data models, IDs, and genre taxonomies
- **Rate limits**: Apple Music ~20 req/sec (undocumented), Spotify 100+ req/sec but with monthly quotas

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| BYOK Claude (users bring API keys) | Small group, no need to subsidize AI costs | — Pending |
| Spotify audio features as primary, librosa as fallback | Free API data > local extraction requiring ffmpeg | — Pending |
| Keep Python engine, wrap with web API | Engine is sophisticated and well-tested, rewriting would be waste | — Pending |
| Local-first deployment | Small group, no need for cloud infra yet | — Pending |
| Both services unified, not pick-one | Users may have both services; unified view is the differentiator | — Pending |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd:transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd:complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-03-26 after Phase 1 completion*
