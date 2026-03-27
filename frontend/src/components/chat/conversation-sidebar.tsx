"use client";

/**
 * Conversation sidebar showing previous conversations.
 *
 * Desktop (lg:): fixed left sidebar (w-72) inside the chat area.
 * Mobile: drawer triggered by hamburger icon, overlays the chat.
 */

import { useState } from "react";
import {
  MessageSquarePlus,
  Trash2,
  Menu,
  X,
  MessageCircle,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  useConversations,
  useDeleteConversation,
} from "@/hooks/use-conversations";

// ── Relative time helper ───────────────────────────────

function relativeTime(dateStr: string): string {
  const now = Date.now();
  const then = new Date(dateStr).getTime();
  const diff = now - then;

  const minutes = Math.floor(diff / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;

  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;

  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d ago`;

  return new Date(dateStr).toLocaleDateString();
}

// ── Types ──────────────────────────────────────────────

interface ConversationSidebarProps {
  activeConversationId: string | null;
  onSelectConversation: (id: string) => void;
  onNewConversation: () => void;
}

// ── Sidebar content (shared between drawer and panel) ──

function SidebarContent({
  activeConversationId,
  onSelectConversation,
  onNewConversation,
  onClose,
}: ConversationSidebarProps & { onClose?: () => void }) {
  const { data: conversations, isLoading } = useConversations();
  const deleteMutation = useDeleteConversation();

  function handleDelete(e: React.MouseEvent, id: string) {
    e.stopPropagation();
    deleteMutation.mutate(id);
  }

  return (
    <div className="flex h-full flex-col">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-border px-4 py-3">
        <h2 className="text-sm font-semibold text-foreground">Conversations</h2>
        <div className="flex items-center gap-1">
          <Button
            size="icon"
            variant="ghost"
            onClick={onNewConversation}
            className="h-8 w-8 text-muted-foreground hover:text-foreground"
            aria-label="New conversation"
          >
            <MessageSquarePlus className="h-4 w-4" />
          </Button>
          {onClose && (
            <Button
              size="icon"
              variant="ghost"
              onClick={onClose}
              className="h-8 w-8 text-muted-foreground hover:text-foreground lg:hidden"
              aria-label="Close sidebar"
            >
              <X className="h-4 w-4" />
            </Button>
          )}
        </div>
      </div>

      {/* Conversation list */}
      <div className="flex-1 overflow-y-auto">
        {isLoading ? (
          <div className="flex items-center justify-center py-8">
            <div className="h-5 w-5 animate-spin rounded-full border-2 border-emerald-500 border-t-transparent" />
          </div>
        ) : !conversations || conversations.length === 0 ? (
          <div className="px-4 py-8 text-center text-xs text-muted-foreground">
            No conversations yet.
            <br />
            Start chatting to create one.
          </div>
        ) : (
          <div className="space-y-0.5 p-2">
            {conversations.map((conv) => {
              const isActive = conv.id === activeConversationId;
              return (
                <button
                  key={conv.id}
                  onClick={() => onSelectConversation(conv.id)}
                  className={`group flex w-full items-start gap-3 rounded-lg px-3 py-2.5 text-left transition-colors ${
                    isActive
                      ? "bg-emerald-600/10 text-foreground"
                      : "text-muted-foreground hover:bg-zinc-800/50 hover:text-foreground"
                  }`}
                >
                  <MessageCircle
                    className={`mt-0.5 h-4 w-4 shrink-0 ${
                      isActive ? "text-emerald-500" : ""
                    }`}
                  />
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium">
                      {conv.title || "Untitled"}
                    </p>
                    <div className="mt-0.5 flex items-center gap-2 text-xs text-muted-foreground">
                      <span>
                        {conv.message_count}{" "}
                        {conv.message_count === 1 ? "message" : "messages"}
                      </span>
                      <span className="text-zinc-600">-</span>
                      <span>{relativeTime(conv.updated_at)}</span>
                    </div>
                  </div>
                  <Button
                    size="icon"
                    variant="ghost"
                    onClick={(e) => handleDelete(e, conv.id)}
                    className="h-7 w-7 shrink-0 text-muted-foreground opacity-0 transition-opacity hover:text-red-400 group-hover:opacity-100"
                    aria-label="Delete conversation"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                </button>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

// ── Main component with responsive behavior ────────────

export function ConversationSidebar(props: ConversationSidebarProps) {
  const [drawerOpen, setDrawerOpen] = useState(false);

  return (
    <>
      {/* Mobile trigger button */}
      <Button
        size="icon"
        variant="ghost"
        onClick={() => setDrawerOpen(true)}
        className="absolute left-3 top-3 z-10 h-9 w-9 text-muted-foreground hover:text-foreground lg:hidden"
        aria-label="Open conversations"
      >
        <Menu className="h-5 w-5" />
      </Button>

      {/* Mobile drawer overlay */}
      {drawerOpen && (
        <>
          <div
            className="fixed inset-0 z-40 bg-black/60 lg:hidden"
            onClick={() => setDrawerOpen(false)}
          />
          <div className="fixed inset-y-0 left-0 z-50 w-80 bg-card shadow-xl lg:hidden">
            <SidebarContent
              {...props}
              onSelectConversation={(id) => {
                props.onSelectConversation(id);
                setDrawerOpen(false);
              }}
              onNewConversation={() => {
                props.onNewConversation();
                setDrawerOpen(false);
              }}
              onClose={() => setDrawerOpen(false)}
            />
          </div>
        </>
      )}

      {/* Desktop sidebar */}
      <div className="hidden lg:block lg:w-72 lg:shrink-0 lg:border-r lg:border-border">
        <SidebarContent {...props} />
      </div>
    </>
  );
}
