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

import { useEffect, useRef, useState } from "react";
import { Settings, Sparkles, ChevronDown } from "lucide-react";
import Link from "next/link";
import { useChatStore } from "@/hooks/use-chat";
import { useKeyStatus } from "@/hooks/use-claude-key";
import { useOpenAIKeyStatus } from "@/hooks/use-openai-key";
import { MODEL_OPTIONS } from "@/components/settings/model-selector";
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
      className="w-full rounded-xl border border-border bg-zinc-800/30 px-4 py-3 text-left text-sm text-muted-foreground transition-colors hover:border-emerald-500/30 hover:bg-zinc-800/60 hover:text-foreground"
    >
      {text}
    </button>
  );
}

// ── No API key state ───────────────────────────────────

function NoKeyState({ selectedModel }: { selectedModel: string }) {
  const model = MODEL_OPTIONS.find((m) => m.id === selectedModel);
  const isOpenAI = model?.provider === "openai";

  return (
    <div className="flex flex-1 items-center justify-center px-6">
      <div className="max-w-sm text-center">
        <div className="mx-auto mb-4 flex h-16 w-16 items-center justify-center rounded-2xl bg-zinc-800">
          <Settings className="h-8 w-8 text-muted-foreground" />
        </div>
        <h2 className="mb-2 text-lg font-semibold text-foreground">
          {isOpenAI ? "OpenAI API key required" : "Claude API key required"}
        </h2>
        <p className="mb-4 text-sm text-muted-foreground">
          {isOpenAI
            ? "Add your OpenAI API key in Settings to use GPT models."
            : "Add your Anthropic API key in Settings to start chatting."}
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

// ── Chat header model selector ────────────────────────

function ChatModelSelector() {
  const selectedModel = useChatStore((s) => s.selectedModel);
  const setSelectedModel = useChatStore((s) => s.setSelectedModel);
  const [open, setOpen] = useState(false);

  const currentModel = MODEL_OPTIONS.find((m) => m.id === selectedModel) ?? MODEL_OPTIONS[0];

  return (
    <div className="relative">
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-1.5 rounded-lg border border-border bg-zinc-800/50 px-3 py-1.5 text-xs font-medium text-muted-foreground transition-colors hover:border-emerald-500/30 hover:text-foreground"
      >
        <Sparkles className="h-3 w-3" />
        {currentModel.name}
        <ChevronDown className={`h-3 w-3 transition-transform ${open ? "rotate-180" : ""}`} />
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} />
          <div className="absolute left-0 top-full z-50 mt-1 min-w-[200px] rounded-lg border border-border bg-popover p-1 shadow-lg">
        {MODEL_OPTIONS.map((model) => (
          <button
            key={model.id}
            onClick={() => {
              setSelectedModel(model.id);
              setOpen(false);
            }}
            className={`flex w-full items-center gap-2 rounded-md px-3 py-2 text-left text-xs transition-colors ${
              selectedModel === model.id
                ? "bg-emerald-500/10 text-emerald-400"
                : "text-muted-foreground hover:bg-zinc-800/60 hover:text-foreground"
            }`}
          >
            <div className="flex-1">
              <span className="font-medium">{model.name}</span>
              <p className="text-[10px] text-muted-foreground/70 mt-0.5">
                {model.description}
              </p>
            </div>
            {selectedModel === model.id && (
              <div className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
            )}
          </button>
        ))}
          </div>
        </>
      )}
    </div>
  );
}

// ── Main chat interface ────────────────────────────────

interface ChatInterfaceProps {
  hasApiKey?: boolean;
}

export function ChatInterface({ hasApiKey: _hasApiKey = true }: ChatInterfaceProps) {
  const {
    messages,
    conversationId,
    isStreaming,
    activeTools,
    error,
    selectedModel,
    sendMessage,
    loadConversation,
    newConversation,
    cancelStream,
    dismissError,
  } = useChatStore();

  // Check API key status for both providers
  const { data: claudeKey } = useKeyStatus();
  const { data: openaiKey } = useOpenAIKeyStatus();

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const messageListRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to bottom on new messages or streaming updates
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, activeTools]);

  // Determine if the selected model has its required key configured
  const currentModel = MODEL_OPTIONS.find((m) => m.id === selectedModel);
  const hasRequiredKey =
    currentModel?.provider === "openai"
      ? (openaiKey?.configured ?? _hasApiKey)
      : (claudeKey?.configured ?? _hasApiKey);

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
        {/* Chat header with model selector */}
        <div className="flex items-center justify-between border-b border-border px-4 py-2">
          <ChatModelSelector />
        </div>

        {!hasRequiredKey ? (
          <NoKeyState selectedModel={selectedModel} />
        ) : messages.length === 0 ? (
          <>
            <EmptyState />
            {/* Input still visible in empty state */}
            <ChatInput
              onSend={sendMessage}
              onCancel={cancelStream}
              isStreaming={isStreaming}
              selectedModel={selectedModel}
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
              selectedModel={selectedModel}
            />
          </>
        )}
      </div>
    </div>
  );
}
