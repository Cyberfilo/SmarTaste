"use client";

/**
 * Audio traits radar chart visualization using Recharts.
 * Displays energy, danceability, valence, acousticness, and additional traits.
 */

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useAudioTraits } from "@/hooks/use-taste";
import { toast } from "sonner";
import { useEffect } from "react";
import {
  RadarChart,
  PolarGrid,
  PolarAngleAxis,
  PolarRadiusAxis,
  Radar,
  ResponsiveContainer,
} from "recharts";

const TRAIT_LABELS: Record<string, string> = {
  energy: "Energy",
  danceability: "Danceability",
  valence: "Valence",
  acousticness: "Acousticness",
  beat_strength: "Beat Strength",
  brightness: "Brightness",
  instrumentalness: "Instrumentalness",
};

function TraitSkeleton() {
  return (
    <div className="flex items-center justify-center py-10">
      <div className="h-[300px] w-full max-w-md animate-pulse rounded-lg bg-muted" />
    </div>
  );
}

export function TasteAudioTraits() {
  const { data, isLoading, error } = useAudioTraits();

  useEffect(() => {
    if (error) {
      toast.error("Failed to load audio traits", {
        description: error.message,
      });
    }
  }, [error]);

  if (isLoading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Audio Traits</CardTitle>
        </CardHeader>
        <CardContent>
          <TraitSkeleton />
        </CardContent>
      </Card>
    );
  }

  // If note is set or no traits data, show informational message
  if (data?.note || !data || Object.keys(data.traits).length === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Audio Traits</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">
            {data?.note ||
              "Audio trait analysis is not available yet. More listening data is needed."}
          </p>
        </CardContent>
      </Card>
    );
  }

  const chartData = Object.entries(data.traits)
    .filter(([key]) => key in TRAIT_LABELS)
    .map(([key, value]) => ({
      trait: TRAIT_LABELS[key] || key,
      value: Math.round(value * 100),
      fullMark: 100,
    }));

  return (
    <Card>
      <CardHeader>
        <CardTitle>Audio Traits</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="mx-auto w-full max-w-md">
          <ResponsiveContainer width="100%" height={320}>
            <RadarChart data={chartData} cx="50%" cy="50%" outerRadius="75%">
              <PolarGrid stroke="var(--color-muted)" strokeOpacity={0.5} />
              <PolarAngleAxis
                dataKey="trait"
                tick={{
                  fill: "var(--color-muted-foreground)",
                  fontSize: 12,
                }}
              />
              <PolarRadiusAxis
                angle={90}
                domain={[0, 100]}
                tick={false}
                axisLine={false}
              />
              <Radar
                name="Your Traits"
                dataKey="value"
                stroke="var(--color-emerald-400)"
                fill="var(--color-emerald-500)"
                fillOpacity={0.3}
                strokeWidth={2}
              />
            </RadarChart>
          </ResponsiveContainer>
        </div>
      </CardContent>
    </Card>
  );
}
