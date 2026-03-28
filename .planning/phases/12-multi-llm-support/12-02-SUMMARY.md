---
phase: 12-multi-llm-support
plan: 02
subsystem: frontend-llm-ui
tags: [openai-byok, model-selector, chat-sse, zustand, tanstack-query]
dependency_graph:
  requires:
    - phase: 12-01
      provides: backend LLM provider abstraction, OpenAI BYOK endpoints, chat model dispatch
  provides:
    - OpenAI BYOK key management UI
    - Model preference selector (Claude/GPT-4o/GPT-4.1)
    - Per-conversation model override in chat header
    - SSE client model param passthrough
  affects: [chat-interface, settings-page]
tech_stack:
  added: []
  patterns: [provider-aware-key-hooks, localStorage-model-preference, hover-dropdown-selector]
key_files:
  created:
    - frontend/src/hooks/use-openai-key.ts
    - frontend/src/components/settings/openai-key-manager.tsx
    - frontend/src/components/settings/model-selector.tsx
  modified:
    - frontend/src/app/(app)/settings/page.tsx
    - frontend/src/hooks/use-chat.ts
    - frontend/src/lib/sse.ts
    - frontend/src/components/chat/chat-interface.tsx
    - frontend/src/components/chat/chat-input.tsx
    - frontend/src/app/(app)/chat/page.tsx
key_decisions:
  - "Custom radio-style buttons for model selector (no RadioGroup dependency needed)"
  - "Model preference stored in localStorage, not backend (no round-trip for UI-only preference)"
  - "Chat header hover-dropdown for per-conversation model override (lightweight, no modal)"
  - "Map frontend model IDs to backend provider names in use-chat.ts (gpt-* -> openai)"
  - "ChatInterface now checks both Claude and OpenAI key status internally via hooks"
patterns_established:
  - "MODEL_OPTIONS constant shared between settings and chat for consistent model definitions"
  - "getStoredModel/setStoredModel helpers for localStorage model preference"
  - "modelToProvider mapping for frontend-to-backend model name translation"
requirements_completed: [CHAT-01, CHAT-02, CHAT-03, CHAT-05, CHAT-10]
duration: 6min
completed: 2026-03-28
---

# Phase 12 Plan 02: Frontend Model Selector and OpenAI BYOK UI Summary

Frontend model selector with OpenAI BYOK key management, per-conversation model override in chat header, and SSE model param passthrough to backend providers.

## What Was Built

### Task 1: OpenAI Key Hooks, Key Manager Component, and Model Selector
**Commit:** 2c2a602

Created the OpenAI BYOK frontend and model preference selector:
- **use-openai-key.ts**: TanStack Query hooks mirroring use-claude-key.ts pattern -- `useOpenAIKeyStatus`, `useStoreOpenAIKey`, `useValidateOpenAIKey`, `useDeleteOpenAIKey`, `useOpenAICostEstimate`. All query keys namespaced with "openai-" prefix.
- **openai-key-manager.tsx**: Full BYOK card component for OpenAI keys with store/validate/delete/update flows, "sk-..." placeholder, link to platform.openai.com/api-keys, and cost estimate section.
- **model-selector.tsx**: Card with three radio-style options (Claude Sonnet 4, GPT-4o, GPT-4.1). Preference stored in localStorage under "musicmind-preferred-model". Shows amber warning when selected model's API key is not configured. Exports `MODEL_OPTIONS`, `getStoredModel`, `setStoredModel` for reuse.
- **settings/page.tsx**: Added ModelSelector and OpenAIKeyManager components in order: ServiceConnections -> BYOKKeyManager (Claude) -> ModelSelector -> OpenAIKeyManager.

### Task 2: Chat SSE Model Passthrough and Interface Model Override
**Commit:** 08d14d0

Wired model selection through the entire chat flow:
- **sse.ts**: Added `model?: string` to `StreamChatParams`. When truthy, included in POST body as `model` field.
- **use-chat.ts**: Added `selectedModel` and `setSelectedModel` to Zustand store. Initialized from localStorage on store creation. `sendMessage` maps model IDs to provider names (gpt-* -> "openai", claude -> "claude") via `modelToProvider()`. Added `openai_key_missing` and `claude_key_missing` error code mappings.
- **chat-interface.tsx**: Added `ChatModelSelector` hover-dropdown in chat header showing all 3 model options with descriptions. `NoKeyState` now adapts message based on selected model's provider. `EmptyState` heading changed to generic "Ask about your music". ChatInterface now checks both `useKeyStatus` and `useOpenAIKeyStatus` hooks internally.
- **chat-input.tsx**: Added "Powered by {model name}" subtle indicator below the input area. Placeholder changed from "Ask Claude about your music..." to "Ask about your music...".
- **chat/page.tsx**: Simplified to delegate all key status checking to ChatInterface (removed manual Claude-only key check).

## Decisions Made

1. **Custom radio buttons over RadioGroup component**: No RadioGroup UI component was available; custom button-based radio selection works cleanly without adding dependencies.
2. **localStorage for model preference**: Avoids backend round-trip for a purely UI preference. Shared via `getStoredModel()`/`setStoredModel()` helpers.
3. **Hover dropdown for chat model selector**: Lightweight UX for per-conversation override -- no modal, no separate dialog. Visible on hover/focus-within.
4. **Frontend maps model IDs to provider names**: The `modelToProvider()` function translates "claude" -> "claude", "gpt-4o" -> "openai", "gpt-4.1" -> "openai". Backend receives just the provider name.
5. **ChatInterface owns key status checking**: Moved from chat page into ChatInterface so it can check the right provider based on selectedModel.

## Deviations from Plan

None -- plan executed exactly as written.

## Known Stubs

None. All UI components are fully wired to real backend endpoints via existing API hooks and SSE client.

## Test Results

- TypeScript compilation: Clean (0 errors)
- All acceptance criteria: Passed (6/6 for Task 1, 4/4 for Task 2)

## Self-Check: PASSED

All 3 created files exist. All 6 modified files exist. Both task commits (2c2a602, 08d14d0) verified in git log.
