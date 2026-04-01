"use client";

import { useState, useMemo } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  ChevronLeft,
  ChevronRight,
  Check,
  Disc3,
  Mic2,
  ListMusic,
  Loader2,
  X,
} from "lucide-react";
import {
  useCalibrationAlbums,
  useCalibrationArtists,
  useSaveCalibration,
  type CalibrationAlbum,
  type CalibrationArtist,
  type CalibrationItem,
} from "@/hooks/use-calibration";
import { usePlaylists, usePlaylistTracks } from "@/hooks/use-playlists";
import type { ServicePlaylist, PlaylistTrack } from "@/types/api";

const STEPS = [
  { label: "Albums", icon: Disc3, description: "Pick albums you listen to on repeat" },
  { label: "Artists", icon: Mic2, description: "Confirm which artists you actually listen to" },
  { label: "Songs", icon: ListMusic, description: "Pick your favorite songs from playlists" },
] as const;

const MAX_ALBUMS = 3;

export function CalibrationWizard() {
  const router = useRouter();
  const [step, setStep] = useState(0);

  // Step 1: Albums
  const [selectedAlbums, setSelectedAlbums] = useState<Set<string>>(new Set());
  const [albumNames, setAlbumNames] = useState<Map<string, string>>(new Map());

  // Step 2: Artists
  const [rejectedArtists, setRejectedArtists] = useState<Set<string>>(new Set());

  // Step 3: Playlist songs
  const [selectedPlaylist, setSelectedPlaylist] = useState<string | null>(null);
  const [selectedPlaylistService, setSelectedPlaylistService] = useState<string | null>(null);
  const [selectedSongs, setSelectedSongs] = useState<Set<string>>(new Set());
  const [songNames, setSongNames] = useState<Map<string, string>>(new Map());

  // Data queries
  const { data: albumsData, isLoading: albumsLoading } = useCalibrationAlbums();
  const { data: artistsData, isLoading: artistsLoading } = useCalibrationArtists();
  const { data: playlistsData, isLoading: playlistsLoading } = usePlaylists();
  const { data: tracksData, isLoading: tracksLoading } = usePlaylistTracks(
    selectedPlaylist,
    selectedPlaylistService
  );

  const saveCalibration = useSaveCalibration();

  // ── Album Selection ────────────────────────────────────

  function toggleAlbum(album: CalibrationAlbum) {
    setSelectedAlbums((prev) => {
      const next = new Set(prev);
      if (next.has(album.album_id)) {
        next.delete(album.album_id);
      } else if (next.size < MAX_ALBUMS) {
        next.add(album.album_id);
      }
      return next;
    });
    setAlbumNames((prev) => {
      const next = new Map(prev);
      next.set(album.album_id, album.name);
      return next;
    });
  }

  // ── Artist Confirm/Reject ──────────────────────────────

  function toggleArtistReject(artist: CalibrationArtist) {
    setRejectedArtists((prev) => {
      const next = new Set(prev);
      if (next.has(artist.name)) {
        next.delete(artist.name);
      } else {
        next.add(artist.name);
      }
      return next;
    });
  }

  // ── Song Selection ─────────────────────────────────────

  function toggleSong(track: PlaylistTrack) {
    setSelectedSongs((prev) => {
      const next = new Set(prev);
      if (next.has(track.catalog_id)) {
        next.delete(track.catalog_id);
      } else {
        next.add(track.catalog_id);
      }
      return next;
    });
    setSongNames((prev) => {
      const next = new Map(prev);
      next.set(track.catalog_id, track.name);
      return next;
    });
  }

  // ── Save ───────────────────────────────────────────────

  function buildCalibrationItems(): CalibrationItem[] {
    const items: CalibrationItem[] = [];

    // Albums: 5x weight
    for (const albumId of selectedAlbums) {
      items.push({
        calibration_type: "album",
        item_id: albumId,
        item_name: albumNames.get(albumId) || "",
        weight: 5.0,
      });
    }

    // Confirmed artists (not rejected, from top artists list)
    const artists = artistsData?.artists || [];
    for (const artist of artists) {
      if (rejectedArtists.has(artist.name)) {
        items.push({
          calibration_type: "artist_reject",
          item_id: artist.name.toLowerCase(),
          item_name: artist.name,
          weight: 0.1,
        });
      }
    }

    // Playlist songs: 3x weight
    for (const songId of selectedSongs) {
      items.push({
        calibration_type: "playlist_song",
        item_id: songId,
        item_name: songNames.get(songId) || "",
        weight: 3.0,
      });
    }

    return items;
  }

  async function handleFinish() {
    const items = buildCalibrationItems();

    if (items.length === 0) {
      // Allow skipping — no selections is valid
      router.push("/dashboard");
      return;
    }

    saveCalibration.mutate(items, {
      onSuccess: () => {
        toast.success("Taste calibration saved!");
        router.push("/dashboard");
      },
      onError: (err) => {
        toast.error(err.message || "Failed to save calibration");
      },
    });
  }

  // ── Navigation ─────────────────────────────────────────

  const canGoNext = step < STEPS.length - 1;
  const canGoBack = step > 0;
  const isLastStep = step === STEPS.length - 1;

  // ── Step Progress Bar ──────────────────────────────────

  const progressBar = (
    <div className="flex items-center gap-2 mb-6">
      {STEPS.map((s, i) => (
        <div key={s.label} className="flex items-center gap-2 flex-1">
          <button
            onClick={() => setStep(i)}
            className={`flex items-center gap-2 rounded-full px-3 py-1.5 text-xs font-medium transition-colors ${
              i === step
                ? "bg-purple-500/20 text-purple-300"
                : i < step
                  ? "bg-purple-500/10 text-purple-400"
                  : "bg-muted text-muted-foreground"
            }`}
          >
            {i < step ? (
              <Check className="h-3 w-3" />
            ) : (
              <s.icon className="h-3 w-3" />
            )}
            <span className="hidden sm:inline">{s.label}</span>
            <span className="sm:hidden">{i + 1}</span>
          </button>
          {i < STEPS.length - 1 && (
            <div
              className={`h-px flex-1 ${
                i < step ? "bg-purple-500/40" : "bg-border"
              }`}
            />
          )}
        </div>
      ))}
    </div>
  );

  // ── Step 1: Albums Grid ────────────────────────────────

  const albumsStep = (
    <div>
      <p className="text-sm text-muted-foreground mb-4">
        Select up to {MAX_ALBUMS} albums you listen to on repeat. These will be
        weighted heavily in your taste profile.
      </p>
      {albumsLoading ? (
        <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-3">
          {Array.from({ length: 8 }).map((_, i) => (
            <Skeleton key={i} className="aspect-square rounded-lg" />
          ))}
        </div>
      ) : !albumsData?.albums.length ? (
        <p className="text-sm text-muted-foreground text-center py-8">
          No albums found in your library. You can skip this step.
        </p>
      ) : (
        <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-3">
          {albumsData.albums.map((album) => {
            const isSelected = selectedAlbums.has(album.album_id);
            const isDisabled = !isSelected && selectedAlbums.size >= MAX_ALBUMS;

            return (
              <button
                key={album.album_id}
                onClick={() => toggleAlbum(album)}
                disabled={isDisabled}
                className={`group relative aspect-square overflow-hidden rounded-lg border-2 transition-all ${
                  isSelected
                    ? "border-purple-500 ring-2 ring-purple-500/30"
                    : isDisabled
                      ? "border-border opacity-40 cursor-not-allowed"
                      : "border-border hover:border-purple-500/50"
                }`}
              >
                {album.artwork_url ? (
                  <img
                    src={album.artwork_url}
                    alt={album.name}
                    className="h-full w-full object-cover"
                  />
                ) : (
                  <div className="flex h-full w-full items-center justify-center bg-muted">
                    <Disc3 className="h-8 w-8 text-muted-foreground" />
                  </div>
                )}

                {/* Overlay with album info */}
                <div className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/80 to-transparent px-2 pb-2 pt-6">
                  <p className="text-xs font-medium text-white truncate">
                    {album.name}
                  </p>
                  <p className="text-[10px] text-white/70 truncate">
                    {album.artist_name}
                  </p>
                </div>

                {/* Selected checkmark */}
                {isSelected && (
                  <div className="absolute top-2 right-2 flex h-6 w-6 items-center justify-center rounded-full bg-purple-500">
                    <Check className="h-3.5 w-3.5 text-white" />
                  </div>
                )}
              </button>
            );
          })}
        </div>
      )}
      <p className="text-xs text-muted-foreground mt-3 text-center">
        {selectedAlbums.size}/{MAX_ALBUMS} selected
      </p>
    </div>
  );

  // ── Step 2: Artist Confirm/Reject ──────────────────────

  const artistsStep = (
    <div>
      <p className="text-sm text-muted-foreground mb-4">
        We detected these artists from your library. Reject any you don&apos;t
        actually listen to — they&apos;ll be demoted in your profile.
      </p>
      {artistsLoading ? (
        <div className="space-y-2">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-12 rounded-lg" />
          ))}
        </div>
      ) : !artistsData?.artists.length ? (
        <p className="text-sm text-muted-foreground text-center py-8">
          No artists found yet. Your profile may still be building — you can
          skip this step.
        </p>
      ) : (
        <div className="space-y-1.5">
          {artistsData.artists.map((artist) => {
            const isRejected = rejectedArtists.has(artist.name);

            return (
              <div
                key={artist.name}
                className={`flex items-center gap-3 rounded-lg px-3 py-2.5 transition-colors ${
                  isRejected
                    ? "bg-red-500/10 border border-red-500/20"
                    : "bg-card border border-border"
                }`}
              >
                <div className="flex-1 min-w-0">
                  <p
                    className={`text-sm font-medium truncate ${
                      isRejected ? "text-muted-foreground line-through" : ""
                    }`}
                  >
                    {artist.name}
                  </p>
                  <p className="text-xs text-muted-foreground">
                    {artist.song_count} songs &middot; affinity{" "}
                    {Math.round(artist.score * 100)}%
                  </p>
                </div>
                <Button
                  variant={isRejected ? "outline" : "ghost"}
                  size="sm"
                  onClick={() => toggleArtistReject(artist)}
                  className={
                    isRejected
                      ? "text-red-400 border-red-500/30 hover:bg-red-500/10"
                      : "text-muted-foreground hover:text-red-400"
                  }
                >
                  {isRejected ? (
                    <>
                      <X className="h-3.5 w-3.5 mr-1" />
                      Rejected
                    </>
                  ) : (
                    "Reject"
                  )}
                </Button>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );

  // ── Step 3: Playlist Song Picker ───────────────────────

  const songsStep = (
    <div>
      <p className="text-sm text-muted-foreground mb-4">
        Pick your favorite songs from your playlists. These get extra weight in
        your profile.
      </p>

      {/* Playlist selector */}
      {playlistsLoading ? (
        <div className="space-y-2 mb-4">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-10 rounded-lg" />
          ))}
        </div>
      ) : !playlistsData?.playlists.length ? (
        <p className="text-sm text-muted-foreground text-center py-8">
          No playlists found. You can skip this step.
        </p>
      ) : (
        <>
          <div className="flex flex-wrap gap-2 mb-4">
            {playlistsData.playlists.slice(0, 10).map((pl: ServicePlaylist) => (
              <button
                key={pl.service_playlist_id}
                onClick={() => {
                  setSelectedPlaylist(pl.service_playlist_id);
                  setSelectedPlaylistService(pl.service);
                }}
                className={`rounded-full px-3 py-1.5 text-xs font-medium transition-colors ${
                  selectedPlaylist === pl.service_playlist_id
                    ? "bg-purple-500/20 text-purple-300 border border-purple-500/30"
                    : "bg-muted text-muted-foreground border border-border hover:border-purple-500/30"
                }`}
              >
                {pl.name}
              </button>
            ))}
          </div>

          {/* Tracks from selected playlist */}
          {selectedPlaylist && (
            <>
              {tracksLoading ? (
                <div className="space-y-2">
                  {Array.from({ length: 5 }).map((_, i) => (
                    <Skeleton key={i} className="h-12 rounded-lg" />
                  ))}
                </div>
              ) : !tracksData?.items.length ? (
                <p className="text-sm text-muted-foreground text-center py-4">
                  No tracks in this playlist.
                </p>
              ) : (
                <div className="space-y-1 max-h-[40vh] overflow-y-auto pr-1">
                  {tracksData.items.map((track: PlaylistTrack) => {
                    const isSelected = selectedSongs.has(track.catalog_id);

                    return (
                      <button
                        key={track.catalog_id}
                        onClick={() => toggleSong(track)}
                        className={`flex w-full items-center gap-3 rounded-lg px-3 py-2 text-left transition-colors ${
                          isSelected
                            ? "bg-purple-500/10 border border-purple-500/30"
                            : "bg-card border border-border hover:border-purple-500/20"
                        }`}
                      >
                        {track.artwork_url ? (
                          <img
                            src={track.artwork_url}
                            alt=""
                            className="h-8 w-8 rounded object-cover"
                          />
                        ) : (
                          <div className="h-8 w-8 rounded bg-muted flex items-center justify-center">
                            <ListMusic className="h-3.5 w-3.5 text-muted-foreground" />
                          </div>
                        )}
                        <div className="flex-1 min-w-0">
                          <p className="text-sm font-medium truncate">
                            {track.name}
                          </p>
                          <p className="text-xs text-muted-foreground truncate">
                            {track.artist_name}
                          </p>
                        </div>
                        {isSelected && (
                          <Check className="h-4 w-4 shrink-0 text-purple-400" />
                        )}
                      </button>
                    );
                  })}
                </div>
              )}
            </>
          )}

          {selectedSongs.size > 0 && (
            <p className="text-xs text-muted-foreground mt-2 text-center">
              {selectedSongs.size} songs selected
            </p>
          )}
        </>
      )}
    </div>
  );

  // ── Render ─────────────────────────────────────────────

  const stepContent = [albumsStep, artistsStep, songsStep];
  const StepIcon = STEPS[step].icon;

  return (
    <div className="mx-auto max-w-2xl">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <StepIcon className="h-5 w-5 text-purple-400" />
            {STEPS[step].label}
          </CardTitle>
          <CardDescription>{STEPS[step].description}</CardDescription>
        </CardHeader>
        <CardContent>
          {progressBar}
          {stepContent[step]}

          {/* Navigation buttons */}
          <div className="flex items-center justify-between mt-6 pt-4 border-t border-border">
            <div>
              {canGoBack ? (
                <Button variant="ghost" onClick={() => setStep(step - 1)}>
                  <ChevronLeft className="h-4 w-4 mr-1" />
                  Back
                </Button>
              ) : (
                <Button
                  variant="ghost"
                  onClick={() => router.push("/dashboard")}
                  className="text-muted-foreground"
                >
                  Skip
                </Button>
              )}
            </div>
            <div>
              {isLastStep ? (
                <Button
                  onClick={handleFinish}
                  disabled={saveCalibration.isPending}
                >
                  {saveCalibration.isPending ? (
                    <Loader2 className="h-4 w-4 mr-1.5 animate-spin" />
                  ) : (
                    <Check className="h-4 w-4 mr-1.5" />
                  )}
                  Finish
                </Button>
              ) : (
                <Button onClick={() => setStep(step + 1)}>
                  Next
                  <ChevronRight className="h-4 w-4 ml-1" />
                </Button>
              )}
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
