/**
 * TanStack Query hooks for playlist API endpoints.
 */

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api";
import type { PlaylistListResponse, PlaylistDetail } from "@/types/api";

export function usePlaylists() {
  return useQuery<PlaylistListResponse>({
    queryKey: ["playlists"],
    queryFn: () => apiFetch<PlaylistListResponse>("/api/playlists"),
    staleTime: 30 * 1000,
  });
}

export function usePlaylist(id: number | null) {
  return useQuery<PlaylistDetail>({
    queryKey: ["playlists", id],
    queryFn: () => apiFetch<PlaylistDetail>(`/api/playlists/${id}`),
    enabled: id !== null,
    staleTime: 30 * 1000,
  });
}

export function useCreatePlaylist() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: { name: string; description?: string; ai_context?: string }) =>
      apiFetch("/api/playlists", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(data),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["playlists"] });
    },
  });
}

export function useDeletePlaylist() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: number) =>
      apiFetch(`/api/playlists/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["playlists"] });
    },
  });
}

export function useAddTracks(playlistId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (catalog_ids: string[]) =>
      apiFetch(`/api/playlists/${playlistId}/tracks`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ catalog_ids }),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["playlists", playlistId] });
      queryClient.invalidateQueries({ queryKey: ["playlists"] });
    },
  });
}

export function useRemoveTrack(playlistId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (itemId: number) =>
      apiFetch(`/api/playlists/${playlistId}/tracks/${itemId}`, {
        method: "DELETE",
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["playlists", playlistId] });
      queryClient.invalidateQueries({ queryKey: ["playlists"] });
    },
  });
}
