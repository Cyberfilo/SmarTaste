# Phase 2: User Accounts - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-03-27
**Phase:** 2-User Accounts
**Areas discussed:** Auth strategy, Password handling, Session persistence, CSRF protection
**Mode:** Auto (all areas selected, recommended defaults chosen)

---

## Auth Strategy

| Option | Description | Selected |
|--------|-------------|----------|
| JWT in httpOnly cookies (Recommended) | SPA-friendly, XSS-protected, standard pattern | ✓ [auto] |
| Session tokens in database | Server-side sessions, more traditional | |
| JWT in localStorage | Simpler but XSS-vulnerable | |

**User's choice:** [auto] JWT in httpOnly secure cookies (recommended default)

---

## Password Handling

| Option | Description | Selected |
|--------|-------------|----------|
| bcrypt via passlib (Recommended) | Industry standard, sufficient for this scale | ✓ [auto] |
| Argon2 | More modern, higher memory cost | |
| scrypt | Alternative, less common in Python | |

**User's choice:** [auto] bcrypt via passlib (recommended default)

---

## Session Persistence

| Option | Description | Selected |
|--------|-------------|----------|
| Short access + long refresh (Recommended) | 30min access, 7-day refresh, database-backed revocation | ✓ [auto] |
| Long-lived access token only | Simpler but less secure, no revocation | |
| Server-side sessions | No JWT, traditional sessions | |

**User's choice:** [auto] Short access token (30min) + long refresh token (7 days) (recommended default)

---

## CSRF Protection

| Option | Description | Selected |
|--------|-------------|----------|
| Double-submit cookie (Recommended) | Works with httpOnly JWT + SPA, standard pattern | ✓ [auto] |
| Synchronizer token | Server-side state, more complex | |
| SameSite=Strict only | Simpler but breaks cross-origin flows | |

**User's choice:** [auto] Double-submit cookie pattern (recommended default)

---

## Claude's Discretion

- API endpoint paths
- JWT library choice
- Whether to include minimal frontend auth pages
- Test fixture patterns

## Deferred Ideas

None
