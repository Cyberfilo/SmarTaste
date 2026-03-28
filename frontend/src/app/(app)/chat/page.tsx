"use client";

/**
 * Chat page -- AI conversation interface.
 *
 * Full-height page rendering the ChatInterface component.
 * Key status checking is handled internally by ChatInterface
 * based on the currently selected model (Claude or OpenAI).
 */

import { ChatInterface } from "@/components/chat/chat-interface";

export default function ChatPage() {
  return (
    <div className="h-[calc(100vh-3.5rem)] lg:h-[calc(100vh-0px)]">
      <ChatInterface />
    </div>
  );
}
