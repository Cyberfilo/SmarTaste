"use client";

/**
 * Chat page -- Claude AI conversation interface.
 *
 * Full-height page rendering the ChatInterface component.
 * Checks Claude API key status on mount to show appropriate state.
 */

import { useEffect, useState } from "react";
import { ChatInterface } from "@/components/chat/chat-interface";
import { apiFetch } from "@/lib/api";

interface KeyStatusResponse {
  configured: boolean;
}

export default function ChatPage() {
  const [hasApiKey, setHasApiKey] = useState<boolean | null>(null);

  useEffect(() => {
    async function checkKeyStatus() {
      try {
        const data = await apiFetch<KeyStatusResponse>(
          "/api/claude/key/status",
        );
        setHasApiKey(data.configured);
      } catch {
        // If the status endpoint fails, assume key is configured
        // and let the chat endpoint handle the error
        setHasApiKey(true);
      }
    }
    checkKeyStatus();
  }, []);

  // Show loading while checking key status
  if (hasApiKey === null) {
    return (
      <div className="flex h-[calc(100vh-3.5rem)] items-center justify-center lg:h-[calc(100vh-0px)]">
        <div className="h-6 w-6 animate-spin rounded-full border-2 border-emerald-500 border-t-transparent" />
      </div>
    );
  }

  return (
    <div className="h-[calc(100vh-3.5rem)] lg:h-[calc(100vh-0px)]">
      <ChatInterface hasApiKey={hasApiKey} />
    </div>
  );
}
