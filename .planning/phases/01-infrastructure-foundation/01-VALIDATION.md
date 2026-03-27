---
phase: 1
slug: infrastructure-foundation
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-26
---

# Phase 1 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8.x + pytest-asyncio 0.23.x |
| **Config file** | `backend/pyproject.toml` (to be created in Phase 1) |
| **Quick run command** | `cd backend && uv run pytest tests/ -x -q` |
| **Full suite command** | `cd backend && uv run pytest tests/ -v` |
| **Estimated runtime** | ~5 seconds |

---

## Sampling Rate

- **After every task commit:** Run `cd backend && uv run pytest tests/ -x -q`
- **After every plan wave:** Run `cd backend && uv run pytest tests/ -v`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 5 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| TBD | TBD | TBD | INFR-01 | integration | `uv run pytest tests/test_db.py` | ❌ W0 | ⬜ pending |
| TBD | TBD | TBD | INFR-02 | integration | `alembic upgrade head && alembic downgrade -1` | ❌ W0 | ⬜ pending |
| TBD | TBD | TBD | INFR-03 | unit | `uv run pytest tests/test_encryption.py` | ❌ W0 | ⬜ pending |
| TBD | TBD | TBD | INFR-05 | integration | `docker-compose up -d && curl localhost:8000/health` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `backend/tests/conftest.py` — shared fixtures (async event loop, test DB, test client)
- [ ] `backend/tests/test_db.py` — stubs for INFR-01 (multi-user schema verification)
- [ ] `backend/tests/test_encryption.py` — stubs for INFR-03 (encrypt/decrypt round-trip)
- [ ] pytest + pytest-asyncio in dev dependencies

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| docker-compose starts PostgreSQL + backend | INFR-05 | Requires Docker daemon running | `docker-compose up -d`, verify both containers healthy |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 5s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
