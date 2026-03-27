"use client";

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api";

// ── Types ──────────────────────────────────────────────

export interface ServiceConnection {
  service: string;
  status: string;
  service_user_id: string | null;
  connected_at: string | null;
}

export interface ServicesResponse {
  services: ServiceConnection[];
}

export interface SpotifyConnectResponse {
  authorize_url: string;
}

export interface DeveloperTokenResponse {
  developer_token: string;
}

// ── Hooks ──────────────────────────────────────────────

export function useServices() {
  return useQuery<ServicesResponse>({
    queryKey: ["services"],
    queryFn: () => apiFetch<ServicesResponse>("/api/services"),
  });
}

export function useSpotifyConnect() {
  return useMutation<SpotifyConnectResponse, Error>({
    mutationFn: () =>
      apiFetch<SpotifyConnectResponse>("/api/services/spotify/connect", {
        method: "POST",
      }),
  });
}

export function useAppleMusicDeveloperToken() {
  return useMutation<DeveloperTokenResponse, Error>({
    mutationFn: () =>
      apiFetch<DeveloperTokenResponse>("/api/services/apple-music/developer-token"),
  });
}

export function useDisconnectService() {
  const queryClient = useQueryClient();

  return useMutation<{ message: string }, Error, string>({
    mutationFn: (service: string) =>
      apiFetch<{ message: string }>(`/api/services/${service}`, {
        method: "DELETE",
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["services"] });
    },
  });
}
