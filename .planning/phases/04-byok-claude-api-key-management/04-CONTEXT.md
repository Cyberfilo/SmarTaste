# Phase 4: BYOK Claude API Key Management - Context

**Gathered:** 2026-03-27
**Status:** Ready for planning
**Source:** Auto-mode (recommended defaults selected)

<domain>
## Phase Boundary

This phase delivers BYOK (Bring Your Own Key) Claude API key management: users can securely store their Anthropic API key (encrypted), validate it works, update or remove it, and see estimated cost per chat message. Builds on Phase 2's user auth and Phase 1's Fernet encryption.

Requirements: BYOK-01 (store encrypted), BYOK-02 (validate key), BYOK-03 (update/remove), BYOK-04 (cost transparency).

</domain>

<decisions>
## Implementation Decisions

### Key Storage
- **D-01:** API key encrypted via Fernet EncryptionService and stored in a new `api_keys` column on the `users` table (or a new `user_api_keys` table). Decrypted only when needed for Claude API calls.
- **D-02:** Key is never returned to the frontend after storage. Frontend only sees "key configured: yes/no" and a masked version (sk-ant-...XXXX).

### Key Validation
- **D-03:** Validation calls the Anthropic API with the user's key using a cheap request (e.g., `messages.create` with a minimal prompt, max_tokens=1). If 200, key is valid. If 401/403, key is invalid.
- **D-04:** Validation happens on-demand when user clicks "validate" — not automatically on every page load.

### Key Management
- **D-05:** Endpoints: POST /api/claude/key (store/update), DELETE /api/claude/key (remove), GET /api/claude/key/status (check if configured + masked preview), POST /api/claude/key/validate (test key).
- **D-06:** Updating a key overwrites the existing encrypted value. No key history.

### Cost Transparency
- **D-07:** Estimated cost per message displayed before the user sends their first chat message. Based on average token counts from Claude's pricing (input/output tokens).
- **D-08:** Cost estimation is a static calculation based on model pricing, not real-time API usage tracking. Show approximate ranges (e.g., "$0.01-0.05 per message").

### Claude's Discretion
- Whether to use the `users` table directly (add `anthropic_api_key_encrypted` column) or a separate `user_api_keys` table
- Anthropic SDK version and exact validation call pattern
- Whether to add Alembic migration for new column/table
- Test mocking strategy for Anthropic API validation calls
- API-only in this phase (no frontend UI — matches Phase 2/3 pattern)

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Phase 1 Outputs
- `backend/src/musicmind/security/encryption.py` — Fernet EncryptionService for key encryption
- `backend/src/musicmind/config.py` — Settings class
- `backend/src/musicmind/db/schema.py` — Database schema (users table)

### Phase 2 Outputs
- `backend/src/musicmind/auth/dependencies.py` — get_current_user dependency
- `backend/src/musicmind/auth/router.py` — Auth router pattern to follow

### Research
- `.planning/research/STACK.md` — Anthropic Python SDK recommendation

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- EncryptionService for key encryption at rest
- get_current_user dependency for protecting endpoints
- Router pattern from auth and services modules

### Established Patterns
- FastAPI router + SQLAlchemy Core + Pydantic schemas
- Encrypted storage via Fernet
- API-only endpoints (no frontend in backend phases)

</code_context>

<specifics>
## Specific Ideas

No specific requirements — auto-mode selected standard BYOK patterns.

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope.

</deferred>

---

*Phase: 04-byok-claude-api-key-management*
*Context gathered: 2026-03-27 via auto-mode*
