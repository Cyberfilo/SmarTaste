/**
 * TanStack Query hooks for playlist API — fetches real playlists from services.
 */

import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api";
import type { PlaylistListResponse, PlaylistTracksResponse } from "@/types/api";

export function usePlaylists() {
  return useQuery<PlaylistListResponse>({
    queryKey: ["playlists"],
    queryFn: () => apiFetch<PlaylistListResponse>("/api/playlists"),
    staleTime: 2 * 60 * 1000,
  });
}

export function usePlaylistTracks(
  playlistId: string | null,
  service: string | null
) {
  return useQuery<PlaylistTracksResponse>({
    queryKey: ["playlists", playlistId, "tracks"],
    queryFn: () =>
      apiFetch<PlaylistTracksResponse>(
        `/api/playlists/${playlistId}/tracks?service=${service}`
      ),
    enabled: playlistId !== null && service !== null,
    staleTime: 2 * 60 * 1000,
  });
}
