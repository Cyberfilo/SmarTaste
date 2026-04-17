/**
 * TanStack Query hooks for taste profile API endpoints.
 * Profile returns instantly from backend cache (even stale data).
 * Long staleTime avoids unnecessary re-fetches on navigation.
 */

import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api";
import type {
  AudioTraitsResponse,
  RecentEnrichmentsResponse,
  SonicNeighborsResponse,
  TasteProfile,
  TopArtistsResponse,
  TopGenresResponse,
} from "@/types/api";

export function useTasteProfile() {
  return useQuery<TasteProfile>({
    queryKey: ["taste", "profile"],
    queryFn: () => apiFetch<TasteProfile>("/api/taste/profile"),
    staleTime: 30 * 60 * 1000, // 30min — backend serves stale cache instantly
  });
}

export function useTopGenres() {
  return useQuery<TopGenresResponse>({
    queryKey: ["taste", "genres"],
    queryFn: () => apiFetch<TopGenresResponse>("/api/taste/genres"),
    staleTime: 30 * 60 * 1000,
  });
}

export function useTopArtists() {
  return useQuery<TopArtistsResponse>({
    queryKey: ["taste", "artists"],
    queryFn: () => apiFetch<TopArtistsResponse>("/api/taste/artists"),
    staleTime: 30 * 60 * 1000,
  });
}

export function useAudioTraits() {
  return useQuery<AudioTraitsResponse>({
    queryKey: ["taste", "audio-traits"],
    queryFn: () => apiFetch<AudioTraitsResponse>("/api/taste/audio-traits"),
    staleTime: 30 * 60 * 1000,
  });
}

export function useSonicNeighbors(limit = 8) {
  return useQuery<SonicNeighborsResponse>({
    queryKey: ["taste", "sonic-neighbors", limit],
    queryFn: () =>
      apiFetch<SonicNeighborsResponse>(
        `/api/taste/sonic-neighbors?limit=${limit}`,
      ),
    staleTime: 30 * 60 * 1000,
  });
}

export function useRecentEnrichments(limit = 12) {
  return useQuery<RecentEnrichmentsResponse>({
    queryKey: ["taste", "recent-enrichments", limit],
    queryFn: () =>
      apiFetch<RecentEnrichmentsResponse>(
        `/api/taste/recent-enrichments?limit=${limit}`,
      ),
    staleTime: 5 * 60 * 1000, // shorter — this data is fresher-dependent
  });
}
