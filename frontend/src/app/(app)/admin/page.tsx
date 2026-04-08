"use client";

import { useQuery } from "@tanstack/react-query";
import { useAuthStore } from "@/stores/auth-store";
import { apiFetch } from "@/lib/api";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Users,
  Music,
  Database,
  Activity,
  AlertTriangle,
  Clock,
  Zap,
  ExternalLink,
  Shield,
  Plug,
  Target,
  Globe,
} from "lucide-react";

// ── Types ────────────────────────────────────────────────

interface SystemStatus {
  version: string;
  users: number;
  connections: number;
  calibrated_users: number;
  total_songs: number;
  total_enriched: number;
  enrichment_pct: number;
  global_isrc_cache: number;
  listening_history_entries: number;
  soundstat_configured: boolean;
}

interface EnrichmentUser {
  user_id: string;
  email: string;
  display_name: string;
  total_songs: number;
  enriched_songs: number;
  percentage: number;
}

interface RequestStats {
  stats: {
    total_today: number;
    errors_today: number;
    avg_duration_ms: number;
    slow_requests_today: number;
  };
  source: string;
}

interface ErrorEntry {
  timestamp: string | null;
  method: string;
  path: string;
  status_code: number;
  duration_ms: number;
  user_id: string | null;
  error_detail: string | null;
}

interface ErrorsResponse {
  errors: ErrorEntry[];
  source: string;
}

// ── Hooks ────────────────────────────────────────────────

function useAdminStatus() {
  return useQuery<SystemStatus>({
    queryKey: ["admin", "status"],
    queryFn: () => apiFetch<SystemStatus>("/api/admin/status"),
    refetchInterval: 30_000,
  });
}

function useAdminProgress() {
  return useQuery<{ users: EnrichmentUser[] }>({
    queryKey: ["admin", "progress"],
    queryFn: () => apiFetch<{ users: EnrichmentUser[] }>("/api/admin/progress"),
    refetchInterval: 30_000,
  });
}

function useRequestStats() {
  return useQuery<RequestStats>({
    queryKey: ["admin", "request-stats"],
    queryFn: () => apiFetch<RequestStats>("/api/admin/request-stats"),
    refetchInterval: 30_000,
  });
}

function useRecentErrors() {
  return useQuery<ErrorsResponse>({
    queryKey: ["admin", "errors"],
    queryFn: () => apiFetch<ErrorsResponse>("/api/admin/errors?limit=20"),
    refetchInterval: 30_000,
  });
}

// ── Components ───────────────────────────────────────────

function StatCard({
  label,
  value,
  icon: Icon,
  sub,
}: {
  label: string;
  value: string | number;
  icon: React.ComponentType<{ className?: string }>;
  sub?: string;
}) {
  return (
    <Card>
      <CardContent className="pt-3">
        <div className="flex items-center gap-2 text-muted-foreground mb-1">
          <Icon className="h-4 w-4" />
          <span className="text-xs font-medium uppercase tracking-wider">
            {label}
          </span>
        </div>
        <p className="text-2xl font-bold tabular-nums">{value}</p>
        {sub && <p className="text-xs text-muted-foreground mt-0.5">{sub}</p>}
      </CardContent>
    </Card>
  );
}

// ── Page ─────────────────────────────────────────────────

