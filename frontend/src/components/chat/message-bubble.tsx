"use client";

/**
 * Chat message bubble component.
 *
 * User messages: right-aligned, emerald-600 background.
 * Assistant messages: left-aligned, zinc-800 background.
 * Tool messages: small, muted, collapsible.
 * Streaming indicator: blinking cursor on last assistant message.
 */

import { useState } from "react";
import { ChevronDown, ChevronRight, Wrench } from "lucide-react";
import type { ChatMessage } from "@/hooks/use-chat";

interface MessageBubbleProps {
  message: ChatMessage;
  isLastAssistant?: boolean;
  isStreaming?: boolean;
}

// ── Lightweight markdown rendering ─────────────────────

function renderMarkdown(text: string): React.ReactNode[] {
  const nodes: React.ReactNode[] = [];
  const parts = text.split("```");

  for (let i = 0; i < parts.length; i++) {
    if (i % 2 === 1) {
      // Code block
      const lines = parts[i].split("\n");
      // First line might be language identifier
      const lang = lines[0]?.trim();
      const code = lang ? lines.slice(1).join("\n") : parts[i];
      nodes.push(
        <pre
          key={`code-${i}`}
          className="my-2 overflow-x-auto rounded-lg bg-zinc-900/80 p-3 text-sm"
        >
          {lang && (
            <div className="mb-1 text-xs text-muted-foreground">{lang}</div>
          )}
          <code>{code}</code>
        </pre>,
      );
    } else {
      // Regular text -- apply inline formatting
      nodes.push(
        <span key={`text-${i}`}>{renderInlineMarkdown(parts[i])}</span>,
      );
    }
  }

  return nodes;
}

function renderInlineMarkdown(text: string): React.ReactNode[] {
  const nodes: React.ReactNode[] = [];
  // Split by lines first for list handling
  const lines = text.split("\n");

  for (let li = 0; li < lines.length; li++) {
    const line = lines[li];

    // Bullet lists
    if (/^\s*[-*]\s/.test(line)) {
      nodes.push(
        <div key={`li-${li}`} className="ml-4 flex gap-2">
          <span className="text-muted-foreground">-</span>
          <span>{formatInline(line.replace(/^\s*[-*]\s/, ""))}</span>
        </div>,
      );
      continue;
    }

    // Numbered lists
    if (/^\s*\d+\.\s/.test(line)) {
      const match = line.match(/^\s*(\d+)\.\s(.*)/);
      if (match) {
        nodes.push(
          <div key={`li-${li}`} className="ml-4 flex gap-2">
            <span className="text-muted-foreground">{match[1]}.</span>
            <span>{formatInline(match[2])}</span>
          </div>,
        );
        continue;
      }
    }

    // Regular line
    if (line.trim()) {
      nodes.push(
        <span key={`line-${li}`}>
          {formatInline(line)}
          {li < lines.length - 1 ? "\n" : ""}
        </span>,
      );
    } else if (li < lines.length - 1) {
      nodes.push(<br key={`br-${li}`} />);
    }
  }

  return nodes;
}

function formatInline(text: string): React.ReactNode[] {
  const nodes: React.ReactNode[] = [];
  // Bold **text**, italic *text*, inline code `text`
  const regex = /(\*\*(.+?)\*\*|\*(.+?)\*|`(.+?)`)/g;
  let lastIndex = 0;
  let match: RegExpExecArray | null;

  while ((match = regex.exec(text)) !== null) {
    // Text before match
    if (match.index > lastIndex) {
      nodes.push(text.slice(lastIndex, match.index));
    }

    if (match[2]) {
      // Bold
      nodes.push(
        <strong key={`b-${match.index}`} className="font-semibold">
          {match[2]}
        </strong>,
      );
    } else if (match[3]) {
      // Italic
      nodes.push(
        <em key={`i-${match.index}`} className="italic">
          {match[3]}
        </em>,
      );
    } else if (match[4]) {
      // Inline code
      nodes.push(
        <code
          key={`c-${match.index}`}
          className="rounded bg-zinc-800 px-1.5 py-0.5 text-sm text-emerald-400"
        >
          {match[4]}
        </code>,
      );
    }

    lastIndex = match.index + match[0].length;
  }

  // Remaining text
  if (lastIndex < text.length) {
    nodes.push(text.slice(lastIndex));
  }

  return nodes.length > 0 ? nodes : [text];
}

// ── Tool message (collapsible) ─────────────────────────

function ToolMessage({ message }: { message: ChatMessage }) {
  const [expanded, setExpanded] = useState(false);
  const toolName = message.toolResult?.tool ?? "Tool";

  return (
    <div className="mx-auto my-1 max-w-[85%] md:max-w-[70%]">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs text-muted-foreground transition-colors hover:bg-zinc-800/50 hover:text-foreground"
      >
        <Wrench className="h-3 w-3" />
        <span className="font-medium">{toolName}</span>
        {expanded ? (
          <ChevronDown className="h-3 w-3" />
        ) : (
          <ChevronRight className="h-3 w-3" />
        )}
      </button>
      {expanded && (
        <div className="mt-1 rounded-lg bg-zinc-900/50 px-3 py-2 text-xs text-muted-foreground">
          <pre className="overflow-x-auto whitespace-pre-wrap">
            {message.content.length > 500
              ? message.content.slice(0, 500) + "..."
              : message.content}
          </pre>
        </div>
      )}
    </div>
  );
}

// ── Main bubble component ──────────────────────────────

export function MessageBubble({
  message,
  isLastAssistant = false,
  isStreaming = false,
}: MessageBubbleProps) {
  // Tool messages get special rendering
  if (message.role === "tool") {
    return <ToolMessage message={message} />;
  }

  const isUser = message.role === "user";
  const showCursor = isLastAssistant && isStreaming;

  return (
    <div
      className={`flex ${isUser ? "justify-end" : "justify-start"} px-4 py-1`}
    >
      <div
        className={`
          max-w-[85%] whitespace-pre-wrap rounded-2xl px-4 py-2.5 text-sm leading-relaxed
          md:max-w-[70%]
          ${
            isUser
              ? "rounded-br-sm bg-emerald-600 text-white"
              : "rounded-bl-sm bg-zinc-800 text-foreground"
          }
        `}
      >
        {renderMarkdown(message.content)}
        {showCursor && (
          <span className="ml-0.5 inline-block h-4 w-0.5 animate-pulse bg-foreground" />
        )}
      </div>
    </div>
  );
}
