"use client";

/**
 * Admin dashboard — enrichment pipeline monitoring, per-user stats,
 * worker status, and diagnostic insights. Auto-refreshes every 30s.
 * Restricted to users with is_admin flag.
 */

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  AlertTriangle,
  Brain,
  Cpu,
  Database,
  Loader2,
  Music,
  Shield,
  ShieldAlert,
  Sparkles,
  Tags,
  Users,
  Waves,
} from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { useAuthStore } from "@/stores/auth-store";
import { apiFetch } from "@/lib/api";

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
  recent: Array<{
    timestamp: string | null;
    user_id: string | null;
    catalog_id: string;
    stage: string;
    result: string;
    duration_ms: number | null;
  }>;
  reason?: string;
}

interface EnrichmentBreakdown {
  total: number;
  unenriched: number;
  unenriched_pct: number;
  partial: number;
  partial_pct: number;
  fully_enriched: number;
  fully_enriched_pct: number;
  stages: {
    audio_features: number;
    effnet_embeddings: number;
    gpu_embeddings: number;
    ai_captions: number;
    classifier_labels: number;
  };
}

interface UserProgress {
  user_id: string;
  email: string;
  display_name: string;
  library_songs: number;
  worker_songs: number;
  total_songs: number;
  enriched_songs: number;
  effnet_embeddings: number;
  clap_embeddings: number;
  mert_embeddings: number;
  ai_captions: number;
  ai_tags: number;
  library_artists: number;
  discovered_artists: number;
  unique_artists: number;
  percentage: number;
  indexing: {
    step: number;
    step_name: string;
    progress_current: number;
    progress_total: number;
  } | null;
  cobweb_total: number;
  cobweb_enriched: number;
}

interface ProgressResponse {
  users: UserProgress[];
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

function pct(n: number, total: number): number {
  return total > 0 ? Math.round((n / total) * 1000) / 10 : 0;
}

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

  const {
    data: status,
    isLoading: statusLoading,
  } = useQuery<SystemStatus>({
    queryKey: ["admin-status"],
    queryFn: () => apiFetch("/api/admin/status"),
    enabled: !!user?.is_admin,
    refetchInterval: REFRESH_INTERVAL,
    staleTime: REFRESH_INTERVAL - 5000,
  });

  const {
    data: worker,
    isLoading: workerLoading,
  } = useQuery<WorkerStatus>({
    queryKey: ["admin-worker"],
    queryFn: () => apiFetch("/api/admin/worker-status"),
    enabled: !!user?.is_admin,
    refetchInterval: REFRESH_INTERVAL,
    staleTime: REFRESH_INTERVAL - 5000,
  });

  const {
    data: breakdown,
    isLoading: breakdownLoading,
  } = useQuery<EnrichmentBreakdown>({
    queryKey: ["admin-breakdown"],
    queryFn: () => apiFetch("/api/admin/enrichment-breakdown"),
    enabled: !!user?.is_admin,
    refetchInterval: REFRESH_INTERVAL,
    staleTime: REFRESH_INTERVAL - 5000,
  });

  const {
    data: progress,
    isLoading: progressLoading,
  } = useQuery<ProgressResponse>({
    queryKey: ["admin-progress"],
    queryFn: () => apiFetch("/api/admin/progress"),
    enabled: !!user?.is_admin,
    refetchInterval: REFRESH_INTERVAL,
    staleTime: REFRESH_INTERVAL - 5000,
  });

