/**
 * SSE client for POST-based streaming chat responses.
 *
 * Standard EventSource only supports GET requests. Since the backend uses
 * POST /api/chat/message returning text/event-stream, we use fetch() with
 * ReadableStream to parse SSE events from a POST response body.
 */

import { API_URL } from "@/lib/api";

// ── Types ──────────────────────────────────────────────

export interface StreamChatParams {
  conversationId?: string;
  message: string;
}

export interface StreamChatCallbacks {
  onTextDelta: (text: string) => void;
  onToolUse: (data: { tool: string; input: Record<string, unknown> }) => void;
  onToolResult: (data: { tool: string; output: string }) => void;
  onComplete: (data: { conversation_id: string; message_id?: string }) => void;
  onError: (data: { error: string; code: string }) => void;
}

// ── Abort controller for cancelling in-flight streams ──

let abortController: AbortController | null = null;

/**
 * Abort the current in-flight chat stream, if any.
 */
export function abortChat(): void {
  if (abortController) {
    abortController.abort();
    abortController = null;
  }
}

// ── SSE line parser ────────────────────────────────────

function getCsrfToken(): string | null {
  if (typeof document === "undefined") return null;
  const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]*)/);
  return match ? decodeURIComponent(match[1]) : null;
}

/**
 * Parse raw SSE text into events. SSE format:
 *   event: <type>\n
 *   data: <json>\n
 *   \n  (blank line = event boundary)
 */
function parseSSEEvents(
  raw: string,
  callbacks: StreamChatCallbacks,
): void {
  const lines = raw.split("\n");
  let currentEvent = "";
  let currentData = "";

  for (const line of lines) {
    if (line.startsWith("event:")) {
      currentEvent = line.slice(6).trim();
    } else if (line.startsWith("data:")) {
      currentData += line.slice(5).trim();
    } else if (line === "" && currentEvent && currentData) {
      // Event boundary -- dispatch
      try {
        const parsed = JSON.parse(currentData);
        switch (currentEvent) {
          case "text":
          case "text_delta":
            callbacks.onTextDelta(parsed.text ?? parsed.delta ?? "");
            break;
          case "tool_start":
            callbacks.onToolUse({ tool: parsed.tool, input: parsed.input ?? {} });
            break;
          case "tool_end":
          case "tool_result":
            callbacks.onToolResult({ tool: parsed.tool, output: parsed.result ?? parsed.output ?? "" });
            break;
          case "conversation_id":
            callbacks.onComplete({ conversation_id: parsed.id ?? parsed.conversation_id });
            break;
          case "message_complete":
          case "done":
            if (parsed.conversation_id || parsed.id) {
              callbacks.onComplete({ conversation_id: parsed.conversation_id ?? parsed.id });
            }
            break;
          case "error":
            callbacks.onError({ error: parsed.message ?? parsed.error ?? "Unknown error", code: parsed.code ?? "internal" });
            break;
        }
      } catch {
        // Malformed JSON -- skip this event
      }
      currentEvent = "";
      currentData = "";
    }
  }
}

// ── Main streaming function ────────────────────────────

/**
 * Stream a chat message via POST /api/chat/message.
 *
 * Sends the message, reads the SSE response stream, and routes
 * parsed events to the provided callbacks.
 */
export async function streamChat(
  params: StreamChatParams,
  callbacks: StreamChatCallbacks,
): Promise<void> {
  // Cancel any existing stream
  abortChat();

  abortController = new AbortController();

  const csrf = getCsrfToken();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  if (csrf) {
    headers["X-CSRFToken"] = csrf;
  }

  const body: Record<string, unknown> = { message: params.message };
  if (params.conversationId) {
    body.conversation_id = params.conversationId;
  }

  let response: Response;
  try {
    response = await fetch(`${API_URL}/api/chat/message`, {
      method: "POST",
      headers,
      body: JSON.stringify(body),
      credentials: "include",
      signal: abortController.signal,
    });
  } catch (err: unknown) {
    if (err instanceof DOMException && err.name === "AbortError") {
      return; // User cancelled -- not an error
    }
    callbacks.onError({
      error: "Network error. Please check your connection.",
      code: "internal",
    });
    return;
  }

  // Handle HTTP-level errors
  if (!response.ok) {
    const code = response.status;
    if (code === 401) {
      // Session expired -- redirect to login
      if (typeof window !== "undefined") {
        window.location.href = "/login";
      }
      return;
    }
    if (code === 403) {
      callbacks.onError({
        error: "CSRF token error. Please refresh the page.",
        code: "internal",
      });
      return;
    }
    if (code === 402 || code === 429) {
      callbacks.onError({
        error: "Rate limit reached. Please wait a moment.",
        code: "rate_limited",
      });
      return;
    }
    // Other server errors
    let detail = "Something went wrong. Please try again.";
    try {
      const errBody = await response.json();
      detail = errBody.detail || errBody.error || detail;
    } catch {
      // Non-JSON error body
    }
    callbacks.onError({ error: detail, code: "internal" });
    return;
  }

  // Read the SSE stream
  if (!response.body) {
    callbacks.onError({
      error: "No response stream received.",
      code: "internal",
    });
    return;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });

      // Process complete events (delimited by double newlines)
      const parts = buffer.split("\n\n");
      // Keep the last part as buffer (may be incomplete)
      buffer = parts.pop() ?? "";

      for (const part of parts) {
        if (part.trim()) {
          parseSSEEvents(part + "\n\n", callbacks);
        }
      }
    }

    // Process any remaining buffer
    if (buffer.trim()) {
      parseSSEEvents(buffer + "\n\n", callbacks);
    }
  } catch (err: unknown) {
    if (err instanceof DOMException && err.name === "AbortError") {
      return; // User cancelled
    }
    callbacks.onError({
      error: "Stream interrupted. Please try again.",
      code: "internal",
    });
  } finally {
    abortController = null;
  }
}
