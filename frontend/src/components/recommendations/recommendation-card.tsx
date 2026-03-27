"use client";

import { useState } from "react";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { ThumbsUp, ThumbsDown, ChevronDown, ChevronUp } from "lucide-react";
import { cn } from "@/lib/utils";
import { useFeedback, type RecommendationItem } from "@/hooks/use-recommendations";
import { ScoreBreakdown } from "./score-breakdown";

interface RecommendationCardProps {
  item: RecommendationItem;
}

function scoreColor(score: number): string {
  if (score < 0.5) return "bg-red-500/15 text-red-400 border-red-500/30";
  if (score < 0.7) return "bg-amber-500/15 text-amber-400 border-amber-500/30";
  return "bg-emerald-500/15 text-emerald-400 border-emerald-500/30";
}

const MAX_VISIBLE_GENRES = 3;

export function RecommendationCard({ item }: RecommendationCardProps) {
  const [expanded, setExpanded] = useState(false);
  const [showDetails, setShowDetails] = useState(false);
  const [feedback, setFeedback] = useState<"thumbs_up" | "thumbs_down" | null>(null);
  const feedbackMutation = useFeedback();

  function handleFeedback(type: "thumbs_up" | "thumbs_down") {
    if (feedback === type) return; // already given
    setFeedback(type);
    feedbackMutation.mutate({
      catalog_id: item.catalog_id,
      feedback_type: type,
    });
  }

  const visibleGenres = item.genre_names.slice(0, MAX_VISIBLE_GENRES);
  const extraGenres = item.genre_names.length - MAX_VISIBLE_GENRES;

  return (
    <Card className="w-full">
      <CardContent className="space-y-3">
        {/* Top row: artwork + info + score */}
        <div className="flex gap-3">
          {/* Album artwork */}
          <div className="shrink-0">
            {item.artwork_url ? (
              <img
                src={item.artwork_url.replace("{w}", "240").replace("{h}", "240")}
                alt={`${item.name} artwork`}
                className="h-20 w-20 md:h-[120px] md:w-[120px] rounded-lg object-cover bg-muted"
                loading="lazy"
              />
            ) : (
              <div className="h-20 w-20 md:h-[120px] md:w-[120px] rounded-lg bg-muted flex items-center justify-center text-muted-foreground text-2xl">
                ♪
              </div>
            )}
          </div>

          {/* Track info */}
          <div className="min-w-0 flex-1 space-y-1">
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <h3 className="truncate text-sm font-semibold leading-tight">
                  {item.name}
                </h3>
                <p className="truncate text-sm text-muted-foreground">
                  {item.artist_name}
                </p>
                <p className="truncate text-xs text-muted-foreground/70">
                  {item.album_name}
                </p>
              </div>
              {/* Score badge */}
              <Badge
                variant="outline"
                className={cn(
                  "shrink-0 text-xs font-bold tabular-nums",
                  scoreColor(item.score)
                )}
              >
                {Math.round(item.score * 100)}%
              </Badge>
            </div>

            {/* Explanation (truncated, expandable) */}
            <p
              onClick={() => setExpanded(!expanded)}
              className={cn(
                "cursor-pointer text-xs text-muted-foreground/80 leading-relaxed",
                !expanded && "line-clamp-2"
              )}
            >
              {item.explanation}
            </p>

            {/* Strategy source + genres */}
            <div className="flex flex-wrap items-center gap-1.5 pt-1">
              <Badge variant="secondary" className="text-[10px] font-normal">
                {item.strategy_source}
              </Badge>
              {visibleGenres.map((genre) => (
                <Badge
                  key={genre}
                  variant="outline"
                  className="text-[10px] font-normal text-muted-foreground"
                >
                  {genre}
                </Badge>
              ))}
              {extraGenres > 0 && (
                <span className="text-[10px] text-muted-foreground/60">
                  +{extraGenres} more
                </span>
              )}
            </div>
          </div>
        </div>

        {/* Action buttons row */}
        <div className="flex items-center gap-2 pt-1">
          <Button
            variant={feedback === "thumbs_up" ? "default" : "outline"}
            size="sm"
            onClick={() => handleFeedback("thumbs_up")}
            disabled={feedbackMutation.isPending}
            className={cn(
              "gap-1.5",
              feedback === "thumbs_up" && "bg-emerald-600 hover:bg-emerald-700 text-white border-emerald-600"
            )}
          >
            <ThumbsUp className="h-3.5 w-3.5" />
            <span className="text-xs">Like</span>
          </Button>
          <Button
            variant={feedback === "thumbs_down" ? "default" : "outline"}
            size="sm"
            onClick={() => handleFeedback("thumbs_down")}
            disabled={feedbackMutation.isPending}
            className={cn(
              "gap-1.5",
              feedback === "thumbs_down" && "bg-red-600 hover:bg-red-700 text-white border-red-600"
            )}
          >
            <ThumbsDown className="h-3.5 w-3.5" />
            <span className="text-xs">Dislike</span>
          </Button>
          <div className="flex-1" />
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setShowDetails(!showDetails)}
            className="gap-1 text-xs text-muted-foreground"
          >
            Details
            {showDetails ? (
              <ChevronUp className="h-3.5 w-3.5" />
            ) : (
              <ChevronDown className="h-3.5 w-3.5" />
            )}
          </Button>
        </div>

        {/* Score breakdown (collapsible) */}
        {showDetails && <ScoreBreakdown catalogId={item.catalog_id} />}
      </CardContent>
    </Card>
  );
}
