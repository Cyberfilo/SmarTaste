"use client";

import { useEffect, useState } from "react";
import { useRouter, usePathname } from "next/navigation";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { useAuthStore } from "@/stores/auth-store";
import { authApi, apiFetch } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { DevPanel } from "@/components/admin/dev-panel";
import {
  LayoutDashboard,
  ListMusic,
  MessageCircle,
  Settings,
  LogOut,
  Code,
  User,
  Sparkles,
  Loader2,
} from "lucide-react";

/**
 * Protected app layout with navigation shell.
 * Checks auth on mount, shows loading spinner while checking.
 * Mobile: bottom tab bar (fixed). Desktop lg+: sidebar (fixed left, w-64).
 */
export default function AppLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const { user, isLoading, isAuthenticated, checkAuth, clearUser } =
    useAuthStore();
  const isChat = pathname === "/chat";
  const [devView, setDevView] = useState(false);
  const isAdmin = user?.is_admin ?? false;

  // Poll user's own enrichment progress (no admin required)
  const { data: enrichmentData } = useQuery<{
    total_songs: number;
    enriched_songs: number;
    percentage: number;
    complete: boolean;
  }>({
    queryKey: ["enrichment-status"],
    queryFn: () => apiFetch("/api/taste/enrichment-status"),
    enabled: isAuthenticated,
    refetchInterval: 5000,
  });

  const enrichmentActive = enrichmentData
    ? !enrichmentData.complete && enrichmentData.total_songs > 0
    : false;

  // Keep triggering /me periodically while enrichment is incomplete
  // Each /me call processes up to 50 un-enriched songs in background
  useEffect(() => {
    if (!enrichmentActive) return;
    const interval = setInterval(() => {
      authApi.me().catch(() => {});
    }, 30000); // Every 30s while enrichment is active
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

  if (isLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" />
      </div>
    );
  }

  if (!isAuthenticated) {
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
      {/* Desktop sidebar */}
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
              <p className="truncate text-sm font-medium">
                {user?.display_name || user?.email || "User"}
              </p>
              {user?.display_name && (
                <p className="truncate text-xs text-muted-foreground">
                  {user.email}
                </p>
              )}
            </div>
            <Button
              variant="ghost"
              size="icon"
              onClick={handleLogout}
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
            <span className="text-sm text-muted-foreground">
              {user?.display_name || user?.email || "User"}
            </span>
            <Button
              variant="ghost"
              size="icon"
              onClick={handleLogout}
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
                  Analyzing your library
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

        {/* Admin dev/user toggle bar */}
        {isAdmin && (
          <div className="flex items-center justify-end gap-2 border-b border-purple-500/20 bg-[#0D0B1A]/80 px-4 py-1.5">
            <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
              {devView ? "Dev View" : "User View"}
            </span>
            <button
              onClick={() => setDevView(!devView)}
              className={`flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-medium transition-colors ${
                devView
                  ? "bg-purple-500/20 text-purple-300"
                  : "bg-muted text-muted-foreground hover:text-foreground"
              }`}
            >
              {devView ? <Code className="h-3 w-3" /> : <User className="h-3 w-3" />}
              {devView ? "Dev" : "User"}
            </button>
          </div>
        )}

        {/* Page content -- chat page manages its own padding and height */}
        <main className={
          isChat
            ? "flex-1 overflow-hidden"
            : `flex-1 p-4 pb-20 sm:p-6 lg:pb-6 ${devView ? "pb-[45vh]" : ""}`
        }>
          {children}
        </main>
      </div>

      {/* Mobile bottom nav -- hidden on chat page which has its own input bar */}
      <nav className={`fixed bottom-0 left-0 right-0 flex items-center justify-around border-t border-border bg-card py-2 lg:hidden ${isChat ? "hidden" : ""} ${devView ? "bottom-[40vh]" : ""}`}>
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

      {/* Admin dev panel (slides in from bottom) */}
      {isAdmin && <DevPanel isOpen={devView} />}
    </div>
  );
}
