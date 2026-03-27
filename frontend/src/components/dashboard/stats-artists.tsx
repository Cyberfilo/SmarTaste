"use client";

/**
 * Top artists ranked list for a given time period with genre badges.
 */

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useTopArtistsStats } from "@/hooks/use-stats";
import type { Period } from "@/types/api";
import { toast } from "sonner";
import { useEffect } from "react";

function ArtistsSkeleton() {
  return (
    <div className="space-y-2">
      {Array.from({ length: 10 }).map((_, i) => (
        <div key={i} className="flex items-center gap-3 px-2 py-2">
          <div className="h-4 w-6 animate-pulse rounded bg-muted" />
          <div className="flex-1 space-y-1">
            <div className="h-4 w-1/2 animate-pulse rounded bg-muted" />
            <div className="flex gap-1">
              <div className="h-4 w-16 animate-pulse rounded bg-muted" />
              <div className="h-4 w-12 animate-pulse rounded bg-muted" />
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

interface StatsArtistsProps {
  period: Period;
}

export function StatsArtists({ period }: StatsArtistsProps) {
  const { data, isLoading, error } = useTopArtistsStats(period);

  useEffect(() => {
    if (error) {
      toast.error("Failed to load top artists", {
        description: error.message,
      });
    }
  }, [error]);

  if (isLoading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Top Artists</CardTitle>
        </CardHeader>
        <CardContent>
          <ArtistsSkeleton />
        </CardContent>
      </Card>
    );
  }

  if (!data || data.items.length === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Top Artists</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">
            No artist data for this period.
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>
          Top Artists
          <span className="ml-2 text-sm font-normal text-muted-foreground">
            ({data.total})
          </span>
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div className="space-y-0.5">
          {data.items.map((artist) => (
            <div
              key={`${artist.rank}-${artist.name}`}
              className={`flex items-center gap-3 rounded-md px-2 py-2 ${
                artist.rank % 2 === 0 ? "bg-muted/30" : ""
              }`}
            >
              <span className="w-6 shrink-0 text-right text-xs tabular-nums text-muted-foreground">
                {artist.rank}
              </span>
              <div className="flex-1 min-w-0">
                <p className="truncate text-sm font-medium">{artist.name}</p>
                {artist.genres.length > 0 && (
                  <div className="mt-1 flex flex-wrap gap-1">
                    {artist.genres.slice(0, 3).map((genre) => (
                      <span
                        key={genre}
                        className="inline-block rounded-full bg-emerald-500/10 px-2 py-0.5 text-xs text-emerald-400"
                      >
                        {genre}
                      </span>
                    ))}
                    {artist.genres.length > 3 && (
                      <span className="inline-block rounded-full bg-muted px-2 py-0.5 text-xs text-muted-foreground">
                        +{artist.genres.length - 3}
                      </span>
                    )}
                  </div>
                )}
              </div>
              {artist.score != null && (
                <span className="hidden shrink-0 text-xs tabular-nums text-muted-foreground md:block">
                  {artist.score.toFixed(2)}
                </span>
              )}
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}
