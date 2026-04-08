/**
 * TanStack Query hooks for taste profile API endpoints.
 * Profile returns instantly from backend cache (even stale data).
 * Long staleTime avoids unnecessary re-fetches on navigation.
 */

import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api";
import type {
  TasteProfile,
  TopGenresResponse,
  TopArtistsResponse,
  AudioTraitsResponse,
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
