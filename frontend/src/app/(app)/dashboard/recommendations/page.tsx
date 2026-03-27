"use client";

import { RecommendationFeed } from "@/components/recommendations/recommendation-feed";

export default function RecommendationsPage() {
  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Recommendations</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Personalized music picks based on your taste profile
        </p>
      </div>
      <RecommendationFeed />
    </div>
  );
}
