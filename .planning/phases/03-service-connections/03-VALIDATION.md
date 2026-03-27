---
phase: 3
slug: service-connections
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-27
---

# Phase 3 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8.x + pytest-asyncio |
| **Config file** | `backend/pyproject.toml` |
| **Quick run command** | `cd backend && uv run python -m pytest tests/ -x -q` |
| **Full suite command** | `cd backend && uv run python -m pytest tests/ -v` |
| **Estimated runtime** | ~8 seconds |

---

## Sampling Rate

- **After every task commit:** Run quick command
- **After every plan wave:** Run full suite
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 8 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| TBD | TBD | TBD | SVCN-01 | integration | `pytest tests/test_services.py::test_spotify_connect` | ❌ W0 | ⬜ pending |
| TBD | TBD | TBD | SVCN-02 | integration | `pytest tests/test_services.py::test_apple_connect` | ❌ W0 | ⬜ pending |
| TBD | TBD | TBD | SVCN-03 | integration | `pytest tests/test_services.py::test_disconnect` | ❌ W0 | ⬜ pending |
| TBD | TBD | TBD | SVCN-04 | integration | `pytest tests/test_services.py::test_list_connections` | ❌ W0 | ⬜ pending |
| TBD | TBD | TBD | SVCN-05 | integration | `pytest tests/test_services.py::test_apple_reauth` | ❌ W0 | ⬜ pending |
| TBD | TBD | TBD | SVCN-06 | integration | `pytest tests/test_services.py::test_spotify_refresh` | ❌ W0 | ⬜ pending |

---

## Wave 0 Requirements

- [ ] `backend/tests/test_services.py` — stubs for SVCN-01 through SVCN-06
- [ ] Mock fixtures for Spotify OAuth responses and Apple Music token validation

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Full Spotify PKCE flow with real redirect | SVCN-01 | Requires Spotify developer app + browser | Register app, initiate /connect, authorize in browser, verify callback |
| MusicKit JS in browser | SVCN-02 | Requires browser with MusicKit JS | Load MusicKit JS page, authorize, verify token POSTed to backend |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity maintained
- [ ] Wave 0 covers all MISSING references
- [ ] Feedback latency < 8s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
