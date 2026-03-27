"use client";

/**
 * Main chat interface combining all chat components.
 *
 * Layout: conversation sidebar (drawer on mobile, sidebar on lg:)
 * + message list (scrollable, auto-scrolls) + tool activity indicator
 * + fixed bottom input.
 *
 * Per D-04: "Chat should feel like a native messaging app. Dark background,
 * subtle borders, smooth auto-scroll. No chatbot widget aesthetic."
 */

import { useEffect, useRef } from "react";
import { Settings, Sparkles } from "lucide-react";
import Link from "next/link";
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
        <div className="mx-auto mb-4 flex h-16 w-16 items-center justify-center rounded-2xl bg-emerald-600/10">
          <Sparkles className="h-8 w-8 text-emerald-500" />
        </div>
        <h2 className="mb-2 text-lg font-semibold text-foreground">
          Ask Claude about your music
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
      className="w-full rounded-xl border border-border bg-zinc-800/30 px-4 py-3 text-left text-sm text-muted-foreground transition-colors hover:border-emerald-500/30 hover:bg-zinc-800/60 hover:text-foreground"
    >
      {text}
    </button>
  );
}

// ── No API key state ───────────────────────────────────

function NoKeyState() {
  return (
    <div className="flex flex-1 items-center justify-center px-6">
      <div className="max-w-sm text-center">
        <div className="mx-auto mb-4 flex h-16 w-16 items-center justify-center rounded-2xl bg-zinc-800">
          <Settings className="h-8 w-8 text-muted-foreground" />
        </div>
        <h2 className="mb-2 text-lg font-semibold text-foreground">
          Claude API key required
        </h2>
        <p className="mb-4 text-sm text-muted-foreground">
          Add your Anthropic API key in Settings to start chatting.
        </p>
        <Link
          href="/settings"
          className="inline-flex items-center gap-2 rounded-lg bg-emerald-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-emerald-700"
        >
          <Settings className="h-4 w-4" />
          Go to Settings
        </Link>
      </div>
    </div>
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

interface ChatInterfaceProps {
  hasApiKey?: boolean;
}

export function ChatInterface({ hasApiKey = true }: ChatInterfaceProps) {
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
      {/* Conversation sidebar */}
      <ConversationSidebar
        activeConversationId={conversationId}
        onSelectConversation={loadConversation}
        onNewConversation={newConversation}
      />

      {/* Chat area */}
      <div className="flex min-w-0 flex-1 flex-col">
        {!hasApiKey ? (
          <NoKeyState />
        ) : messages.length === 0 ? (
          <>
            <EmptyState />
            {/* Input still visible in empty state */}
            <ChatInput
              onSend={sendMessage}
              onCancel={cancelStream}
              isStreaming={isStreaming}
            />
          </>
        ) : (
          <>
            {/* Message list */}
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

                {/* Tool activity indicator */}
                <ToolActivityIndicator activeTools={activeTools} />

                {/* Scroll anchor */}
                <div ref={messagesEndRef} />
              </div>
            </div>

            {/* Error banner */}
            {error && (
              <ErrorBanner message={error} onDismiss={dismissError} />
            )}

            {/* Input */}
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
