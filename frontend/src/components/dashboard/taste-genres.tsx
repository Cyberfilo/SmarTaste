"use client";

/**
 * Top genres visualization as horizontal bars.
 * Spotify Wrapped-style ranked list with gradient bars.
 */

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useTopGenres } from "@/hooks/use-taste";
import { toast } from "sonner";
import { useEffect } from "react";

function GenreSkeleton() {
  return (
    <div className="space-y-3">
      {Array.from({ length: 8 }).map((_, i) => (
        <div key={i} className="flex items-center gap-3">
          <div className="h-4 w-6 animate-pulse rounded bg-muted" />
          <div className="flex-1">
            <div className="h-4 animate-pulse rounded bg-muted" style={{ width: `${80 - i * 8}%` }} />
          </div>
        </div>
      ))}
    </div>
  );
}

export function TasteGenres() {
  const { data, isLoading, error } = useTopGenres();

  useEffect(() => {
    if (error) {
      toast.error("Failed to load genres", { description: error.message });
    }
  }, [error]);

  if (isLoading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Top Genres</CardTitle>
        </CardHeader>
        <CardContent>
          <GenreSkeleton />
        </CardContent>
      </Card>
    );
  }

  if (!data || data.genres.length === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Top Genres</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">
            No genre data available. Listen to more music to build your profile.
          </p>
        </CardContent>
      </Card>
    );
  }

  const genres = data.genres.slice(0, 15);
  const maxWeight = genres[0]?.weight ?? 1;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Top Genres</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="space-y-2.5">
          {genres.map((entry, index) => {
            const pct = (entry.weight / maxWeight) * 100;
            return (
              <div key={entry.genre} className="group flex items-center gap-3">
                <span className="w-6 shrink-0 text-right text-xs tabular-nums text-muted-foreground">
                  {index + 1}
                </span>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center justify-between gap-2 mb-1">
                    <span className="truncate text-sm font-medium md:text-base">
                      {entry.genre}
                    </span>
                    <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
                      {Math.round(entry.weight * 100)}%
                    </span>
                  </div>
                  <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
                    <div
                      className="h-full rounded-full transition-all duration-500"
                      style={{
                        width: `${pct}%`,
                        background: `linear-gradient(90deg, var(--color-emerald-500), var(--color-emerald-600))`,
                        opacity: 1 - index * 0.04,
                      }}
                    />
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      </CardContent>
    </Card>
  );
}
