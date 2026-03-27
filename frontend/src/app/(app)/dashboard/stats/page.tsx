"use client";

/**
 * Listening stats page.
 * Period selector at top, three sections: Top Tracks, Top Artists, Top Genres.
 * Period change triggers refetch via TanStack Query key invalidation.
 */

import { useState } from "react";
import { PeriodSelector } from "@/components/dashboard/period-selector";
import { StatsTracks } from "@/components/dashboard/stats-tracks";
import { StatsArtists } from "@/components/dashboard/stats-artists";
import { StatsGenres } from "@/components/dashboard/stats-genres";
import type { Period } from "@/types/api";

export default function ListeningStatsPage() {
  const [period, setPeriod] = useState<Period>("month");

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight sm:text-3xl">
            Listening Stats
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Your top tracks, artists, and genres
          </p>
        </div>
        <PeriodSelector period={period} onPeriodChange={setPeriod} />
      </div>

      <StatsTracks period={period} />
      <StatsArtists period={period} />
      <StatsGenres period={period} />
    </div>
  );
}
