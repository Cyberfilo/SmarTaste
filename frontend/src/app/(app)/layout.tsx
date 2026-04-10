"use client";

import { useEffect } from "react";
import { useRouter, usePathname } from "next/navigation";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { useAuthStore } from "@/stores/auth-store";
import { authApi, apiFetch } from "@/lib/api";
import { useCalibrationStatus } from "@/hooks/use-calibration";
import { useServices } from "@/hooks/use-services";
import { Button } from "@/components/ui/button";
import {
  LayoutDashboard,
  ListMusic,
  MessageCircle,
  Settings,
  LogOut,
  Sparkles,
  Loader2,
} from "lucide-react";

/**
 * Protected app layout with navigation shell.
 * Shows the shell immediately with skeleton content while auth loads.
 * Mobile: bottom tab bar (fixed). Desktop lg+: sidebar (fixed left, w-64).
 */
export default function AppLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const { user, isLoading, isAuthenticated, checkAuth, clearUser } =
    useAuthStore();
  const isChat = pathname === "/chat";

  // Poll enrichment progress — only show for active indexing (calibration)
  const { data: enrichmentData } = useQuery<{
    total_songs: number;
    enriched_songs: number;
    percentage: number;
    complete: boolean;
    indexing: boolean;
  }>({
    queryKey: ["enrichment-status"],
    queryFn: () => apiFetch("/api/taste/enrichment-status"),
    enabled: isAuthenticated,
    refetchInterval: 30_000, // 30s — only for showing indexing bar
    staleTime: 25_000,
  });

  // Only show enrichment bar when actively indexing (calibration top artist enrichment)
  // Worker enrichment is invisible to the user
  const enrichmentActive = enrichmentData?.indexing ?? false;

  // Redirect to onboarding if user has a service connected but hasn't calibrated
  const { data: calibrationStatus } = useCalibrationStatus();
  const { data: servicesData } = useServices();
  const isOnboarding = pathname === "/onboarding";
  const isSettings = pathname === "/settings";

  useEffect(() => {
    if (!isAuthenticated || isOnboarding || isSettings) return;
    const hasConnected = servicesData?.services.some(
      (s) => s.status === "connected"
    );
    if (hasConnected && calibrationStatus && !calibrationStatus.completed) {
      router.replace("/onboarding");
    }
  }, [isAuthenticated, isOnboarding, isSettings, calibrationStatus, servicesData, router]);

  // Trigger /me for background enrichment — only when actively indexing
  useEffect(() => {
    if (!enrichmentActive) return;
    const interval = setInterval(() => {
      authApi.me().catch(() => {});
    }, 60_000); // 60s, not 30s
    return () => clearInterval(interval);
  }, [enrichmentActive]);

  useEffect(() => {
    checkAuth();
  }, [checkAuth]);

  async function handleLogout() {
    try {
      await authApi.logout();
    } catch {
      // Even if logout API fails, clear local state
    }
    clearUser();
    router.push("/login");
  }

  // Redirect to login if not authenticated (after loading completes)
  useEffect(() => {
    if (!isLoading && !isAuthenticated) {
      router.replace("/login");
    }
  }, [isLoading, isAuthenticated, router]);

  if (!isLoading && !isAuthenticated) {
    return null;
  }

  const navItems = [
    { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
    { href: "/recommendations", label: "Suggested", icon: Sparkles },
    { href: "/playlists", label: "Playlists", icon: ListMusic },
    { href: "/chat", label: "Chat", icon: MessageCircle },
    { href: "/settings", label: "Settings", icon: Settings },
  ];

  return (
    <div className="flex min-h-screen flex-col lg:flex-row">
      {/* Desktop sidebar — always visible, even during auth loading */}
      <aside className="hidden lg:flex lg:w-64 lg:flex-col lg:fixed lg:inset-y-0 border-r border-border bg-card">
        <div className="flex h-16 items-center gap-2.5 px-6 border-b border-border">
          <Link href="/dashboard" className="flex items-center gap-2.5">
            <img src="/logo-white.svg" alt="SmarTaste" className="h-7 w-7" />
            <span className="text-xl font-bold tracking-tight" style={{ fontFamily: "var(--font-heading)" }}>
              <span className="text-purple-500">Smar</span>Taste
            </span>
          </Link>
        </div>
        <nav className="flex-1 space-y-1 px-3 py-4">
          {navItems.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              className="flex items-center gap-3 rounded-lg px-3 py-2 text-sm text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground"
            >
              <item.icon className="h-4 w-4" />
              {item.label}
            </Link>
          ))}
        </nav>
        <div className="border-t border-border p-4">
          <div className="flex items-center justify-between">
            <div className="min-w-0">
              {isLoading ? (
                <div className="h-4 w-24 rounded bg-muted animate-pulse" />
              ) : (
                <>
                  <p className="truncate text-sm font-medium">
                    {user?.display_name || user?.email || "User"}
                  </p>
                  {user?.display_name && (
                    <p className="truncate text-xs text-muted-foreground">
                      {user.email}
                    </p>
                  )}
                </>
              )}
            </div>
            <Button
              variant="ghost"
              size="icon"
              onClick={handleLogout}
              disabled={isLoading}
              className="shrink-0 text-muted-foreground hover:text-foreground"
              aria-label="Log out"
            >
              <LogOut className="h-4 w-4" />
            </Button>
          </div>
        </div>
      </aside>

      {/* Main content area */}
      <div className="flex flex-1 flex-col lg:pl-64">
        {/* Mobile top bar */}
        <header className="flex h-14 items-center justify-between border-b border-border px-4 lg:hidden">
          <Link href="/dashboard" className="flex items-center gap-2">
            <img src="/logo-white.svg" alt="SmarTaste" className="h-6 w-6" />
            <span className="text-lg font-bold tracking-tight" style={{ fontFamily: "var(--font-heading)" }}>
              <span className="text-purple-500">Smar</span>Taste
            </span>
          </Link>
          <div className="flex items-center gap-2">
            {isLoading ? (
              <div className="h-4 w-16 rounded bg-muted animate-pulse" />
            ) : (
              <span className="text-sm text-muted-foreground">
                {user?.display_name || user?.email || "User"}
              </span>
            )}
            <Button
              variant="ghost"
              size="icon"
              onClick={handleLogout}
              disabled={isLoading}
              className="text-muted-foreground hover:text-foreground"
              aria-label="Log out"
            >
              <LogOut className="h-4 w-4" />
            </Button>
          </div>
        </header>

        {/* Enrichment progress bar (visible to all users when active) */}
        {enrichmentActive && enrichmentData && (
          <div className="flex items-center gap-3 border-b border-border bg-card/80 px-4 py-2">
            <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-purple-400" />
            <div className="flex-1 min-w-0">
              <div className="flex items-center justify-between mb-1">
                <span className="text-xs text-muted-foreground">
                  {enrichmentData.indexing ? "Indexing" : "Analyzing your library"}
                  {enrichmentData.indexing && (
                    <span className="ml-1.5 inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-purple-400" />
                  )}
                </span>
                <span className="text-xs font-mono font-medium text-purple-300">
                  {enrichmentData.enriched_songs}/{enrichmentData.total_songs}
                </span>
              </div>
              <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
                <div
                  className="h-full rounded-full bg-purple-500 transition-all duration-1000"
                  style={{
                    width: `${Math.round(
                      (enrichmentData.enriched_songs / enrichmentData.total_songs) * 100
                    )}%`,
                  }}
                />
              </div>
            </div>
          </div>
        )}

        {/* Page content — shows skeleton pulse while auth is loading */}
        <main className={
          isChat
            ? "flex-1 overflow-hidden"
            : "flex-1 p-4 pb-20 sm:p-6 lg:pb-6"
        }>
          {isLoading ? (
            <div className="space-y-4 animate-pulse">
              <div className="h-8 w-48 rounded bg-muted" />
              <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
                <div className="h-20 rounded-lg bg-muted" />
                <div className="h-20 rounded-lg bg-muted" />
                <div className="h-20 rounded-lg bg-muted" />
                <div className="h-20 rounded-lg bg-muted" />
              </div>
              <div className="h-48 rounded-lg bg-muted" />
            </div>
          ) : (
            children
          )}
        </main>
      </div>

      {/* Mobile bottom nav */}
      <nav className={`fixed bottom-0 left-0 right-0 flex items-center justify-around border-t border-border bg-card py-2 lg:hidden ${isChat ? "hidden" : ""}`}>
        {navItems.map((item) => (
          <Link
            key={item.href}
            href={item.href}
            className="flex flex-col items-center gap-1 px-3 py-1 text-muted-foreground transition-colors hover:text-foreground"
          >
            <item.icon className="h-5 w-5" />
            <span className="text-xs">{item.label}</span>
          </Link>
        ))}
      </nav>

    </div>
  );
}