  const {
    data: diagnostics,
    isLoading: diagnosticsLoading,
  } = useQuery<DiagnosticsResponse>({
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

  const isLoading = statusLoading || workerLoading || breakdownLoading;

  return (
    <div className="space-y-6">
      {/* Page header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Shield className="h-6 w-6 text-purple-400" />
          <div>
            <h1 className="text-2xl font-bold tracking-tight">Admin Dashboard</h1>
            <p className="text-sm text-muted-foreground">
              Enrichment pipeline monitoring
              {status && (
                <span className="ml-2 text-xs opacity-60">v{status.version}</span>
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
          sub={status ? `${status.global_isrc_cache.toLocaleString()} ISRC cache` : undefined}
          loading={isLoading}
        />
        <OverviewCard
          icon={Database}
          label="Enriched"
          value={status ? `${status.enrichment_pct}%` : undefined}
          sub={status ? `${status.total_enriched.toLocaleString()} songs` : undefined}
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

      {/* ── 2. Pipeline Stages Card ────────────────────────── */}
      <Card>
        <CardHeader className="border-b">
          <CardTitle className="flex items-center gap-2 text-sm">
            <Cpu className="h-4 w-4 text-purple-400" />
            Pipeline Stages
            {breakdown && (
              <span className="ml-auto text-xs font-normal text-muted-foreground">
                {breakdown.fully_enriched.toLocaleString()} fully enriched / {breakdown.total.toLocaleString()} total
              </span>
            )}
          </CardTitle>
        </CardHeader>
        <CardContent className="pt-4">
          {breakdownLoading ? (
            <div className="space-y-4">
              {[1, 2, 3, 4, 5].map((i) => (
                <div key={i} className="animate-pulse">
                  <div className="mb-1 h-3 w-40 rounded bg-muted" />
                  <div className="h-2 w-full rounded-full bg-muted" />
                </div>
              ))}
            </div>
          ) : breakdown ? (
            <div className="space-y-4">
              <PipelineStage
                icon={Waves}
                label="Audio Features (Essentia scalars)"
                count={breakdown.stages.audio_features}
                total={breakdown.total}
                color="bg-purple-500"
              />
              <PipelineStage
                icon={Brain}
                label="EffNet Embeddings (1280-dim)"
                count={breakdown.stages.effnet_embeddings}
                total={breakdown.total}
                color="bg-blue-500"
              />
              <PipelineStage
                icon={Cpu}
                label="GPU Embeddings (CLAP + MERT)"
                count={breakdown.stages.gpu_embeddings}
                total={breakdown.total}
                color="bg-cyan-500"
              />
              <PipelineStage
                icon={Sparkles}
                label="AI Captions (OpenAI)"
                count={breakdown.stages.ai_captions}
                total={breakdown.total}
                color="bg-pink-500"
              />
              <PipelineStage
                icon={Tags}
                label="Classifier Labels (Essentia heads)"
                count={breakdown.stages.classifier_labels}
                total={breakdown.total}
                color="bg-amber-500"
              />
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">No data available</p>
          )}
        </CardContent>
      </Card>

      {/* ── 3. Per-User Table ──────────────────────────────── */}
      <Card>
        <CardHeader className="border-b">
          <CardTitle className="flex items-center gap-2 text-sm">
            <Users className="h-4 w-4 text-purple-400" />
            Per-User Enrichment
          </CardTitle>
        </CardHeader>
        <CardContent className="pt-0 px-0">
          {progressLoading ? (
            <div className="p-4 space-y-3">
              {[1, 2, 3].map((i) => (
                <div key={i} className="h-10 animate-pulse rounded bg-muted" />
              ))}
            </div>
          ) : progress?.users.length ? (
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-border text-left text-muted-foreground">
                    <th className="px-4 py-2.5 font-medium">User</th>
                    <th className="px-3 py-2.5 font-medium text-right">Library</th>
                    <th className="px-3 py-2.5 font-medium text-right">Total</th>
                    <th className="px-3 py-2.5 font-medium text-right">Audio</th>
                    <th className="px-3 py-2.5 font-medium text-right">EffNet</th>
                    <th className="px-3 py-2.5 font-medium text-right">CLAP</th>
                    <th className="px-3 py-2.5 font-medium text-right">MERT</th>
                    <th className="px-3 py-2.5 font-medium text-right">Captions</th>
                    <th className="px-3 py-2.5 font-medium text-right">Tags</th>
                    <th className="px-3 py-2.5 font-medium text-center">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {progress.users.map((u) => (
                    <tr
                      key={u.user_id}
                      className="border-b border-border/50 hover:bg-muted/30 transition-colors"
                    >
                      <td className="px-4 py-2.5">
                        <div>
                          <span className="font-medium text-sm">
                            {u.display_name}
                          </span>
                          <span className="block text-muted-foreground">
                            {u.email}
                          </span>
                        </div>
                      </td>
                      <td className="px-3 py-2.5 text-right tabular-nums">
                        {u.library_songs.toLocaleString()}
                      </td>
                      <td className="px-3 py-2.5 text-right tabular-nums">
                        {u.total_songs.toLocaleString()}
                      </td>
                      <td className="px-3 py-2.5 text-right">
                        <CellWithPct value={u.enriched_songs} total={u.total_songs} />
                      </td>
                      <td className="px-3 py-2.5 text-right">
                        <CellWithPct value={u.effnet_embeddings} total={u.total_songs} />
                      </td>
                      <td className="px-3 py-2.5 text-right">
                        <CellWithPct value={u.clap_embeddings} total={u.total_songs} />
                      </td>
                      <td className="px-3 py-2.5 text-right">
                        <CellWithPct value={u.mert_embeddings} total={u.total_songs} />
                      </td>
                      <td className="px-3 py-2.5 text-right">
                        <CellWithPct value={u.ai_captions} total={u.total_songs} />
                      </td>
                      <td className="px-3 py-2.5 text-right">
                        <CellWithPct value={u.ai_tags} total={u.total_songs} />
                      </td>
                      <td className="px-3 py-2.5 text-center">
                        {u.indexing ? (
                          <div className="inline-flex items-center gap-1.5">
                            <Loader2 className="h-3 w-3 animate-spin text-purple-400" />
                            <span className="text-purple-300">
                              {u.indexing.step_name || `Step ${u.indexing.step}`}
                            </span>
                          </div>
                        ) : u.percentage >= 100 ? (
                          <Badge variant="secondary" className="bg-green-500/15 text-green-400 border-green-500/20">
                            Complete
                          </Badge>
                        ) : u.percentage > 0 ? (
                          <Badge variant="secondary" className="bg-purple-500/15 text-purple-300 border-purple-500/20">
                            {u.percentage}%
                          </Badge>
                        ) : (
                          <Badge variant="secondary" className="bg-muted text-muted-foreground">
                            Pending
                          </Badge>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="p-4 text-sm text-muted-foreground">No user data available</p>
          )}
        </CardContent>
      </Card>

      {/* ── 4. Insights Panel ──────────────────────────────── */}
      {diagnostics && diagnostics.insights.length > 0 && (
        <Card>
          <CardHeader className="border-b">
            <CardTitle className="flex items-center gap-2 text-sm">
              <AlertTriangle className="h-4 w-4 text-amber-400" />
              Diagnostic Insights
              <Badge variant="secondary" className="ml-auto bg-muted text-muted-foreground">
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

      {/* ── 6. Recent Worker Activity ──────────────────────── */}
      {worker?.recent && worker.recent.length > 0 && (
        <Card>
          <CardHeader className="border-b">
            <CardTitle className="flex items-center gap-2 text-sm">
              <Activity className="h-4 w-4 text-purple-400" />
              Recent Worker Activity
            </CardTitle>
          </CardHeader>
          <CardContent className="pt-0 px-0">
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-border text-left text-muted-foreground">
                    <th className="px-4 py-2.5 font-medium">Time</th>
                    <th className="px-3 py-2.5 font-medium">Stage</th>
                    <th className="px-3 py-2.5 font-medium">Song ID</th>
                    <th className="px-3 py-2.5 font-medium">Result</th>
                    <th className="px-3 py-2.5 font-medium text-right">Duration</th>
                  </tr>
                </thead>
                <tbody>
                  {worker.recent.map((r, i) => (
                    <tr
                      key={i}
                      className="border-b border-border/50 hover:bg-muted/30 transition-colors"
                    >
                      <td className="px-4 py-2 text-muted-foreground">
                        {timeAgo(r.timestamp)}
                      </td>
                      <td className="px-3 py-2 font-medium">
                        {r.stage}
                      </td>
                      <td className="px-3 py-2 font-mono text-muted-foreground">
                        {r.catalog_id.length > 12
                          ? r.catalog_id.slice(0, 12) + "..."
                          : r.catalog_id}
                      </td>
                      <td className="px-3 py-2">
                        {r.result === "success" ? (
                          <span className="text-green-400">success</span>
                        ) : (
                          <span className="text-red-400">{r.result}</span>
                        )}
                      </td>
                      <td className="px-3 py-2 text-right tabular-nums text-muted-foreground">
                        {r.duration_ms != null ? `${r.duration_ms}ms` : "-"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
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
                {typeof value === "number" ? value.toLocaleString() : value ?? "-"}
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
            {sub && (
              <p className="text-xs text-muted-foreground">{sub}</p>
            )}
          </>
        )}
      </CardContent>
    </Card>
  );
}

function PipelineStage({
  icon: Icon,
  label,
  count,
  total,
  color,
}: {
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  count: number;
  total: number;
  color: string;
}) {
  const percentage = pct(count, total);
  return (
    <div>
      <div className="mb-1.5 flex items-center justify-between text-xs">
        <div className="flex items-center gap-2">
          <Icon className="h-3.5 w-3.5 text-muted-foreground" />
          <span className="font-medium">{label}</span>
        </div>
        <span className="tabular-nums text-muted-foreground">
          {count.toLocaleString()} / {total.toLocaleString()}
          <span className="ml-1.5 font-medium text-foreground">{percentage}%</span>
        </span>
      </div>
      <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
        <div
          className={`h-full rounded-full transition-all duration-700 ${color}`}
          style={{ width: `${Math.min(percentage, 100)}%` }}
        />
      </div>
    </div>
  );
}

function CellWithPct({ value, total }: { value: number; total: number }) {
  const percentage = pct(value, total);
  const isComplete = percentage >= 100;
  const isZero = value === 0;
  return (
    <div className="tabular-nums">
      <span className={isComplete ? "text-green-400" : isZero ? "text-muted-foreground" : ""}>
        {value.toLocaleString()}
      </span>
      <span className="ml-1 text-muted-foreground/60">
        ({percentage}%)
      </span>
    </div>
  );
}

function InsightRow({ insight }: { insight: DiagnosticInsight }) {
  const colorMap = {
    critical: {
      bg: "bg-red-500/10",
      border: "border-red-500/20",
      icon: "text-red-400",
      dot: "bg-red-400",
    },
    warning: {
      bg: "bg-amber-500/10",
      border: "border-amber-500/20",
      icon: "text-amber-400",
      dot: "bg-amber-400",
    },
    info: {
      bg: "bg-blue-500/10",
      border: "border-blue-500/20",
      icon: "text-blue-400",
      dot: "bg-blue-400",
    },
  };
  const c = colorMap[insight.level] || colorMap.info;

  return (
    <div className={`flex items-start gap-3 rounded-md border px-3 py-2.5 ${c.bg} ${c.border}`}>
      <span className={`mt-0.5 inline-block h-2 w-2 shrink-0 rounded-full ${c.dot}`} />
      <div className="min-w-0 flex-1">
        <span className="text-xs font-medium text-muted-foreground">
          {insight.user}
        </span>
        <p className="text-xs leading-relaxed">
          {insight.message}
        </p>
      </div>
    </div>
  );
}
