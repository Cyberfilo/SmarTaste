"use client";

/**
 * Admin dashboard — enrichment pipeline monitoring, per-song + per-artist
 * drill-down, worker heartbeat, diagnostic insights. Auto-refreshes every 30s.
 * Restricted to users with is_admin flag.
 */

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  AlertTriangle,
  Database,
  Loader2,
  Music,
  Shield,
  ShieldAlert,
  Sparkles,
  Users,
} from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { useAuthStore } from "@/stores/auth-store";
import { apiFetch } from "@/lib/api";
import { SongsTableCard } from "@/components/admin/songs-table-card";
import { ArtistsTableCard } from "@/components/admin/artists-table-card";

// ── Types ────────────────────────────────────────────────────

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
}

interface WorkerHeartbeat {
  phase: string | null;
  detail: string | null;
  progress_current: number | null;
  progress_total: number | null;
  cycle: number | null;
  updated_at: string | null;
  started_at: string | null;
}

interface WorkerStatus {
  available: boolean;
  heartbeat: WorkerHeartbeat | null;
  last_activity: string | null;
  enriched_today: number;
  failures_today: number;
  stages_today: Record<string, number>;
  reason?: string;
}

interface DiagnosticInsight {
  level: "info" | "warning" | "critical";
  user: string;
  message: string;
}

interface DiagnosticsResponse {
  users: Array<Record<string, unknown>>;
  insights: DiagnosticInsight[];
  totals: Record<string, number>;
  failure_analysis?: {
    today: Array<{ stage: string; result: string; count: number }>;
    recent_errors: Array<{
      level: string;
      logger: string;
      message: string;
      timestamp: string | null;
    }>;
  };
}

// ── Helpers ──────────────────────────────────────────────────

const REFRESH_INTERVAL = 30_000;

function timeAgo(iso: string | null | undefined): string {
  if (!iso) return "never";
  const diff = Date.now() - new Date(iso).getTime();
  if (diff < 60_000) return `${Math.round(diff / 1000)}s ago`;
  if (diff < 3_600_000) return `${Math.round(diff / 60_000)}m ago`;
  if (diff < 86_400_000) return `${Math.round(diff / 3_600_000)}h ago`;
  return `${Math.round(diff / 86_400_000)}d ago`;
}

// ── Component ────────────────────────────────────────────────

