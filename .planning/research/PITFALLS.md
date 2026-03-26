# Domain Pitfalls

**Domain:** Multi-service music discovery webapp (Spotify + Apple Music + Claude AI)
**Researched:** 2026-03-26

---

## Critical Pitfalls

Mistakes that cause rewrites, loss of core features, or architectural dead ends.

---

### Pitfall 1: Spotify Audio Features API Is Dead for New Apps

**What goes wrong:** The project's KEY DECISION assumes "Spotify audio features as primary, librosa as fallback" -- but Spotify deprecated the `/v1/audio-features` and `/v1/audio-analysis` endpoints on November 27, 2024. New apps created after that date receive a 403 Forbidden error. Since MusicMind has no existing Spotify app with extended quota access, this endpoint is permanently unavailable.

**Why it happens:** The PROJECT.md was written based on pre-November-2024 Spotify API capabilities. The deprecation was sudden and widely reported only in developer communities.

**Consequences:** The entire audio feature strategy collapses. Without Spotify audio features (danceability, energy, valence, tempo, acousticness, instrumentalness, speechiness, liveness), the 7-dimension scoring engine loses its "audio" dimension (weighted 0.20) for Spotify tracks. The project degrades to metadata-only scoring unless an alternative audio data source is found.

**Prevention:**
- Elevate librosa from "fallback" to primary audio feature extraction for ALL services.
- Investigate whether Spotify's 30-second preview URLs are still available (also deprecated for new apps in development mode -- verify with an actual API call during Phase 1).
- If previews are gone, explore: (a) using Apple Music preview URLs for cross-matched tracks via ISRC, (b) sourcing previews from other APIs (Deezer provides 30s previews freely), (c) accepting metadata-only scoring for Spotify tracks.
- Update PROJECT.md Key Decisions table immediately.

**Detection:** First Spotify API integration test will fail with 403 on `/v1/audio-features`. Do not wait until scoring integration to discover this.

**Confidence:** HIGH -- confirmed by Spotify's official blog post (Nov 2024), community forum threads (2025-2026), and the February 2026 migration guide which does not restore these endpoints.

**Phase impact:** Phase 1 (Spotify integration). Must be addressed before architecture decisions about scoring are finalized.

