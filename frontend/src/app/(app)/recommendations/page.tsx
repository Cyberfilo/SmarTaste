"use client";

/**
 * Recommendations page — top-level sidebar tab.
 * Reuses the existing recommendation components from dashboard.
 */

import { RecommendationFeed } from "@/components/recommendations/recommendation-feed";

export default function RecommendationsPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1
          className="text-2xl font-bold tracking-tight sm:text-3xl"
          style={{ fontFamily: "var(--font-heading)" }}
        >
          Suggested
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Personalized recommendations based on your taste profile
        </p>
      </div>

      <RecommendationFeed />
    </div>
  );
}
