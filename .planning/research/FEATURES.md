# Feature Landscape

**Domain:** Music Discovery Webapp (Dashboard + AI Chat)
**Researched:** 2026-03-26
**Overall confidence:** MEDIUM-HIGH

## Table Stakes

Features users expect from a music discovery webapp. Missing any of these and the product feels broken or incomplete. Ordered by implementation priority.

| # | Feature | Why Expected | Complexity | Notes |
|---|---------|--------------|------------|-------|
| T1 | **Service connection (OAuth)** | Users cannot do anything without connecting Spotify/Apple Music. Zero-value product without this. | High | Three OAuth flows (user accounts, Spotify, Apple Music). Each has different token lifetimes, refresh patterns, and scoping. Spotify OAuth is well-documented; Apple Music MusicKit JS flow is quirkier. |
| T2 | **Recommendation feed with explanations** | The core value proposition. Users come here for discovery. A feed without "why" feels like a black box. | Medium | Engine already generates scores across 7 dimensions. Surface the top 2-3 scoring factors per recommendation as natural-language explanations ("Strong genre match with your Italian Hip-Hop taste", "Similar energy and tempo to tracks you love"). |
| T3 | **Taste profile overview** | stats.fm, Obscurify, Spotify Wrapped, and the new Spotify Taste Profile (March 2026 beta) have set the expectation that you can see how the algorithm understands you. Without this, users have no way to trust or calibrate the system. | Medium | Show top genres (with regional specificity -- "Italian Hip-Hop/Rap" not just "Hip-Hop"), top artists, audio feature averages (energy, danceability, valence). The existing engine already computes all of this in `profile.py`. |
| T4 | **Feedback loop** | Recommendations must improve. Without thumbs up/down or similar, the system is static. Users abandon static recommendation engines quickly. | Low | Already have `recommendation_feedback` table. Surface simple thumbs up/down/skip UI. The adaptive weight system in `weights.py` already consumes this. |
| T5 | **Claude chat interface** | Listed as a core project requirement. The conversational exploration pattern is the primary differentiator. Without it, this is just another stats dashboard. | High | Requires: streaming responses, tool_use loop (Claude calls engine tools, backend executes, returns results), conversation history management, BYOK key input. See Claude integration notes below. |
| T6 | **BYOK API key management** | Required by architecture (no subsidized AI costs). Users cannot access the chat without providing a key. Must be friction-minimized. | Medium | Validate key on entry with a cheap API call. Store encrypted server-side (never client-side). Show clear usage estimates. Provide a "test your key" flow before first chat. JetBrains and GitHub Copilot BYOK patterns are good references. |
| T7 | **Multi-service unified view** | If a user connects both Spotify and Apple Music, they expect to see one taste profile, not two separate dashboards. The unified cross-service view is listed as a key project differentiator. | High | Data normalization is the hard part (different genre taxonomies, different track IDs, no shared identifiers except ISRC). The recommendation feed should pull from both libraries transparently. |
| T8 | **Basic listening stats** | stats.fm, Receiptify, and Last.fm have normalized the "see your top tracks/artists" pattern. Users connecting their account expect to immediately see their data reflected. | Low | Top tracks, top artists, top genres, by time period (last month / 6 months / all time). Spotify API provides this directly via `top/tracks` and `top/artists` endpoints. Apple Music requires computation from library + recently played. |

## Differentiators

Features that set the product apart. Not expected, but create delight and competitive advantage.

