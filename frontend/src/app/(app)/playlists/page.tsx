"use client";

/**
 * Playlists page — browse your real Apple Music / Spotify playlists.
 * Click a playlist to see its tracks. Use AI chat to get recommendations.
 */

import { useState } from "react";
import { usePlaylists, usePlaylistTracks, usePlaylistRecommendations } from "@/hooks/use-playlists";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  ListMusic,
  ArrowLeft,
  Music,
  ChevronRight,
  Sparkles,
} from "lucide-react";
import type { ServicePlaylist } from "@/types/api";

export default function PlaylistsPage() {
  const [selected, setSelected] = useState<ServicePlaylist | null>(null);

  if (selected) {
    return (
      <PlaylistDetail playlist={selected} onBack={() => setSelected(null)} />
    );
  }

  return (
    <div className="space-y-6">
      <div>
        <h1
          className="text-2xl font-bold tracking-tight sm:text-3xl"
          style={{ fontFamily: "var(--font-heading)" }}
        >
          Playlists
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Your playlists from connected services
        </p>
      </div>

      <PlaylistGrid onSelect={setSelected} />
    </div>
  );
}

function PlaylistGrid({
  onSelect,
}: {
  onSelect: (pl: ServicePlaylist) => void;
}) {
  const { data, isLoading } = usePlaylists();

  if (isLoading) {
    return (
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {Array.from({ length: 6 }).map((_, i) => (
          <Card key={i}>
            <CardContent className="flex items-center gap-3 pt-3">
              <Skeleton className="h-14 w-14 shrink-0 rounded-lg" />
              <div className="flex-1 space-y-2">
                <Skeleton className="h-4 w-3/4" />
                <Skeleton className="h-3 w-1/2" />
              </div>
            </CardContent>
          </Card>
        ))}
      </div>
    );
  }

  if (!data || data.playlists.length === 0) {
    return (
      <Card>
        <CardContent className="flex flex-col items-center gap-3 py-16 text-center">
          <ListMusic className="h-12 w-12 text-muted-foreground/40" />
          <div>
            <p className="text-lg font-medium">No playlists found</p>
            <p className="mt-1 text-sm text-muted-foreground">
              Connect a music service in Settings to see your playlists.
            </p>
          </div>
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
      {data.playlists.map((pl) => (
        <Card
          key={`${pl.service}-${pl.service_playlist_id}`}
          className="cursor-pointer transition-colors hover:bg-muted/50"
          onClick={() => onSelect(pl)}
        >
          <CardContent className="flex items-center gap-3 pt-3">
            {pl.artwork_url ? (
              <img
                src={pl.artwork_url}
                alt={`${pl.name} cover art`}
                className="h-14 w-14 shrink-0 rounded-lg object-cover"
                loading="lazy"
              />
            ) : (
              <div className="flex h-14 w-14 shrink-0 items-center justify-center rounded-lg bg-muted">
                <ListMusic className="h-6 w-6 text-muted-foreground" />
              </div>
            )}
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-semibold">{pl.name}</p>
              <p className="text-xs text-muted-foreground">
                {pl.track_count > 0
                  ? `${pl.track_count} tracks`
                  : pl.service === "apple_music"
                    ? "Apple Music"
                    : "Spotify"}
              </p>
              {pl.description && (
                <p className="mt-0.5 line-clamp-1 text-xs text-muted-foreground/70">
                  {pl.description}
                </p>
              )}
            </div>
            <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground" />
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

function PlaylistDetail({
  playlist,
  onBack,
}: {
  playlist: ServicePlaylist;
  onBack: () => void;
}) {
  const { data, isLoading } = usePlaylistTracks(
    playlist.service_playlist_id,
    playlist.service
  );
  const { data: recsData, isLoading: recsLoading } = usePlaylistRecommendations(
    playlist.service_playlist_id,
    playlist.service
  );

  return (
    <div className="space-y-4">
      {/* Header */}
      <div>
        <button
          onClick={onBack}
          className="mb-3 inline-flex items-center gap-1 text-sm text-muted-foreground transition-colors hover:text-foreground"
        >
          <ArrowLeft className="h-4 w-4" />
          Back to playlists
        </button>
        <div className="flex items-start gap-4">
          {playlist.artwork_url ? (
            <img
              src={playlist.artwork_url}
              alt={`${playlist.name} cover art`}
              className="h-20 w-20 shrink-0 rounded-xl object-cover sm:h-28 sm:w-28"
            />
          ) : (
            <div className="flex h-20 w-20 shrink-0 items-center justify-center rounded-xl bg-muted sm:h-28 sm:w-28">
              <ListMusic className="h-8 w-8 text-muted-foreground" />
            </div>
          )}
          <div className="min-w-0">
            <h1
              className="text-xl font-bold tracking-tight sm:text-2xl"
              style={{ fontFamily: "var(--font-heading)" }}
            >
              {playlist.name}
            </h1>
            {playlist.description && (
              <p className="mt-1 text-sm text-muted-foreground">
                {playlist.description}
              </p>
            )}
            <p className="mt-1 text-xs text-muted-foreground">
              {playlist.service === "apple_music" ? "Apple Music" : "Spotify"}
              {playlist.owner && ` · ${playlist.owner}`}
              {data && ` · ${data.total} tracks`}
            </p>
          </div>
        </div>
      </div>

      {/* Tracks */}
      {isLoading ? (
        <Card>
          <CardContent className="space-y-3 pt-4">
            {Array.from({ length: 8 }).map((_, i) => (
              <div key={i} className="flex items-center gap-3">
                <Skeleton className="h-3 w-5" />
                <Skeleton className="h-9 w-9 rounded" />
                <div className="flex-1 space-y-1.5">
                  <Skeleton className="h-3.5 w-3/4" />
                  <Skeleton className="h-3 w-1/2" />
                </div>
              </div>
            ))}
          </CardContent>
        </Card>
      ) : data && data.items.length > 0 ? (
        <Card>
          <CardContent className="max-h-[60vh] divide-y divide-border overflow-y-auto pt-2">
            {data.items.map((track, i) => (
              <div
                key={`${track.catalog_id}-${i}`}
                className="flex items-center gap-3 py-2 first:pt-0 last:pb-0"
              >
                <span className="w-5 text-right text-xs font-medium text-muted-foreground tabular-nums">
                  {i + 1}
                </span>

                {track.artwork_url ? (
                  <img
                    src={track.artwork_url}
                    alt={`${track.name} by ${track.artist_name}`}
                    className="h-9 w-9 shrink-0 rounded object-cover"
                    loading="lazy"
                  />
                ) : (
                  <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded bg-muted">
                    <Music className="h-3.5 w-3.5 text-muted-foreground" />
                  </div>
                )}

                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium">{track.name}</p>
                  <p className="truncate text-xs text-muted-foreground">
                    {track.artist_name}
                    {track.album_name ? ` · ${track.album_name}` : ""}
                  </p>
                </div>

                {track.genre_names.length > 0 && (
                  <span className="hidden shrink-0 rounded bg-purple-500/10 px-1.5 py-0.5 text-[10px] text-purple-400 sm:inline">
                    {track.genre_names[0]}
                  </span>
                )}
              </div>
            ))}
          </CardContent>
        </Card>
      ) : (
        <Card>
          <CardContent className="flex flex-col items-center gap-3 py-12 text-center">
            <Music className="h-10 w-10 text-muted-foreground/40" />
            <p className="text-sm text-muted-foreground">
              This playlist is empty.
            </p>
          </CardContent>
        </Card>
      )}

      {/* Per-playlist recommendations */}
      <div>
        <div className="mb-3 flex items-center gap-2">
          <Sparkles className="h-4 w-4 text-purple-400" />
          <h2 className="text-sm font-semibold">Suggested for this playlist</h2>
        </div>
        {recsLoading ? (
          <div className="grid gap-2 sm:grid-cols-2">
            {Array.from({ length: 4 }).map((_, i) => (
              <Card key={i}>
                <CardContent className="flex items-center gap-3 pt-3">
                  <Skeleton className="h-10 w-10 rounded" />
                  <div className="flex-1 space-y-1.5">
                    <Skeleton className="h-3.5 w-3/4" />
                    <Skeleton className="h-3 w-1/2" />
                  </div>
                </CardContent>
              </Card>
            ))}
          </div>
        ) : recsData && recsData.items.length > 0 ? (
          <div className="grid gap-2 sm:grid-cols-2">
            {recsData.items.map((rec) => (
              <Card key={rec.catalog_id} className="transition-colors hover:bg-muted/50">
                <CardContent className="flex items-center gap-3 pt-3">
                  {rec.artwork_url ? (
                    <img
                      src={rec.artwork_url}
                      alt={`${rec.name} by ${rec.artist_name}`}
                      className="h-10 w-10 shrink-0 rounded object-cover"
                      loading="lazy"
                    />
                  ) : (
                    <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded bg-muted">
                      <Music className="h-4 w-4 text-muted-foreground" />
                    </div>
                  )}
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium">{rec.name}</p>
                    <p className="truncate text-xs text-muted-foreground">
                      {rec.artist_name}
                    </p>
                  </div>
                  <span className="shrink-0 text-xs font-medium tabular-nums text-purple-400">
                    {Math.round(rec.score * 100)}%
                  </span>
                </CardContent>
              </Card>
            ))}
          </div>
        ) : (
          <p className="text-xs text-muted-foreground">
            No recommendations available yet.
          </p>
        )}
      </div>
    </div>
  );
}
