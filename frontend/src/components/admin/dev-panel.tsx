"use client";

/**
 * Admin dev panel — live logs + enrichment progress.
 * Slides in from the bottom when dev view is active.
 * Only renders for users with is_admin=true.
 */

import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api";

interface LogEntry {
  timestamp: string;
  level: string;
  logger: string;
  message: string;
}

interface UserProgress {
  user_id: string;
  email: string;
  display_name: string;
  total_songs: number;
  enriched_songs: number;
  percentage: number;
}

interface SystemStatus {
  version: string;
  users: number;
  total_songs: number;
  total_enriched: number;
  enrichment_pct: number;
  soundstat_configured: boolean;
}

export function DevPanel({ isOpen }: { isOpen: boolean }) {
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [tab, setTab] = useState<"logs" | "progress" | "status">("logs");
  const logEndRef = useRef<HTMLDivElement>(null);
  const eventSourceRef = useRef<EventSource | null>(null);

  // Connect to SSE log stream
  useEffect(() => {
    if (!isOpen) {
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
        eventSourceRef.current = null;
      }
      return;
    }

    // Use fetch-based SSE (same pattern as chat)
    const controller = new AbortController();
    let buffer = "";

    fetch("/api/admin/logs/stream", {
      signal: controller.signal,
      credentials: "include",
    })
      .then((resp) => {
        if (!resp.ok || !resp.body) return;
        const reader = resp.body.getReader();
        const decoder = new TextDecoder();

        function read() {
          reader.read().then(({ done, value }) => {
            if (done) return;
            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split("\n");
            buffer = lines.pop() || "";

            for (const line of lines) {
              if (line.startsWith("data: ")) {
                try {
                  const entry = JSON.parse(line.slice(6));
                  setLogs((prev) => [...prev.slice(-300), entry]);
                } catch {
                  // ignore
                }
              }
            }
            read();
          });
        }
        read();
      })
      .catch(() => {
        // Connection lost — silent
      });

    return () => {
      controller.abort();
    };
  }, [isOpen]);

  // Auto-scroll logs
  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [logs]);

  // Enrichment progress query — always runs when panel is open (for counter)
  const { data: progressData } = useQuery<{ users: UserProgress[] }>({
    queryKey: ["admin", "progress"],
    queryFn: () => apiFetch("/api/admin/progress"),
    enabled: isOpen,
    refetchInterval: 3000,
  });

  // System status query
  const { data: statusData } = useQuery<SystemStatus>({
    queryKey: ["admin", "status"],
    queryFn: () => apiFetch("/api/admin/status"),
    enabled: isOpen && tab === "status",
    refetchInterval: 10000,
  });

  if (!isOpen) return null;

  const levelColor: Record<string, string> = {
    ERROR: "text-red-400",
    WARNING: "text-amber-400",
    INFO: "text-blue-400",
    DEBUG: "text-gray-500",
  };

  return (
    <div className="fixed inset-x-0 bottom-0 z-50 flex max-h-[40vh] flex-col border-t border-purple-500/30 bg-[#0D0B1A]/95 backdrop-blur">
      {/* Tab bar */}
      <div className="flex shrink-0 items-center gap-1 border-b border-border px-3 py-1">
        {(["logs", "progress", "status"] as const).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`rounded px-2.5 py-1 text-xs font-medium transition-colors ${
              tab === t
                ? "bg-purple-500/20 text-purple-300"
                : "text-muted-foreground hover:text-foreground"
            }`}
          >
            {t === "logs" ? "Live Logs" : t === "progress" ? "Enrichment" : "System"}
          </button>
        ))}
        {/* Live enrichment counter */}
        {progressData && progressData.users.length > 0 && (
          <span className="ml-2 rounded bg-purple-500/15 px-2 py-0.5 text-[10px] font-mono font-medium text-purple-300">
            {progressData.users.reduce((a, u) => a + u.enriched_songs, 0)}
            /
            {progressData.users.reduce((a, u) => a + u.total_songs, 0)}
            {" features"}
          </span>
        )}
        <span className="ml-auto text-[10px] text-muted-foreground">
          DEV PANEL
        </span>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-2 font-mono text-xs">
        {tab === "logs" && (
          <div className="space-y-0.5">
            {logs.length === 0 && (
              <p className="text-muted-foreground">Waiting for logs...</p>
            )}
            {logs.map((entry, i) => (
              <div key={i} className="flex gap-2">
                <span className="shrink-0 text-muted-foreground/50">
                  {entry.timestamp.split("T")[1]?.split(".")[0] || ""}
                </span>
                <span
                  className={`shrink-0 w-12 ${levelColor[entry.level] || "text-gray-400"}`}
                >
                  {entry.level}
                </span>
                <span className="shrink-0 text-purple-400/60">
                  {entry.logger.replace("musicmind.", "")}
                </span>
                <span className="text-foreground/80">{entry.message}</span>
              </div>
            ))}
            <div ref={logEndRef} />
          </div>
        )}

        {tab === "progress" && (
          <div className="space-y-3 p-1">
            {progressData?.users.map((u) => (
              <div key={u.user_id}>
                <div className="mb-1 flex items-center justify-between">
                  <span className="text-foreground/80">{u.display_name || u.email}</span>
                  <span className="text-muted-foreground">
                    {u.enriched_songs}/{u.total_songs} ({u.percentage}%)
                  </span>
                </div>
                <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
                  <div
                    className="h-full rounded-full bg-purple-500 transition-all duration-500"
                    style={{ width: `${u.percentage}%` }}
                  />
                </div>
              </div>
            ))}
            {(!progressData || progressData.users.length === 0) && (
              <p className="text-muted-foreground">No enrichment data yet.</p>
            )}
          </div>
        )}

        {tab === "status" && statusData && (
          <div className="grid grid-cols-2 gap-3 p-1 sm:grid-cols-3">
            <div>
              <p className="text-muted-foreground">Version</p>
              <p className="text-lg font-semibold">{statusData.version}</p>
            </div>
            <div>
              <p className="text-muted-foreground">Users</p>
              <p className="text-lg font-semibold">{statusData.users}</p>
            </div>
            <div>
              <p className="text-muted-foreground">Total Songs</p>
              <p className="text-lg font-semibold">{statusData.total_songs}</p>
            </div>
            <div>
              <p className="text-muted-foreground">Enriched</p>
              <p className="text-lg font-semibold">{statusData.total_enriched}</p>
            </div>
            <div>
              <p className="text-muted-foreground">Enrichment %</p>
              <p className="text-lg font-semibold">{statusData.enrichment_pct}%</p>
            </div>
            <div>
              <p className="text-muted-foreground">SoundStat</p>
              <p className="text-lg font-semibold">
                {statusData.soundstat_configured ? "Active" : "Not set"}
              </p>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
