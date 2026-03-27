"use client";

import { useState } from "react";
import { useRecommendations } from "@/hooks/use-recommendations";
import { StrategySelector } from "./strategy-selector";
import { MoodFilter } from "./mood-filter";
import { RecommendationCard } from "./recommendation-card";
import { Skeleton } from "@/components/ui/skeleton";
import { Card, CardContent } from "@/components/ui/card";
import { toast } from "sonner";
import { Music } from "lucide-react";
import { useEffect } from "react";

export function RecommendationFeed() {
  const [strategy, setStrategy] = useState("auto");
  const [mood, setMood] = useState<string | null>(null);

  const { data, isLoading, isError, error } = useRecommendations(strategy, mood);

  useEffect(() => {
    if (isError && error) {
      toast.error(error.message || "Failed to load recommendations");
    }
  }, [isError, error]);

  return (
    <div className="space-y-6">
      {/* Controls */}
      <div className="space-y-4">
        <StrategySelector value={strategy} onChange={setStrategy} />
        <MoodFilter value={mood} onChange={setMood} />
      </div>

      {/* Adapted weights indicator */}
      {data?.weights_adapted && (
        <p className="text-xs text-emerald-400">
          Scoring weights adapted from your feedback
        </p>
      )}

      {/* Feed */}
      <div className="space-y-4">
        {isLoading ? (
          // Skeleton loading state
          Array.from({ length: 4 }).map((_, i) => (
            <Card key={i} className="w-full">
              <CardContent className="space-y-3">
                <div className="flex gap-3">
                  <Skeleton className="h-20 w-20 md:h-[120px] md:w-[120px] rounded-lg shrink-0" />
                  <div className="flex-1 space-y-2">
                    <Skeleton className="h-4 w-3/4" />
                    <Skeleton className="h-3 w-1/2" />
                    <Skeleton className="h-3 w-full" />
                    <Skeleton className="h-3 w-full" />
                    <div className="flex gap-1.5 pt-1">
                      <Skeleton className="h-4 w-16 rounded-full" />
                      <Skeleton className="h-4 w-12 rounded-full" />
                    </div>
                  </div>
                </div>
                <div className="flex gap-2 pt-1">
                  <Skeleton className="h-7 w-16" />
                  <Skeleton className="h-7 w-20" />
                </div>
              </CardContent>
            </Card>
          ))
        ) : data && data.items.length > 0 ? (
          data.items.map((item) => (
            <RecommendationCard key={item.catalog_id} item={item} />
          ))
        ) : (
          // Empty state
          <div className="flex flex-col items-center justify-center py-16 text-center">
            <Music className="h-12 w-12 text-muted-foreground/40 mb-4" />
            <h3 className="text-sm font-medium text-muted-foreground">
              No recommendations yet
            </h3>
            <p className="mt-1 max-w-sm text-xs text-muted-foreground/70">
              Connect a music service and build your taste profile first.
              Recommendations will appear here once your profile has enough data.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
