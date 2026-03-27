/**
 * TanStack Query hooks for conversation CRUD operations.
 *
 * Conversations are server state (cacheable, fetchable) unlike the
 * real-time chat messages which use Zustand. These hooks manage the
 * conversation list sidebar and individual conversation loading.
 */

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api";

// ── Types ──────────────────────────────────────────────

export interface Conversation {
  id: string;
  title: string;
  message_count: number;
  created_at: string;
  updated_at: string;
}

interface ConversationsResponse {
  conversations: Conversation[];
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

// ── Query keys ─────────────────────────────────────────

export const conversationKeys = {
  all: ["conversations"] as const,
  detail: (id: string) => ["conversations", id] as const,
};

// ── Hooks ──────────────────────────────────────────────

/**
 * Fetch the list of all conversations.
 * staleTime 30s -- conversations update frequently during chat.
 */
export function useConversations() {
  return useQuery({
    queryKey: conversationKeys.all,
    queryFn: () =>
      apiFetch<ConversationsResponse>("/api/chat/conversations"),
    staleTime: 30 * 1000, // 30 seconds
    select: (data) => data.conversations,
  });
}

/**
 * Fetch a single conversation with its messages.
 * Only enabled when id is provided.
 */
export function useConversation(id: string | null) {
  return useQuery({
    queryKey: conversationKeys.detail(id ?? ""),
    queryFn: () =>
      apiFetch<ConversationDetail>(`/api/chat/conversations/${id}`),
    enabled: !!id,
  });
}

/**
 * Delete a conversation mutation.
 * Invalidates the conversation list on success.
 */
export function useDeleteConversation() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (id: string) =>
      apiFetch<void>(`/api/chat/conversations/${id}`, {
        method: "DELETE",
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: conversationKeys.all });
    },
  });
}