**Sources:**
- [Spotify Web API Changes Nov 2024](https://developer.spotify.com/blog/2024-11-27-changes-to-the-web-api)
- [February 2026 Migration Guide](https://developer.spotify.com/documentation/web-api/tutorials/february-2026-migration-guide)
- [Community confirmation of 403 errors](https://community.spotify.com/t5/Spotify-for-Developers/Web-API-Get-Track-s-Audio-Features-403-error/td-p/6654507)

---

### Pitfall 2: Spotify's February 2026 API Gutting Breaks Standard Patterns

**What goes wrong:** Spotify's February 2026 development mode changes remove or restructure nearly every commonly-used endpoint. Developers building against 2024-era tutorials and examples will write code against endpoints that no longer exist.

**Why it happens:** Spotify is aggressively restricting development mode access to combat "AI-aided or automated usage." The changes took effect February 11, 2026 (new apps) and March 9, 2026 (existing apps).

**Consequences:** The following assumptions from PROJECT.md are now constrained:
- **Batch track fetching removed:** `GET /tracks?ids=...` no longer works; must fetch tracks individually. A user library of 500 tracks requires 500 API calls instead of 25.
- **Search limit reduced:** Max 10 results per search (was 50), default 5 (was 20). Discovery strategies that search Spotify need heavy pagination.
- **Artist top tracks removed:** `GET /artists/{id}/top-tracks` is gone. Similar artist crawl strategy cannot use this as a song source.
- **Browse/new-releases removed:** `GET /browse/new-releases` and category endpoints are gone. Chart-based discovery has no Spotify source.
- **Premium required:** The app owner (developer) must have Spotify Premium to maintain a development mode app.
- **5 user limit:** Development mode caps at 5 authorized users. The "small group of friends" use case fits IF the group is 5 or fewer. Extended quota requires a formal application with "established, scalable, and impactful use cases."
- **Removed fields:** `popularity`, `followers`, `external_ids` (including ISRC!) are stripped from dev mode responses.

**Prevention:**
- Design the Spotify client from day one against the February 2026 API surface, not older documentation.
- Accept that Spotify in development mode is severely limited. The primary value is: user library access, recently played, saved tracks, playlists, and basic search.
- For cross-service matching, if `external_ids` (ISRC) is removed from dev mode track responses, matching must fall back to title+artist fuzzy matching. Verify this with an actual API call in Phase 1.
- If the friend group exceeds 5, investigate extended quota requirements. The criteria ("established, scalable, and impactful") is a high bar for a personal project.
- Build the app so Spotify is an enrichment layer on top of the already-working Apple Music engine, not a co-equal pillar.

**Detection:** Test every Spotify endpoint you plan to use in Phase 1 before writing integration code. Print the actual response payloads and check for missing fields.

**Confidence:** HIGH -- official Spotify documentation and migration guide.

**Phase impact:** Phase 1 (Spotify integration). Affects architecture of every Spotify-related feature.

**Sources:**
- [February 2026 Migration Guide](https://developer.spotify.com/documentation/web-api/tutorials/february-2026-migration-guide)
- [TechCrunch coverage](https://techcrunch.com/2026/02/06/spotify-changes-developer-mode-api-to-require-premium-accounts-limits-test-users/)
- [Spotify Feb 2026 Changelog](https://developer.spotify.com/documentation/web-api/references/changes/february-2026)

---

### Pitfall 3: Genre Taxonomy Mismatch Between Services Is Structural, Not Cosmetic

**What goes wrong:** Developers treat cross-service genre normalization as a simple string-mapping exercise. It is not. Spotify and Apple Music assign genres at fundamentally different levels of their data model, use incompatible taxonomies, and have different granularity.

**Why it happens:** The mismatch is structural:
- **Spotify assigns genres to artists, not tracks.** To get genre data for a track, you must look up the track's artist(s) and read their genre arrays. An artist like Childish Gambino has genres `["hip hop", "pop rap", "rap"]` applied uniformly to every track, including his funk and R&B songs.
- **Apple Music assigns genres to albums and tracks.** Each song has a `genreNames` array (e.g., `["Italian Hip-Hop/Rap", "Music"]`) with hierarchical names. Genres are per-release, not per-artist.
- **Spotify uses ~6,000+ micro-genres** (e.g., "uk drill", "chicago drill", "vapor soul", "escape room"). Apple Music uses a curated taxonomy of ~100-200 genres with hierarchical parent/child relationships (e.g., "Italian Hip-Hop/Rap" is a child of "Hip-Hop/Rap").

**Consequences:** If you naively merge genre vectors from both services:
- A Spotify artist genre like "vapor soul" has no Apple Music equivalent.
- An Apple Music genre like "Italian Hip-Hop/Rap" has no Spotify equivalent (Spotify would say the artist is "hip hop" or "italian hip hop").
- The existing MusicMind taste profile engine builds genre vectors with cosine similarity. If Spotify genres and Apple Music genres occupy disjoint vector spaces, cross-service recommendations will have near-zero genre overlap scores, making the engine recommend only within-service tracks.
- Regional genre prioritization (the engine's key differentiator for Italian music) relies on Apple Music's hierarchical genre names. Spotify's flat micro-genre list does not encode hierarchy.

**Prevention:**
- Build a genre normalization layer that maps both taxonomies to a shared internal representation. This is a canonical mapping table, not a runtime algorithm.
- For Spotify genres, strip micro-genre prefixes to extract parent categories (e.g., "uk drill" -> "drill" -> "hip hop"). Use the existing `expand_genres` pattern from the Apple Music engine but in reverse.
- For Apple Music genres, the existing engine already handles hierarchy well. Keep this as the primary genre source.
- Accept that genre normalization will be lossy. "Italian Hip-Hop/Rap" and "italian hip hop" are semantically similar but need explicit mapping. Start with a curated mapping for the user's top 20 genres, expand as needed.
- Weight Apple Music genre data higher than Spotify genre data in the unified profile, since Apple Music genres are per-track (more precise) while Spotify genres are per-artist (more generic).
- Consider genre normalization a first-class data model concern, not an afterthought.

**Detection:** When building the first unified taste profile from a user with both services connected, if genre overlap between services is near zero, the normalization layer is broken.

**Confidence:** HIGH -- Spotify's artist-level genre assignment is confirmed in their API docs and developer forum. Apple Music's track-level genres are confirmed in the existing codebase.

**Phase impact:** Phase 2 (data normalization / unified model). Must be solved before the scoring engine can work cross-service.

**Sources:**
- [Spotify community: genres on artists not tracks](https://community.spotify.com/t5/Spotify-for-Developers/How-to-identify-a-track-s-genre-in-spotify-api/td-p/5602422)
- [Spotify community: no genre on TrackObject](https://community.spotify.com/t5/Spotify-for-Developers/Spotify-Web-API-How-to-get-genres-from-TrackObject-in-playlist/td-p/5700713)

---

### Pitfall 4: Apple Music User Token Has No Refresh Mechanism

**What goes wrong:** The Apple Music User Token (MUT) expires after approximately 6 months (some reports say sooner -- even days in edge cases). There is no refresh token. When it expires, the user must re-authenticate through the MusicKit JS browser flow. The existing MCP codebase already flags this as a missing critical feature (CONCERNS.md: "No Token Refresh Mechanism").

**Why it happens:** Apple's MusicKit authentication is proprietary, not standard OAuth. The MUT is obtained via MusicKit JS's `authorize()` method, which opens Apple's authentication UI. There is no programmatic way to refresh it.

**Consequences:** In a webapp context, this is worse than the MCP context:
- In the MCP server, a single user can manually re-run `setup.py`. In a webapp, every user's Apple Music connection will silently break after some months.
- API calls will start returning 401 errors with no automatic recovery.
- If the app does not detect this and prompt re-auth, it will appear broken.
- The token lifetime is inconsistent and undocumented -- some users report tokens dying after password changes, iCloud sign-out, or other account events.

**Prevention:**
- Build proactive token health checking: before every Apple Music API call batch, make a lightweight API call (e.g., `GET /v1/me/storefront`) and check for 401. If 401, set a flag in the user's session requiring re-authentication.
- Build a clear re-auth flow in the webapp UI: "Your Apple Music connection has expired. Click here to reconnect." This must be frictionless, not a full re-onboarding.
- Store the token creation timestamp and warn users proactively when approaching the 6-month mark.
- Consider using MusicKit JS v3 on the frontend for the auth flow (it handles the Apple UI natively) rather than the localhost server approach from `setup.py`.
- Do not treat Apple Music disconnection as an error -- treat it as an expected lifecycle event.

**Detection:** Monitor 401 error rates from Apple Music API calls. If they spike, the token expiry issue is manifesting.

**Confidence:** HIGH -- confirmed by Apple Developer Forums, existing CONCERNS.md, and the reverse-engineering analysis of MusicKit JS auth.

**Phase impact:** Phase 1 (Apple Music OAuth adaptation for web). The re-auth flow must be designed into the webapp from the start.

**Sources:**
- [Apple Developer Forums: token expiry](https://developer.apple.com/forums/thread/654814)
- [Apple Developer Forums: user token issues](https://developer.apple.com/forums/thread/703942)
- [Existing CONCERNS.md: "No Token Refresh Mechanism"](no external link -- internal codebase)

---

### Pitfall 5: Claude tool_use Agentic Loop Misimplementation

**What goes wrong:** Developers building Claude chat interfaces with tool_use make the agentic loop work in a basic demo but break in production with subtle, hard-to-debug failures: infinite loops, swallowed tool calls, empty responses, or malformed conversation history.

**Why it happens:** The Claude tool_use protocol has strict requirements that are easy to violate:

1. **Stop reason ignored:** The backend must check `stop_reason == "tool_use"` to know Claude wants to call a tool. If it only checks for text content, mixed responses (text + tool_use in the same message) cause the tool call to be silently dropped.
2. **Tool result ordering violation:** `tool_result` blocks MUST come before any `text` blocks in the user message content array. Putting text first (e.g., "Here are the results:") causes the API to reject or misinterpret the message.
3. **Missing tool_use_id:** Each `tool_result` must include the `tool_use_id` from the corresponding `tool_use` block. If the ID is wrong or missing, Claude cannot correlate results with requests.
4. **No loop termination:** Without a maximum iteration cap, Claude can keep requesting tools indefinitely. The recommended engine cap is ~10 iterations, after which `pause_turn` is returned.
5. **Parallel tool calls mishandled:** Claude can return multiple `tool_use` blocks in a single response. If the backend executes only the first one, the others are lost. All results must be returned in a single user message.

**Consequences:** Users experience: the chat "thinking" forever (infinite loop), responses that ignore tool output (ordering violation), or "something went wrong" errors that are hard to reproduce.

**Prevention:**
- Use the official Python SDK's `tool_runner` (beta) which handles the agentic loop automatically: `client.beta.messages.tool_runner()`. This eliminates loop, ordering, and ID-matching bugs.
- If building the loop manually, follow this exact pattern:
  ```
  while response.stop_reason == "tool_use":
      tool_results = [execute(block) for block in response.content if block.type == "tool_use"]
      messages.append({"role": "assistant", "content": response.content})
      messages.append({"role": "user", "content": tool_results})
      response = client.messages.create(...)
  ```
- Set `max_tokens` conservatively and add a hard iteration cap (e.g., 8 tool rounds).
- For the MusicMind use case with 30+ tools, use `disable_parallel_tool_use=True` initially to simplify debugging, then enable parallelism once the loop is stable.
- Implement `is_error: true` on tool results when engine functions fail, so Claude can recover gracefully rather than hallucinating.

**Detection:** Log every agentic loop iteration: tool requested, tool executed, result returned, stop_reason. If iterations exceed 5 for simple queries, the loop logic is likely broken.

**Confidence:** HIGH -- Anthropic's official documentation explicitly warns about these patterns.

**Phase impact:** Phase 3 (Claude chat integration). This is the core of the chat experience.

**Sources:**
- [Claude tool_use implementation guide](https://platform.claude.com/docs/en/agents-and-tools/tool-use/implement-tool-use)
- [Claude handling stop reasons](https://platform.claude.com/docs/en/build-with-claude/handling-stop-reasons)
- [Agentic loop explained](https://dev.to/ajbuilds/claudes-agentic-loop-explained-stopreason-tooluse-and-the-pattern-behind-every-ai-agent-2l61)

---

## Moderate Pitfalls

Mistakes that cause significant rework, performance problems, or security issues.

---

### Pitfall 6: Spotify OAuth PKCE -- HTTPS and Redirect URI Traps

**What goes wrong:** The Spotify OAuth flow fails silently or with cryptic errors due to redirect URI mismatches, HTTP vs HTTPS requirements, or lost PKCE code verifiers.

**Why it happens:** Spotify enforced strict new OAuth requirements as of November 2025:
- ALL redirect URIs must use HTTPS, except loopback IPs (`http://127.0.0.1` is allowed, but `http://localhost` is NOT).
- The PKCE code verifier must be stored (typically in `localStorage` or server-side session) between the authorization redirect and the token exchange callback. If lost, the exchange fails.
- Redirect URIs are matched EXACTLY -- including trailing slashes, case, port numbers.

**Prevention:**
- For local development, register `http://127.0.0.1:PORT/callback` (NOT `localhost`).
- For production, use HTTPS with an exact match in the Spotify developer dashboard.
- Store the PKCE code verifier server-side in the user's session, not in browser localStorage (which can be cleared by extensions or privacy settings).
- Use the `state` parameter for CSRF protection -- Spotify "strongly recommends" it.
- Test the full OAuth flow end-to-end in Phase 1 before building any Spotify data features.

**Detection:** OAuth failures manifest as redirect errors, "invalid_grant" responses, or the token exchange returning 400. Check the Spotify developer dashboard URI list character-for-character against your code.

**Confidence:** HIGH -- official Spotify documentation and migration blog.

**Phase impact:** Phase 1 (Spotify OAuth).

**Sources:**
- [Spotify PKCE Flow](https://developer.spotify.com/documentation/web-api/tutorials/code-pkce-flow)
- [Spotify OAuth migration announcement](https://developer.spotify.com/blog/2025-02-12-increasing-the-security-requirements-for-integrating-with-spotify)

---

### Pitfall 7: BYOK API Key Storage and Proxy Security

**What goes wrong:** User-provided Claude API keys are leaked through logs, error messages, browser network tabs, or stored insecurely on the server.

**Why it happens:** In a BYOK architecture, the user's API key travels from the browser to the backend, is stored in the database, and is used in API calls. Each step is a potential leak point:
- Key sent to backend in plaintext (needs HTTPS).
- Key stored in database without encryption at rest.
- Key logged in error messages or request traces.
- Key exposed in frontend JavaScript if the architecture mistakenly calls Claude from the browser.
- Key accessible to other users if database access control is weak.

**Consequences:** A leaked Anthropic API key allows unlimited API usage billed to the key owner. Unlike Spotify/Apple tokens which are scoped, Claude API keys have full account access including billing.

**Prevention:**
- **Never call the Claude API from the browser.** The key must only exist on the backend. The frontend sends user messages to the backend; the backend calls Claude.
- Encrypt API keys at rest in the database using a server-side encryption key (e.g., Fernet symmetric encryption). The encryption key lives in environment variables, not in the database.
- Sanitize all error logs and HTTP responses to strip API key patterns (anything matching `sk-ant-*`).
- Allow users to delete their key and verify it is actually removed (not just soft-deleted).
- Validate the key on submission by making a lightweight Claude API call (e.g., count tokens on a trivial message) to confirm it works before storing.
- Display only the last 4 characters of the key in the UI (masked: `sk-ant-...xxxx`).
- Consider a session-only key mode where the key is held only in server memory for the session duration and never persisted to disk. This is more secure but requires re-entry on each login.

**Detection:** Audit server logs for any string matching `sk-ant-`. Run a security scan on all API responses for key patterns.

**Confidence:** MEDIUM -- general security best practices applied to this specific BYOK architecture. No MusicMind-specific sources.

**Phase impact:** Phase 1 (user accounts / key management). Security architecture must be right before any key is stored.

---

### Pitfall 8: SQLite to Multi-User Database Migration Breaks Assumptions

**What goes wrong:** The existing SQLite-based database layer has single-user assumptions baked throughout. Migrating to multi-user is not just "add a user_id column" -- it requires rethinking queries, concurrency, and data isolation.

**Why it happens:** The existing codebase (confirmed in CONCERNS.md) has:
- **No user_id on any table.** Every table (song_metadata_cache, listening_history, taste_profiles, recommendation_feedback, audio_features_cache) stores data for a single implicit user.
- **SQLite single-writer lock.** If two users trigger profile rebuilds simultaneously, one blocks the other.
- **N+1 upsert patterns.** Already slow for a single user (CONCERNS.md: "1000+ songs triggers ~200 queries"), these become untenable with multiple concurrent users.
- **Full-table scans.** `get_all_cached_songs()` loads the entire song cache into memory. With multiple users' data in one table, this grows proportionally.
- **SQLAlchemy Core, not ORM.** Raw SQL queries must be manually updated to include `WHERE user_id = ?` clauses -- there is no ORM-level tenant filtering.

**Consequences:** Data leaks between users (seeing another user's taste profile), deadlocks under concurrent access, and performance degradation as the database grows.

**Prevention:**
- Adopt PostgreSQL from the start of the webapp (do not try to make SQLite work for multi-user). The codebase already uses SQLAlchemy Core, which supports dialect switching.
- Add `user_id` as a foreign key on EVERY data table. Create a migration script, not a manual ALTER TABLE.
- Use Alembic for schema migrations from day one. The current codebase has no migration system.
- Watch for SQLite-specific syntax in existing queries: `INSERT OR REPLACE` (SQLite) vs `INSERT ... ON CONFLICT DO UPDATE` (PostgreSQL). SQLAlchemy Core handles some of this, but the CONCERNS.md notes that raw SQLite-specific patterns are used.
- Enable WAL mode on SQLite during development (`PRAGMA journal_mode=WAL`) but plan for PostgreSQL in production.
- Audit every query in `queries.py` for missing user scoping. There are 6+ query methods that load data without any user filter.
- Replace `get_all_cached_songs()` with SQL-level aggregation for profile building rather than loading all rows into Python.
- Boolean columns: SQLite stores as 0/1 integers, PostgreSQL has native BOOLEAN. Test after migration.
- Date/time columns: SQLite stores as TEXT, PostgreSQL uses native temporal types. Verify all date parsing.

**Detection:** Write a multi-user integration test early: create two users, populate both with different data, verify complete isolation. If User A's genres appear in User B's profile, the migration is incomplete.

**Confidence:** HIGH -- confirmed by CONCERNS.md analysis and standard SQLite-to-PostgreSQL migration literature.

**Phase impact:** Phase 2 (multi-user database). This is foundational -- must be completed before any user-facing feature is built on top.

**Sources:**
- [Existing CONCERNS.md: "SQLite Concurrency" and "N+1 Upsert Pattern"](internal codebase)
- [SQLite to PostgreSQL migration guide](https://render.com/articles/how-to-migrate-from-sqlite-to-postgresql)
- [Open WebUI SQLite to PostgreSQL pitfalls](https://github.com/open-webui/open-webui/discussions/21609)

---

### Pitfall 9: ISRC Cross-Matching Fails for Edge Cases and Dev Mode

**What goes wrong:** Developers assume ISRC (International Standard Recording Code) provides reliable 1:1 matching between Spotify and Apple Music tracks. It does not in several common cases, and the February 2026 Spotify API changes may remove ISRC from development mode responses entirely.

**Why it happens:**
- **ISRC is per-recording, not per-song.** A remaster, deluxe edition, radio edit, and album version of the same song each have different ISRCs. A user who saved the "remastered" version on Spotify and the "original" on Apple Music will not match.
- **Some tracks lack ISRCs.** Independent releases, regional-only tracks, and older catalog items may have missing or inconsistent ISRCs across platforms.
- **The `external_ids` field (which contains ISRC) was removed from Spotify dev mode track responses in February 2026.** If this field is truly gone, ISRC matching is impossible without extended quota.

**Consequences:** Without reliable cross-matching, the "unified multi-service data model" cannot deduplicate tracks. A user with 500 songs on Spotify and 300 on Apple Music might show 800 unique tracks when 200 of them are duplicates. This inflates genre vectors, distorts taste profiles, and wastes recommendation slots on songs the user already knows across services.

**Prevention:**
- **Verify `external_ids` availability immediately.** Make a test API call with a dev mode Spotify app and check if the field is present. If it is gone, ISRC matching is not possible for Spotify tracks.
- Build a multi-layer matching strategy:
  1. ISRC match (if available): exact, highest confidence.
  2. Title + primary artist name fuzzy match (Levenshtein distance or normalized comparison): handles remasters, capitalization, and minor title differences. Accept matches above 0.9 similarity.
  3. Title + artist + duration match: duration within 5 seconds adds confidence to fuzzy matches.
- Store match confidence in the database. A "maybe matched" track should be treated differently from a "definitely matched" track.
- Accept that some tracks will not match. Design the data model to handle "Spotify-only" and "Apple Music-only" tracks alongside "unified" tracks.
- The 97% accuracy claim for ISRC matching (from Tunarc) applies to well-distributed major-label catalog. For niche Italian music, accuracy will be lower.

**Detection:** After building the first cross-service profile, count matched vs unmatched tracks. If match rate is below 50% for a user with significant overlap, the matching strategy needs refinement.

**Confidence:** MEDIUM -- ISRC matching reliability is well-documented, but whether `external_ids` is truly removed from Spotify dev mode needs verification with a live API call.

**Phase impact:** Phase 2 (data normalization). Directly affects unified data model design.

**Sources:**
- [ISRC matching accuracy](https://tunarc.com/guides/isrc-matching-explained)
- [February 2026 field removals](https://developer.spotify.com/documentation/web-api/tutorials/february-2026-migration-guide)

---

### Pitfall 10: MusicKit JS Web Auth Cannot Be Replicated Server-Side

**What goes wrong:** Developers try to automate or server-side-render the Apple Music user authentication flow. It cannot be done. MusicKit JS requires a browser-based interactive flow that talks to Apple's servers directly.

**Why it happens:** The existing MCP codebase uses a localhost HTTP server (`setup.py`) that serves MusicKit JS in a browser for the user to authenticate. This works for single-user setup but does not translate to a multi-user webapp pattern.

Apple's MusicKit auth is:
- Not standard OAuth (no authorization code exchange, no redirect URI pattern).
- Requires MusicKit JS to be loaded in the user's browser.
- The `authorize()` method opens Apple's auth UI.
- The user token is returned via a JavaScript event, not a URL callback.
- There is no documented server-to-server auth for user tokens.

**Consequences:** If you try to build Apple Music OAuth like Spotify OAuth (server-side redirect flow), it will not work. The frontend MUST handle Apple Music auth, and the user token must be sent from the browser to the backend after the interactive flow completes.

**Prevention:**
- Use MusicKit JS v3 on the frontend for all Apple Music authentication.
- The flow is: frontend loads MusicKit JS -> user clicks "Connect Apple Music" -> MusicKit JS calls `authorize()` -> Apple auth UI appears -> user signs in -> MusicKit JS receives user token via event -> frontend sends token to backend API.
- The backend never touches the Apple auth flow. It only stores and uses the resulting user token.
- The developer token (JWT signed with the .p8 key) is still generated server-side and passed to MusicKit JS as configuration.
- Do NOT expose the .p8 private key or developer token generation to the frontend. Generate the developer token on the backend and inject it into the MusicKit JS configuration via an API endpoint.

**Detection:** If you find yourself building a server-side redirect URI for Apple Music auth, stop. That is not how MusicKit works.

**Confidence:** HIGH -- confirmed by Apple Developer Forums, MusicKit JS documentation, and reverse-engineering analysis.

**Phase impact:** Phase 1 (Apple Music OAuth adaptation). The existing `setup.py` flow must be completely replaced for the webapp.

**Sources:**
- [Reverse engineering MusicKit JS auth](https://dev.to/jurooravec/reverse-engineering-apple-s-musickitjs-to-create-apple-music-strategy-for-passportjs-12h)
- [MusicKit JS v3 documentation](https://js-cdn.music.apple.com/musickit/v3/docs/index.html?path=/story/get-started--page)
- [Apple Developer Forums: user token issues](https://developer.apple.com/forums/thread/703942)

---

## Minor Pitfalls

Mistakes that cause friction, bugs, or suboptimal UX but are recoverable.

---

### Pitfall 11: Claude Tool Definitions Bloat Token Usage

**What goes wrong:** Sending all 30+ MusicMind tool definitions with every Claude API call consumes significant input tokens, increasing cost and latency. With BYOK pricing, users see unexpectedly high API bills.

**Prevention:**
- Start with a curated subset of 8-10 most-used tools per conversation context. The full 30+ tools are an MCP artifact; a webapp chat does not need all of them simultaneously.
- Use Claude's `tool_choice` parameter to guide tool selection rather than providing everything.
- Consider dynamic tool injection: for a "recommend me music" query, inject recommendation + taste tools. For a "search for an artist" query, inject catalog + search tools.
- Estimate token cost: each tool definition is roughly 200-400 tokens. 30 tools = 6,000-12,000 input tokens per message, at roughly $0.03-0.12 per conversation turn on Claude Sonnet.

**Detection:** Track token usage per conversation. If input tokens consistently exceed output tokens by 10x+, tool definitions are the likely cause.

**Phase impact:** Phase 3 (Claude chat integration). Optimization concern, not a blocker.

---

### Pitfall 12: Spotify 5-User Development Mode Limit

**What goes wrong:** The app works for the developer but the 6th friend cannot connect their Spotify account. Development mode silently rejects new user authorizations after 5 users.

**Prevention:**
- Count your friend group. If it exceeds 5, you need Spotify Extended Quota Mode, which requires a formal application and review process.
- The extended quota criteria (as of April 2025) requires "established, scalable, and impactful use cases that help drive Spotify's platform strategy." A personal friend-group app may not qualify.
- Fallback plan: if extended quota is denied, limit Spotify features to the developer's account and focus the multi-user experience on Apple Music.
- Each dev mode app now also requires the developer to maintain a Spotify Premium subscription.

**Detection:** The 6th user's OAuth flow fails. Check the Spotify developer dashboard for user count.

**Phase impact:** Phase 1 (Spotify integration setup).

**Sources:**
- [Spotify dev mode limits](https://developer.spotify.com/documentation/web-api/concepts/quota-modes)

---

### Pitfall 13: Conversation History Grows Unbounded With Tool Calls

**What goes wrong:** Each tool_use round adds both the tool request and tool result to the conversation history. A 5-round agentic loop with tool calls can add 10+ messages to the context window. Over a long chat session, the conversation exceeds Claude's context window or the per-message token limit.

**Prevention:**
- Implement conversation history windowing: keep the system prompt + last N messages + a summary of older messages.
- Tool results can be large (e.g., a list of 50 recommended songs with metadata). Truncate or summarize tool results before appending to history.
- Set `max_tokens` on each API call to prevent runaway responses.
- Consider a "conversation reset" button in the UI for users who hit context limits.
- Track total token count in the conversation and warn users when approaching limits.

**Detection:** Claude starts "forgetting" earlier parts of the conversation, or API calls fail with context length errors.

**Phase impact:** Phase 3 (Claude chat integration).

---

### Pitfall 14: Rate Limit Stacking Across Services

**What goes wrong:** A single user action (e.g., "build my taste profile") triggers calls to both Apple Music and Spotify APIs simultaneously, each with their own rate limits. Claude's tool calls add a third API (Anthropic) to the mix. Without coordination, one service's rate limit causes cascading failures.

**Prevention:**
- Implement per-service rate limiters with separate token buckets:
  - Apple Music: ~20 req/sec (undocumented, conservative)
  - Spotify: ~180 req/min in development mode (varies)
  - Anthropic: varies by tier, check `x-ratelimit-*` response headers
- Respect `Retry-After` headers from Spotify (429 responses include this).
- Queue API calls per-service rather than firing them all at once.
- For taste profile building, stagger: Apple Music data first, then Spotify data, then profile computation. Do not parallelize cross-service fetches unless you are confident in rate limits.
- The existing codebase's Apple Music client already has basic retry logic (3 retries on 429). Extend this pattern to Spotify.

**Detection:** Monitor 429 response rates per service. If any service exceeds 5% error rate, the rate limiter is too aggressive.

**Phase impact:** Phases 1-2 (service integrations).

---

## Phase-Specific Warnings

| Phase Topic | Likely Pitfall | Mitigation |
|-------------|---------------|------------|
| Spotify OAuth setup | PKCE verifier loss, HTTPS requirement, redirect URI mismatch | Test full flow end-to-end before building features (Pitfall 6) |
| Spotify data integration | Audio features API unavailable, batch endpoints removed, 5-user limit | Verify every endpoint with live API calls in Phase 1 (Pitfalls 1, 2, 12) |
| Apple Music OAuth for web | Cannot use server-side redirect flow, token has no refresh | Use MusicKit JS on frontend, build re-auth flow (Pitfalls 4, 10) |
| Multi-user database | Single-user assumptions in every query, SQLite concurrency limits | Migrate to PostgreSQL, add user_id everywhere, use Alembic (Pitfall 8) |
| Data normalization | Genre taxonomy mismatch, ISRC matching unreliable in dev mode | Build normalization layer and multi-strategy matching (Pitfalls 3, 9) |
| Claude chat integration | Agentic loop bugs, tool definition bloat, unbounded conversation | Use SDK tool_runner, subset tools, window history (Pitfalls 5, 11, 13) |
| BYOK key management | Key leakage, no encryption at rest, exposed in logs | Encrypt keys, sanitize logs, never call Claude from browser (Pitfall 7) |
| Cross-service recommendations | Genre vectors from different services occupy disjoint spaces | Normalize genres before scoring, weight Apple Music higher (Pitfall 3) |

---

## Summary of Impact on Project Assumptions

The following PROJECT.md assumptions need revision based on this research:

| Assumption | Status | Revision Needed |
|------------|--------|----------------|
| "Spotify audio features as primary, librosa as fallback" | **INVALIDATED** | Spotify audio features are unavailable for new apps. Librosa must be primary, or accept metadata-only scoring. |
| "Both services unified, not pick-one" | **CONSTRAINED** | Unification is harder than expected due to genre mismatch and ISRC availability. Design as asymmetric: Apple Music is primary, Spotify enriches. |
| "Small group of friends" | **CONSTRAINED** | Spotify dev mode caps at 5 users. Group must be 5 or fewer, OR apply for extended quota. |
| "Keep Python engine, wrap with web API" | **VALID** | Engine is reusable, but queries need user_id scoping and PostgreSQL compatibility. |
| "Local-first deployment" | **VALID** | Compatible with development mode restrictions. |

---

*Pitfalls audit: 2026-03-26*
*Confidence: HIGH overall -- critical pitfalls verified with official sources; moderate pitfalls verified with community sources and existing codebase analysis.*
