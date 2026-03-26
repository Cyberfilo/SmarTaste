# Architecture Patterns

**Domain:** Music discovery webapp (MCP server transformation)
**Researched:** 2026-03-26

## Recommended Architecture

### High-Level System

```
Browser (React SPA)
    |
    | HTTP REST + SSE (streaming chat)
    |
FastAPI Backend
    |
    +---> Auth Router          (login, signup, OAuth callbacks)
    +---> Dashboard Router     (taste profile, recommendations, stats)
    +---> Chat Router          (Claude conversation with SSE streaming)
    +---> Service Router       (connect/disconnect Spotify, Apple Music)
    |
    +---> Claude Orchestrator  (agentic loop with tool_use, per-user API key)
    |         |
    |         +---> Tool Registry (engine functions exposed as Claude tools)
    |
    +---> MusicMind Engine     (existing: profile, scorer, discovery, mood)
    |         |
    |         +---> Multi-Service Adapter
    |         |         |
    |         |         +---> Apple Music Client (existing, adapted)
    |         |         +---> Spotify Client     (new)
    |         |
    |         +---> Database Layer (SQLAlchemy async, multi-user)
    |
    +---> PostgreSQL (or SQLite with user_id columns for local-first)
```

### Component Boundaries

| Component | Responsibility | Communicates With | Build Phase |
|-----------|---------------|-------------------|-------------|
| **React SPA** | UI rendering, routing, state management | Backend via REST + SSE | Phase 2+ |
| **FastAPI Backend** | HTTP routing, auth middleware, session management | All backend components | Phase 1 |
| **Auth Module** | User signup/login, session tokens, OAuth flow management | Database, external OAuth providers | Phase 1 |
| **Dashboard Router** | Taste profile, recommendation feed, stats endpoints | Engine, Database | Phase 2 |
| **Chat Router** | Claude conversation management, SSE streaming | Claude Orchestrator | Phase 3 |
| **Claude Orchestrator** | Agentic tool_use loop, manages Claude API calls with user's key | Tool Registry, Anthropic API | Phase 3 |
| **Tool Registry** | Maps engine functions to Claude tool definitions | Engine, Service Adapter, Database | Phase 3 |
| **Multi-Service Adapter** | Unified interface over Spotify + Apple Music APIs | Spotify Client, Apple Music Client | Phase 2 |
| **MusicMind Engine** | Taste profiling, scoring, discovery (existing, adapted) | Service Adapter, Database | Phase 1 (wrap) |
| **Database Layer** | Multi-user persistence, SQLAlchemy async | PostgreSQL or SQLite | Phase 1 |

---

## Data Flow

### 1. Dashboard Request Flow

```
Browser -> GET /api/taste-profile
    -> FastAPI auth middleware (extract user from session)
    -> Dashboard Router
        -> Engine.build_taste_profile(user_id)
            -> DB.get_all_cached_songs(user_id)
            -> DB.get_listening_history(user_id)
            -> returns taste dict
        -> JSON response to browser
```

### 2. Chat Message Flow (the most complex flow)

```
Browser -> POST /api/chat/message  (body: {message, conversation_id})
    -> FastAPI auth middleware
    -> Chat Router
        -> Load user's Anthropic API key from DB
        -> Load conversation history from DB
        -> Anthropic client = Anthropic(api_key=user_key)
        -> Start SSE stream to browser
        -> AGENTIC LOOP:
            |
            | (1) client.messages.stream(tools=TOOL_REGISTRY, messages=history)
            |     -> Stream text tokens to browser via SSE
            |     -> If stop_reason == "tool_use":
            |         (2) Extract tool_name + input from response
            |         (3) Execute tool via Tool Registry:
            |             tool_name("discover") -> engine.discover(user_id, ...)
            |                 -> Service Adapter -> Apple Music / Spotify
            |                 -> DB cache
            |                 -> Scorer -> ranked results
            |         (4) Send tool_result back to Claude
            |         (5) Stream Claude's response about results via SSE
            |         (6) If stop_reason == "tool_use" again -> goto (2)
            |     -> If stop_reason == "end_turn":
            |         Save conversation to DB
            |         Close SSE stream
```

### 3. Multi-Service Data Flow

```
Service Adapter receives: get_library_songs(user_id)
    -> Look up user's connected services in DB
    -> For each connected service:
        Apple Music:
            -> AppleMusicClient.get_library_songs() (existing)
            -> Normalize to UnifiedSong model
        Spotify:
            -> SpotifyClient.get_saved_tracks()
            -> Normalize to UnifiedSong model
    -> Deduplicate by ISRC (cross-service matching)
    -> Return merged list
```

