"use client";

/**
 * TanStack Query provider for server state management.
 * Global error handling for mutations via onError callback.
 */

import { QueryClient, QueryClientProvider, MutationCache } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";

export function QueryProvider({ children }: { children: React.ReactNode }) {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 5 * 60 * 1000, // 5 minutes
            retry: 1,
          },
        },
        mutationCache: new MutationCache({
          onError: (error) => {
            // Global fallback toast for mutations that don't handle their own errors
            if (error.message && !error.message.includes("Session expired")) {
              toast.error(error.message);
            }
          },
        }),
      }),
  );

  return (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
}
