# Phase 11: UI Design & Frontend Shell - Context

**Gathered:** 2026-03-27
**Status:** Ready for planning
**Source:** Autonomous smart discuss (recommended defaults)
**Skill note:** Use /ui-ux-pro-max skill during execution for design decisions

<domain>
## Phase Boundary

This phase delivers the complete Next.js frontend: login/signup pages, dashboard with taste profile + stats + recommendations, settings page (service connections, BYOK key), and Claude chat interface. Covers INFR-04 (responsive design). Uses the ui-ux-pro-max skill for high-quality, distinctive design that avoids generic AI aesthetics.

Requirements: INFR-04 (responsive design), plus visual implementations for all user-facing features from Phases 2-10.

</domain>

<decisions>
## Implementation Decisions

### Tech Stack
- **D-01:** Next.js 16 with App Router and React Server Components.
- **D-02:** Tailwind CSS 4 + shadcn/ui for components.
- **D-03:** Zustand for client state, TanStack Query for server state.
- **D-04:** Use /ui-ux-pro-max skill for design decisions (colors, typography, component styling).

### Pages
- **D-05:** Login/Signup page — email/password form, calls backend /api/auth/signup and /api/auth/login.
- **D-06:** Dashboard — tabs or sections for Taste Profile, Listening Stats, Recommendations, Chat.
- **D-07:** Settings — Service connections (Spotify/Apple Music connect/disconnect), BYOK API key management.
- **D-08:** Chat — Streaming Claude chat interface with tool activity indicators.

### API Integration
- **D-09:** Frontend calls backend API at localhost:8000 (configurable via env var).
- **D-10:** Auth tokens managed via httpOnly cookies (set by backend). Frontend just includes credentials in fetch.
- **D-11:** SSE for Claude chat streaming.

### Responsive Design (INFR-04)
- **D-12:** Mobile-first responsive design. All pages work on desktop and mobile.
- **D-13:** Tailwind breakpoints: sm (640px), md (768px), lg (1024px).

### Claude's Discretion
- Color palette and typography (use ui-ux-pro-max for guidance)
- Component library choices (shadcn/ui variants)
- Layout structure (sidebar vs top nav, grid layouts)
- Animation and transitions
- Chart library for taste/stats visualizations (Recharts recommended)

</decisions>

<canonical_refs>
## Canonical References

### Backend API (all endpoints the frontend calls)
- Auth: POST /api/auth/signup, /login, /logout, /refresh, /me
- Services: GET /api/services, POST spotify/connect, GET spotify/callback, POST apple-music/connect, DELETE /{service}
- BYOK: POST /api/claude/key, GET /status, POST /validate, DELETE, GET /cost
- Taste: GET /api/taste/profile, /genres, /artists, /audio-traits
- Stats: GET /api/stats/tracks, /artists, /genres
- Recommendations: GET /api/recommendations, POST /{id}/feedback
- Breakdown: GET /api/recommendations/{id}/breakdown
- Audio: GET /api/tracks/{id}/audio-features
- Chat: POST /api/chat/message (SSE), GET/DELETE /api/chat/conversations

### Research
- `.planning/research/STACK.md` — Next.js 16, Tailwind 4, shadcn/ui, Recharts, Vercel AI SDK

</canonical_refs>

<code_context>
## Existing Code Insights

### Frontend Directory
- `frontend/.gitkeep` exists (placeholder from Phase 1)
- No frontend code yet — greenfield Next.js setup

### Backend API Contract
- All endpoints authenticated via JWT httpOnly cookies
- CSRF double-submit cookie pattern on POST/PUT/DELETE
- SSE streaming on POST /api/chat/message
- JSON responses throughout

</code_context>

<specifics>
## Specific Ideas

- Use /ui-ux-pro-max for the design system (colors, fonts, spacing)
- Music-themed aesthetic — dark mode default, album art colors as accents
- Dashboard should feel like stats.fm / Spotify Wrapped quality
- Chat should feel native, not like a chatbot widget

</specifics>

<deferred>
## Deferred Ideas

- PWA / offline support → v2
- Dark/light theme toggle → v2 (start with dark only)
- Accessibility audit → v2

</deferred>

---

*Phase: 11-ui-design-frontend-shell*
*Context gathered: 2026-03-27 via autonomous smart discuss*
