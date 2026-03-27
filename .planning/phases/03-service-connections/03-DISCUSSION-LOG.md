# Phase 3: Service Connections - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.

**Date:** 2026-03-27
**Phase:** 3-Service Connections
**Areas discussed:** Spotify OAuth, Apple Music auth, Connection management, Token lifecycle
**Mode:** Auto (all areas selected, recommended defaults chosen)

---

## Spotify OAuth Flow

| Option | Description | Selected |
|--------|-------------|----------|
| Backend handles code exchange (Recommended) | Backend-initiated PKCE, callback on backend, tokens stay server-side | ✓ [auto] |
| Frontend handles code exchange | Frontend gets code, sends to backend | |

**User's choice:** [auto] Backend handles code exchange (recommended default)

## Apple Music Auth

| Option | Description | Selected |
|--------|-------------|----------|
| Frontend MusicKit JS → POST to backend (Recommended) | MusicKit JS in browser, token POSTed to backend API | ✓ [auto] |
| Server-side MusicKit (not possible) | MusicKit JS is browser-only | |

**User's choice:** [auto] Frontend MusicKit JS → POST to backend (recommended default)

## Connection Management

| Option | Description | Selected |
|--------|-------------|----------|
| API-only endpoints (Recommended) | Backend endpoints, no frontend UI in this phase | ✓ [auto] |
| Include minimal settings page | Basic frontend for connection management | |

**User's choice:** [auto] API-only endpoints (recommended default)

## Token Lifecycle

| Option | Description | Selected |
|--------|-------------|----------|
| On-demand refresh (Recommended) | Refresh Spotify tokens before API calls, sufficient for friend-group | ✓ [auto] |
| Background refresh job | Periodic token refresh, more complex | |

**User's choice:** [auto] On-demand refresh (recommended default)

## Claude's Discretion

- PKCE implementation details
- SpotifyOAuth helper class design
- Apple Developer Token generation approach
- Test strategy for OAuth mocking
- Schema migration needs

## Deferred Ideas

None
