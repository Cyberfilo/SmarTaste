---
phase: 11-ui-design-frontend-shell
plan: 03
subsystem: ui
tags: [react, tanstack-query, shadcn-ui, recharts, recommendations, settings, byok, services]

# Dependency graph
requires:
  - phase: 11-ui-design-frontend-shell
    provides: Next.js 16 frontend scaffold, shadcn/ui, TanStack Query, API client with CSRF handling
provides:
  - Recommendations feed page with strategy/mood filters, feedback buttons, and 7-dimension score breakdown
  - Settings page with Spotify/Apple Music connection management and BYOK Claude API key management
  - TanStack Query hooks for recommendations, services, and Claude key APIs
  - Recharts radar chart for audio features visualization
affects: [11-04]

# Tech tracking
tech-stack:
  added: [shadcn/badge, shadcn/skeleton, shadcn/select, shadcn/separator, shadcn/dialog, shadcn/alert-dialog]
  patterns: [tanstack-query-hooks-per-domain, mutation-with-query-invalidation, expandable-card-details, mobile-select-desktop-buttons, horizontal-scrollable-pills]

key-files:
  created:
    - frontend/src/hooks/use-recommendations.ts
    - frontend/src/hooks/use-services.ts
    - frontend/src/hooks/use-claude-key.ts
    - frontend/src/components/recommendations/recommendation-card.tsx
    - frontend/src/components/recommendations/recommendation-feed.tsx
    - frontend/src/components/recommendations/strategy-selector.tsx
    - frontend/src/components/recommendations/mood-filter.tsx
    - frontend/src/components/recommendations/score-breakdown.tsx
    - frontend/src/components/settings/service-connections.tsx
    - frontend/src/components/settings/byok-key-manager.tsx
    - frontend/src/app/(app)/dashboard/recommendations/page.tsx
    - frontend/src/app/(app)/settings/page.tsx
  modified: []

key-decisions:
  - "Strategy selector uses native select on mobile, segmented button group on md: breakpoint"
  - "Mood filter as horizontally scrollable pill chips with overflow-x-auto on mobile"
  - "Score badge color gradient: red (<50%), amber (50-70%), emerald (>70%)"
  - "Audio features radar chart only shows when API returns non-null features"
  - "Apple Music connect shows TODO toast for MusicKit JS integration (requires Apple script loading)"
  - "BYOK key input uses type=password for security with sk-ant-... placeholder"

patterns-established:
  - "Domain-specific hooks: one hook file per API domain (use-recommendations, use-services, use-claude-key)"
  - "Mutation+invalidation: POST/DELETE mutations invalidate related query keys on success"
  - "Expandable card: collapsed state with Details button, expanded fetches breakdown on demand"
  - "Confirm dialogs: AlertDialog for destructive actions (disconnect, delete key)"
  - "Responsive controls: mobile-first with select/scroll, desktop inline buttons/wrap"

requirements-completed: [INFR-04, RECO-01, RECO-02, RECO-03, RECO-04, RECO-05, RECO-06, RECO-07, RECO-08, SVCN-01, SVCN-02, SVCN-03, SVCN-04, BYOK-01, BYOK-02, BYOK-03, BYOK-04]

# Metrics
duration: 5min
completed: 2026-03-27
---

# Phase 11 Plan 03: Recommendations & Settings Summary

**Recommendations feed with strategy/mood filters, feedback, and 7-dimension score breakdown, plus settings page with Spotify/Apple Music connections and BYOK Claude key management**

## Performance

- **Duration:** 5 min
- **Started:** 2026-03-27T20:36:15Z
- **Completed:** 2026-03-27T20:41:44Z
- **Tasks:** 2
- **Files modified:** 18

## Accomplishments
- Recommendations feed page with interactive cards showing artwork, score badge, explanation, genre pills, and thumbs up/down feedback buttons
- Strategy selector (5 discovery strategies) and mood filter (7 moods) controlling recommendation fetch via TanStack Query
- Expandable score breakdown with 7 horizontal dimension bars and optional Recharts radar chart for audio features
- Settings page with Spotify OAuth connect flow, Apple Music connect placeholder, and disconnect with confirmation dialogs
- BYOK Claude key manager with save/validate/update/remove flows and cost estimate display

