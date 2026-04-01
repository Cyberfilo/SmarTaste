"use client";

import { useState, useEffect, useCallback } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  ChevronLeft,
  ChevronRight,
  Check,
  ListMusic,
  Mic2,
  Music,
  Loader2,
  X,
} from "lucide-react";
import {
  useCalibrationArtists,
  useSaveCalibration,
  type CalibrationArtist,
  type CalibrationItem,
} from "@/hooks/use-calibration";
import { usePlaylists } from "@/hooks/use-playlists";
import { apiFetch } from "@/lib/api";
import type { ServicePlaylist, PlaylistTrack, PlaylistTracksResponse } from "@/types/api";

const STEPS = [
  { label: "Playlists", icon: ListMusic, description: "Pick playlists you listen to on repeat" },
  { label: "Artists", icon: Mic2, description: "Confirm which artists you actually listen to" },
  { label: "Songs", icon: Music, description: "Pick your favorite songs from those playlists" },
] as const;

const MAX_PLAYLISTS = 5;

export function CalibrationWizard() {
  const router = useRouter();
  const [step, setStep] = useState(0);

  // Step 1: Playlists
  const [selectedPlaylists, setSelectedPlaylists] = useState<Map<string, ServicePlaylist>>(
    new Map()
  );

  // Step 2: Artists
  const [rejectedArtists, setRejectedArtists] = useState<Set<string>>(new Set());

  // Step 3: Combined songs from selected playlists
  const [combinedTracks, setCombinedTracks] = useState<PlaylistTrack[]>([]);
  const [tracksLoading, setTracksLoading] = useState(false);
  const [selectedSongs, setSelectedSongs] = useState<Set<string>>(new Set());
  const [songNames, setSongNames] = useState<Map<string, string>>(new Map());

  // Data queries
  const { data: playlistsData, isLoading: playlistsLoading } = usePlaylists();
  const { data: artistsData, isLoading: artistsLoading } = useCalibrationArtists();

  const saveCalibration = useSaveCalibration();

  // ── Playlist Selection (Step 1) ────────────────────────

  function togglePlaylist(pl: ServicePlaylist) {
    setSelectedPlaylists((prev) => {
      const next = new Map(prev);
      if (next.has(pl.service_playlist_id)) {
        next.delete(pl.service_playlist_id);
      } else if (next.size < MAX_PLAYLISTS) {
        next.set(pl.service_playlist_id, pl);
      }
      return next;
    });
  }

  // ── Fetch combined tracks when entering Step 3 ─────────

  const fetchCombinedTracks = useCallback(async () => {
    if (selectedPlaylists.size === 0) {
      setCombinedTracks([]);
      return;
    }

    setTracksLoading(true);
    const allTracks: PlaylistTrack[] = [];
    const seenIds = new Set<string>();

    for (const [playlistId, pl] of selectedPlaylists) {
      try {
        const data = await apiFetch<PlaylistTracksResponse>(
          `/api/playlists/${playlistId}/tracks?service=${pl.service}`
        );
        for (const track of data.items) {
          if (!seenIds.has(track.catalog_id)) {
            seenIds.add(track.catalog_id);
            allTracks.push(track);
          }
        }
      } catch {
        // Skip playlists that fail to load
      }
    }

    setCombinedTracks(allTracks);
    setTracksLoading(false);
  }, [selectedPlaylists]);

  useEffect(() => {
    if (step === 2) {
      fetchCombinedTracks();
    }
  }, [step, fetchCombinedTracks]);

  // ── Artist Confirm/Reject (Step 2) ─────────────────────

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

  // ── Song Selection (Step 3) ────────────────────────────

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

    // Playlists: 5x weight (stored as playlist type)
    for (const [id, pl] of selectedPlaylists) {
      items.push({
        calibration_type: "playlist",
        item_id: id,
        item_name: pl.name,
        weight: 5.0,
      });
    }

    // Rejected artists
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

    // Favorite songs from playlists: 3x weight
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

  const canGoBack = step > 0;
  const isLastStep = step === STEPS.length - 1;

  // ── Step Progress Bar ──────────────────────────────────

  const progressBar = (
    <div className="flex items-center gap-2 mb-6">
      {STEPS.map((s, i) => {
        const Icon = s.icon;
        return (
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
                <Icon className="h-3 w-3" />
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
        );
      })}
    </div>
  );

  // ── Step 1: Playlist Grid ─────────────────────────────

  const playlistsStep = (
    <div>
      <p className="text-sm text-muted-foreground mb-4">
        Select up to {MAX_PLAYLISTS} playlists you listen to on repeat. Their
        songs will be weighted heavily in your taste profile.
      </p>
      {playlistsLoading ? (
        <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="aspect-square rounded-lg" />
          ))}
        </div>
      ) : !playlistsData?.playlists.length ? (
        <p className="text-sm text-muted-foreground text-center py-8">
          No playlists found in your library. You can skip this step.
        </p>
      ) : (
        <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
          {playlistsData.playlists.map((pl: ServicePlaylist) => {
            const isSelected = selectedPlaylists.has(pl.service_playlist_id);
            const isDisabled =
              !isSelected && selectedPlaylists.size >= MAX_PLAYLISTS;

            return (
              <button
                key={pl.service_playlist_id}
                onClick={() => togglePlaylist(pl)}
                disabled={isDisabled}
                className={`group relative aspect-square overflow-hidden rounded-lg border-2 transition-all ${
                  isSelected
                    ? "border-purple-500 ring-2 ring-purple-500/30"
                    : isDisabled
                      ? "border-border opacity-40 cursor-not-allowed"
                      : "border-border hover:border-purple-500/50"
                }`}
              >
                {pl.artwork_url ? (
                  <img
                    src={pl.artwork_url}
                    alt={pl.name}
                    className="h-full w-full object-cover"
                  />
                ) : (
                  <div className="flex h-full w-full items-center justify-center bg-muted">
                    <ListMusic className="h-8 w-8 text-muted-foreground" />
                  </div>
                )}

                {/* Overlay with playlist info */}
                <div className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/80 to-transparent px-2 pb-2 pt-6">
                  <p className="text-xs font-medium text-white truncate">
                    {pl.name}
                  </p>
                  {pl.track_count > 0 && (
                    <p className="text-[10px] text-white/70">
                      {pl.track_count} tracks
                    </p>
                  )}
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
        {selectedPlaylists.size}/{MAX_PLAYLISTS} selected
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

  // ── Step 3: Combined Songs from Selected Playlists ─────

  const songsStep = (
    <div>
      <p className="text-sm text-muted-foreground mb-4">
        Pick your favorite songs from the playlists you selected. These get
        extra weight in your profile.
      </p>

      {selectedPlaylists.size === 0 ? (
        <p className="text-sm text-muted-foreground text-center py-8">
          No playlists selected in step 1. Go back to pick some, or skip this
          step.
        </p>
      ) : tracksLoading ? (
        <div className="space-y-2">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-12 rounded-lg" />
          ))}
        </div>
      ) : combinedTracks.length === 0 ? (
        <p className="text-sm text-muted-foreground text-center py-8">
          No tracks found in the selected playlists.
        </p>
      ) : (
        <>
          <div className="space-y-1 max-h-[50vh] overflow-y-auto pr-1">
            {combinedTracks.map((track) => {
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
                      <Music className="h-3.5 w-3.5 text-muted-foreground" />
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
          <p className="text-xs text-muted-foreground mt-2 text-center">
            {selectedSongs.size} of {combinedTracks.length} songs selected
          </p>
        </>
      )}
    </div>
  );

  // ── Render ─────────────────────────────────────────────

  const stepContent = [playlistsStep, artistsStep, songsStep];
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
