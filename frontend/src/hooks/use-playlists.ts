/**
 * TanStack Query hooks for playlist API — fetches real playlists from services.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api";
import type {
  BriefMentionedSong,
  BriefResponse,
  PlaylistListResponse,
  PlaylistRecsResponse,
  PlaylistTracksResponse,
  SongAutocompleteResponse,
} from "@/types/api";

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

export function usePlaylistRecommendations(
  playlistId: string | null,
  service: string | null,
  opts: { briefId?: string | null; enabled?: boolean; limit?: number } = {},
) {
  const briefId = opts.briefId ?? null;
  const limit = opts.limit ?? 20;
  return useQuery<PlaylistRecsResponse>({
    queryKey: ["playlists", playlistId, "recommendations", briefId, limit],
    queryFn: () => {
      const params = new URLSearchParams();
      if (service) params.set("service", service);
      params.set("limit", String(limit));
      if (briefId) params.set("brief_id", briefId);
      return apiFetch<PlaylistRecsResponse>(
        `/api/playlists/${playlistId}/recommendations?${params.toString()}`,
      );
    },
    enabled:
      (opts.enabled ?? true)
      && playlistId !== null
      && service !== null,
    staleTime: 5 * 60 * 1000,
  });
}

// ── Song autocomplete (V 6.420) ─────────────────────────

export function useSongAutocomplete(
  query: string,
  opts: { service?: string; limit?: number } = {},
) {
  const trimmed = query.trim();
  return useQuery<SongAutocompleteResponse>({
    queryKey: ["search", "songs", trimmed, opts.service, opts.limit],
    queryFn: () => {
      const p = new URLSearchParams({ q: trimmed });
      if (opts.service) p.set("service", opts.service);
      p.set("limit", String(opts.limit ?? 10));
      return apiFetch<SongAutocompleteResponse>(
        `/api/search/songs?${p.toString()}`,
      );
    },
    enabled: trimmed.length >= 1,
    staleTime: 60 * 1000,
  });
}

// ── Playlist brief (V 6.440 / 6.441) ─────────────────────

interface CreateBriefInput {
  playlistId: string;
  service: string;
  briefText: string;
  mentionedSongs: BriefMentionedSong[];
}

export function useCreateBrief() {
  const qc = useQueryClient();
  return useMutation<BriefResponse, Error, CreateBriefInput>({
    mutationFn: async (input) => {
      const body = JSON.stringify({
        service: input.service,
        brief_text: input.briefText,
        mentioned_songs: input.mentionedSongs,
      });
      return apiFetch<BriefResponse>(
        `/api/playlists/${input.playlistId}/brief`,
        { method: "POST", body },
      );
    },
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({ queryKey: ["playlists", vars.playlistId, "briefs"] });
    },
  });
}

// ── Add track to playlist (V 6.470, Apple Music only) ───

interface AddTrackInput {
  playlistId: string;
  catalogId: string;
  service: string;
}

interface AddTrackResponse {
  playlist_id: string;
  catalog_id: string;
  service: string;
  added: boolean;
}

export function useAddTrackToPlaylist() {
  return useMutation<AddTrackResponse, Error, AddTrackInput>({
    mutationFn: async (input) => {
      const body = JSON.stringify({
        service: input.service,
        catalog_id: input.catalogId,
      });
      return apiFetch<AddTrackResponse>(
        `/api/playlists/${input.playlistId}/tracks`,
        { method: "POST", body },
      );
    },
  });
}
