"use client";

/**
 * Dashboard overview page.
 * Shows summary cards (songs analyzed, listening hours, familiarity, service)
 * with tab navigation to Taste Profile and Listening Stats sub-pages.
 */

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Music, Clock, Compass, Plug } from "lucide-react";
import { toast } from "sonner";
import { useEffect } from "react";
import { Card, CardContent } from "@/components/ui/card";
import { useTasteProfile } from "@/hooks/use-taste";

const tabs = [
  { href: "/dashboard/taste", label: "Taste Profile" },
  { href: "/dashboard/stats", label: "Listening Stats" },
  { href: "/dashboard/recommendations", label: "Recommendations" },
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

  // Empty state: no profile data
  const isEmpty = !isLoading && !error && !profile;

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
                  ? profile.services_included.join(", ").replace(/_/g, " ")
                  : profile.service.replace(/_/g, " ")}
              </p>
            </CardContent>
          </Card>
        </div>
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
