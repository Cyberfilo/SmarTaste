"use client";

/**
 * Top tracks ranked list for a given time period.
 */

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useTopTracks } from "@/hooks/use-stats";
import type { Period } from "@/types/api";
import { toast } from "sonner";
import { useEffect } from "react";

function TracksSkeleton() {
  return (
    <div className="space-y-2">
      {Array.from({ length: 10 }).map((_, i) => (
        <div key={i} className="flex items-center gap-3 px-2 py-2">
          <div className="h-4 w-6 animate-pulse rounded bg-muted" />
          <div className="flex-1 space-y-1">
            <div className="h-4 w-3/4 animate-pulse rounded bg-muted" />
            <div className="h-3 w-1/2 animate-pulse rounded bg-muted" />
          </div>
        </div>
      ))}
    </div>
  );
}

interface StatsTracksProps {
  period: Period;
}

export function StatsTracks({ period }: StatsTracksProps) {
  const { data, isLoading, error } = useTopTracks(period);

  useEffect(() => {
    if (error) {
      toast.error("Failed to load top tracks", { description: error.message });
    }
  }, [error]);

  if (isLoading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Top Tracks</CardTitle>
        </CardHeader>
        <CardContent>
          <TracksSkeleton />
        </CardContent>
      </Card>
    );
  }

  if (!data || data.items.length === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Top Tracks</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">
            No track data for this period.
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>
          Top Tracks
          <span className="ml-2 text-sm font-normal text-muted-foreground">
            ({data.total})
          </span>
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div className="space-y-0.5">
          {data.items.map((track) => (
            <div
              key={`${track.rank}-${track.name}`}
              className={`flex items-center gap-3 rounded-md px-2 py-2 ${
                track.rank % 2 === 0 ? "bg-muted/30" : ""
              }`}
            >
              <span className="w-6 shrink-0 text-right text-xs tabular-nums text-muted-foreground">
                {track.rank}
              </span>
              <div className="flex-1 min-w-0">
                <p className="truncate text-sm font-medium">{track.name}</p>
                <p className="truncate text-xs text-muted-foreground">
                  {track.artist_name}
                  <span className="hidden md:inline">
                    {track.album_name ? ` \u2014 ${track.album_name}` : ""}
                  </span>
                </p>
              </div>
              {track.play_count_estimate != null && (
                <span className="shrink-0 rounded-full bg-muted px-2 py-0.5 text-xs tabular-nums text-muted-foreground">
                  {track.play_count_estimate} plays
                </span>
              )}
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}