export default function AdminPage() {
  const router = useRouter();
  const { user, isLoading: authLoading } = useAuthStore();

  // Redirect non-admins
  useEffect(() => {
    if (!authLoading && user && !user.is_admin) {
      router.replace("/dashboard");
    }
  }, [authLoading, user, router]);

  const { data: status, isLoading: statusLoading } = useQuery<SystemStatus>({
    queryKey: ["admin-status"],
    queryFn: () => apiFetch("/api/admin/status"),
    enabled: !!user?.is_admin,
    refetchInterval: REFRESH_INTERVAL,
    staleTime: REFRESH_INTERVAL - 5000,
  });

  const { data: worker, isLoading: workerLoading } = useQuery<WorkerStatus>({
    queryKey: ["admin-worker"],
    queryFn: () => apiFetch("/api/admin/worker-status"),
    enabled: !!user?.is_admin,
    refetchInterval: REFRESH_INTERVAL,
    staleTime: REFRESH_INTERVAL - 5000,
  });

  const { data: diagnostics } = useQuery<DiagnosticsResponse>({
    queryKey: ["admin-diagnostics"],
    queryFn: () => apiFetch("/api/admin/diagnostics"),
    enabled: !!user?.is_admin,
    refetchInterval: REFRESH_INTERVAL,
    staleTime: REFRESH_INTERVAL - 5000,
  });

  // Gate: don't render until auth is resolved
  if (authLoading) {
    return (
      <div className="flex items-center justify-center py-20">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (!user?.is_admin) {
    return null;
  }

  const isLoading = statusLoading || workerLoading;

  return (
    <div className="space-y-6">
      {/* Page header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Shield className="h-6 w-6 text-purple-400" />
          <div>
            <h1 className="text-2xl font-bold tracking-tight">
              Admin Dashboard
            </h1>
            <p className="text-sm text-muted-foreground">
              Enrichment pipeline monitoring
              {status && (
                <span className="ml-2 text-xs opacity-60">
                  v{status.version}
                </span>
              )}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-green-400" />
          Auto-refresh 30s
        </div>
      </div>

      {/* ── 1. System Overview Bar ──────────────────────────── */}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-5 lg:gap-4">
        <OverviewCard
          icon={Users}
          label="Users"
          value={status?.users}
          sub={status ? `${status.calibrated_users} calibrated` : undefined}
          loading={isLoading}
        />
        <OverviewCard
          icon={Music}
          label="Total Songs"
          value={status?.total_songs}
          sub={
            status
              ? `${status.global_isrc_cache.toLocaleString()} ISRC cache`
              : undefined
          }
          loading={isLoading}
        />
        <OverviewCard
          icon={Database}
          label="Enriched"
          value={status ? `${status.enrichment_pct}%` : undefined}
          sub={
            status ? `${status.total_enriched.toLocaleString()} songs` : undefined
          }
          loading={isLoading}
        />
        <OverviewCard
          icon={Activity}
          label="Worker"
          value={
            worker?.heartbeat?.phase
              ? worker.heartbeat.phase.replace(/_/g, " ")
              : worker?.available
                ? "idle"
                : "offline"
          }
          sub={
            worker?.heartbeat?.cycle != null
              ? `Cycle ${worker.heartbeat.cycle}`
              : worker?.last_activity
                ? timeAgo(worker.last_activity)
                : undefined
          }
          loading={workerLoading}
          badge={
            worker?.heartbeat?.phase
              ? "running"
              : worker?.available
                ? "idle"
                : "offline"
          }
        />
        <OverviewCard
          icon={Sparkles}
          label="Today"
          value={worker?.enriched_today}
          sub={
            worker?.failures_today
              ? `${worker.failures_today} failures`
              : "0 failures"
          }
          loading={workerLoading}
        />
      </div>

      {/* ── 2. Songs Table (per-song enrichment grid) ───────── */}
      <SongsTableCard />

      {/* ── 3. Artists Table (source + library/discovery ratio) */}
      <ArtistsTableCard />

      {/* ── 4. Insights Panel ──────────────────────────────── */}
      {diagnostics && diagnostics.insights.length > 0 && (
        <Card>
          <CardHeader className="border-b">
            <CardTitle className="flex items-center gap-2 text-sm">
              <AlertTriangle className="h-4 w-4 text-amber-400" />
              Diagnostic Insights
              <Badge
                variant="secondary"
                className="ml-auto bg-muted text-muted-foreground"
              >
                {diagnostics.insights.length}
              </Badge>
            </CardTitle>
          </CardHeader>
          <CardContent className="pt-3">
            <div className="space-y-2 max-h-80 overflow-y-auto">
              {diagnostics.insights.map((insight, i) => (
                <InsightRow key={i} insight={insight} />
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      {/* ── 5. Failure Analysis (from logs DB) ─────────────── */}
      {diagnostics?.failure_analysis &&
        diagnostics.failure_analysis.today.length > 0 && (
          <Card>
            <CardHeader className="border-b">
              <CardTitle className="flex items-center gap-2 text-sm">
                <ShieldAlert className="h-4 w-4 text-red-400" />
                Failure Analysis (Today)
              </CardTitle>
            </CardHeader>
            <CardContent className="pt-3">
              <div className="space-y-1.5">
                {diagnostics.failure_analysis.today.map((f, i) => (
                  <div
                    key={i}
                    className="flex items-center justify-between rounded-md bg-red-500/5 px-3 py-2 text-xs"
                  >
                    <div className="flex items-center gap-2">
                      <span className="font-medium text-red-400">{f.stage}</span>
                      <span className="text-muted-foreground">{f.result}</span>
                    </div>
                    <span className="font-mono font-medium tabular-nums text-red-300">
                      {f.count}x
                    </span>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>
        )}
    </div>
  );
}

// ── Sub-components ───────────────────────────────────────────

function OverviewCard({
  icon: Icon,
  label,
  value,
  sub,
  loading,
  badge,
}: {
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  value: string | number | undefined;
  sub?: string;
  loading: boolean;
  badge?: "running" | "idle" | "offline";
}) {
  return (
    <Card>
      <CardContent className="pt-2">
        {loading ? (
          <div className="animate-pulse space-y-2">
            <div className="h-3 w-16 rounded bg-muted" />
            <div className="h-7 w-20 rounded bg-muted" />
          </div>
        ) : (
          <>
            <div className="flex items-center gap-2 text-muted-foreground">
              <Icon className="h-4 w-4" />
              <span className="text-xs font-medium uppercase tracking-wider">
                {label}
              </span>
            </div>
            <div className="mt-1 flex items-center gap-2">
              <p className="text-xl font-bold tabular-nums sm:text-2xl">
                {typeof value === "number"
                  ? value.toLocaleString()
                  : (value ?? "-")}
              </p>
              {badge && (
                <span
                  className={`inline-block h-2 w-2 rounded-full ${
                    badge === "running"
                      ? "bg-green-400 animate-pulse"
                      : badge === "idle"
                        ? "bg-amber-400"
                        : "bg-red-400"
                  }`}
                />
              )}
            </div>
            {sub && <p className="text-xs text-muted-foreground">{sub}</p>}
          </>
        )}
      </CardContent>
    </Card>
  );
}

function InsightRow({ insight }: { insight: DiagnosticInsight }) {
  const colorMap = {
    critical: {
      bg: "bg-red-500/10",
      border: "border-red-500/20",
      dot: "bg-red-400",
    },
    warning: {
      bg: "bg-amber-500/10",
      border: "border-amber-500/20",
      dot: "bg-amber-400",
    },
    info: {
      bg: "bg-blue-500/10",
      border: "border-blue-500/20",
      dot: "bg-blue-400",
    },
  };
  const c = colorMap[insight.level] || colorMap.info;

  return (
    <div
      className={`flex items-start gap-3 rounded-md border px-3 py-2.5 ${c.bg} ${c.border}`}
    >
      <span
        className={`mt-0.5 inline-block h-2 w-2 shrink-0 rounded-full ${c.dot}`}
      />
      <div className="min-w-0 flex-1">
        <span className="text-xs font-medium text-muted-foreground">
          {insight.user}
        </span>
        <p className="text-xs leading-relaxed">{insight.message}</p>
      </div>
    </div>
  );
}
