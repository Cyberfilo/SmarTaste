"use client";

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api";

// ── Types ──────────────────────────────────────────────

export interface KeyStatusResponse {
  configured: boolean;
  masked_key: string | null;
  service: string;
}

export interface ValidateKeyResponse {
  valid: boolean;
  error: string | null;
}

export interface CostEstimateResponse {
  model: string;
  estimated_cost_per_message: string;
  input_token_price: string;
  output_token_price: string;
}

// ── Hooks ──────────────────────────────────────────────

export function useOpenAIKeyStatus() {
  return useQuery<KeyStatusResponse>({
    queryKey: ["openai-key-status"],
    queryFn: () => apiFetch<KeyStatusResponse>("/api/openai/key/status"),
  });
}

export function useStoreOpenAIKey() {
  const queryClient = useQueryClient();

  return useMutation<void, Error, string>({
    mutationFn: (apiKey: string) =>
      apiFetch<void>("/api/openai/key", {
        method: "POST",
        body: JSON.stringify({ api_key: apiKey }),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["openai-key-status"] });
    },
  });
}

export function useValidateOpenAIKey() {
  return useMutation<ValidateKeyResponse, Error>({
    mutationFn: () =>
      apiFetch<ValidateKeyResponse>("/api/openai/key/validate", {
        method: "POST",
      }),
  });
}

export function useDeleteOpenAIKey() {
  const queryClient = useQueryClient();

  return useMutation<void, Error>({
    mutationFn: () =>
      apiFetch<void>("/api/openai/key", {
        method: "DELETE",
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["openai-key-status"] });
    },
  });
}

export function useOpenAICostEstimate() {
  return useQuery<CostEstimateResponse>({
    queryKey: ["openai-cost-estimate"],
    queryFn: () => apiFetch<CostEstimateResponse>("/api/openai/key/cost"),
  });
}
