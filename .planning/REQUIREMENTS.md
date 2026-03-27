# Requirements: MusicMind Web

**Defined:** 2026-03-26
**Core Value:** Users get genuinely good music recommendations powered by real audio analysis and their actual listening data across services

## v1 Requirements

Requirements for initial release. Each maps to roadmap phases.

### User Accounts

- [x] **ACCT-01**: User can create an account with email and password
- [x] **ACCT-02**: User can log in and stay logged in across browser sessions (JWT)
- [x] **ACCT-03**: User can log out from any page
- [x] **ACCT-04**: User session is secure (httpOnly cookies, CSRF protection)

### Service Connection

- [x] **SVCN-01**: User can connect their Spotify account via OAuth PKCE flow
- [x] **SVCN-02**: User can connect their Apple Music account via MusicKit JS OAuth flow
- [x] **SVCN-03**: User can disconnect a connected service
- [x] **SVCN-04**: User can see which services are connected and their connection status
- [x] **SVCN-05**: User is prompted to re-authenticate when Apple Music token expires (no silent refresh available)
- [x] **SVCN-06**: Spotify access tokens are automatically refreshed using stored refresh token

### BYOK Claude API

- [x] **BYOK-01**: User can enter and store their Anthropic API key (encrypted server-side)
- [x] **BYOK-02**: User can validate their key works before first chat (cheap test API call)
- [x] **BYOK-03**: User can update or remove their stored API key
- [x] **BYOK-04**: User sees estimated cost per chat message for transparency

### Taste Profile

- [x] **TAST-01**: User can view their taste profile showing top genres with regional specificity
- [x] **TAST-02**: User can view their top artists ranked by affinity
- [x] **TAST-03**: User can view their audio trait preferences (energy, danceability, valence, acousticness)
- [x] **TAST-04**: User can view their taste profile built from a single connected service
- [ ] **TAST-05**: User can view a unified taste profile merging both services (if both connected)

### Listening Stats

- [x] **STAT-01**: User can view their top tracks by time period (last month, 6 months, all time)
- [x] **STAT-02**: User can view their top artists by time period
- [x] **STAT-03**: User can view their top genres by time period
- [x] **STAT-04**: User can view stats from either or both connected services

### Recommendations

- [x] **RECO-01**: User can view a recommendation feed with personalized song suggestions
- [x] **RECO-02**: Each recommendation includes a natural-language explanation of why it was suggested
- [ ] **RECO-03**: User can give thumbs up/down feedback on any recommendation
- [ ] **RECO-04**: Feedback drives adaptive weight optimization for future recommendations
- [x] **RECO-05**: User can select a discovery strategy (similar artist, genre adjacent, editorial mining, chart filter)
- [x] **RECO-06**: User can filter recommendations by mood (focus, energy, chill, melancholy, etc.)
- [ ] **RECO-07**: User can view the full 7-dimension scoring breakdown for any recommendation
- [ ] **RECO-08**: User can view a per-track audio feature visualization (radar chart: energy, danceability, valence, acousticness, tempo, instrumentalness)

### Claude Chat

- [ ] **CHAT-01**: User can open a Claude chat interface and send messages
- [ ] **CHAT-02**: Claude responses stream in real-time (SSE) for perceived speed
- [ ] **CHAT-03**: Claude can call MusicMind engine tools (taste profile, discovery, scoring) via tool_use
- [ ] **CHAT-04**: User sees "searching your library" indicators during tool execution
- [ ] **CHAT-05**: User can have a multi-turn conversation with context preserved
- [ ] **CHAT-06**: User can ask Claude to explain any recommendation in detail
- [ ] **CHAT-07**: User can ask Claude to find music matching a description ("something like early Radiohead but more electronic")
- [ ] **CHAT-08**: User can ask Claude to adjust their taste preferences via natural language ("less mainstream pop")
- [ ] **CHAT-09**: Conversation history is persisted per user across sessions
- [ ] **CHAT-10**: Errors (key expired, rate limited, insufficient balance) show user-friendly messages

### Multi-Service

- [ ] **MSVC-01**: Data from both services is normalized to a shared internal representation
- [ ] **MSVC-02**: Recommendations can draw from both services' catalogs transparently
- [ ] **MSVC-03**: Genre taxonomies are mapped to a shared canonical representation
- [ ] **MSVC-04**: Cross-service track deduplication via ISRC (with fuzzy title+artist fallback)

### Infrastructure

