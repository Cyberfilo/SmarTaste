"use client";

/**
 * Dashboard overview page.
 * Shows summary cards, top genres, top artists, audio traits, and release year
 * distribution — all from the taste profile API.
 */

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Music,
  Clock,
  Compass,
  Plug,
  BarChart3,
  Users,
  Mic2,
  Calendar,
} from "lucide-react";
import { toast } from "sonner";
import { useEffect } from "react";
import { Card, CardContent } from "@/components/ui/card";
import { useTasteProfile } from "@/hooks/use-taste";
import {
  PieChart,
  Pie,
  Cell,
  RadarChart,
  PolarGrid,
  PolarAngleAxis,
  Radar,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
} from "recharts";

const tabs = [
  { href: "/dashboard/taste", label: "Taste Profile" },
  { href: "/dashboard/stats", label: "Listening Stats" },
  { href: "/dashboard/recommendations", label: "Recommendations" },
];

// Emerald-based palette for charts (dark mode friendly)
const CHART_COLORS = [
  "#10b981", // emerald-500
  "#34d399", // emerald-400
  "#6ee7b7", // emerald-300
  "#059669", // emerald-600
  "#047857", // emerald-700
  "#a7f3d0", // emerald-200
  "#065f46", // emerald-800
  "#d1fae5", // emerald-100
];

function SkeletonCard() {
  return (
    <Card>
      <CardContent className="pt-2">
        <div className="animate-pulse space-y-3">
          <div className="h-3 w-20 rounded bg-muted" />
          <div className="h-7 w-16 rounded bg-muted" />
        </div>
      </CardContent>
    </Card>
  );
}

function SkeletonChart() {
  return (
    <Card>
      <CardContent className="pt-4">
        <div className="animate-pulse space-y-4">
          <div className="h-4 w-28 rounded bg-muted" />
          <div className="h-48 rounded bg-muted" />
        </div>
      </CardContent>
    </Card>
  );
}