| # | Feature | Value Proposition | Complexity | Notes |
|---|---------|-------------------|------------|-------|
| D1 | **Conversational music exploration via Claude** | No mainstream music app lets you have a real conversation with an AI that has deep access to your actual listening data and a sophisticated recommendation engine. Spotify DJ is voice-only, pre-scripted, and cannot answer follow-up questions. This is a genuine conversation with tool access. | High | Claude with tool_use calling 30+ MusicMind engine tools. User says "find me something like early Radiohead but more electronic" and Claude calls `taste_profile`, `discover_similar_artists`, `score_candidates`, and explains the results. This is the killer feature. |
| D2 | **Recommendation transparency (full scoring breakdown)** | Most platforms are opaque. MusicMind already scores on 7 dimensions with real numbers. Showing users exactly why a song scored 0.82 (genre: 0.91, audio: 0.78, novelty: 0.65, ...) builds trust and lets power users understand the system. | Low | Data already exists in the scorer output. Just needs a UI to expand/collapse the scoring breakdown per recommendation. Radar chart per song showing the 7 dimension scores is a natural fit. |
| D3 | **Taste evolution timeline** | Showing how your taste changes over time is something stats.fm Plus charges for and bijou.fm offers for Last.fm users. It turns a snapshot into a narrative. "You discovered Italian drill in March, then moved toward Japanese city pop by June." | Medium | Requires storing profile snapshots over time (periodic taste profile saves). Visualization: stacked area chart of genre weights over weeks/months. The engine already builds time-aware profiles; just need to persist historical snapshots. |
| D4 | **Cross-service taste reconciliation** | Nobody does this well. Soundiiz and TuneMyMusic transfer playlists but do not reconcile taste profiles. Showing "on Spotify you lean toward electronic, on Apple Music you listen to more acoustic singer-songwriter" with a unified merged profile is unique. | High | Requires the normalized data model to work. Map genres across taxonomies, deduplicate artists, merge play history. The reconciled view is the payoff for the hard T7 work. |
| D5 | **Natural language taste editing** | Spotify just announced this (March 2026, beta, NZ-only, Premium-only). MusicMind can ship this immediately through Claude chat: "I want to hear less mainstream pop in my recommendations" and Claude adjusts profile weights or filters through tool calls. | Medium | Partially free -- Claude can already interpret these requests and call engine tools. Needs: explicit "adjust my profile" tools exposed to Claude, confirmation before applying changes, undo capability. |
| D6 | **Discovery strategy picker** | Let users choose HOW they want to discover: similar artist crawl, genre adjacent exploration, editorial mining, or chart filtering. Most apps just give you "Discover Weekly" with no control over the discovery method. | Low | Four strategies already exist in `discovery.py`. Just surface them as selectable modes in the UI with clear explanations of what each does and what kind of results to expect. |
| D7 | **Mood-contextual recommendations** | "What should I listen to while studying?" or "Give me energy for a workout." The mood filtering engine already exists in `mood.py`. Surfacing this as quick-select buttons or via chat makes the product contextually useful. | Low | Engine already handles this. UI needs mood selector chips (focus, energy, chill, melancholy, etc.) that filter the recommendation feed. Also naturally accessible via Claude chat. |
| D8 | **Audio feature visualization per track** | Show a radar/spider chart of audio features (energy, danceability, valence, acousticness, tempo, instrumentalness) for individual tracks. Obscurify does aggregate scores; showing per-track gives users "oh, THAT'S why I like this song" moments. | Low | Spotify provides per-track audio features via API. Chart.js radar chart is sufficient -- no need for D3 complexity. Display inline on recommendation cards or as an expandable detail view. |

## Anti-Features

Features to explicitly NOT build. These seem tempting but would harm the product, bloat scope, or violate project constraints.

| # | Anti-Feature | Why Avoid | What to Do Instead |
|---|--------------|-----------|-------------------|
| A1 | **In-app music playback** | PROJECT.md explicitly scopes this out. Building a player means dealing with DRM, streaming licenses, and competing with native apps that do it infinitely better. It would also require Spotify Premium for full playback and Apple Music subscription. | Show 30-second preview snippets (available via both APIs) for quick taste. Deep-link to the native app for full playback. "Open in Spotify" / "Open in Apple Music" buttons. |
| A2 | **Social features (sharing, friends, leaderboards)** | PROJECT.md scopes this out. Small friend group does not need a social network. Social features create moderation burden, privacy concerns, and massive scope expansion. | The friend group can share findings via chat/messaging outside the app. If social desire grows, add it in a future milestone -- do not bake it in from the start. |
| A3 | **Playlist generation and sync** | Tempting ("make me a playlist from these recommendations") but requires write access to users' streaming accounts, careful handling of playlist conflicts, and ongoing sync management. High complexity for moderate value. | Instead, let users save individual recommendations to their library (simpler write scope). Claude can suggest a track list that users manually create as a playlist in their native app. Revisit playlist features later if demand emerges. |
| A4 | **Real-time notification system** | "New music matching your taste dropped today!" Push notifications, email digests, etc. Requires notification infrastructure, scheduling, and users rarely want more notifications. | Dashboard shows fresh recommendations when users visit. Claude can mention new releases in conversation. No background notification system needed for a small group. |
| A5 | **Admin panel / user analytics** | PROJECT.md explicitly scopes this out. Not needed at friend-group scale. Building it steals time from user-facing features. | Log basic metrics to the database for manual querying if debugging is needed. No admin UI. |
| A6 | **Custom algorithm tuning UI (sliders/knobs)** | Tempting to let users manually adjust the 7 scoring dimension weights with sliders. But this is a terrible UX -- users do not think in "genre weight: 0.35, audio weight: 0.20" terms. It feels like a debug panel, not a product. | Natural language taste editing via Claude (D5) is far superior. "I want more genre diversity" is better than dragging a slider from 0.08 to 0.15. The thumbs up/down feedback loop (T4) handles implicit tuning. |
| A7 | **Multi-language interface** | Small friend group with a known language context. Internationalization is enormous engineering effort for zero return at this scale. | Hardcode the UI language. The engine already handles multi-language music metadata (Italian genres, etc.) -- that is different from UI localization. |
| A8 | **Mobile-native app** | PROJECT.md explicitly scopes this out. Web-only. Building a mobile app doubles the frontend work and introduces app store review cycles. | Build a responsive web app that works acceptably on mobile browsers. PWA is overkill for this scale but responsive CSS is mandatory. |

