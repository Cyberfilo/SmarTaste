"use client";

import { useEffect } from "react";
import { useSearchParams } from "next/navigation";
import { toast } from "sonner";
import { ServiceConnections } from "@/components/settings/service-connections";
import { BYOKKeyManager } from "@/components/settings/byok-key-manager";

export default function SettingsPage() {
  const searchParams = useSearchParams();

  // Handle OAuth callbacks (Spotify redirects back here with query params)
  useEffect(() => {
    const service = searchParams.get("service");
    const connStatus = searchParams.get("status");
    const detail = searchParams.get("detail");
    if (service && connStatus === "connected") {
      toast.success(`${service.charAt(0).toUpperCase() + service.slice(1)} connected!`);
    } else if (service && connStatus === "error") {
      toast.error(`Failed to connect ${service}`, {
        description: detail || "Please try again.",
      });
    }
    if (service && connStatus) {
      // Clean the URL without causing a navigation
      window.history.replaceState({}, "", "/settings");
    }
  }, [searchParams]);

  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Settings</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Manage your music services and AI configuration
        </p>
      </div>
      <ServiceConnections />
      <BYOKKeyManager />
    </div>
  );
}
