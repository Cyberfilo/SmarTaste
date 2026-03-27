---
phase: 11-ui-design-frontend-shell
plan: 01
subsystem: ui
tags: [nextjs, tailwind, shadcn-ui, zustand, tanstack-query, react, typescript]

# Dependency graph
requires:
  - phase: 02-user-accounts
    provides: Backend auth API endpoints (signup, login, logout, refresh, me)
provides:
  - Next.js 16 frontend project scaffold in frontend/
  - Tailwind CSS 4 with dark music-themed aesthetic
  - shadcn/ui component library (button, input, card, label, sonner)
  - API client with CSRF handling, auto token refresh, credentials include
  - Zustand auth store for client-side user state
  - TanStack Query provider for server state management
  - Login and signup pages calling backend auth API
  - App shell layout with desktop sidebar and mobile bottom nav
  - Route protection via proxy.ts (Next.js 16 convention)
affects: [11-02, 11-03, 11-04]

# Tech tracking
tech-stack:
  added: [next@16.2.1, react@19.2.4, tailwindcss@4, shadcn/ui, zustand@5, @tanstack/react-query@5, recharts@3, lucide-react, sonner]
  patterns: [app-router-route-groups, client-components-use-client, zustand-store, csrf-double-submit, proxy-route-protection]

key-files:
  created:
    - frontend/package.json
    - frontend/src/lib/api.ts
    - frontend/src/stores/auth-store.ts
    - frontend/src/lib/auth.ts
    - frontend/src/providers/query-provider.tsx
    - frontend/src/app/(auth)/login/page.tsx
    - frontend/src/app/(auth)/signup/page.tsx
    - frontend/src/app/(auth)/layout.tsx
    - frontend/src/app/(app)/layout.tsx
    - frontend/src/app/(app)/dashboard/page.tsx
    - frontend/src/proxy.ts
    - frontend/src/components/ui/button.tsx
    - frontend/src/components/ui/input.tsx
    - frontend/src/components/ui/card.tsx
    - frontend/src/components/ui/label.tsx
    - frontend/src/components/ui/sonner.tsx
  modified:
    - frontend/src/app/layout.tsx
    - frontend/src/app/globals.css
    - frontend/src/app/page.tsx

key-decisions:
  - "proxy.ts instead of middleware.ts -- Next.js 16 renamed middleware to proxy convention"
  - "Dark-only theme with emerald/teal primary accent -- no light/dark toggle per deferred decisions"
  - "Inter font from Google Fonts for clean, modern typography"
  - "shadcn base-nova style with neutral base color"
  - "toast deprecated in shadcn v4 -- using sonner component instead"

patterns-established:
  - "Route groups: (auth) for login/signup, (app) for protected pages"
  - "API client pattern: apiFetch with CSRF, auto-refresh, credentials:include"
  - "Auth store: Zustand with checkAuth() calling /api/auth/me"
  - "Mobile-first responsive: bottom nav on mobile, sidebar on lg:"
  - "Error display via sonner toast, not inline form errors"

requirements-completed: [INFR-04, ACCT-01, ACCT-02, ACCT-03, ACCT-04]

# Metrics
duration: 6min
completed: 2026-03-27
---

# Phase 11 Plan 01: Frontend Shell Summary

**Next.js 16 frontend scaffold with Tailwind 4 dark theme, shadcn/ui, Zustand auth store, login/signup pages calling backend API, and proxy-based route protection**

## Performance

- **Duration:** 6 min
- **Started:** 2026-03-27T20:25:33Z
- **Completed:** 2026-03-27T20:31:14Z
- **Tasks:** 2
- **Files modified:** 31

## Accomplishments
- Next.js 16 project fully scaffolded with App Router, Tailwind CSS 4, shadcn/ui, Zustand, TanStack Query, and Recharts
- Login and signup pages with form validation, error handling via sonner toast, and backend API integration
- Protected app shell with desktop sidebar (lg:) and mobile bottom tab bar navigation
- Route protection via proxy.ts redirecting unauthenticated users to /login

## Task Commits

Each task was committed atomically:

1. **Task 1: Scaffold Next.js 16 project** - `daaf0f2` (feat)
2. **Task 2: Login/signup pages and app shell** - `1f60324` (feat)