### 4. OAuth Connection Flow

```
Browser -> GET /api/auth/spotify/connect
    -> Generate state token, store in session
    -> Redirect to Spotify OAuth authorize URL
Browser -> Spotify grants permission -> Redirect to callback
Browser -> GET /api/auth/spotify/callback?code=...&state=...
    -> Validate state token
    -> Exchange code for access_token + refresh_token
    -> Store encrypted tokens in DB for user
    -> Redirect to dashboard
```

---

## Detailed Component Designs

### Multi-Service Adapter Pattern

The adapter uses a protocol (Python Protocol / ABC) defining a common interface, with concrete implementations per service.

```python
# Protocol
class MusicServiceAdapter(Protocol):
    async def get_library_songs(self, limit: int, offset: int) -> list[UnifiedSong]: ...
    async def get_recently_played(self, limit: int) -> list[UnifiedSong]: ...
    async def search_catalog(self, query: str, limit: int) -> list[UnifiedSong]: ...
    async def get_artist(self, artist_id: str) -> UnifiedArtist: ...
    async def create_playlist(self, name: str, track_ids: list[str]) -> str: ...
    async def get_audio_features(self, track_ids: list[str]) -> list[AudioFeatures]: ...

# Unified models
@dataclass
class UnifiedSong:
    """Normalized song across services."""
    internal_id: str          # MusicMind internal
    name: str
    artist_name: str
    album_name: str
    genre_names: list[str]
    duration_ms: int
    release_date: str | None
    isrc: str | None          # Cross-service matching key
    preview_url: str | None
    artwork_url: str | None
    source_service: str       # "apple_music" | "spotify"
    source_id: str            # Original service ID

# Aggregate adapter that fans out to connected services
class MultiServiceAdapter:
    def __init__(self, user_id: str, db: QueryExecutor):
        self._adapters: dict[str, MusicServiceAdapter] = {}
        # Loaded lazily based on user's connected services

    async def get_library_songs(self, limit: int = 25, offset: int = 0) -> list[UnifiedSong]:
        """Fetch from all connected services, deduplicate by ISRC."""
        all_songs = []
        for service, adapter in self._adapters.items():
            songs = await adapter.get_library_songs(limit, offset)
            all_songs.extend(songs)
        return self._deduplicate_by_isrc(all_songs)
```

**Why this pattern:** The existing `AppleMusicClient` already has the right shape. Wrapping it behind a protocol requires minimal changes to the existing code. The Spotify adapter matches the same interface. The aggregate adapter handles fan-out and deduplication.

**ISRC deduplication:** Both Spotify and Apple Music expose ISRC (International Standard Recording Code) per track. This is the reliable cross-service matching key. Not all tracks have ISRCs (notably remixes, local files), so fallback to name+artist fuzzy matching is needed.

### Claude Orchestrator (BYOK Tool Use)

The orchestrator manages the agentic loop where Claude calls MusicMind engine functions as tools.