## Task Commits

Each task was committed atomically:

1. **Task 1: Recommendations feed with strategy/mood filters, feedback, and score breakdown** - `8b2ea49` (feat)
2. **Task 2: Settings page with service connections and BYOK API key management** - `abd63d2` (feat)

## Files Created/Modified
- `frontend/src/hooks/use-recommendations.ts` - TanStack Query hooks for recommendations, feedback, breakdown, audio features APIs
- `frontend/src/hooks/use-services.ts` - TanStack Query hooks for services list, Spotify connect, Apple Music token, disconnect
- `frontend/src/hooks/use-claude-key.ts` - TanStack Query hooks for Claude key status, store, validate, delete, cost estimate
- `frontend/src/components/recommendations/strategy-selector.tsx` - Mobile select / desktop segmented buttons for 5 strategies
- `frontend/src/components/recommendations/mood-filter.tsx` - Horizontal scrollable pill chips for 7 moods
- `frontend/src/components/recommendations/recommendation-card.tsx` - Card with artwork, info, score, explanation, genres, feedback
- `frontend/src/components/recommendations/recommendation-feed.tsx` - Feed combining controls, loading skeletons, empty state
- `frontend/src/components/recommendations/score-breakdown.tsx` - 7-dimension bars + Recharts radar for audio features
- `frontend/src/components/settings/service-connections.tsx` - Spotify/Apple Music connect/disconnect with status badges
- `frontend/src/components/settings/byok-key-manager.tsx` - API key input, validate, update, remove, cost estimate
- `frontend/src/app/(app)/dashboard/recommendations/page.tsx` - Recommendations page route
- `frontend/src/app/(app)/settings/page.tsx` - Settings page route with max-w-2xl layout
- `frontend/src/components/ui/badge.tsx` - shadcn badge component
- `frontend/src/components/ui/skeleton.tsx` - shadcn skeleton component
- `frontend/src/components/ui/select.tsx` - shadcn select component
- `frontend/src/components/ui/separator.tsx` - shadcn separator component
- `frontend/src/components/ui/dialog.tsx` - shadcn dialog component
- `frontend/src/components/ui/alert-dialog.tsx` - shadcn alert-dialog component

## Decisions Made
- Strategy selector uses native `<select>` on mobile for compactness, segmented button group on `md:` for desktop
- Mood filter uses horizontal scrollable pills with `overflow-x-auto` on mobile, wrapping on desktop
- Score badge uses color gradient: red for <50%, amber for 50-70%, emerald for >70%
- Audio features radar chart only renders when the API returns non-null feature values
- Apple Music connect fetches developer token but shows info toast about MusicKit JS requirement (full browser-based MusicKit JS authorization needs Apple's script tag -- documented as TODO)
- BYOK key input uses `type="password"` with `sk-ant-...` placeholder for security

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None.

## User Setup Required
None - no external service configuration required. Frontend connects to backend at localhost:8000 via existing .env.local configuration from Plan 11-01.

## Next Phase Readiness
- Recommendations feed and settings pages are complete and ready for use
- Chat page (Plan 11-04) is the final remaining frontend page
- All TanStack Query hooks follow established patterns for consistent API integration
- Apple Music MusicKit JS integration noted as TODO for future enhancement

## Known Stubs

- `frontend/src/components/settings/service-connections.tsx` line ~147: Apple Music connect shows info toast instead of full MusicKit JS authorization flow. This is documented in the plan as intentional -- MusicKit JS requires loading Apple's external script and the plan explicitly states to create the button and API call with a TODO comment. The developer token endpoint is wired correctly.

## Self-Check: PASSED

- All 18 key files verified present
- Both task commits (8b2ea49, abd63d2) verified in git log
- `npm run build` succeeds with zero errors
- No unintentional stubs found

---
*Phase: 11-ui-design-frontend-shell*
*Completed: 2026-03-27*
