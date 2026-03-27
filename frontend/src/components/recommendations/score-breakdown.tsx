"use client";

import { useBreakdown, useAudioFeatures } from "@/hooks/use-recommendations";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Radar,
  RadarChart,
  PolarGrid,
  PolarAngleAxis,
  ResponsiveContainer,
} from "recharts";

interface ScoreBreakdownProps {
  catalogId: string;
}

const dimensionColors: Record<string, string> = {
  genre_match: "bg-emerald-500",
  audio_similarity: "bg-blue-500",
  novelty: "bg-purple-500",
  freshness: "bg-amber-500",
  diversity: "bg-pink-500",
  artist_affinity: "bg-cyan-500",
  anti_staleness: "bg-orange-500",
};

export function ScoreBreakdown({ catalogId }: ScoreBreakdownProps) {
  const { data: breakdown, isLoading: breakdownLoading } = useBreakdown(catalogId);
  const { data: audioFeatures } = useAudioFeatures(catalogId);

  if (breakdownLoading) {
    return (
      <div className="space-y-3 pt-3">
        {Array.from({ length: 7 }).map((_, i) => (
          <Skeleton key={i} className="h-5 w-full" />
        ))}
      </div>
    );
  }

  if (!breakdown) {
    return (
      <p className="pt-3 text-sm text-muted-foreground">
        Score breakdown unavailable.
      </p>
    );
  }

  // Audio features radar chart data
  const radarData = audioFeatures
    ? [
        { trait: "Energy", value: audioFeatures.energy ?? 0 },
        { trait: "Dance", value: audioFeatures.danceability ?? 0 },
        { trait: "Valence", value: audioFeatures.valence ?? 0 },
        { trait: "Acoustic", value: audioFeatures.acousticness ?? 0 },
        { trait: "Instrumental", value: audioFeatures.instrumentalness ?? 0 },
        { trait: "Beat", value: audioFeatures.beat_strength ?? 0 },
        { trait: "Bright", value: audioFeatures.brightness ?? 0 },
      ].filter((d) => d.value > 0)
    : [];

  const hasAudioFeatures = radarData.length > 0;

  return (
    <div className="space-y-4 pt-3 border-t border-border">
      {/* Overall score */}
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium">Overall Score</span>
        <span className="text-sm font-bold text-primary">
          {Math.round(breakdown.overall_score * 100)}%
        </span>
      </div>

      {/* Dimension bars */}
      <div className="space-y-2.5">
        {breakdown.dimensions.map((dim) => {
          const barColor = dimensionColors[dim.name] || "bg-primary";
          const weightedScore = dim.score * dim.weight;
          return (
            <div key={dim.name} className="space-y-1">
              <div className="flex items-center justify-between text-xs">
                <span className="text-muted-foreground">{dim.label}</span>
                <span className="font-mono text-muted-foreground">
                  {(dim.score * 100).toFixed(0)}%
                  <span className="ml-1 text-[10px] opacity-60">
                    (w:{(dim.weight * 100).toFixed(0)})
                  </span>
                </span>
              </div>
              <div className="h-1.5 w-full rounded-full bg-muted">
                <div
                  className={`h-full rounded-full transition-all ${barColor}`}
                  style={{ width: `${Math.min(dim.score * 100, 100)}%` }}
                />
              </div>
              {/* Weighted contribution indicator */}
              <div className="h-0.5 w-full rounded-full bg-muted/50">
                <div
                  className="h-full rounded-full bg-foreground/20"
                  style={{ width: `${Math.min(weightedScore * 100 * 3, 100)}%` }}
                />
              </div>
            </div>
          );
        })}
      </div>

      {/* Audio features radar */}
      {hasAudioFeatures && (
        <div className="pt-2">
          <p className="text-xs font-medium text-muted-foreground mb-2">
            Audio Features
          </p>
          <div className="h-48 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <RadarChart data={radarData} cx="50%" cy="50%" outerRadius="75%">
                <PolarGrid stroke="hsl(var(--border))" />
                <PolarAngleAxis
                  dataKey="trait"
                  tick={{ fill: "hsl(var(--muted-foreground))", fontSize: 11 }}
                />
                <Radar
                  dataKey="value"
                  stroke="hsl(var(--primary))"
                  fill="hsl(var(--primary))"
                  fillOpacity={0.2}
                  strokeWidth={2}
                />
              </RadarChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}

      {/* Explanation */}
      {breakdown.explanation && (
        <p className="text-xs text-muted-foreground leading-relaxed pt-1">
          {breakdown.explanation}
        </p>
      )}
    </div>
  );
}