- [x] **INFR-01**: Multi-user database with user-scoped data isolation
- [x] **INFR-02**: Database migrations via Alembic for schema evolution
- [x] **INFR-03**: API key and OAuth token encryption at rest
- [ ] **INFR-04**: Responsive web design (works on desktop and mobile browsers)
- [x] **INFR-05**: Local-first deployment (runs via docker-compose or similar)

## v2 Requirements

Deferred to future release. Tracked but not in current roadmap.

### Taste Evolution

- **EVOL-01**: User can view a timeline showing how their taste changes over months
- **EVOL-02**: Historical profile snapshots are stored periodically (build storage in v1, UI in v2)

### Cross-Service Reconciliation

- **XSVC-01**: User can see a narrated comparison of their taste across services
- **XSVC-02**: Reconciliation narrative highlights genre/mood differences between services

### Playlist Features

- **PLST-01**: User can save individual recommendations to their streaming library
- **PLST-02**: User can generate a playlist from recommendations (write access to streaming account)

## Out of Scope

Explicitly excluded. Documented to prevent scope creep.

| Feature | Reason |
|---------|--------|
| In-app music playback | DRM/licensing complexity, native apps do it better. Use 30s previews + deep links |
| Social features (sharing, friends, leaderboards) | Moderation burden, scope explosion. Friend group shares via external messaging |
| Admin panel / user analytics | Not needed at friend-group scale |
| Custom algorithm tuning UI (sliders) | Natural language editing via Claude (CHAT-08) is superior UX |
| Multi-language interface | Small friend group with known language context |
| Mobile native app | Responsive web is sufficient. PWA is overkill at this scale |
| Real-time notification system | Dashboard shows fresh recs on visit. No background push needed |
| Public SaaS / billing / payment | Friend group only, no monetization |

## Traceability

Which phases cover which requirements. Updated during roadmap creation.

| Requirement | Phase | Status |
|-------------|-------|--------|
| ACCT-01 | Phase 2 | Complete |
| ACCT-02 | Phase 2 | Complete |
| ACCT-03 | Phase 2 | Complete |
| ACCT-04 | Phase 2 | Complete |
| SVCN-01 | Phase 3 | Complete |
| SVCN-02 | Phase 3 | Complete |
| SVCN-03 | Phase 3 | Complete |
| SVCN-04 | Phase 3 | Complete |
| SVCN-05 | Phase 3 | Complete |
| SVCN-06 | Phase 3 | Complete |
| BYOK-01 | Phase 4 | Complete |
| BYOK-02 | Phase 4 | Complete |
| BYOK-03 | Phase 4 | Complete |
| BYOK-04 | Phase 4 | Complete |
| TAST-01 | Phase 5 | Complete |
| TAST-02 | Phase 5 | Complete |
| TAST-03 | Phase 5 | Complete |
| TAST-04 | Phase 5 | Complete |
| TAST-05 | Phase 8 | Pending |
| STAT-01 | Phase 6 | Complete |
| STAT-02 | Phase 6 | Complete |
| STAT-03 | Phase 6 | Complete |
| STAT-04 | Phase 6 | Complete |
| RECO-01 | Phase 7 | Complete |
| RECO-02 | Phase 7 | Complete |
| RECO-03 | Phase 7 | Pending |
| RECO-04 | Phase 7 | Pending |
| RECO-05 | Phase 7 | Complete |
| RECO-06 | Phase 7 | Complete |
| RECO-07 | Phase 10 | Pending |
| RECO-08 | Phase 10 | Pending |
| CHAT-01 | Phase 9 | Pending |
| CHAT-02 | Phase 9 | Pending |
| CHAT-03 | Phase 9 | Pending |
| CHAT-04 | Phase 9 | Pending |
| CHAT-05 | Phase 9 | Pending |
| CHAT-06 | Phase 9 | Pending |
| CHAT-07 | Phase 9 | Pending |
| CHAT-08 | Phase 9 | Pending |
| CHAT-09 | Phase 9 | Pending |
| CHAT-10 | Phase 9 | Pending |
| MSVC-01 | Phase 8 | Pending |
| MSVC-02 | Phase 8 | Pending |
| MSVC-03 | Phase 8 | Pending |
| MSVC-04 | Phase 8 | Pending |
| INFR-01 | Phase 1 | Complete |
| INFR-02 | Phase 1 | Complete |
| INFR-03 | Phase 1 | Complete |
| INFR-04 | Phase 10 | Pending |
| INFR-05 | Phase 1 | Complete |

**Coverage:**
- v1 requirements: 50 total
- Mapped to phases: 50
- Unmapped: 0

---
*Requirements defined: 2026-03-26*
*Last updated: 2026-03-26 after roadmap creation*
