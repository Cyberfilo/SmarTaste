"use client";

/**
 * Top artists visualization as a ranked list with affinity score bars.
 */

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useTopArtists } from "@/hooks/use-taste";
import { toast } from "sonner";
import { useEffect } from "react";

function ArtistSkeleton() {
  return (
    <div className="space-y-3">
      {Array.from({ length: 10 }).map((_, i) => (
        <div key={i} className="flex items-center gap-3">
          <div className="h-4 w-6 animate-pulse rounded bg-muted" />
          <div className="h-4 flex-1 animate-pulse rounded bg-muted" />
          <div className="h-4 w-10 animate-pulse rounded bg-muted" />
        </div>
      ))}
    </div>
  );
}

export function TasteArtists() {
  const { data, isLoading, error } = useTopArtists();

  useEffect(() => {
    if (error) {
      toast.error("Failed to load artists", { description: error.message });
    }
  }, [error]);

  if (isLoading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Top Artists</CardTitle>
        </CardHeader>
        <CardContent>
          <ArtistSkeleton />
        </CardContent>
      </Card>
    );
  }

  if (!data || data.artists.length === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Top Artists</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">
            No artist data available yet.
          </p>
        </CardContent>
      </Card>
    );
  }

  const artists = data.artists.slice(0, 20);
  const maxScore = artists[0]?.score ?? 1;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Top Artists</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="space-y-2">
          {artists.map((artist, index) => {
            const pct = (artist.score / maxScore) * 100;
            return (
              <div
                key={artist.name}
                className="flex items-center gap-3 rounded-md px-2 py-1.5 transition-colors hover:bg-muted/50"
              >
                <span className="w-6 shrink-0 text-right text-xs tabular-nums text-muted-foreground">
                  {index + 1}
                </span>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="truncate text-sm font-medium md:text-base">
                      {artist.name}
                    </span>
                    <span className="shrink-0 rounded-full bg-muted px-2 py-0.5 text-xs tabular-nums text-muted-foreground">
                      {artist.song_count} {artist.song_count === 1 ? "song" : "songs"}
                    </span>
                  </div>
                  <div className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-muted">
                    <div
                      className="h-full rounded-full bg-emerald-500 transition-all duration-500"
                      style={{ width: `${pct}%` }}
                    />
                  </div>
                </div>
                <span className="hidden shrink-0 text-xs tabular-nums text-muted-foreground md:block">
                  {artist.score.toFixed(2)}
                </span>
              </div>
            );
          })}
        </div>
      </CardContent>
    </Card>
  );
}