export default function DashboardPage() {
  const pathname = usePathname();
  const { data: profile, isLoading, error } = useTasteProfile();

  useEffect(() => {
    if (error) {
      toast.error("Failed to load taste profile", {
        description: error.message,
      });
    }
  }, [error]);

  const isEmpty = !isLoading && !error && !profile;

  // Prepare chart data from profile
  const genreData = profile
    ? Object.entries(profile.genre_vector)
        .sort(([, a], [, b]) => b - a)
        .slice(0, 8)
        .map(([name, weight]) => ({
          name: name.length > 20 ? name.slice(0, 18) + "..." : name,
          fullName: name,
          value: Math.round(weight * 100),
        }))
    : [];

  const topArtists = profile
    ? profile.top_artists.slice(0, 8)
    : [];

  const audioTraitData = profile
    ? Object.entries(profile.audio_trait_preferences)
        .filter(([, v]) => v > 0)
        .map(([key, value]) => ({
          trait: key
            .replace(/_/g, " ")
            .replace(/\b\w/g, (c) => c.toUpperCase()),
          value: Math.round(value * 100),
          fullMark: 100,
        }))
    : [];

  const yearData = profile
    ? Object.entries(profile.release_year_distribution)
        .sort(([a], [b]) => a.localeCompare(b))
        .slice(-15) // last 15 years
        .map(([year, count]) => ({
          year,
          songs: count,
        }))
    : [];

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold tracking-tight sm:text-3xl">
        Dashboard
      </h1>

      {/* Overview cards */}
      {isLoading ? (
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4 lg:gap-4">
          <SkeletonCard />
          <SkeletonCard />
          <SkeletonCard />
          <SkeletonCard />
        </div>
      ) : isEmpty ? (
        <Card>
          <CardContent className="flex flex-col items-center gap-3 py-10 text-center">
            <Plug className="h-10 w-10 text-muted-foreground" />
            <div>
              <p className="text-lg font-medium">No music data yet</p>
              <p className="text-sm text-muted-foreground">
                Connect a service to see your music profile.
              </p>
            </div>
            <Link
              href="/settings"
              className="mt-2 inline-flex items-center rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90"
            >
              Go to Settings
            </Link>
          </CardContent>
        </Card>
      ) : profile ? (
        <>
          {/* Summary cards */}
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4 lg:gap-4">
            <Card>
              <CardContent className="pt-2">
                <div className="flex items-center gap-2 text-muted-foreground">
                  <Music className="h-4 w-4" />
                  <span className="text-xs font-medium uppercase tracking-wider">
                    Songs Analyzed
                  </span>
                </div>
                <p className="mt-1 text-2xl font-bold tabular-nums sm:text-3xl">
                  {profile.total_songs_analyzed.toLocaleString()}
                </p>
              </CardContent>
            </Card>

            <Card>
              <CardContent className="pt-2">
                <div className="flex items-center gap-2 text-muted-foreground">
                  <Clock className="h-4 w-4" />
                  <span className="text-xs font-medium uppercase tracking-wider">
                    Listening Hours
                  </span>
                </div>
                <p className="mt-1 text-2xl font-bold tabular-nums sm:text-3xl">
                  {profile.listening_hours_estimated.toFixed(1)}
                </p>
              </CardContent>
            </Card>

            <Card>
              <CardContent className="pt-2">
                <div className="flex items-center gap-2 text-muted-foreground">
                  <Compass className="h-4 w-4" />
                  <span className="text-xs font-medium uppercase tracking-wider">
                    Familiarity
                  </span>
                </div>
                <p className="mt-1 text-2xl font-bold tabular-nums sm:text-3xl">
                  {Math.round(profile.familiarity_score * 100)}%
                </p>
                <p className="text-xs text-muted-foreground">
                  {profile.familiarity_score < 0.3
                    ? "Focused listener"
                    : profile.familiarity_score < 0.7
                      ? "Balanced explorer"
                      : "Adventurous listener"}
                </p>
              </CardContent>
            </Card>

            <Card>
              <CardContent className="pt-2">
                <div className="flex items-center gap-2 text-muted-foreground">
                  <Plug className="h-4 w-4" />
                  <span className="text-xs font-medium uppercase tracking-wider">
                    Service
                  </span>
                </div>
                <p className="mt-1 text-lg font-bold capitalize sm:text-xl">
                  {profile.services_included.length > 0
                    ? profile.services_included
                        .join(", ")
                        .replace(/_/g, " ")
                    : profile.service.replace(/_/g, " ")}
                </p>
              </CardContent>
            </Card>
          </div>

          {/* Charts row 1: Genres + Top Artists */}
          <div className="grid gap-4 md:grid-cols-2">
            {/* Genre Distribution */}
            {genreData.length > 0 && (
              <Card>
                <CardContent className="pt-4">
                  <div className="mb-3 flex items-center gap-2 text-muted-foreground">
                    <BarChart3 className="h-4 w-4" />
                    <span className="text-xs font-medium uppercase tracking-wider">
                      Top Genres
                    </span>
                  </div>
                  <div className="flex items-center gap-4">
                    <ResponsiveContainer width="50%" height={200}>
                      <PieChart>
                        <Pie
                          data={genreData}
                          cx="50%"
                          cy="50%"
                          innerRadius={45}
                          outerRadius={80}
                          dataKey="value"
                          stroke="none"
                        >
                          {genreData.map((_, i) => (
                            <Cell
                              key={i}
                              fill={CHART_COLORS[i % CHART_COLORS.length]}
                            />
                          ))}
                        </Pie>
                        <Tooltip
                          contentStyle={{
                            background: "hsl(var(--card))",
                            border: "1px solid hsl(var(--border))",
                            borderRadius: "8px",
                            fontSize: "12px",
                          }}
                          formatter={(value) => [`${value}%`, ""]}
                        />
                      </PieChart>
                    </ResponsiveContainer>
                    <div className="flex-1 space-y-1.5">
                      {genreData.map((g, i) => (
                        <div key={g.name} className="flex items-center gap-2">
                          <div
                            className="h-2.5 w-2.5 shrink-0 rounded-full"
                            style={{
                              backgroundColor:
                                CHART_COLORS[i % CHART_COLORS.length],
                            }}
                          />
                          <span className="truncate text-xs text-muted-foreground">
                            {g.name}
                          </span>
                          <span className="ml-auto text-xs font-medium tabular-nums">
                            {g.value}%
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>
                </CardContent>
              </Card>
            )}

            {/* Top Artists */}
            {topArtists.length > 0 && (
              <Card>
                <CardContent className="pt-4">
                  <div className="mb-3 flex items-center gap-2 text-muted-foreground">
                    <Users className="h-4 w-4" />
                    <span className="text-xs font-medium uppercase tracking-wider">
                      Top Artists
                    </span>
                  </div>
                  <div className="space-y-2.5">
                    {topArtists.map((artist, i) => (
                      <div key={artist.name} className="flex items-center gap-3">
                        <span className="w-5 text-right text-xs font-medium text-muted-foreground tabular-nums">
                          {i + 1}
                        </span>
                        <div className="flex-1 min-w-0">
                          <p className="truncate text-sm font-medium">
                            {artist.name}
                          </p>
                          <p className="text-xs text-muted-foreground">
                            {artist.song_count} song{artist.song_count !== 1 ? "s" : ""}
                          </p>
                        </div>
                        {/* Affinity bar */}
                        <div className="w-20 sm:w-28">
                          <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
                            <div
                              className="h-full rounded-full bg-emerald-500 transition-all duration-500"
                              style={{ width: `${Math.round(artist.score * 100)}%` }}
                            />
                          </div>
                        </div>
                        <span className="w-10 text-right text-xs font-medium tabular-nums">
                          {Math.round(artist.score * 100)}%
                        </span>
                      </div>
                    ))}
                  </div>
                </CardContent>
              </Card>
            )}
          </div>

          {/* Charts row 2: Audio Traits + Release Years */}
          <div className="grid gap-4 md:grid-cols-2">
            {/* Audio Traits Radar */}
            {audioTraitData.length > 0 && (
              <Card>
                <CardContent className="pt-4">
                  <div className="mb-3 flex items-center gap-2 text-muted-foreground">
                    <Mic2 className="h-4 w-4" />
                    <span className="text-xs font-medium uppercase tracking-wider">
                      Audio Traits
                    </span>
                  </div>
                  <ResponsiveContainer width="100%" height={220}>
                    <RadarChart
                      cx="50%"
                      cy="50%"
                      outerRadius="70%"
                      data={audioTraitData}
                    >
                      <PolarGrid stroke="hsl(var(--border))" />
                      <PolarAngleAxis
                        dataKey="trait"
                        tick={{ fill: "hsl(var(--muted-foreground))", fontSize: 11 }}
                      />
                      <Radar
                        dataKey="value"
                        stroke="#10b981"
                        fill="#10b981"
                        fillOpacity={0.2}
                        strokeWidth={2}
                      />
                      <Tooltip
                        contentStyle={{
                          background: "hsl(var(--card))",
                          border: "1px solid hsl(var(--border))",
                          borderRadius: "8px",
                          fontSize: "12px",
                        }}
                        formatter={(v) => [`${v}%`, "Score"]}
                      />
                    </RadarChart>
                  </ResponsiveContainer>
                </CardContent>
              </Card>
            )}

            {/* Release Year Distribution */}
            {yearData.length > 0 && (
              <Card>
                <CardContent className="pt-4">
                  <div className="mb-3 flex items-center gap-2 text-muted-foreground">
                    <Calendar className="h-4 w-4" />
                    <span className="text-xs font-medium uppercase tracking-wider">
                      Release Years
                    </span>
                  </div>
                  <ResponsiveContainer width="100%" height={220}>
                    <BarChart
                      data={yearData}
                      margin={{ top: 5, right: 5, bottom: 5, left: -20 }}
                    >
                      <XAxis
                        dataKey="year"
                        tick={{ fill: "hsl(var(--muted-foreground))", fontSize: 10 }}
                        tickLine={false}
                        axisLine={false}
                        interval="preserveStartEnd"
                      />
                      <YAxis
                        tick={{ fill: "hsl(var(--muted-foreground))", fontSize: 10 }}
                        tickLine={false}
                        axisLine={false}
                        allowDecimals={false}
                      />
                      <Tooltip
                        contentStyle={{
                          background: "hsl(var(--card))",
                          border: "1px solid hsl(var(--border))",
                          borderRadius: "8px",
                          fontSize: "12px",
                        }}
                        formatter={(v) => [`${v} songs`, "Count"]}
                      />
                      <Bar
                        dataKey="songs"
                        fill="#10b981"
                        radius={[4, 4, 0, 0]}
                        maxBarSize={32}
                      />
                    </BarChart>
                  </ResponsiveContainer>
                </CardContent>
              </Card>
            )}
          </div>
        </>
      ) : null}

      {/* Tab navigation */}
      <div className="flex gap-1 border-b border-border">
        {tabs.map((tab) => {
          const isActive = pathname === tab.href;
          return (
            <Link
              key={tab.href}
              href={tab.href}
              className={`border-b-2 px-4 py-2.5 text-sm font-medium transition-colors ${
                isActive
                  ? "border-primary text-foreground"
                  : "border-transparent text-muted-foreground hover:text-foreground"
              }`}
            >
              {tab.label}
            </Link>
          );
        })}
      </div>
    </div>
  );
}
