/**
 * TanStack Query hooks for taste profile API endpoints.
 * All hooks use apiFetch with proper typing and 5-minute stale time.
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
    staleTime: 5 * 60 * 1000,
  });
}

export function useTopGenres() {
  return useQuery<TopGenresResponse>({
    queryKey: ["taste", "genres"],
    queryFn: () => apiFetch<TopGenresResponse>("/api/taste/genres"),
    staleTime: 5 * 60 * 1000,
  });
}

export function useTopArtists() {
  return useQuery<TopArtistsResponse>({
    queryKey: ["taste", "artists"],
    queryFn: () => apiFetch<TopArtistsResponse>("/api/taste/artists"),
    staleTime: 5 * 60 * 1000,
  });
}

export function useAudioTraits() {
  return useQuery<AudioTraitsResponse>({
    queryKey: ["taste", "audio-traits"],
    queryFn: () => apiFetch<AudioTraitsResponse>("/api/taste/audio-traits"),
    staleTime: 5 * 60 * 1000,
  });
}
