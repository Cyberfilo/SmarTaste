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

export function useKeyStatus() {
  return useQuery<KeyStatusResponse>({
    queryKey: ["claude-key-status"],
    queryFn: () => apiFetch<KeyStatusResponse>("/api/claude/key/status"),
  });
}

export function useStoreKey() {
  const queryClient = useQueryClient();

  return useMutation<void, Error, string>({
    mutationFn: (apiKey: string) =>
      apiFetch<void>("/api/claude/key", {
        method: "POST",
        body: JSON.stringify({ api_key: apiKey }),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["claude-key-status"] });
    },
  });
}

export function useValidateKey() {
  return useMutation<ValidateKeyResponse, Error>({
    mutationFn: () =>
      apiFetch<ValidateKeyResponse>("/api/claude/key/validate", {
        method: "POST",
      }),
  });
}

export function useDeleteKey() {
  const queryClient = useQueryClient();

  return useMutation<void, Error>({
    mutationFn: () =>
      apiFetch<void>("/api/claude/key", {
        method: "DELETE",
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["claude-key-status"] });
    },
  });
}

export function useCostEstimate() {
  return useQuery<CostEstimateResponse>({
    queryKey: ["claude-cost-estimate"],
    queryFn: () => apiFetch<CostEstimateResponse>("/api/claude/key/cost"),
  });
}
