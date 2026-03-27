"use client";

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api";

// ── Types ──────────────────────────────────────────────

export interface RecommendationItem {
  catalog_id: string;
  name: string;
  artist_name: string;
  album_name: string;
  artwork_url: string;
  preview_url: string;
  score: number;
  explanation: string;
  strategy_source: string;
  genre_names: string[];
}

export interface RecommendationsResponse {
  items: RecommendationItem[];
  strategy: string;
  mood: string | null;
  total: number;
  weights_adapted: boolean;
}

export interface FeedbackRequest {
  catalog_id: string;
  feedback_type: "thumbs_up" | "thumbs_down" | "skip";
}

export interface FeedbackResponse {
  catalog_id: string;
  feedback_type: string;
  recorded: boolean;
}

export interface BreakdownDimension {
  name: string;
  label: string;
  score: number;
  weight: number;
}

export interface BreakdownResponse {
  catalog_id: string;
  overall_score: number;
  dimensions: BreakdownDimension[];
  explanation: string;
}

export interface AudioFeaturesResponse {
  catalog_id: string;
  energy: number | null;
  danceability: number | null;
  valence: number | null;
  acousticness: number | null;
  tempo: number | null;
  instrumentalness: number | null;
  beat_strength: number | null;
  brightness: number | null;
}

// ── Hooks ──────────────────────────────────────────────

export function useRecommendations(strategy: string, mood: string | null) {
  const params = new URLSearchParams({ strategy, limit: "20" });
  if (mood) {
    params.set("mood", mood);
  }

  return useQuery<RecommendationsResponse>({
    queryKey: ["recommendations", strategy, mood],
    queryFn: () => apiFetch<RecommendationsResponse>(`/api/recommendations?${params}`),
  });
}

export function useFeedback() {
  const queryClient = useQueryClient();

  return useMutation<FeedbackResponse, Error, FeedbackRequest>({
    mutationFn: ({ catalog_id, feedback_type }) =>
      apiFetch<FeedbackResponse>(`/api/recommendations/${catalog_id}/feedback`, {
        method: "POST",
        body: JSON.stringify({ feedback_type }),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["recommendations"] });
    },
  });
}

export function useBreakdown(catalogId: string | null) {
  return useQuery<BreakdownResponse>({
    queryKey: ["breakdown", catalogId],
    queryFn: () => apiFetch<BreakdownResponse>(`/api/recommendations/${catalogId}/breakdown`),
    enabled: !!catalogId,
  });
}

export function useAudioFeatures(catalogId: string | null) {
  return useQuery<AudioFeaturesResponse>({
    queryKey: ["audio-features", catalogId],
    queryFn: () => apiFetch<AudioFeaturesResponse>(`/api/tracks/${catalogId}/audio-features`),
    enabled: !!catalogId,
  });
}
