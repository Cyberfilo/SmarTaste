"use client";

/**
 * Chat input bar with auto-growing textarea and send/stop buttons.
 *
 * Fixed at the bottom of the chat interface (like iMessage/WhatsApp).
 * Enter submits, Shift+Enter adds newline. Send button shows arrow
 * icon when ready, stop icon during streaming.
 */

import { useState, useRef, useCallback, useEffect } from "react";
import { ArrowUp, Square } from "lucide-react";
import { Button } from "@/components/ui/button";

import { MODEL_OPTIONS } from "@/components/settings/model-selector";

interface ChatInputProps {
  onSend: (text: string) => void;
  onCancel: () => void;
  isStreaming: boolean;
  disabled?: boolean;
  selectedModel?: string;
}

export function ChatInput({
  onSend,
  onCancel,
  isStreaming,
  disabled = false,
  selectedModel,
}: ChatInputProps) {
  const [text, setText] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Auto-resize textarea (1-4 rows)
  const adjustHeight = useCallback(() => {
    const textarea = textareaRef.current;
    if (!textarea) return;
    textarea.style.height = "auto";
    const lineHeight = 24; // ~1.5rem
    const maxHeight = lineHeight * 4;
    textarea.style.height = `${Math.min(textarea.scrollHeight, maxHeight)}px`;
  }, []);

  useEffect(() => {
    adjustHeight();
  }, [text, adjustHeight]);

  function handleSubmit() {
    const trimmed = text.trim();
    if (!trimmed || isStreaming || disabled) return;
    onSend(trimmed);
    setText("");
    // Reset textarea height
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
    }
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  }

  const canSend = text.trim().length > 0 && !isStreaming && !disabled;

  const modelLabel = selectedModel
    ? MODEL_OPTIONS.find((m) => m.id === selectedModel)?.name
    : undefined;

  return (
    <div className="border-t border-border bg-card px-4 pb-[env(safe-area-inset-bottom,0.5rem)] pt-3">
      <div className="mx-auto flex max-w-3xl items-end gap-2">
        <textarea
          ref={textareaRef}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Ask about your music..."
          disabled={disabled}
          rows={1}
          className="flex-1 resize-none rounded-xl border border-border bg-zinc-800/50 px-4 py-2.5 text-sm text-foreground placeholder:text-muted-foreground focus:border-emerald-500/50 focus:outline-none focus:ring-1 focus:ring-emerald-500/30 disabled:opacity-50"
        />
        {isStreaming ? (
          <Button
            size="icon"
            variant="ghost"
            onClick={onCancel}
            className="h-10 w-10 shrink-0 rounded-xl bg-red-500/10 text-red-400 hover:bg-red-500/20 hover:text-red-300"
            aria-label="Stop streaming"
          >
            <Square className="h-4 w-4" />
          </Button>
        ) : (
          <Button
            size="icon"
            onClick={handleSubmit}
            disabled={!canSend}
            className="h-10 w-10 shrink-0 rounded-xl bg-emerald-600 text-white hover:bg-emerald-700 disabled:opacity-30"
            aria-label="Send message"
          >
            <ArrowUp className="h-4 w-4" />
          </Button>
        )}
      </div>
      {modelLabel && (
        <p className="mx-auto max-w-3xl mt-1.5 text-[10px] text-muted-foreground/50 text-center">
          Powered by {modelLabel}
        </p>
      )}
    </div>
  );
}
