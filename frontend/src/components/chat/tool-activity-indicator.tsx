"use client";

/**
 * Tool activity indicator shown when Claude is executing tools.
 *
 * Displays inline in the message flow between user message and
 * assistant response. Shows human-readable tool names with a
 * subtle pulse animation.
 */

import { Loader2 } from "lucide-react";
import type { ActiveTool } from "@/hooks/use-chat";

// ── Tool name to human-readable description ────────────

function getToolLabel(toolName: string): string {
  const toolLabels: Record<string, string> = {
    taste_profile: "Analyzing your taste profile...",
    get_recommendations: "Finding recommendations...",
    score_track: "Scoring a track...",
    discover_similar: "Discovering similar music...",
    get_library_songs: "Browsing your library...",
    get_listening_history: "Reviewing your listening history...",
    get_audio_features: "Analyzing audio features...",
    search_catalog: "Searching the catalog...",
  };

  return toolLabels[toolName] ?? "Searching your library...";
}

// ── Component ──────────────────────────────────────────

interface ToolActivityIndicatorProps {
  activeTools: ActiveTool[];
}

export function ToolActivityIndicator({
  activeTools,
}: ToolActivityIndicatorProps) {
  if (activeTools.length === 0) return null;

  return (
    <div className="flex w-full justify-center px-4 py-2">
      <div className="flex items-center gap-2 rounded-full bg-zinc-800/60 px-4 py-2">
        <Loader2 className="h-3.5 w-3.5 animate-spin text-emerald-500" />
        <span className="text-xs text-muted-foreground">
          {activeTools.length === 1
            ? getToolLabel(activeTools[0].tool)
            : `Running ${activeTools.length} tools...`}
        </span>
      </div>
    </div>
  );
}