```python
# Tool definition format for Anthropic API
MUSICMIND_TOOLS = [
    {
        "name": "discover_songs",
        "description": "Discover new songs personalized to the user's taste. "
                      "Uses taste profile to find and rank songs.",
        "input_schema": {
            "type": "object",
            "properties": {
                "count": {"type": "integer", "description": "Number of songs (max 50)"},
                "strategy": {
                    "type": "string",
                    "enum": ["auto", "similar_artists", "genre_adjacent", "editorial", "charts"],
                },
                "mood": {
                    "type": "string",
                    "enum": ["workout", "chill", "focus", "party", "sad", "driving"],
                },
            },
        },
    },
    {
        "name": "get_taste_profile",
        "description": "Get the user's taste profile showing top genres, "
                      "artists, audio preferences, and listening stats.",
        "input_schema": {"type": "object", "properties": {}},
    },
    # ... ~15-20 tools derived from existing MCP tool definitions
]

class ClaudeOrchestrator:
    """Manages the agentic tool_use loop for a user's chat session."""

    def __init__(self, user_api_key: str, user_id: str, engine: MusicMindEngine):
        self.client = anthropic.AsyncAnthropic(api_key=user_api_key)
        self.user_id = user_id
        self.engine = engine

    async def stream_response(
        self, messages: list[dict], system_prompt: str
    ) -> AsyncIterator[str]:
        """Run the agentic loop, yielding SSE events."""
        while True:
            async with self.client.messages.stream(
                model="claude-sonnet-4-20250514",  # Good balance of cost/quality
                max_tokens=4096,
                system=system_prompt,
                tools=MUSICMIND_TOOLS,
                messages=messages,
            ) as stream:
                collected_content = []
                async for event in stream:
                    if hasattr(event, 'type'):
                        if event.type == 'content_block_delta':
                            if hasattr(event.delta, 'text'):
                                yield f"data: {json.dumps({'type': 'text', 'text': event.delta.text})}\n\n"

                response = await stream.get_final_message()
                messages.append({"role": "assistant", "content": response.content})

                if response.stop_reason == "tool_use":
                    # Execute each tool call
                    tool_results = []
                    for block in response.content:
                        if block.type == "tool_use":
                            yield f"data: {json.dumps({'type': 'tool_call', 'name': block.name})}\n\n"
                            result = await self._execute_tool(block.name, block.input)
                            tool_results.append({
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": result,
                            })
                    messages.append({"role": "user", "content": tool_results})
                    continue  # Loop back for Claude to process results
                else:
                    break  # end_turn -> done

    async def _execute_tool(self, name: str, input: dict) -> str:
        """Route tool call to engine function. Returns string result."""
        # Map tool names to engine methods
        # These return structured data, not markdown (unlike MCP tools)
        handler = self.engine.get_tool_handler(name)
        return await handler(user_id=self.user_id, **input)
```

**Key design decisions:**

1. **Tool results are structured data, not markdown.** The existing MCP tools return markdown strings for Claude Desktop consumption. The webapp tools should return JSON-serializable data that Claude can reason about and the frontend can render. This means creating a thin adapter layer over the existing tool functions that returns dicts instead of formatted strings.

2. **Streaming via SSE, not WebSocket.** SSE is simpler, works with standard HTTP, and is the natural fit for server-push-only chat streaming. WebSocket would be needed only if the frontend needs to send messages mid-stream (cancel, interrupt), which can be handled via a separate REST endpoint instead.

3. **User API key per request.** The `AsyncAnthropic` client is instantiated per-request with the user's decrypted API key. Keys are stored encrypted in the database. The backend never stores or logs the key in plaintext.

4. **System prompt includes user context.** The system prompt is dynamically built with the user's taste profile summary, connected services, and available capabilities. This gives Claude context without requiring a tool call.

### Database: Multi-User Migration

**Strategy: Add user_id column to every table, keep SQLAlchemy Core.**

The existing codebase uses SQLAlchemy Core (not ORM) with explicit queries via `QueryExecutor`. This is ideal for multi-user because:

1. Every query is already centralized in `QueryExecutor`
2. Adding `WHERE user_id = ?` to every query is mechanical
3. No ORM magic to fight with

```python
# Schema changes: add user_id to every table
listening_history = sa.Table(
    "listening_history", metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("user_id", sa.Text, nullable=False, index=True),  # NEW
    sa.Column("song_id", sa.Text, nullable=False, index=True),
    # ... rest unchanged
)

# New table: users
users = sa.Table(
    "users", metadata,
    sa.Column("id", sa.Text, primary_key=True),  # UUID
    sa.Column("email", sa.Text, nullable=False, unique=True),
    sa.Column("password_hash", sa.Text, nullable=False),
    sa.Column("display_name", sa.Text, nullable=False),
    sa.Column("anthropic_api_key_encrypted", sa.Text, nullable=True),
    sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
)

# New table: service_connections
service_connections = sa.Table(
    "service_connections", metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("user_id", sa.Text, nullable=False, index=True),
    sa.Column("service", sa.Text, nullable=False),  # "apple_music" | "spotify"
    sa.Column("access_token_encrypted", sa.Text, nullable=False),
    sa.Column("refresh_token_encrypted", sa.Text, nullable=True),
    sa.Column("token_expires_at", sa.DateTime, nullable=True),
    sa.Column("service_user_id", sa.Text, nullable=True),
    sa.Column("storefront", sa.Text, nullable=True),  # Apple Music storefront
    sa.Column("connected_at", sa.DateTime, server_default=sa.func.now()),
)

# New table: chat_conversations
chat_conversations = sa.Table(
    "chat_conversations", metadata,
    sa.Column("id", sa.Text, primary_key=True),  # UUID
    sa.Column("user_id", sa.Text, nullable=False, index=True),
    sa.Column("title", sa.Text, default=""),
    sa.Column("messages_json", sa.JSON, default=list),  # Full conversation history
    sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
    sa.Column("updated_at", sa.DateTime, server_default=sa.func.now()),
)

# QueryExecutor changes: every method gains user_id parameter
class QueryExecutor:
    async def get_listening_history(
        self, user_id: str, since: datetime | None = None, limit: int = 1000
    ) -> list[dict]:
        stmt = sa.select(listening_history).where(
            listening_history.c.user_id == user_id  # NEW filter
        ).order_by(listening_history.c.observed_at.desc()).limit(limit)
        # ... rest similar
```

