---
phase: 2
slug: user-accounts
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-27
---

# Phase 2 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8.x + pytest-asyncio |
| **Config file** | `backend/pyproject.toml` |
| **Quick run command** | `cd backend && uv run python -m pytest tests/ -x -q` |
| **Full suite command** | `cd backend && uv run python -m pytest tests/ -v` |
| **Estimated runtime** | ~5 seconds |

---

## Sampling Rate

- **After every task commit:** Run `cd backend && uv run python -m pytest tests/ -x -q`
- **After every plan wave:** Run `cd backend && uv run python -m pytest tests/ -v`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 5 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| TBD | TBD | TBD | ACCT-01 | integration | `pytest tests/test_auth.py::test_signup` | ❌ W0 | ⬜ pending |
| TBD | TBD | TBD | ACCT-02 | integration | `pytest tests/test_auth.py::test_login_persistent` | ❌ W0 | ⬜ pending |
| TBD | TBD | TBD | ACCT-03 | integration | `pytest tests/test_auth.py::test_logout` | ❌ W0 | ⬜ pending |
| TBD | TBD | TBD | ACCT-04 | integration | `pytest tests/test_auth.py::test_csrf` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `backend/tests/test_auth.py` — stubs for ACCT-01 through ACCT-04
- [ ] Auth test fixtures in conftest.py (test user creation, authenticated client)

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Browser session persistence after close/reopen | ACCT-02 | Requires real browser state | Close browser tab, reopen, check still logged in |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 5s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
