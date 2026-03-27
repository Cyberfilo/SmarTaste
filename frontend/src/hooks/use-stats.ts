/**
 * TanStack Query hooks for listening stats API endpoints.
 * Period is part of the query key so changing period triggers refetch.
 */

import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api";
import type {
  Period,
  TopTracksResponse,
  TopArtistsStatsResponse,
  TopGenresStatsResponse,
} from "@/types/api";

export function useTopTracks(period: Period) {
  return useQuery<TopTracksResponse>({
    queryKey: ["stats", "tracks", period],
    queryFn: () =>
      apiFetch<TopTracksResponse>(`/api/stats/tracks?period=${period}`),
    staleTime: 5 * 60 * 1000,
  });
}

export function useTopArtistsStats(period: Period) {
  return useQuery<TopArtistsStatsResponse>({
    queryKey: ["stats", "artists", period],
    queryFn: () =>
      apiFetch<TopArtistsStatsResponse>(`/api/stats/artists?period=${period}`),
    staleTime: 5 * 60 * 1000,
  });
}

export function useTopGenresStats(period: Period) {
  return useQuery<TopGenresStatsResponse>({
    queryKey: ["stats", "genres", period],
    queryFn: () =>
      apiFetch<TopGenresStatsResponse>(`/api/stats/genres?period=${period}`),
    staleTime: 5 * 60 * 1000,
  });
}