## Feature Dependencies

```
T1 (Service OAuth) --> T8 (Basic Stats)
T1 (Service OAuth) --> T3 (Taste Profile)
T1 (Service OAuth) --> T7 (Multi-Service Unified View)
T3 (Taste Profile) --> T2 (Recommendation Feed)
T4 (Feedback Loop) --> T2 (Recommendation Feed) [feedback improves recs]
T6 (BYOK Key Mgmt) --> T5 (Claude Chat)
T7 (Multi-Service) --> D4 (Cross-Service Reconciliation)
T3 (Taste Profile) --> D3 (Taste Evolution Timeline)
T2 (Recommendation Feed) --> D2 (Scoring Breakdown)
T2 (Recommendation Feed) --> D6 (Discovery Strategy Picker)
T2 (Recommendation Feed) --> D7 (Mood Contextual Recs)
T5 (Claude Chat) --> D1 (Conversational Exploration)
T5 (Claude Chat) --> D5 (NL Taste Editing)
D1 (Conversational Exploration) --> D5 (NL Taste Editing)
```

**Critical path:** T1 --> T3 --> T2 is the backbone. Nothing works without OAuth, and recommendations need a taste profile.

**Parallel track:** T6 --> T5 --> D1 can be developed alongside the recommendation pipeline since Claude chat is independent of the dashboard visualizations.

## Claude Chat Integration -- Feature Details

This deserves expanded treatment because it is the most complex and differentiated feature.

### What the Chat Should Do

| Capability | How It Works | Engine Tools Involved |
|------------|-------------|----------------------|
| "What should I listen to right now?" | Claude calls taste profile + discovery + scoring, returns ranked results with explanations | `get_taste_profile`, `discover_*`, `score_candidates` |
| "Find me something like [artist/song]" | Claude identifies the reference, finds similar, scores and filters | `search_catalog`, `discover_similar_artists`, `score_candidates` |
| "Why did you recommend this?" | Claude looks up the scoring breakdown for a specific recommendation | `get_recommendation_details` (needs to be built) |
| "I want more [genre/mood/vibe]" | Claude interprets the request and either adjusts profile weights or filters future recommendations | `update_taste_preferences` (needs to be built), `set_mood_filter` |
| "What are my top genres?" | Claude queries the taste profile and presents it conversationally | `get_taste_profile` |
| "Compare my Spotify and Apple Music taste" | Claude queries both service profiles and narrates the differences | `get_service_profile` (needs per-service variant) |

### Implementation Pattern (from Anthropic docs, HIGH confidence)

1. **Backend receives user message** with conversation history
2. **Backend calls Anthropic API** with user's BYOK key, system prompt describing MusicMind capabilities, tool definitions for engine functions, and conversation history
3. **Claude responds** with text and/or `tool_use` blocks
4. **Backend executes tool calls** against the MusicMind engine, collects results
5. **Backend sends `tool_result`** blocks back to Claude
6. **Claude synthesizes** final response incorporating tool results
7. **Frontend streams** Claude's text responses for perceived speed
8. **Repeat** for multi-turn conversation

