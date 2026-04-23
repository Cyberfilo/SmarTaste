"use client";

/**
 * Chat page -- AI conversation interface.
 *
 * Full-height page rendering the ChatInterface component.
 * Always routes through the operator-provided OpenAI key
 * (MUSICMIND_OPENAI_API_KEY) on the backend.
 */

import { ChatInterface } from "@/components/chat/chat-interface";

export default function ChatPage() {
  return (
    <div className="h-[calc(100vh-3.5rem)] lg:h-[calc(100vh-0px)]">
      <ChatInterface />
    </div>
  );
}
