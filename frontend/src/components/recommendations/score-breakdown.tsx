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

// 6 weighted dimensions from the new embedding-based scorer.
const WEIGHTED_DIMS = new Set([
  "clap", "mert", "effnet", "genre", "scalar", "artist",
]);

// Bar colors for weighted dims (fall-through uses primary)
const dimensionColors: Record<string, string> = {
  clap: "bg-violet-500",
  mert: "bg-blue-500",
  effnet: "bg-cyan-500",
  genre: "bg-amber-500",
  scalar: "bg-emerald-500",
  artist: "bg-rose-500",
};

export function ScoreBreakdown({ catalogId }: ScoreBreakdownProps) {
  const { data: breakdown, isLoading: breakdownLoading } = useBreakdown(catalogId);
  const { data: audioFeatures } = useAudioFeatures(catalogId);

  if (breakdownLoading) {
    return (
      <div className="space-y-3 pt-3">
        {Array.from({ length: 8 }).map((_, i) => (
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

  const weighted = breakdown.dimensions.filter((d) => WEIGHTED_DIMS.has(d.name));
  const modifiers = breakdown.dimensions.filter(
    (d) => !WEIGHTED_DIMS.has(d.name),
  );

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

      {/* Weighted dimensions (primary composition) */}
      <div className="space-y-2.5">
        <p className="text-xs font-medium text-muted-foreground/80">
          Weighted dimensions
        </p>
        {weighted.map((dim) => {
          const barColor = dimensionColors[dim.name] || "bg-primary";
          const weightedScore = dim.score * dim.weight;
          return (
            <div key={dim.name} className="space-y-1">
              <div className="flex items-center justify-between text-xs">
                <span className="text-muted-foreground">{dim.label}</span>
                <span className="font-mono text-muted-foreground">
                  {(dim.score * 100).toFixed(0)}%
                  <span className="ml-1 text-[10px] opacity-60">
                    (w:{(dim.weight * 100).toFixed(0)}%)
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
                  style={{
                    width: `${Math.min(Math.abs(weightedScore) * 100 * 3, 100)}%`,
                  }}
                />
              </div>
            </div>
          );
        })}
      </div>

      {/* Modifiers (bonuses + penalties, additive) */}
      {modifiers.length > 0 && (
        <div className="space-y-2 pt-2 border-t border-border/50">
          <p className="text-xs font-medium text-muted-foreground/80">
            Modifiers
          </p>
          <div className="space-y-1.5">
            {modifiers.map((mod) => {
              const pct = mod.score * 100;
              const isPositive = mod.score >= 0;
              const barColor = isPositive ? "bg-emerald-400" : "bg-red-400";
              // Width scales against weight (the absolute cap of the modifier)
              const widthPct = mod.weight > 0
                ? Math.min((Math.abs(mod.score) / mod.weight) * 100, 100)
                : 0;
              return (
                <div key={mod.name} className="space-y-1">
                  <div className="flex items-center justify-between text-xs">
                    <span className="text-muted-foreground">{mod.label}</span>
                    <span className="font-mono text-muted-foreground">
                      {isPositive ? "+" : ""}{pct.toFixed(1)}%
                      <span className="ml-1 text-[10px] opacity-60">
                        (cap:±{(mod.weight * 100).toFixed(0)}%)
                      </span>
                    </span>
                  </div>
                  <div className="h-1 w-full rounded-full bg-muted/60">
                    <div
                      className={`h-full rounded-full transition-all ${barColor}`}
                      style={{ width: `${widthPct}%` }}
                    />
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

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