**Why row-level (user_id column) not schema-based:** For a friends-group app with 3-10 users, schema-per-user is massive overkill. Row-level with user_id column is simpler, uses a single connection pool, and requires only mechanical changes to the existing QueryExecutor. Every method already exists; each just gains a `user_id` parameter and a WHERE clause.

**SQLite vs PostgreSQL:** Start with SQLite (local-first, zero setup, already works). Add PostgreSQL support later if needed. SQLAlchemy Core abstracts the difference. The only SQLite-specific concern is concurrent writes from multiple users, which is fine at friends-group scale with WAL mode enabled.

### Frontend Architecture

```
src/
  app/
    layout.tsx              # Root layout with auth context
    page.tsx                # Landing / login
    dashboard/
      page.tsx              # Main dashboard
      components/
        TasteProfile.tsx    # Genre radar chart, top artists, audio traits
        RecommendationFeed.tsx  # Scrollable recommendation cards
        ListeningStats.tsx  # History heatmap, listening hours
        ServiceConnections.tsx  # Connect/disconnect Spotify, Apple Music
    chat/
      page.tsx              # Claude chat interface
      components/
        ChatMessage.tsx     # Individual message bubble
        ToolCallIndicator.tsx  # "Searching for songs..." loading state
        RecommendationCard.tsx  # Inline song card in chat
    settings/
      page.tsx              # API key, account settings
  lib/
    api.ts                  # REST client functions
    sse.ts                  # SSE stream handler for chat
    auth.ts                 # Auth context, token management
```

**Framework: Next.js (App Router).** Because:
- File-based routing reduces boilerplate
- Server components for dashboard data fetching
- Client components for interactive chat
- Built-in API route proxying if needed
- Strong TypeScript support

**Charting: Recharts.** Lightweight, React-native, sufficient for radar charts (genre distribution), bar charts (listening stats), and line charts (taste evolution over time). No need for D3 complexity.

**State management: React Query (TanStack Query).** For server state (API data), not Redux. The app's state is mostly server-derived (taste profiles, recommendations, listening history). React Query handles caching, refetching, and optimistic updates.

**Chat SSE handling:**
```typescript
// Simplified SSE consumer for chat
async function* streamChat(message: string, conversationId: string) {
  const response = await fetch('/api/chat/message', {
    method: 'POST',
    body: JSON.stringify({ message, conversation_id: conversationId }),
    headers: { 'Content-Type': 'application/json' },
  });

  const reader = response.body!.getReader();
  const decoder = new TextDecoder();

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    const text = decoder.decode(value);
    // Parse SSE events
    for (const line of text.split('\n\n')) {
      if (line.startsWith('data: ')) {
        yield JSON.parse(line.slice(6));
        // Yields: {type: "text", text: "..."} or {type: "tool_call", name: "..."}
      }
    }
  }
}
```

---

## Patterns to Follow

### Pattern 1: Engine Wrapper (Existing Code Preservation)

**What:** Wrap the existing engine modules with a thin user-scoped layer rather than rewriting them.

**When:** Every interaction with the engine from the web layer.

**Why:** The engine (profile.py, scorer.py, discovery.py, mood.py, weights.py) is tested and sophisticated. Rewriting it for web would be months of wasted work. Instead, wrap it.

```python
class MusicMindEngine:
    """Web-layer wrapper around existing engine modules.
    Adds user scoping and structured data output."""

    def __init__(self, queries: QueryExecutor, adapter: MultiServiceAdapter):
        self.queries = queries
        self.adapter = adapter

    async def discover(self, user_id: str, count: int = 15,
                       strategy: str = "auto", mood: str | None = None) -> dict:
        """Wraps the existing discovery flow with user scoping.
        Returns structured data (not markdown)."""
        profile = await self._get_or_build_profile(user_id)
        # ... same logic as recommend.py's musicmind_discover
        # but using self.adapter instead of raw AppleMusicClient
        # and returning dict instead of markdown string
        return {
            "songs": [song.to_dict() for song in ranked],
            "strategies_used": strategies_used,
            "mood": mood,
        }
```