### Key Implementation Requirements

- **Streaming is mandatory** for chat UX. Use the Anthropic streaming API to send text tokens as they arrive. Tool calls interrupt the stream; show a "thinking" or "searching your library" indicator during tool execution.
- **Conversation context management**: Store conversation history per user. Implement a context window budget -- the 30+ tools take significant token space, so be selective about which tools to include per turn based on likely intent.
- **System prompt**: Describe the user's connected services, current taste profile summary, and available capabilities. Update per conversation based on context.
- **Cost transparency**: Show estimated cost per message or per conversation. Claude API calls with tool_use can be expensive (multiple round trips). Users with their own keys need visibility.
- **Error handling**: Key expired, rate limited, insufficient balance -- all need graceful user-facing messages, not stack traces.

## MVP Recommendation

**Phase 1 -- Core Pipeline (build first):**
1. T1: Service connection (at least Spotify; Apple Music can come second)
2. T8: Basic listening stats (immediate value on first connection)
3. T3: Taste profile overview (the "aha moment")
4. T4: Feedback loop (thumbs up/down on profile accuracy)

**Phase 2 -- Recommendations + Chat:**
1. T2: Recommendation feed with explanations
2. T6: BYOK API key management
3. T5: Claude chat interface (basic -- text in, text out with tool calls)
4. D1: Conversational exploration (emerges naturally from T5 working)
5. D6: Discovery strategy picker (low-complexity differentiator)
6. D7: Mood contextual recommendations (low-complexity differentiator)

**Phase 3 -- Polish and Differentiation:**
1. T7: Multi-service unified view (if Apple Music not yet connected)
2. D2: Full scoring breakdown UI
3. D8: Audio feature per-track visualization
4. D3: Taste evolution timeline
5. D5: Natural language taste editing

**Defer indefinitely:**
- D4 (Cross-service reconciliation): Only valuable if both services connected AND normalized data model is solid. Research-flag for later.
- A3 (Playlist generation): Revisit only if users explicitly request it after using the core product.

## Complexity Budget

| Complexity | Features | Total |
|------------|----------|-------|
| **High** | T1 (OAuth), T5 (Claude Chat), T7 (Multi-Service), D1 (Conversational), D4 (Cross-Service) | 5 |
| **Medium** | T2 (Rec Feed), T3 (Taste Profile), T6 (BYOK), D3 (Timeline), D5 (NL Editing) | 5 |
| **Low** | T4 (Feedback), T8 (Stats), D2 (Scoring UI), D6 (Strategy Picker), D7 (Mood), D8 (Audio Viz) | 6 |

The balance is reasonable: most high-complexity items are in the critical path or the core differentiator, and there are enough low-complexity wins to ship visible progress between the hard parts.

## Sources

**HIGH confidence (official documentation, primary sources):**
- Anthropic tool_use docs: https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview
- Spotify Taste Profile announcement (March 2026): https://newsroom.spotify.com/2026-03-13/taste-profile-beta-announcement/
- Spotify DJ feature: https://newsroom.spotify.com/2023-02-22/spotify-debuts-a-new-ai-dj-right-in-your-pocket/
- Spotify discovery features (Jan 2026): https://newsroom.spotify.com/2026-01-28/music-discovery-features/
- stats.fm: https://www.stats.fm/
- musictaste.space: https://musictaste.space/

**MEDIUM confidence (verified across multiple sources):**
- BYOK patterns from JetBrains, GitHub Copilot, Vercel implementations (2025-2026)
- Music recommendation explainability research: https://onlinelibrary.wiley.com/doi/full/10.1002/aaai.12056
- EU Digital Services Act impact on recommendation transparency: https://www.music-tomorrow.com/blog/fairness-transparency-music-recommender-systems
- AI chat UX patterns: https://www.smashingmagazine.com/2025/07/design-patterns-ai-interfaces/
- Radar chart visualization (D3 vs Chart.js): https://d3-graph-gallery.com/spider

**LOW confidence (single source or unverified):**
- 75% of users prefer advanced personalization (claimed in discovery article, no primary source found)
- bijou.fm timeline features (inferred from marketing copy, not verified hands-on)
