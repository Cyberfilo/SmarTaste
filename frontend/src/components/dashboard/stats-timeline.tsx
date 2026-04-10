"use client";

/**
 * Listening timeline — chronological view of songs with dates and artwork.
 * Shows when songs were added (or released, as fallback) with grouping by month.
 */

import { useTimeline } from "@/hooks/use-stats";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { CalendarDays, Music } from "lucide-react";

import type { TimelineEntry } from "@/types/api";

function groupByMonth(
  items: TimelineEntry[]
): Map<string, TimelineEntry[]> {
  const groups = new Map<string, TimelineEntry[]>();
  for (const item of items) {
    const key = item.date
      ? new Date(item.date).toLocaleDateString("en-US", {
          year: "numeric",
          month: "long",
        })
      : "Unknown Date";
    const group = groups.get(key) ?? [];
    group.push(item);
    groups.set(key, group);
  }
  return groups;
}

export function StatsTimeline() {
  const { data, isLoading } = useTimeline(100);

  if (isLoading) {
    return (
      <Card>
        <CardContent className="space-y-3 pt-4">
          <div className="flex items-center gap-2 text-muted-foreground">
            <CalendarDays className="h-4 w-4" />
            <span className="text-xs font-medium uppercase tracking-wider">
              Timeline
            </span>
          </div>
          {Array.from({ length: 5 }).map((_, i) => (
            <div key={i} className="flex items-center gap-3">
              <Skeleton className="h-10 w-10 shrink-0 rounded" />
              <div className="flex-1 space-y-1.5">
                <Skeleton className="h-3.5 w-3/4" />
                <Skeleton className="h-3 w-1/2" />
              </div>
            </div>
          ))}
        </CardContent>
      </Card>
    );
  }

  if (!data || data.items.length === 0) {
    return (
      <Card>
        <CardContent className="flex flex-col items-center gap-2 py-10 text-center">
          <Music className="h-8 w-8 text-muted-foreground/40" />
          <p className="text-sm text-muted-foreground">
            No timeline data available yet.
          </p>
        </CardContent>
      </Card>
    );
  }

  const grouped = groupByMonth(data.items);

  return (
    <Card>
      <CardContent className="pt-4">
        <div className="mb-4 flex items-center gap-2 text-muted-foreground">
          <CalendarDays className="h-4 w-4" />
          <span className="text-xs font-medium uppercase tracking-wider">
            Listening Timeline
          </span>
          <span className="ml-auto text-xs tabular-nums">
            {data.total} songs
          </span>
        </div>

        <div className="space-y-6">
          {Array.from(grouped.entries()).map(([month, songs]) => (
            <div key={month}>
              {/* Month header */}
              <div className="sticky top-0 z-10 mb-2 bg-card pb-1">
                <h3 className="text-xs font-semibold uppercase tracking-wider text-purple-400">
                  {month}
                </h3>
              </div>

              {/* Songs in this month */}
              <div className="space-y-1.5">
                {songs.map((song) => (
                  <div
                    key={`${song.catalog_id}`}
                    className="flex items-center gap-3 rounded-lg px-2 py-1.5 transition-colors hover:bg-muted/50"
                  >
                    {/* Artwork placeholder or image */}
                    {song.artwork_url ? (
                      <img
                        src={String(song.artwork_url)}
                        alt={`${String(song.name)} by ${String(song.artist_name)}`}
                        className="h-10 w-10 shrink-0 rounded object-cover"
                        loading="lazy"
                      />
                    ) : (
                      <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded bg-muted">
                        <Music className="h-4 w-4 text-muted-foreground" />
                      </div>
                    )}

                    {/* Song info */}
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-medium">
                        {String(song.name)}
                      </p>
                      <p className="truncate text-xs text-muted-foreground">
                        {String(song.artist_name)}
                        {song.album_name ? ` · ${String(song.album_name)}` : ""}
                      </p>
                    </div>

                    {/* Date + type badge */}
                    <div className="shrink-0 text-right">
                      {song.date && (
                        <p className="text-xs tabular-nums text-muted-foreground">
                          {new Date(String(song.date)).toLocaleDateString(
                            "en-US",
                            { month: "short", day: "numeric" }
                          )}
                        </p>
                      )}
                      <span
                        className={`text-[10px] uppercase tracking-wider ${
                          song.date_type === "added"
                            ? "text-purple-400"
                            : song.date_type === "release"
                              ? "text-blue-400"
                              : "text-muted-foreground"
                        }`}
                      >
                        {String(song.date_type)}
                      </span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}