### Pattern 2: Token Refresh Middleware

**What:** Automatically refresh expired OAuth tokens before API calls.

**When:** Every call through the service adapter.

```python
class TokenRefreshMiddleware:
    async def ensure_valid_token(self, user_id: str, service: str) -> str:
        """Check token expiry, refresh if needed, return valid token."""
        conn = await self.queries.get_service_connection(user_id, service)
        if conn["token_expires_at"] < datetime.now(UTC) - timedelta(minutes=5):
            new_token = await self._refresh_token(service, conn["refresh_token"])
            await self.queries.update_service_token(user_id, service, new_token)
            return new_token["access_token"]
        return conn["access_token"]
```

### Pattern 3: Tool Result Formatting (MCP-to-Web Bridge)

**What:** Convert existing MCP tool functions (which return markdown strings) to web-friendly structured data.

**When:** Bridging the existing tool implementations to the Claude Orchestrator.

```python
# WRONG: Reusing MCP tool output directly
# The existing tools return markdown like "## Discovered Songs (15)\n*Strategies: ...*"
# This is meant for Claude Desktop, not for a web UI.

# RIGHT: Create structured equivalents
# The engine functions (profile.py, scorer.py) return dicts.
# The MCP tools format those dicts into markdown.
# The web layer should call the engine functions directly and return dicts.
# Claude can reason about the structured data; the frontend renders it.
```

---

## Anti-Patterns to Avoid

### Anti-Pattern 1: Calling MCP Tools from the Web Backend

**What:** Importing and calling the existing `@mcp.tool()` decorated functions from FastAPI routes.

**Why bad:** The MCP tools depend on `mcp.get_context()` for state injection, return markdown strings, and are tightly coupled to the MCP server lifecycle. They cannot be called outside an MCP request context.

**Instead:** Call the engine and database layers directly. The MCP tools are a presentation layer over the engine -- the web backend is a different presentation layer over the same engine.

### Anti-Pattern 2: Storing Claude API Keys in Plaintext

**What:** Saving the user's Anthropic API key as-is in the database.

**Why bad:** Database compromise exposes all users' API keys, enabling unauthorized Claude usage billed to them.

**Instead:** Encrypt API keys at rest using a server-side encryption key (Fernet symmetric encryption from the `cryptography` library). Decrypt only in memory when making API calls. The encryption key lives in server environment variables, not in the database.

### Anti-Pattern 3: Single Anthropic Client Instance

**What:** Creating one `anthropic.Anthropic()` client and swapping API keys per request.

**Why bad:** Race conditions between concurrent users, key leakage between requests.

**Instead:** Create a new `AsyncAnthropic(api_key=user_key)` per chat request. The SDK is lightweight; this is the intended usage pattern for BYOK scenarios.

### Anti-Pattern 4: WebSocket for Chat

**What:** Using WebSocket for the Claude chat interface.

**Why bad:** Adds connection management complexity (reconnection, heartbeat), requires WebSocket-aware infrastructure, and the chat is unidirectional server-push during streaming. The only bidirectional need (sending a new message) happens via REST.

**Instead:** Use SSE (Server-Sent Events) via FastAPI's `StreamingResponse`. Simpler, works through proxies and CDNs, native browser `EventSource` API.

### Anti-Pattern 5: Premature PostgreSQL

**What:** Requiring PostgreSQL from day one for a friends-group app.

**Why bad:** Adds deployment complexity (install Postgres, manage connections, backups) when SQLite with WAL mode handles 5-10 concurrent users fine. The existing codebase already uses SQLAlchemy Core which abstracts the difference.

**Instead:** Keep SQLite for local development and initial deployment. Structure the code so PostgreSQL is a configuration swap (change connection string). Migrate when actual scale demands it.

---

## Critical Architecture Discovery: Spotify Audio Features

**IMPORTANT:** Spotify deprecated the Audio Features endpoint for new applications as of November 27, 2024. New Spotify Developer apps registered after this date receive 403 errors on `/v1/audio-features`. Only apps with existing extended quota mode access retain this endpoint.

