"use client";

/**
 * Top genres ranked list for a given time period.
 * Table layout on md+, stacked cards on mobile.
 */

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useTopGenresStats } from "@/hooks/use-stats";
import type { Period } from "@/types/api";
import { toast } from "sonner";
import { useEffect } from "react";

function GenresSkeleton() {
  return (
    <div className="space-y-2">
      {Array.from({ length: 8 }).map((_, i) => (
        <div key={i} className="flex items-center gap-3 px-2 py-2">
          <div className="h-4 w-6 animate-pulse rounded bg-muted" />
          <div className="h-4 flex-1 animate-pulse rounded bg-muted" />
          <div className="h-4 w-16 animate-pulse rounded bg-muted" />
        </div>
      ))}
    </div>
  );
}

interface StatsGenresProps {
  period: Period;
}

export function StatsGenres({ period }: StatsGenresProps) {
  const { data, isLoading, error } = useTopGenresStats(period);

  useEffect(() => {
    if (error) {
      toast.error("Failed to load top genres", { description: error.message });
    }
  }, [error]);

  if (isLoading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Top Genres</CardTitle>
        </CardHeader>
        <CardContent>
          <GenresSkeleton />
        </CardContent>
      </Card>
    );
  }

  if (!data || data.items.length === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Top Genres</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">
            No genre data for this period.
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>
          Top Genres
          <span className="ml-2 text-sm font-normal text-muted-foreground">
            ({data.total})
          </span>
        </CardTitle>
      </CardHeader>
      <CardContent>
        {/* Desktop: table layout */}
        <div className="hidden md:block">
          <div className="grid grid-cols-[2rem_1fr_5rem_5rem] gap-x-4 border-b border-border pb-2 text-xs font-medium uppercase tracking-wider text-muted-foreground">
            <span className="text-right">#</span>
            <span>Genre</span>
            <span className="text-right">Tracks</span>
            <span className="text-right">Artists</span>
          </div>
          <div className="divide-y divide-border/50">
            {data.items.map((genre) => (
              <div
                key={`${genre.rank}-${genre.genre}`}
                className="grid grid-cols-[2rem_1fr_5rem_5rem] gap-x-4 py-2.5 text-sm"
              >
                <span className="text-right tabular-nums text-muted-foreground">
                  {genre.rank}
                </span>
                <span className="truncate font-medium">{genre.genre}</span>
                <span className="text-right tabular-nums text-muted-foreground">
                  {genre.track_count}
                </span>
                <span className="text-right tabular-nums text-muted-foreground">
                  {genre.artist_count}
                </span>
              </div>
            ))}
          </div>
        </div>

        {/* Mobile: stacked cards */}
        <div className="space-y-1 md:hidden">
          {data.items.map((genre) => (
            <div
              key={`${genre.rank}-${genre.genre}-mobile`}
              className={`rounded-md px-3 py-2.5 ${
                genre.rank % 2 === 0 ? "bg-muted/30" : ""
              }`}
            >
              <div className="flex items-center gap-2">
                <span className="text-xs tabular-nums text-muted-foreground">
                  {genre.rank}.
                </span>
                <span className="truncate text-sm font-medium">
                  {genre.genre}
                </span>
              </div>
              <div className="mt-1 flex gap-3 text-xs text-muted-foreground">
                <span>{genre.track_count} tracks</span>
                <span>{genre.artist_count} artists</span>
              </div>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}
