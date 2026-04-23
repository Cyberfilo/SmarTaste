/**
 * Chat state management hook with SSE streaming.
 *
 * V 6.410 — BYOK removed. Chat always uses the globally-configured OpenAI
 * key on the backend; no per-user key state or model selection.
 */

import { create } from "zustand";
import { streamChat, abortChat } from "@/lib/sse";
import { apiFetch } from "@/lib/api";

// ── Types ──────────────────────────────────────────────

export interface ChatMessage {
  id?: string;
  role: "user" | "assistant" | "tool";
  content: string;
  toolUse?: { tool: string; input: Record<string, unknown> };
  toolResult?: { tool: string; output: string };
}

export interface ActiveTool {
  tool: string;
  input: Record<string, unknown>;
}

interface ConversationDetail {
  id: string;
  title: string;
  messages: Array<{
    role: string;
    content: string;
    tool_use: object | null;
    tool_result: object | null;
  }>;
  created_at: string;
  updated_at: string;
}

interface ChatState {
  messages: ChatMessage[];
  conversationId: string | null;
  isStreaming: boolean;
  activeTools: ActiveTool[];
  error: string | null;

  // Actions
  sendMessage: (text: string) => void;
  loadConversation: (id: string) => Promise<void>;
  newConversation: () => void;
  cancelStream: () => void;
  dismissError: () => void;
}

// ── Error code to user-friendly message mapping ────────

function mapErrorCode(code: string, fallback: string): string {
  switch (code) {
    case "rate_limited":
      return "Rate limit reached. Please wait a moment.";
    case "internal":
    default:
      return fallback || "Something went wrong. Please try again.";
  }
}

// ── Zustand store ──────────────────────────────────────

export const useChatStore = create<ChatState>((set, get) => ({
  messages: [],
  conversationId: null,
  isStreaming: false,
  activeTools: [],
  error: null,

  sendMessage: (text: string) => {
    const { conversationId } = get();

    const userMessage: ChatMessage = { role: "user", content: text };
    set((state) => ({
      messages: [...state.messages, userMessage],
      isStreaming: true,
      error: null,
      activeTools: [],
    }));

    set((state) => ({
      messages: [...state.messages, { role: "assistant", content: "" }],
    }));

    streamChat(
      {
        conversationId: conversationId ?? undefined,
        message: text,
      },
      {
        onTextDelta: (delta: string) => {
          set((state) => {
            const msgs = [...state.messages];
            for (let i = msgs.length - 1; i >= 0; i--) {
              if (msgs[i].role === "assistant") {
                msgs[i] = { ...msgs[i], content: msgs[i].content + delta };
                break;
              }
            }
            return { messages: msgs };
          });
        },

        onToolUse: (data) => {
          set((state) => ({
            activeTools: [...state.activeTools, { tool: data.tool, input: data.input }],
          }));
        },

        onToolResult: (data) => {
          set((state) => ({
            activeTools: state.activeTools.filter((t) => t.tool !== data.tool),
            messages: [
              ...state.messages,
              {
                role: "tool" as const,
                content: data.output,
                toolResult: { tool: data.tool, output: data.output },
              },
            ],
          }));
        },

        onComplete: (data) => {
          set({
            conversationId: data.conversation_id,
            isStreaming: false,
            activeTools: [],
          });
        },

        onError: (data) => {
          set({
            error: mapErrorCode(data.code, data.error),
            isStreaming: false,
            activeTools: [],
          });
        },
      },
    );
  },

  loadConversation: async (id: string) => {
    try {
      const data = await apiFetch<ConversationDetail>(
        `/api/chat/conversations/${id}`,
      );
      const messages: ChatMessage[] = data.messages.map((m) => ({
        role: m.role as ChatMessage["role"],
        content: m.content,
        ...(m.tool_use ? { toolUse: m.tool_use as ChatMessage["toolUse"] } : {}),
        ...(m.tool_result
          ? { toolResult: m.tool_result as ChatMessage["toolResult"] }
          : {}),
      }));
      set({
        messages,
        conversationId: id,
        isStreaming: false,
        activeTools: [],
        error: null,
      });
    } catch {
      set({ error: "Failed to load conversation." });
    }
  },

  newConversation: () => {
    abortChat();
    set({
      messages: [],
      conversationId: null,
      isStreaming: false,
      activeTools: [],
      error: null,
    });
  },

  cancelStream: () => {
    abortChat();
    set({ isStreaming: false, activeTools: [] });
  },

  dismissError: () => {
    set({ error: null });
  },
}));