## Files Created/Modified
- `frontend/package.json` - Next.js 16 project with all dependencies
- `frontend/src/lib/api.ts` - API client with CSRF handling, auto token refresh, typed auth wrappers
- `frontend/src/stores/auth-store.ts` - Zustand auth store (user, isLoading, isAuthenticated, checkAuth)
- `frontend/src/lib/auth.ts` - requireAuth helper and CSRF token reader
- `frontend/src/providers/query-provider.tsx` - TanStack Query provider (5min staleTime, retry:1)
- `frontend/src/app/layout.tsx` - Root layout with Inter font, dark class, QueryProvider, Toaster
- `frontend/src/app/globals.css` - Dark music-themed CSS variables (emerald primary, zinc backgrounds)
- `frontend/src/app/page.tsx` - Root redirect to /dashboard or /login
- `frontend/src/app/(auth)/layout.tsx` - Centered card layout with MusicMind branding
- `frontend/src/app/(auth)/login/page.tsx` - Login form (email, password) calling POST /api/auth/login
- `frontend/src/app/(auth)/signup/page.tsx` - Signup form (email, password, display name) calling POST /api/auth/signup
- `frontend/src/app/(app)/layout.tsx` - Protected layout with nav shell, auth check, logout
- `frontend/src/app/(app)/dashboard/page.tsx` - Dashboard placeholder confirming auth flow
- `frontend/src/proxy.ts` - Route protection (redirect unauth to /login, auth on /login to /dashboard)
- `frontend/src/components/ui/` - shadcn components: button, input, card, label, sonner

## Decisions Made
- Used proxy.ts instead of middleware.ts per Next.js 16 convention (middleware deprecated, renamed to proxy)
- Dark-only theme with emerald/teal accent -- matches music/audio aesthetic, no toggle per deferred decisions
- Inter font for clean modern typography (replacing Geist from create-next-app defaults)
- shadcn toast component deprecated in v4 -- used sonner component instead
- Error display via sonner toast (not inline form errors) for cleaner form UI

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Renamed middleware.ts to proxy.ts for Next.js 16 compatibility**
- **Found during:** Task 2 (middleware creation)
- **Issue:** Next.js 16 deprecates `middleware.ts` in favor of `proxy.ts` with exported `proxy()` function
- **Fix:** Renamed file and exported function to match Next.js 16 convention
- **Files modified:** frontend/src/proxy.ts
- **Verification:** Build succeeds without deprecation warning
- **Committed in:** 1f60324 (Task 2 commit)

**2. [Rule 3 - Blocking] shadcn toast component deprecated in v4**
- **Found during:** Task 1 (shadcn component installation)
- **Issue:** `npx shadcn add toast` fails -- toast component deprecated in shadcn v4, replaced by sonner
- **Fix:** Installed sonner component instead of toast
- **Files modified:** frontend/src/components/ui/sonner.tsx
- **Verification:** Build succeeds, toast notifications work via sonner
- **Committed in:** daaf0f2 (Task 1 commit)

---

**Total deviations:** 2 auto-fixed (2 blocking)
**Impact on plan:** Both fixes necessary for Next.js 16 and shadcn v4 compatibility. No scope creep.

## Issues Encountered
None beyond the auto-fixed deviations above.

## User Setup Required
None - no external service configuration required. Frontend connects to backend at localhost:8000 via .env.local.

## Next Phase Readiness
- Frontend scaffold complete -- all subsequent plans (11-02, 11-03, 11-04) can build on this foundation
- Auth flow wired end-to-end (login, signup, logout, route protection)
- App shell navigation ready for dashboard content, chat, and settings pages
- Dark theme and component library established for consistent UI across all pages

## Known Stubs

- `frontend/src/app/(app)/dashboard/page.tsx` - Dashboard is an intentional placeholder. Real content is delivered in Plan 02 as specified.

## Self-Check: PASSED

- All 19 key files verified present
- Both task commits (daaf0f2, 1f60324) verified in git log
- `npm run build` succeeds with zero errors
- No unintentional stubs found

---
*Phase: 11-ui-design-frontend-shell*
*Completed: 2026-03-27*
