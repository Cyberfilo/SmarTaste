"use client";

import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api";

export interface SongRow {
  catalog_id: string;
  name: string;
  artist_name: string;
  artwork_url: string;
  isrc_ok: boolean;
  user_linked: boolean;
  has_essentia: boolean;
  has_clap: boolean;
  has_mert: boolean;
  has_cached_audio: boolean;
}

export interface ArtistRow {
  artist_name: string;
  source: "library" | "cobweb" | "feat";
  user: string;
  tracks_library: number;
  tracks_discovered: number;
  tracks_total: number;
  tracks_global: number;
  discovery_pct: number;
  library_pct: number;
}

interface TableResponse<T> {
  total: number;
  limit: number;
  offset: number;
  items: T[];
}

export function useSongsTable(limit: number, offset: number) {
  return useQuery<TableResponse<SongRow>>({
    queryKey: ["admin-songs-table", limit, offset],
    queryFn: () =>
      apiFetch(`/api/admin/songs-table?limit=${limit}&offset=${offset}`),
    placeholderData: (prev) => prev,
    staleTime: 30_000,
  });
}

export function useArtistsTable(limit: number, offset: number) {
  return useQuery<TableResponse<ArtistRow>>({
    queryKey: ["admin-artists-table", limit, offset],
    queryFn: () =>
      apiFetch(`/api/admin/artists-table?limit=${limit}&offset=${offset}`),
    placeholderData: (prev) => prev,
    staleTime: 30_000,
  });
}