export default function AdminDashboard() {
  const { user } = useAuthStore();
  const isAdmin = user?.is_admin ?? false;

  const { data: status, isLoading: statusLoading } = useAdminStatus();
  const { data: progress } = useAdminProgress();
  const { data: requestStats } = useRequestStats();
  const { data: errorsData } = useRecentErrors();

  if (!isAdmin) {
    return (
      <div className="flex min-h-[50vh] items-center justify-center">
        <Card className="max-w-sm">
          <CardContent className="flex flex-col items-center gap-3 py-8 text-center">
            <Shield className="h-10 w-10 text-muted-foreground" />
            <p className="text-lg font-medium">Admin access required</p>
          </CardContent>
        </Card>
      </div>
    );
  }

  const nocodbUrl = "https://admin.music.menghi.dev";
  const stats = requestStats?.stats;
  const errors = errorsData?.errors || [];

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Admin Dashboard</h1>
          {status && (
            <p className="text-sm text-muted-foreground mt-0.5">
              SmarTaste v{status.version}
            </p>
          )}
        </div>
        <a href={nocodbUrl} target="_blank" rel="noopener noreferrer">
          <Button variant="outline" size="sm">
            <Database className="h-3.5 w-3.5 mr-1.5" />
            NocoDB
            <ExternalLink className="h-3 w-3 ml-1.5" />
          </Button>
        </a>
      </div>

      {/* Stat cards */}
      {statusLoading ? (
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          {Array.from({ length: 8 }).map((_, i) => (
            <Skeleton key={i} className="h-20 rounded-lg" />
          ))}
        </div>
      ) : status ? (
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <StatCard
            label="Users"
            value={status.users}
            icon={Users}
            sub={`${status.calibrated_users} calibrated`}
          />
          <StatCard
            label="Connections"
            value={status.connections}
            icon={Plug}
          />
          <StatCard
            label="Songs Cached"
            value={status.total_songs.toLocaleString()}
            icon={Music}
            sub={`${status.total_enriched.toLocaleString()} enriched (${status.enrichment_pct}%)`}
          />
          <StatCard
            label="Global ISRC Cache"
            value={status.global_isrc_cache.toLocaleString()}
            icon={Globe}
            sub="Shared across users"
          />
          <StatCard
            label="History Entries"
            value={status.listening_history_entries.toLocaleString()}
            icon={Activity}
          />
          <StatCard
            label="Requests Today"
            value={stats?.total_today?.toLocaleString() || "—"}
            icon={Zap}
            sub={stats ? `avg ${stats.avg_duration_ms}ms` : ""}
          />
          <StatCard
            label="Errors Today"
            value={stats?.errors_today || 0}
            icon={AlertTriangle}
            sub={stats?.slow_requests_today ? `${stats.slow_requests_today} slow (>5s)` : ""}
          />
          <StatCard
            label="Enrichment"
            value={`${status.enrichment_pct}%`}
            icon={Target}
            sub={status.soundstat_configured ? "SoundStat active" : "Free tier only"}
          />
        </div>
      ) : null}

      {/* Per-user enrichment progress */}
      {progress?.users && progress.users.length > 0 && (
        <Card>
          <CardContent className="pt-4">
            <h2 className="text-sm font-medium uppercase tracking-wider text-muted-foreground mb-3">
              Per-User Enrichment
            </h2>
            <div className="space-y-3">
              {progress.users.map((u) => (
                <div key={u.user_id} className="flex items-center gap-3">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-sm font-medium truncate">
                        {u.display_name}
                      </span>
                      <span className="text-xs font-mono text-muted-foreground">
                        {u.enriched_songs}/{u.total_songs}
                      </span>
                    </div>
                    <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
                      <div
                        className="h-full rounded-full bg-purple-500 transition-all"
                        style={{ width: `${Math.min(100, u.percentage)}%` }}
                      />
                    </div>
                  </div>
                  <Badge
                    variant="outline"
                    className={
                      u.percentage >= 100
                        ? "bg-green-500/10 text-green-400 border-green-500/20"
                        : u.percentage > 50
                          ? "bg-purple-500/10 text-purple-400 border-purple-500/20"
                          : "text-muted-foreground"
                    }
                  >
                    {u.percentage}%
                  </Badge>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      {/* Recent errors */}
      <Card>
        <CardContent className="pt-4">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-medium uppercase tracking-wider text-muted-foreground">
              Recent Errors
            </h2>
            {errorsData?.source === "unavailable" && (
              <Badge variant="outline" className="text-amber-400 border-amber-500/20">
                Logs DB not connected
              </Badge>
            )}
          </div>
          {errors.length === 0 ? (
            <p className="text-sm text-muted-foreground text-center py-4">
              No errors recorded
            </p>
          ) : (
            <div className="space-y-1.5 max-h-[40vh] overflow-y-auto">
              {errors.map((err, i) => (
                <div
                  key={i}
                  className="flex items-center gap-3 rounded-lg bg-red-500/5 border border-red-500/10 px-3 py-2 text-xs"
                >
                  <span className="font-mono text-red-400 shrink-0">
                    {err.status_code}
                  </span>
                  <span className="font-medium truncate">
                    {err.method} {err.path}
                  </span>
                  <span className="text-muted-foreground shrink-0">
                    <Clock className="h-3 w-3 inline mr-0.5" />
                    {err.duration_ms}ms
                  </span>
                  {err.timestamp && (
                    <span className="text-muted-foreground shrink-0 ml-auto">
                      {new Date(err.timestamp).toLocaleTimeString()}
                    </span>
                  )}
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
