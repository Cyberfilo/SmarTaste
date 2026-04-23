"use client";

/**
 * Main chat interface combining all chat components.
 *
 * V 6.410 — BYOK removed; chat always routes through the global
 * OpenAI key on the backend. No per-user key gating, no model selector.
 */

import { useEffect, useRef } from "react";
import { Sparkles } from "lucide-react";
import { useChatStore } from "@/hooks/use-chat";
import { MessageBubble } from "@/components/chat/message-bubble";
import { ToolActivityIndicator } from "@/components/chat/tool-activity-indicator";
import { ChatInput } from "@/components/chat/chat-input";
import { ConversationSidebar } from "@/components/chat/conversation-sidebar";

// ── Empty state component ──────────────────────────────

function EmptyState() {
  return (
    <div className="flex flex-1 items-center justify-center px-6">
      <div className="max-w-md text-center">
        <div className="mx-auto mb-4 flex h-16 w-16 items-center justify-center rounded-2xl bg-purple-600/10">
          <Sparkles className="h-8 w-8 text-purple-500" />
        </div>
        <h2 className="mb-2 text-lg font-semibold text-foreground">
          Ask about your music
        </h2>
        <p className="mb-6 text-sm leading-relaxed text-muted-foreground">
          Have a conversation about your taste, discover new music, or explore
          what your listening habits say about you.
        </p>
        <div className="space-y-2">
          <ExamplePrompt text="What does my taste profile say about me?" />
          <ExamplePrompt text="Find me something like early Radiohead but more electronic." />
          <ExamplePrompt text="What are my top genres this month?" />
        </div>
      </div>
    </div>
  );
}

function ExamplePrompt({ text }: { text: string }) {
  const sendMessage = useChatStore((s) => s.sendMessage);

  return (
    <button
      onClick={() => sendMessage(text)}
      className="w-full rounded-xl border border-border bg-zinc-800/30 px-4 py-3 text-left text-sm text-muted-foreground transition-colors hover:border-purple-500/30 hover:bg-zinc-800/60 hover:text-foreground"
    >
      {text}
    </button>
  );
}

// ── Error banner ───────────────────────────────────────

function ErrorBanner({
  message,
  onDismiss,
}: {
  message: string;
  onDismiss: () => void;
}) {
  return (
    <div className="mx-4 mb-2 flex items-center justify-between rounded-lg border border-red-500/20 bg-red-500/10 px-4 py-2.5">
      <p className="text-sm text-red-400">{message}</p>
      <button
        onClick={onDismiss}
        className="ml-3 text-xs text-red-400/70 transition-colors hover:text-red-400"
      >
        Dismiss
      </button>
    </div>
  );
}

// ── Main chat interface ────────────────────────────────

export function ChatInterface() {
  const {
    messages,
    conversationId,
    isStreaming,
    activeTools,
    error,
    sendMessage,
    loadConversation,
    newConversation,
    cancelStream,
    dismissError,
  } = useChatStore();

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const messageListRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to bottom on new messages or streaming updates
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, activeTools]);

  // Find the index of the last assistant message for cursor display
  let lastAssistantIndex = -1;
  for (let i = messages.length - 1; i >= 0; i--) {
    if (messages[i].role === "assistant") {
      lastAssistantIndex = i;
      break;
    }
  }

  return (
    <div className="relative flex h-full">
      <ConversationSidebar
        activeConversationId={conversationId}
        onSelectConversation={loadConversation}
        onNewConversation={newConversation}
      />

      <div className="flex min-w-0 flex-1 flex-col">
        {messages.length === 0 ? (
          <>
            <EmptyState />
            <ChatInput
              onSend={sendMessage}
              onCancel={cancelStream}
              isStreaming={isStreaming}
            />
          </>
        ) : (
          <>
            <div
              ref={messageListRef}
              className="flex-1 overflow-y-auto pb-4 pt-4"
            >
              <div className="mx-auto max-w-3xl">
                {messages.map((msg, i) => (
                  <MessageBubble
                    key={`${msg.role}-${i}`}
                    message={msg}
                    isLastAssistant={i === lastAssistantIndex}
                    isStreaming={isStreaming}
                  />
                ))}

                <ToolActivityIndicator activeTools={activeTools} />

                <div ref={messagesEndRef} />
              </div>
            </div>

            {error && (
              <ErrorBanner message={error} onDismiss={dismissError} />
            )}

            <ChatInput
              onSend={sendMessage}
              onCancel={cancelStream}
              isStreaming={isStreaming}
            />
          </>
        )}
      </div>
    </div>
  );
}
