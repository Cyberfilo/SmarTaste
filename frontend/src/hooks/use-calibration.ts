/**
 * TanStack Query hooks for the calibration/onboarding wizard API.
 */

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api";

// ── Types ──────────────────────────────────────────────

export interface CalibrationAlbum {
  album_id: string;
  name: string;
  artist_name: string;
  artwork_url: string;
  track_count: number;
  release_date: string;
  service: string;
}

export interface CalibrationAlbumsResponse {
  albums: CalibrationAlbum[];
  total: number;
}

export interface CalibrationArtist {
  name: string;
  score: number;
  song_count: number;
}

export interface CalibrationArtistsResponse {
  artists: CalibrationArtist[];
  service: string;
}

export interface CalibrationItem {
  calibration_type: string;
  item_id: string;
  item_name: string;
  weight: number;
}

export interface CalibrationStatusResponse {
  completed: boolean;
  item_count: number;
}

// ── Hooks ──────────────────────────────────────────────

export function useCalibrationAlbums() {
  return useQuery<CalibrationAlbumsResponse>({
    queryKey: ["calibration", "albums"],
    queryFn: () => apiFetch<CalibrationAlbumsResponse>("/api/calibration/albums"),
    staleTime: 5 * 60 * 1000,
  });
}

export function useCalibrationArtists() {
  return useQuery<CalibrationArtistsResponse>({
    queryKey: ["calibration", "artists"],
    queryFn: () =>
      apiFetch<CalibrationArtistsResponse>("/api/calibration/artists"),
    staleTime: 5 * 60 * 1000,
  });
}

export function useCalibrationStatus() {
  return useQuery<CalibrationStatusResponse>({
    queryKey: ["calibration", "status"],
    queryFn: () =>
      apiFetch<CalibrationStatusResponse>("/api/calibration/status"),
    staleTime: 30 * 1000,
  });
}

export function useSaveCalibration() {
  const queryClient = useQueryClient();

  return useMutation<{ message: string; items_saved: number }, Error, CalibrationItem[]>({
    mutationFn: (items: CalibrationItem[]) =>
      apiFetch<{ message: string; items_saved: number }>(
        "/api/calibration/save",
        {
          method: "POST",
          body: JSON.stringify({ items }),
        }
      ),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["calibration", "status"] });
      queryClient.invalidateQueries({ queryKey: ["taste"] });
    },
  });
}
