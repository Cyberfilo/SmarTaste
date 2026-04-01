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

  // Step 2: Artists — hierarchical ordered list (position = priority)
  const [artistOrder, setArtistOrder] = useState<string[]>([]);

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

  // ── Artist Hierarchical Ranking (Step 2) ────────────────

  // Initialize artist order from API data when it arrives
  useEffect(() => {
    if (artistsData?.artists && artistOrder.length === 0) {
      setArtistOrder(artistsData.artists.map((a) => a.name));
    }
  }, [artistsData, artistOrder.length]);

  // Drag state for reordering
  const [dragIdx, setDragIdx] = useState<number | null>(null);

  function moveArtist(fromIdx: number, toIdx: number) {
    if (fromIdx === toIdx) return;
    setArtistOrder((prev) => {
      const next = [...prev];
      const [moved] = next.splice(fromIdx, 1);
      next.splice(toIdx, 0, moved);
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

    // Playlists: 5x weight
    for (const [id, pl] of selectedPlaylists) {
      items.push({
        calibration_type: "playlist",
        item_id: id,
        item_name: pl.name,
        weight: 5.0,
      });
    }

    // Artists in hierarchical order — top 3 get "top_artist" type (triggers enrichment)
    // Rest get "artist_rank" with descending weight based on position
    for (let i = 0; i < artistOrder.length; i++) {
      const name = artistOrder[i];
      if (i < 3) {
        items.push({
          calibration_type: "top_artist",
          item_id: name.toLowerCase(),
          item_name: name,
          weight: 5.0,
        });
      } else {
        const weight = Math.max(1.0, 3.0 - (i - 3) * 0.2);
        items.push({
          calibration_type: "artist_rank",
          item_id: name.toLowerCase(),
          item_name: name,
          weight,
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

  // ── Step 2: Hierarchical Artist Ranking ─────────────────

  const artistsStep = (
    <div>
      <p className="text-sm text-muted-foreground mb-4">
        Drag to reorder your artists by priority. The top 3 get their full
        discography analyzed for better recommendations.
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
        <div className="space-y-1 max-h-[50vh] overflow-y-auto pr-1">
          {artistOrder.map((name, idx) => {
            const artist = artistsData.artists.find((a) => a.name === name);
            if (!artist) return null;
            const isTop3 = idx < 3;

            return (
              <div
                key={name}
                draggable
                onDragStart={() => setDragIdx(idx)}
                onDragOver={(e) => {
                  e.preventDefault();
                  if (dragIdx !== null && dragIdx !== idx) {
                    moveArtist(dragIdx, idx);
                    setDragIdx(idx);
                  }
                }}
                onDragEnd={() => setDragIdx(null)}
                className={`flex items-center gap-3 rounded-lg px-3 py-2.5 cursor-grab active:cursor-grabbing select-none transition-colors ${
                  dragIdx === idx
                    ? "opacity-50 border border-purple-500/50 bg-purple-500/5"
                    : isTop3
                      ? "bg-purple-500/10 border border-purple-500/20"
                      : "bg-card border border-border"
                }`}
              >
                {/* Rank badge */}
                <span
                  className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-xs font-bold ${
                    isTop3
                      ? "bg-purple-500 text-white"
                      : "bg-muted text-muted-foreground"
                  }`}
                >
                  {idx + 1}
                </span>

                {/* Artist info */}
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium truncate">{name}</p>
                  <p className="text-xs text-muted-foreground">
                    {artist.song_count} songs &middot; affinity{" "}
                    {Math.round(artist.score * 100)}%
                  </p>
                </div>

                {/* Top 3 indicator */}
                {isTop3 && (
                  <span className="text-[10px] font-medium text-purple-400 uppercase tracking-wider shrink-0">
                    Full scan
                  </span>
                )}

                {/* Grab handle */}
                <div className="flex flex-col gap-px text-muted-foreground shrink-0">
                  <div className="flex gap-px">
                    <div className="h-1 w-1 rounded-full bg-current" />
                    <div className="h-1 w-1 rounded-full bg-current" />
                  </div>
                  <div className="flex gap-px">
                    <div className="h-1 w-1 rounded-full bg-current" />
                    <div className="h-1 w-1 rounded-full bg-current" />
                  </div>
                  <div className="flex gap-px">
                    <div className="h-1 w-1 rounded-full bg-current" />
                    <div className="h-1 w-1 rounded-full bg-current" />
                  </div>
                </div>
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