**Impact on project architecture:**
- The PROJECT.md assumption "Spotify audio features as primary, librosa as fallback" is **inverted**. For a new Spotify app registration, audio features are NOT available from Spotify.
- Librosa-based extraction (existing Tier 2) becomes the primary audio feature source for ALL tracks, not just Apple Music tracks.
- Alternatively, the SoundNet Track Analysis API or similar third-party services could provide audio features.
- The multi-service adapter should still be designed with an `get_audio_features()` method, but the Spotify implementation will return empty/None, and the engine should fallback gracefully (which it already does -- Tier 1 metadata-only scoring works without audio features).

**Recommended approach:** Design the adapter interface to include audio features, but accept that Tier 1 (metadata-only) scoring is the reliable baseline. If the developer can obtain extended Spotify API access, audio features become a bonus. Librosa extraction remains available as Tier 2 for tracks with preview URLs.

**Spotify Dev Mode also limits to 5 users per app** -- but this aligns with the friends-group scope defined in PROJECT.md.

---

## Scalability Considerations

| Concern | At 5 users (target) | At 50 users | At 500+ users |
|---------|---------------------|-------------|---------------|
| Database | SQLite + WAL mode | SQLite still fine | PostgreSQL needed |
| Claude API | Per-user BYOK, no backend cost | Same | Same |
| OAuth tokens | Stored in DB, refreshed on demand | Same, add connection pooling | Redis for token cache |
| API rate limits | Apple: 20 req/s shared, Spotify: 100+ req/s | May hit Apple limits | Need per-user rate limiting |
| Chat history | JSON in DB, load on demand | Same | Paginate, summarize old conversations |
| Recommendations | On-demand computation | Same | Background job queue |

---

## Suggested Build Order (Dependency Graph)

The components have strict dependency ordering:

```
Phase 1: Foundation (everything else depends on this)
    [Database multi-user schema] <-- all data operations need user scoping
    [FastAPI skeleton + auth]    <-- all endpoints need authentication
    [Engine wrapper]             <-- dashboard and chat need the engine

Phase 2: Core Experience
    [Multi-service adapter]      <-- needs Phase 1 DB for token storage
    [Spotify client]             <-- needs adapter interface defined
    [Apple Music client adapter] <-- wrap existing client behind protocol
    [Dashboard API + frontend]   <-- needs engine wrapper + adapter

Phase 3: Claude Chat
    [Claude Orchestrator]        <-- needs engine wrapper + tool definitions
    [Tool Registry]              <-- needs engine wrapper
    [Chat frontend]              <-- needs orchestrator SSE endpoint
    [Conversation persistence]   <-- needs DB schema

Phase 4: Polish
    [Taste visualization]        <-- needs dashboard data flowing
    [Service connection UI]      <-- needs OAuth flows working
    [Settings / API key mgmt]    <-- needs encryption working
```

**Why this order:**
1. **Database first** because every component reads/writes user-scoped data. Without multi-user schema, nothing works.
2. **Auth second** because every API endpoint needs to know which user is making the request.
3. **Engine wrapper third** because both the dashboard and chat depend on engine operations.
4. **Service adapter before dashboard** because the dashboard needs to display data from connected services.
5. **Claude chat after dashboard** because the chat's tool calls invoke the same engine functions the dashboard uses. The dashboard validates that the engine wrapper works correctly before adding the complexity of Claude's agentic loop.

---

## Sources

- [Anthropic Tool Use Documentation](https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview) -- HIGH confidence, official docs
- [Anthropic Streaming Documentation](https://platform.claude.com/docs/en/build-with-claude/streaming) -- HIGH confidence, official docs
- [Spotify API Changes Blog Post (Nov 2024)](https://developer.spotify.com/blog/2024-11-27-changes-to-the-web-api) -- HIGH confidence, official Spotify announcement
- [Spotify February 2026 Migration Guide](https://developer.spotify.com/documentation/web-api/tutorials/february-2026-migration-guide) -- HIGH confidence, official docs
- [Spotify March 2026 Changelog](https://developer.spotify.com/documentation/web-api/references/changes/march-2026) -- HIGH confidence, official docs
- [FastAPI SSE Tutorial](https://fastapi.tiangolo.com/tutorial/server-sent-events/) -- HIGH confidence, official docs
- [Multi-tenancy with FastAPI + SQLAlchemy](https://mergeboard.com/blog/6-multitenancy-fastapi-sqlalchemy-postgresql/) -- MEDIUM confidence, well-documented blog
- [MusicAPI.com Unified Music API](https://musicapi.com/) -- MEDIUM confidence, commercial reference for adapter pattern

---

*Architecture analysis: 2026-03-26*
